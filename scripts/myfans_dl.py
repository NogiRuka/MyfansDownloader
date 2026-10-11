from __future__ import annotations

import os
import re
import sys
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests
from PIL import Image, ImageOps
from tqdm import tqdm

from scripts.api import (
    MyFansClient,
    collect_image_urls,
    find_video_poster_url,
    get_video_info,
    has_auth_token,
    media_headers,
    read_headers_from_file,
    videos_from_payload,
)
from scripts.filename_utils import (
    generate_filename,
    generate_metadata,
    read_filename_config,
    update_file_date,
    validate_filename_config,
)
from scripts.hls import download_stream
from scripts.settings_loader import (
    download_poster_enabled,
    load_config,
    organization_settings,
    output_directory,
    segment_thread_count,
    thread_count,
)
from scripts.utils import (
    clean_filename,
    emit,
    get_post_date,
    height_for_resolution,
    normalize_resolution,
    pick_video_variant,
    post_is_available,
    setup_logging,
    verify_video_file,
)

logger = setup_logging()


def _progress_label(filename: str, max_length: int = 48) -> str:
    """生成适合在命令行进度中显示的简短作品名称。"""

    stem = os.path.splitext(filename)[0]
    if len(stem) <= max_length:
        return stem
    return f"{stem[: max_length - 1]}…"


def _extension_from_url(url: str, default: str = ".jpg") -> str:
    path = urlparse(url).path
    ext = os.path.splitext(path)[1]
    if ext and len(ext) <= 5:
        return ext
    return default


def _filter_posts(posts: List[Dict[str, Any]], download_type: str) -> List[Dict[str, Any]]:
    if download_type == "free":
        return [post for post in posts if post.get("free")]
    if download_type in {"subscribed", "subscribe"}:
        return [post for post in posts if not post.get("free")]
    return posts


def _list_posts(client: MyFansClient, user: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    posts = []
    for post in client.iter_all_posts(user, kind=kind):
        if post.get("kind") == kind or not post.get("kind"):
            posts.append(post)
    return posts


def list_creator_posts(
    client: MyFansClient,
    username: str,
    post_type: str = "videos",
    download_type: str = "all",
) -> List[Dict[str, Any]]:
    """读取并筛选创作者作品列表。"""

    user = client.get_user_by_username(username)
    if not user.get("id"):
        raise ValueError("无法获取创作者信息，请检查用户名和登录令牌。")
    kind = "image" if post_type == "images" else "video"
    return _filter_posts(_list_posts(client, user, kind), download_type)


def _season_folder_name(pattern: str, number: int) -> str:
    """根据用户模板生成安全的分卷目录名。"""

    template = (pattern or "S{number:02d}").strip()
    if "{number" not in template:
        template = f"{template}{{number:02d}}"
    try:
        name = template.format(number=number)
    except (KeyError, ValueError):
        name = f"S{number:02d}"
    return clean_filename(name, max_length=60)


def build_season_plan(
    posts: List[Dict[str, Any]],
    enabled: bool = True,
    size: int = 20,
    pattern: str = "S{number:02d}",
    order: str = "oldest",
) -> List[Dict[str, Any]]:
    """按发布时间排序作品，并为每个作品分配分卷目录。"""

    dated = [post for post in posts if get_post_date(post) is not None]
    undated = [post for post in posts if get_post_date(post) is None]
    reverse = str(order).lower() == "newest"
    dated.sort(key=lambda post: get_post_date(post).timestamp(), reverse=reverse)
    ordered = dated + undated
    group_size = max(1, int(size or 20))
    plan = []
    for index, post in enumerate(ordered):
        number = index // group_size + 1
        plan.append(
            {
                "post": post,
                "season_number": number,
                "folder": _season_folder_name(pattern, number) if enabled else None,
            }
        )
    return plan


def download_image_bytes(session: requests.Session, url: str, headers: Dict[str, str], dest: str) -> bool:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    response = session.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    with open(dest, "wb") as handle:
        handle.write(response.content)
    return os.path.getsize(dest) > 0


def download_image_as_jpeg(
    session: requests.Session,
    url: str,
    headers: Dict[str, str],
    dest: str,
) -> bool:
    """下载图片并统一转换为 JPEG，透明区域使用白色背景。"""

    response = session.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with Image.open(BytesIO(response.content)) as source:
        image = ImageOps.exif_transpose(source)
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            converted = Image.new("RGB", rgba.size, "white")
            converted.paste(rgba, mask=rgba.getchannel("A"))
        else:
            converted = image.convert("RGB")
        converted.save(dest, format="JPEG", quality=95, optimize=True)
    return os.path.getsize(dest) > 0


def download_video_poster(
    post: Dict[str, Any],
    video: Dict[str, Any],
    video_path: str,
    client: MyFansClient,
    progress_queue=None,
) -> bool:
    """下载视频封面，并使用“视频名-poster”作为文件名。"""

    poster_url = find_video_poster_url(video, post)
    if not poster_url:
        emit(progress_queue, f"作品 {post.get('id', '')} 没有找到封面。", "warning")
        return False

    video_stem = os.path.splitext(os.path.basename(video_path))[0]
    poster_path = os.path.join(os.path.dirname(video_path), f"{video_stem}-poster.jpg")
    if os.path.exists(poster_path) and os.path.getsize(poster_path) > 0:
        _remove_stale_poster_files(video_path, poster_path)
        update_file_date(post, poster_path)
        emit(progress_queue, f"封面已存在：{os.path.basename(poster_path)}")
        return True

    try:
        downloaded = download_image_as_jpeg(
            client.session,
            poster_url,
            media_headers(client.headers),
            poster_path,
        )
        if not downloaded:
            raise OSError("下载结果为空")
        _remove_stale_poster_files(video_path, poster_path)
        update_file_date(post, poster_path)
        emit(progress_queue, f"封面下载完成：{os.path.basename(poster_path)}")
        return True
    except (OSError, requests.RequestException) as exc:
        emit(progress_queue, f"封面下载失败，但视频不受影响：{exc}", "warning")
        return False


def _remove_stale_poster_files(video_path: str, current_poster_path: str) -> None:
    """删除同一视频由旧封面规则生成的其他图片文件。"""

    video_file = Path(video_path)
    current = Path(current_poster_path)
    supported_extensions = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
    for candidate in video_file.parent.glob(f"{video_file.stem}-poster.*"):
        if candidate == current or candidate.suffix.lower() not in supported_extensions:
            continue
        try:
            candidate.unlink()
        except OSError:
            logger.warning("无法删除旧封面文件：%s", candidate)


def process_image_post(
    post_id: str,
    client: MyFansClient,
    output_dir: str,
    filename_config: Dict[str, str],
    progress_queue=None,
    download_state=None,
) -> bool:
    try:
        if download_state and download_state.is_completed(post_id):
            emit(progress_queue, f"图片作品 {post_id} 已下载，跳过。")
            return True
        data = client.get_post(post_id)
        if not post_is_available(data):
            emit(progress_queue, f"无法访问图片作品 {post_id}。", "error")
            if download_state:
                download_state.mark_failed(post_id, "无访问权限")
            return False
        urls = collect_image_urls(data)
        if not urls:
            emit(progress_queue, f"作品 {post_id} 中没有找到图片。", "error")
            if download_state:
                download_state.mark_failed(post_id, "没有找到图片")
            return False
        username = (data.get("user") or {}).get("username") or "unknown"
        folder = os.path.join(output_dir, username, "images")
        os.makedirs(folder, exist_ok=True)
        for idx, url in enumerate(urls):
            ext = _extension_from_url(url)
            index = idx + 1 if len(urls) > 1 else None
            filename = generate_filename(data, filename_config, folder, ext=ext, index=index)
            full_path = os.path.join(folder, filename)
            if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
                update_file_date(data, full_path)
                generate_metadata(data, filename, folder, ext.lstrip("."))
                continue
            if not download_image_bytes(client.session, url, media_headers(client.headers), full_path):
                raise RuntimeError(f"图片下载结果为空：{url}")
            update_file_date(data, full_path)
            generate_metadata(data, filename, folder, ext.lstrip("."))
            emit(progress_queue, f"图片下载完成：{filename}")
        if download_state:
            download_state.mark_completed(post_id)
        return True
    except Exception as exc:
        emit(progress_queue, f"下载作品 {post_id} 的图片时出错：{exc}", "error")
        if download_state:
            download_state.mark_failed(post_id, str(exc))
        return False


def handle_image_download(post_id, session, headers, output_dir, filename_config, progress_queue=None):
    client = MyFansClient(headers=headers, session=session)
    return process_image_post(post_id, client, output_dir, filename_config, progress_queue)


def process_video_post(
    post_id: str,
    client: MyFansClient,
    selected_resolution: str,
    output_dir: str,
    filename_config: Dict[str, str],
    progress_queue=None,
    download_state=None,
    target_subfolder: Optional[str] = None,
    download_poster: bool = True,
) -> bool:
    try:
        if download_state and download_state.is_completed(post_id):
            emit(progress_queue, f"视频作品 {post_id} 已下载或已归档，跳过。")
            return True
        if download_state:
            download_state.add_download(post_id, status="in_progress")
        data = client.get_post(post_id)
        if not post_is_available(data):
            emit(progress_queue, f"无法访问作品 {post_id}。", "error")
            if download_state:
                download_state.mark_failed(post_id, "无访问权限")
            return False
        try:
            video_payload = client.get_post_videos(post_id)
        except requests.RequestException:
            video_payload = data.get("videos")
        variants = videos_from_payload(video_payload) or videos_from_payload(data.get("videos"))
        main_variants = [item for item in variants if item.get("source") != "trial"]
        chosen = pick_video_variant(main_variants, selected_resolution)
        if not chosen:
            emit(progress_queue, f"作品 {post_id} 没有可用的视频地址，可能尚未解锁。", "error")
            if download_state:
                download_state.mark_failed(post_id, "没有可用的视频地址")
            return False

        username = (data.get("user") or {}).get("username") or "unknown"
        folder = os.path.join(output_dir, username, "videos")
        if target_subfolder:
            folder = os.path.join(folder, clean_filename(target_subfolder, max_length=60))
        os.makedirs(folder, exist_ok=True)
        filename = generate_filename(data, filename_config, folder, ext=".mp4")
        full_path = os.path.join(folder, filename)

        if os.path.exists(full_path) and verify_video_file(full_path):
            generate_metadata(data, filename, folder)
            update_file_date(data, full_path)
            emit(progress_queue, f"文件已存在且检查正常：{filename}")
            if download_poster:
                download_video_poster(data, chosen, full_path, client, progress_queue)
            if download_state:
                download_state.mark_completed(post_id, full_path)
            return True
        if os.path.exists(full_path):
            emit(progress_queue, f"发现损坏文件，将重新下载：{filename}", "warning")
            try:
                os.remove(full_path)
            except OSError:
                pass

        def on_progress(message: str) -> None:
            emit(progress_queue, message)
            if not download_state:
                return
            ffmpeg_match = re.search(r"视频进度 .*?：(\d+)%", message)
            segment_total_match = re.search(r"共需下载 (\d+) 个视频分片", message)
            segment_progress_match = re.search(r"分片进度：(\d+)/(\d+)", message)
            if ffmpeg_match:
                download_state.update_progress(post_id, int(ffmpeg_match.group(1)), 100)
            elif segment_total_match:
                download_state.update_progress(post_id, 0, int(segment_total_match.group(1)))
            elif segment_progress_match:
                download_state.update_progress(
                    post_id,
                    int(segment_progress_match.group(1)),
                    int(segment_progress_match.group(2)),
                )

        success = download_stream(
            chosen["url"],
            full_path,
            media_headers(client.headers),
            _progress_label(filename),
            requested_height=height_for_resolution(selected_resolution),
            progress=on_progress,
            segment_threads=segment_thread_count(),
        )
        if success:
            generate_metadata(data, filename, folder)
            update_file_date(data, full_path)
            emit(progress_queue, f"视频下载完成：{filename}")
            if download_poster:
                download_video_poster(data, chosen, full_path, client, progress_queue)
            if download_state:
                download_state.mark_completed(post_id, full_path)
            return True
        emit(progress_queue, f"作品 {post_id} 的视频下载失败。", "error")
        if download_state:
            download_state.mark_failed(post_id, "下载失败")
        return False
    except Exception as exc:
        emit(progress_queue, f"处理作品 {post_id} 时出错：{exc}", "error")
        if download_state:
            download_state.mark_failed(post_id, str(exc))
        return False


def process_post_id(
    input_post_id,
    session,
    headers,
    selected_resolution,
    output_dir,
    filename_config,
    progress_bar=None,
    progress_queue=None,
):
    client = MyFansClient(headers=headers, session=session)
    try:
        return process_video_post(
            input_post_id,
            client,
            selected_resolution,
            output_dir,
            filename_config,
            progress_queue=progress_queue,
        )
    finally:
        if progress_bar:
            progress_bar.update(1)


def download_videos_concurrently(
    session,
    post_ids,
    selected_resolution,
    output_dir,
    filename_config,
    progress_queue=None,
    max_workers=1,
    download_state=None,
    headers=None,
    post_folders=None,
    download_poster=True,
):
    headers = headers or read_headers_from_file("header.txt")
    client = MyFansClient(headers=headers, session=session)
    total = len(post_ids)
    emit(progress_queue, f"准备下载 {total} 个视频作品。")
    workers = max(1, int(max_workers or 1))
    progress_bar = tqdm(
        total=total,
        desc="下载视频",
        unit="个",
        disable=progress_queue is not None,
    )
    if workers == 1:
        for post_id in post_ids:
            process_video_post(
                post_id,
                client,
                selected_resolution,
                output_dir,
                filename_config,
                progress_queue,
                download_state,
                target_subfolder=(post_folders or {}).get(str(post_id)),
                download_poster=download_poster,
            )
            progress_bar.update(1)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def worker(post_id: str) -> None:
            thread_client = MyFansClient(headers=headers)
            process_video_post(
                post_id,
                thread_client,
                selected_resolution,
                output_dir,
                filename_config,
                progress_queue,
                download_state,
                target_subfolder=(post_folders or {}).get(str(post_id)),
                download_poster=download_poster,
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(worker, post_id) for post_id in post_ids]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    emit(progress_queue, f"下载过程中发生错误：{exc}", "error")
                progress_bar.update(1)
    progress_bar.close()
    emit(progress_queue, "视频下载流程已结束。")


def download_images_concurrently(
    session,
    post_ids,
    output_dir,
    filename_config,
    progress_queue=None,
    download_state=None,
    max_workers=1,
    headers=None,
):
    headers = headers or read_headers_from_file("header.txt")
    client = MyFansClient(headers=headers, session=session)
    total = len(post_ids)
    emit(progress_queue, f"准备下载 {total} 个图片作品。")
    progress_bar = tqdm(
        total=total,
        desc="下载图片",
        unit="个",
        disable=progress_queue is not None,
    )
    workers = max(1, int(max_workers or 1))
    if workers == 1:
        for post_id in post_ids:
            process_image_post(post_id, client, output_dir, filename_config, progress_queue, download_state)
            progress_bar.update(1)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def worker(post_id: str) -> None:
            thread_client = MyFansClient(headers=headers)
            process_image_post(
                post_id, thread_client, output_dir, filename_config, progress_queue, download_state
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(worker, post_id) for post_id in post_ids]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    emit(progress_queue, f"下载过程中发生错误：{exc}", "error")
                progress_bar.update(1)
    progress_bar.close()
    emit(progress_queue, "图片下载流程已结束。")


def download_single_file(session, post_id, selected_resolution, output_dir, filename_config):
    headers = read_headers_from_file("header.txt")
    client = MyFansClient(headers=headers, session=session)
    process_video_post(post_id, client, selected_resolution, output_dir, filename_config)


def start_download(
    username,
    post_type,
    download_type,
    progress_queue,
    download_state=None,
    post_id=None,
    resolution="best",
    season_number=None,
    selected_post_ids=None,
):
    try:
        config = load_config()
        output_dir = output_directory(config)
        filename_config = read_filename_config(config)
        validate_filename_config(filename_config)
        headers = read_headers_from_file("header.txt")
        if not has_auth_token(headers):
            emit(
                progress_queue,
                "提醒：尚未设置登录令牌，只能下载免费或公开内容。",
                "warning",
            )
        session = requests.Session()
        session.headers.update(headers)
        client = MyFansClient(headers=headers, session=session)
        workers = thread_count(config)
        download_poster = download_poster_enabled(config)
        organization = organization_settings(config)
        resolution = normalize_resolution(resolution or "best")

        if post_id:
            emit(progress_queue, f"开始处理作品：{post_id}")
            if post_type == "images":
                process_image_post(
                    post_id, client, output_dir, filename_config, progress_queue, download_state
                )
            else:
                process_video_post(
                    post_id,
                    client,
                    resolution,
                    output_dir,
                    filename_config,
                    progress_queue,
                    download_state,
                    download_poster=download_poster,
                )
            return

        if not username:
            emit(progress_queue, "批量下载需要填写创作者用户名。", "error")
            return

        type_name = "图片" if post_type == "images" else "视频"
        emit(progress_queue, f"开始获取创作者 {username} 的{type_name}作品。")
        try:
            user = client.get_user_by_username(username)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (401, 403):
                emit(progress_queue, "MyFans 拒绝了访问，请检查设置中的登录令牌。", "error")
                return
            raise
        user_id = user.get("id")
        if not user_id:
            emit(progress_queue, "无法获取创作者信息，请检查用户名和登录令牌。", "error")
            return
        emit(progress_queue, f"已找到创作者，用户 ID：{user_id}")

        kind = "image" if post_type == "images" else "video"
        kind_name = "图片" if kind == "image" else "视频"
        emit(progress_queue, f"正在获取{kind_name}作品列表……")
        posts = _filter_posts(_list_posts(client, user, kind), download_type)
        emit(progress_queue, f"共有 {len(posts)} 个{kind_name}作品符合条件。")
        post_folders = {}
        requested_post_ids = list(
            dict.fromkeys(str(selected_id) for selected_id in (selected_post_ids or []) if selected_id)
        )
        requested_post_id_set = set(requested_post_ids)
        if kind == "video":
            plan = build_season_plan(
                posts,
                enabled=bool(organization["season_enabled"]),
                size=int(organization["season_size"]),
                pattern=str(organization["season_pattern"]),
                order=str(organization["season_order"]),
            )
            if organization["season_enabled"] and season_number not in (None, "", "all"):
                raw_seasons = season_number if isinstance(season_number, list) else [season_number]
                selected_seasons = set()
                for value in raw_seasons:
                    try:
                        selected_seasons.add(max(1, int(value)))
                    except (TypeError, ValueError):
                        continue
                if selected_seasons:
                    plan = [item for item in plan if item["season_number"] in selected_seasons]
                    labels = "、".join(str(number) for number in sorted(selected_seasons))
                    emit(progress_queue, f"本次下载第 {labels} 卷，共 {len(plan)} 个作品。")
            if requested_post_id_set:
                plan = [
                    item
                    for item in plan
                    if str(item["post"].get("id")) in requested_post_id_set
                ]
                emit(progress_queue, f"本次仅下载选中的 {len(plan)} 个作品。")
            posts = [item["post"] for item in plan]
            post_folders = {
                str(item["post"].get("id")): item["folder"]
                for item in plan
                if item["post"].get("id") and item["folder"]
            }
        elif requested_post_id_set:
            posts = [post for post in posts if str(post.get("id")) in requested_post_id_set]
            emit(progress_queue, f"本次仅下载选中的 {len(posts)} 个作品。")
        post_ids = [post.get("id") for post in posts if post.get("id")]
        if not post_ids:
            emit(progress_queue, "没有符合当前条件的作品。")
            return
        if kind == "video":
            download_videos_concurrently(
                session,
                post_ids,
                resolution,
                output_dir,
                filename_config,
                progress_queue,
                max_workers=workers,
                download_state=download_state,
                headers=headers,
                post_folders=post_folders,
                download_poster=download_poster,
            )
        else:
            download_images_concurrently(
                session,
                post_ids,
                output_dir,
                filename_config,
                progress_queue,
                download_state,
                max_workers=workers,
                headers=headers,
            )
    except Exception as exc:
        emit(progress_queue, f"任务执行失败：{exc}", "error")
    finally:
        if progress_queue is not None:
            progress_queue.put("DONE")


def _prompt_resolution() -> str:
    print("Select quality:")
    print("1. Best available")
    print("2. 2160p (4K)")
    print("3. 1080p")
    print("4. 720p")
    print("5. 480p")
    print("6. 360p")
    choice = input("Enter your choice (1-6, default 1): ").strip() or "1"
    return {
        "1": "best",
        "2": "uhd",
        "3": "fhd",
        "4": "hd",
        "5": "sd",
        "6": "ld",
    }.get(choice, "best")


def main() -> None:
    config = load_config()
    output_dir = output_directory(config)
    filename_config = read_filename_config(config)
    validate_filename_config(filename_config)
    try:
        headers = read_headers_from_file("header.txt")
    except FileNotFoundError as exc:
        print(exc)
        print("Create header.txt with: authorization: Token token=YOUR_TOKEN")
        sys.exit(1)
    if not has_auth_token(headers):
        print("Warning: authorization token is empty. Free content may still download.")

    session = requests.Session()
    session.headers.update(headers)
    client = MyFansClient(headers=headers, session=session)

    print("Select an option:")
    print("1. Download all video posts")
    print("2. Download a single video post by ID")
    print("3. List video post IDs")
    choice = input("Enter your choice (1/2/3): ").strip()

    if choice == "2":
        post_id = input("Enter the post ID to download: ").strip()
        if not post_id:
            print("Post ID is required.")
            return
        download_single_file(session, post_id, _prompt_resolution(), output_dir, filename_config)
        return
    if choice not in {"1", "3"}:
        print("Invalid choice.")
        return

    while True:
        name_creator = input("Enter a creator's username (without @) or type '0' to exit: ").strip()
        if name_creator == "0":
            return
        try:
            user = client.get_user_by_username(name_creator)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (401, 403):
                print("API returned 401/403. Check the token in header.txt.")
            else:
                print(f"An error occurred while connecting to the API: {exc}")
            continue
        except requests.RequestException as exc:
            print(f"An error occurred while connecting to the API: {exc}")
            continue
        if user.get("id"):
            break
        print("Failed to retrieve user ID. Please try again.")

    print("Fetching posts...")
    posts = _list_posts(client, user, "video")
    print(f"Total video posts found: {len(posts)}")
    print("Select which posts to download:")
    print("1. Free posts only")
    print("2. Subscribe posts only")
    print("3. All posts")
    save_choice = input("Enter your choice (1/2/3): ").strip()
    mapping = {"1": "free", "2": "subscribed", "3": "all"}
    filtered = _filter_posts(posts, mapping.get(save_choice, "all"))
    post_ids = [post.get("id") for post in filtered if post.get("id")]
    if not post_ids:
        print("No posts match the selected criteria.")
        return
    if choice == "3":
        print("\n".join(str(post_id) for post_id in post_ids))
        print(f"\n{len(post_ids)} post IDs")
        return
    download_videos_concurrently(
        session,
        post_ids,
        _prompt_resolution(),
        output_dir,
        filename_config,
        max_workers=thread_count(config),
        headers=headers,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)

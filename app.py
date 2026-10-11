from flask import Flask, Response, jsonify, render_template, request
import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from queue import Empty, Queue

import requests

import scripts.myfans_dl as downloader
from scripts.api import MyFansClient, has_auth_token, read_headers_from_file, write_auth_token
from scripts.download_state import DownloadState
from scripts.filename_utils import generate_filename, read_filename_config
from scripts.paths import ensure_dir, log_file_path
from scripts.settings_loader import (
    current_settings,
    load_config,
    organization_settings,
    output_directory,
    save_config,
)
from scripts.utils import get_post_date

log_path = log_file_path()
ensure_dir(log_path.parent)
file_handler = RotatingFileHandler(log_path, maxBytes=10485760, backupCount=5, encoding="utf-8")
console_handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)
logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])
logger = logging.getLogger(__name__)

app = Flask(__name__)
progress_queue = Queue()
download_state = DownloadState()
download_lock = threading.Lock()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/status")
def get_status():
    state = download_state.get_serializable_state()
    state["running"] = download_lock.locked()
    return jsonify(state)


@app.route("/download", methods=["POST"])
def start_download():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    post_type = data.get("type", "videos")
    download_type = data.get("download_type", "all")
    post_id = (data.get("post_id") or "").strip() or None
    resolution = data.get("resolution", "best")
    season_numbers = data.get("season_numbers", data.get("season_number"))
    selected_post_ids = data.get("post_ids")

    if selected_post_ids is not None and not isinstance(selected_post_ids, list):
        return jsonify({"error": "作品列表格式不正确。"}), 400
    if isinstance(selected_post_ids, list):
        selected_post_ids = list(
            dict.fromkeys(str(selected_id).strip() for selected_id in selected_post_ids)
        )
        selected_post_ids = [selected_id for selected_id in selected_post_ids if selected_id]
        if not selected_post_ids:
            return jsonify({"error": "请至少选择一个作品。"}), 400
        if len(selected_post_ids) > 1000:
            return jsonify({"error": "单次最多选择 1000 个作品。"}), 400

    if not post_id and not username:
        return jsonify({"error": "请输入创作者用户名或作品 ID。"}), 400
    if not download_lock.acquire(blocking=False):
        return jsonify({"error": "已有下载任务正在执行，请等待当前任务结束。"}), 409

    logger.info(
        "Starting download request - Username: %s, Type: %s, Mode: %s, PostID: %s, Resolution: %s",
        username,
        post_type,
        download_type,
        post_id,
        resolution,
    )

    def download_thread():
        try:
            downloader.start_download(
                username,
                post_type,
                download_type,
                progress_queue,
                download_state,
                post_id=post_id,
                resolution=resolution,
                season_number=season_numbers,
                selected_post_ids=selected_post_ids,
            )
        except Exception as exc:
            error = f"下载任务发生错误：{exc}"
            logger.exception(error)
            progress_queue.put(error)
            progress_queue.put("DONE")
        finally:
            download_lock.release()

    threading.Thread(target=download_thread, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/progress")
def progress():
    def generate():
        try:
            while True:
                try:
                    message = progress_queue.get(timeout=15)
                    text = str(message).replace("\r", " ").replace("\n", " ")
                    yield f"data: {text}\n\n"
                    if message == "DONE":
                        break
                except Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            return
        except Exception as exc:
            logger.error("Error in progress stream: %s", exc)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/test_post/<post_id>")
def test_post(post_id):
    try:
        session = requests.Session()
        headers = read_headers_from_file("header.txt")
        data, resolution_info, error = downloader.get_video_info(post_id, session, headers)
        if error:
            return jsonify({"error": error}), 400
        return jsonify(
            {
                "post_type": "video"
                if data.get("videos")
                else "image"
                if data.get("images")
                else "unknown",
                "available": data.get("available", True),
                "is_free": data.get("free", False),
                "available_resolutions": list(resolution_info.keys()) if resolution_info else [],
                "title": data.get("title") or data.get("body", ""),
                "created_at": data.get("published_at") or data.get("created_at", ""),
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/creator-posts")
def creator_posts():
    username = (request.args.get("username") or "").strip().lstrip("@")
    post_type = request.args.get("type", "videos")
    download_type = request.args.get("download_type", "all")
    if not username:
        return jsonify({"error": "请输入创作者用户名。"}), 400
    try:
        headers = read_headers_from_file("header.txt")
        session = requests.Session()
        session.headers.update(headers)
        client = MyFansClient(headers=headers, session=session)
        posts = downloader.list_creator_posts(client, username, post_type, download_type)
        config = load_config()
        organization = organization_settings(config)
        filename_config = read_filename_config(config)
        video_root = os.path.join(output_directory(config), username, "videos")
        plan = downloader.build_season_plan(
            posts,
            enabled=bool(organization["season_enabled"]),
            size=int(organization["season_size"]),
            pattern=str(organization["season_pattern"]),
            order=str(organization["season_order"]),
        )
        state = download_state.get_serializable_state()
        completed = set(state.get("completed_files") or [])
        downloads = state.get("downloads") or {}
        items = []
        for entry in plan:
            post = entry["post"]
            post_id = str(post.get("id") or "")
            detail = downloads.get(post_id) or {}
            status = detail.get("status", "pending")
            if post_type == "videos":
                filename = generate_filename(post, filename_config, video_root, ext=".mp4")
                expected_paths = []
                recorded_path = detail.get("file_path")
                if recorded_path:
                    expected_paths.append(recorded_path)
                if entry["folder"]:
                    expected_paths.append(os.path.join(video_root, entry["folder"], filename))
                expected_paths.append(os.path.join(video_root, filename))
                file_exists = any(
                    os.path.isfile(path) and os.path.getsize(path) > 0 for path in expected_paths
                )
                if file_exists:
                    status = "completed"
                elif post_id in completed or status == "completed":
                    status = "archived"
            elif post_id in completed:
                status = "completed"
            title = ""
            for key in ("title", "body", "content", "name"):
                value = post.get(key)
                if isinstance(value, str) and value.strip():
                    title = value.strip().splitlines()[0]
                    break
            date = get_post_date(post)
            items.append(
                {
                    "id": post_id,
                    "url": f"https://myfans.jp/posts/{post_id}",
                    "title": title or post_id,
                    "published_at": date.isoformat() if date else "",
                    "free": bool(post.get("free")),
                    "status": status,
                    "season_number": entry["season_number"],
                    "folder": entry["folder"],
                }
            )
        return jsonify(
            {
                "username": username,
                "total": len(items),
                "completed": sum(1 for item in items if item["status"] == "completed"),
                "archived": sum(1 for item in items if item["status"] == "archived"),
                "season_enabled": bool(organization["season_enabled"]),
                "items": items,
            }
        )
    except FileNotFoundError:
        return jsonify({"error": "尚未配置 header.txt，请先在设置中填写 MyFans 令牌。"}), 400
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        message = "MyFans 拒绝了访问，请检查登录令牌。" if status in (401, 403) else str(exc)
        return jsonify({"error": message}), 400
    except (requests.RequestException, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/download-state/<post_id>", methods=["POST"])
def update_download_state(post_id):
    data = request.get_json(silent=True) or {}
    status = str(data.get("status") or "").strip().lower()
    if status == "archived":
        download_state.mark_archived(post_id)
    elif status == "pending":
        download_state.mark_missing(post_id)
    else:
        return jsonify({"error": "状态只能设置为 archived 或 pending。"}), 400
    return jsonify({"status": "success"})


@app.route("/api/download-state", methods=["POST"])
def update_download_states():
    data = request.get_json(silent=True) or {}
    status = str(data.get("status") or "").strip().lower()
    post_ids = data.get("post_ids")
    if not isinstance(post_ids, list) or not post_ids:
        return jsonify({"error": "请至少选择一个作品。"}), 400
    normalized_ids = list(dict.fromkeys(str(post_id).strip() for post_id in post_ids))
    normalized_ids = [post_id for post_id in normalized_ids if post_id]
    if not normalized_ids:
        return jsonify({"error": "请至少选择一个有效作品。"}), 400
    if len(normalized_ids) > 1000:
        return jsonify({"error": "单次最多处理 1000 个作品。"}), 400
    if status not in {"archived", "pending"}:
        return jsonify({"error": "状态只能设置为 archived 或 pending。"}), 400
    download_state.set_status_many(normalized_ids, status)
    return jsonify({"status": "success", "updated": len(normalized_ids)})


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        return settings_api()
    return render_template("settings.html")


@app.route("/api/settings", methods=["GET", "POST"])
def settings_api():
    config = load_config()
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if not config.has_section("Filename"):
            config.add_section("Filename")
        if not config.has_section("Threads"):
            config.add_section("Threads")
        if not config.has_section("Settings"):
            config.add_section("Settings")
        if not config.has_section("Organization"):
            config.add_section("Organization")
        config.set("Filename", "pattern", str(data.get("filename_pattern") or "{date} {title}"))
        config.set("Filename", "separator", str(data.get("filename_separator") or "_"))
        try:
            threads = max(1, int(data.get("thread_count") or 3))
        except (TypeError, ValueError):
            threads = 3
        config.set("Threads", "threads", str(threads))
        if "write_metadata" in data:
            config.set("Settings", "write_metadata", "1" if data.get("write_metadata") else "0")
            os.environ["WRITE_METADATA"] = config.get("Settings", "write_metadata")
        if "download_poster" in data:
            config.set("Settings", "download_poster", "1" if data.get("download_poster") else "0")
            os.environ["DOWNLOAD_POSTER"] = config.get("Settings", "download_poster")
        config.set("Organization", "season_enabled", "1" if data.get("season_enabled", True) else "0")
        pattern = str(data.get("season_pattern") or "S{number:02d}").strip()
        config.set("Organization", "season_pattern", pattern)
        try:
            season_size = max(1, int(data.get("season_size") or 20))
        except (TypeError, ValueError):
            season_size = 20
        config.set("Organization", "season_size", str(season_size))
        order = str(data.get("season_order") or "oldest").strip().lower()
        config.set("Organization", "season_order", order if order in {"oldest", "newest"} else "oldest")
        token = (data.get("auth_token") or "").strip()
        if token:
            os.environ["AUTH_TOKEN"] = token
            try:
                write_auth_token(token)
            except OSError as exc:
                logger.warning("无法写入 header.txt：%s", exc)
        if config.has_option("Settings", "auth_token"):
            config.remove_option("Settings", "auth_token")
        os.environ["FILENAME_PATTERN"] = config.get("Filename", "pattern")
        os.environ["FILENAME_SEPARATOR"] = config.get("Filename", "separator")
        os.environ["THREAD_COUNT"] = config.get("Threads", "threads")
        os.environ["SEASON_ENABLED"] = config.get("Organization", "season_enabled")
        os.environ["SEASON_PATTERN"] = config.get("Organization", "season_pattern")
        os.environ["SEASON_SIZE"] = config.get("Organization", "season_size")
        os.environ["SEASON_ORDER"] = config.get("Organization", "season_order")
        save_config(config)
        return jsonify({"status": "success"})

    settings = current_settings(config)
    token_set = bool(str(settings.get("auth_token") or "").strip())
    try:
        headers = read_headers_from_file("header.txt")
        token_set = token_set or has_auth_token(headers)
    except FileNotFoundError:
        pass
    settings.pop("auth_token", None)
    settings["auth_token_set"] = token_set
    return jsonify(settings)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

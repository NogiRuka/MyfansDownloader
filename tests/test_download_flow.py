from __future__ import annotations

import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from queue import Queue
from unittest.mock import MagicMock, patch

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from MyfansDownloader_unified import _run_cli_entry
from scripts.utils import post_is_available


class StartDownloadDoneTests(unittest.TestCase):
    def test_start_download_always_emits_done(self):
        from scripts import myfans_dl

        queue = Queue()
        with patch.object(myfans_dl, "load_config", side_effect=RuntimeError("boom")):
            myfans_dl.start_download("user", "videos", "all", queue)
        items = []
        while not queue.empty():
            items.append(queue.get_nowait())
        self.assertIn("DONE", items)
        self.assertTrue(any("boom" in str(item) for item in items))

    def test_start_download_requires_username_for_bulk(self):
        from scripts import myfans_dl

        queue = Queue()
        fake_config = MagicMock()
        with patch.object(myfans_dl, "load_config", return_value=fake_config), patch.object(
            myfans_dl, "output_directory", return_value="downloads"
        ), patch.object(myfans_dl, "read_filename_config", return_value={"pattern": "{id}"}), patch.object(
            myfans_dl, "validate_filename_config", return_value=True
        ), patch.object(
            myfans_dl, "read_headers_from_file", return_value={"user-agent": "test"}
        ), patch.object(
            myfans_dl, "has_auth_token", return_value=False
        ), patch.object(
            myfans_dl, "thread_count", return_value=1
        ), patch.object(
            myfans_dl, "download_poster_enabled", return_value=True
        ), patch.object(
            myfans_dl,
            "organization_settings",
            return_value={
                "season_enabled": True,
                "season_pattern": "S{number:02d}",
                "season_size": 20,
                "season_order": "oldest",
            },
        ):
            myfans_dl.start_download("", "videos", "all", queue)
        items = []
        while not queue.empty():
            items.append(queue.get_nowait())
        self.assertEqual(items[-1], "DONE")
        self.assertTrue(any("创作者用户名" in str(item) for item in items))

    def test_start_download_only_sends_selected_season(self):
        from scripts import myfans_dl

        posts = [
            {"id": str(index), "published_at": f"2024-01-{index + 1:02d}T00:00:00Z"}
            for index in range(25)
        ]
        queue = Queue()
        client = MagicMock()
        client.get_user_by_username.return_value = {"id": "creator-id"}
        organization = {
            "season_enabled": True,
            "season_pattern": "S{number:02d}",
            "season_size": 20,
            "season_order": "oldest",
        }
        with patch.object(myfans_dl, "load_config", return_value=MagicMock()), patch.object(
            myfans_dl, "output_directory", return_value="downloads"
        ), patch.object(myfans_dl, "read_filename_config", return_value={"pattern": "{id}"}), patch.object(
            myfans_dl, "validate_filename_config", return_value=True
        ), patch.object(myfans_dl, "read_headers_from_file", return_value={"user-agent": "test"}), patch.object(
            myfans_dl, "has_auth_token", return_value=True
        ), patch.object(myfans_dl, "thread_count", return_value=1), patch.object(
            myfans_dl, "download_poster_enabled", return_value=True
        ), patch.object(myfans_dl, "organization_settings", return_value=organization), patch.object(
            myfans_dl, "MyFansClient", return_value=client
        ), patch.object(myfans_dl, "_list_posts", return_value=posts), patch.object(
            myfans_dl, "download_videos_concurrently"
        ) as download_mock:
            myfans_dl.start_download("creator", "videos", "all", queue, season_number=2)

        args, kwargs = download_mock.call_args
        self.assertEqual(args[1], ["20", "21", "22", "23", "24"])
        self.assertEqual(set(kwargs["post_folders"].values()), {"S02"})

    def test_start_download_accepts_multiple_seasons(self):
        from scripts import myfans_dl

        posts = [
            {"id": str(index), "published_at": f"2024-01-{index + 1:02d}T00:00:00Z"}
            for index in range(25)
        ]
        queue = Queue()
        client = MagicMock()
        client.get_user_by_username.return_value = {"id": "creator-id"}
        organization = {
            "season_enabled": True,
            "season_pattern": "S{number:02d}",
            "season_size": 10,
            "season_order": "oldest",
        }
        with patch.object(myfans_dl, "load_config", return_value=MagicMock()), patch.object(
            myfans_dl, "output_directory", return_value="downloads"
        ), patch.object(myfans_dl, "read_filename_config", return_value={"pattern": "{id}"}), patch.object(
            myfans_dl, "validate_filename_config", return_value=True
        ), patch.object(myfans_dl, "read_headers_from_file", return_value={"user-agent": "test"}), patch.object(
            myfans_dl, "has_auth_token", return_value=True
        ), patch.object(myfans_dl, "thread_count", return_value=1), patch.object(
            myfans_dl, "download_poster_enabled", return_value=True
        ), patch.object(myfans_dl, "organization_settings", return_value=organization), patch.object(
            myfans_dl, "MyFansClient", return_value=client
        ), patch.object(myfans_dl, "_list_posts", return_value=posts), patch.object(
            myfans_dl, "download_videos_concurrently"
        ) as download_mock:
            myfans_dl.start_download("creator", "videos", "all", queue, season_number=[1, 3])

        args, kwargs = download_mock.call_args
        self.assertEqual(args[1], [str(index) for index in range(10)] + [str(index) for index in range(20, 25)])
        self.assertEqual(set(kwargs["post_folders"].values()), {"S01", "S03"})


class LauncherTests(unittest.TestCase):
    def test_cli_entry_swallows_system_exit(self):
        def boom():
            raise SystemExit(1)

        _run_cli_entry(boom)


class AccessLogicTests(unittest.TestCase):
    def test_old_subscribed_check_was_wrong(self):
        post = {"free": False, "id": "abc"}
        old_blocked = post.get("free") is False and not post.get("subscribed")
        self.assertTrue(old_blocked)
        self.assertTrue(post_is_available(post))


class VideoPosterTests(unittest.TestCase):
    def test_archived_video_is_not_downloaded_again(self):
        from scripts import myfans_dl

        client = MagicMock()
        state = MagicMock()
        state.is_completed.return_value = True

        result = myfans_dl.process_video_post(
            "archived-1",
            client,
            "best",
            "downloads",
            {"pattern": "{id}"},
            download_state=state,
        )

        self.assertTrue(result)
        client.get_post.assert_not_called()
        state.add_download.assert_not_called()

    def test_disabled_poster_setting_skips_poster_download(self):
        from scripts import myfans_dl

        client = MagicMock()
        client.get_post.return_value = {"id": "1", "user": {"username": "creator"}}
        client.get_post_videos.return_value = {"main": [{"url": "https://cdn.example/video.m3u8"}]}

        with tempfile.TemporaryDirectory() as tmp:
            video_folder = Path(tmp) / "creator" / "videos"
            video_folder.mkdir(parents=True)
            (video_folder / "1.mp4").write_bytes(b"video")
            with patch.object(myfans_dl, "verify_video_file", return_value=True), patch.object(
                myfans_dl, "download_video_poster"
            ) as poster_mock:
                result = myfans_dl.process_video_post(
                    "1",
                    client,
                    "best",
                    tmp,
                    {"pattern": "{id}"},
                    download_poster=False,
                )

        self.assertTrue(result)
        poster_mock.assert_not_called()

    def test_poster_uses_video_filename(self):
        from scripts import myfans_dl

        post = {"id": "1", "published_at": "2024-12-05T00:00:00Z"}
        video = {"image_url": "https://cdn.example/placeholder.png"}
        post["thumbnail_url"] = "https://cdn.example/cover.jpg"
        client = MagicMock()

        with tempfile.TemporaryDirectory() as tmp:
            video_path = str(Path(tmp) / "20241205 title.mp4")

            def write_poster(_session, _url, _headers, destination):
                Path(destination).write_bytes(b"poster")
                return True

            with patch.object(myfans_dl, "download_image_as_jpeg", side_effect=write_poster):
                downloaded = myfans_dl.download_video_poster(post, video, video_path, client)

            self.assertTrue(downloaded)
            self.assertTrue((Path(tmp) / "20241205 title-poster.jpg").is_file())

    def test_new_poster_removes_old_generated_image(self):
        from scripts import myfans_dl

        post = {"id": "1", "thumbnail_url": "https://cdn.example/cover.webp"}
        client = MagicMock()

        with tempfile.TemporaryDirectory() as tmp:
            video_path = str(Path(tmp) / "20241205 title.mp4")
            old_poster = Path(tmp) / "20241205 title-poster.png"
            old_poster.write_bytes(b"wrong")

            def write_poster(_session, _url, _headers, destination):
                Path(destination).write_bytes(b"correct")
                return True

            with patch.object(myfans_dl, "download_image_as_jpeg", side_effect=write_poster):
                downloaded = myfans_dl.download_video_poster(post, {}, video_path, client)

            self.assertTrue(downloaded)
            self.assertFalse(old_poster.exists())
            self.assertTrue((Path(tmp) / "20241205 title-poster.jpg").is_file())

    def test_webp_poster_is_converted_to_jpeg(self):
        from scripts import myfans_dl

        image_bytes = BytesIO()
        Image.new("RGBA", (4, 3), (255, 0, 0, 128)).save(image_bytes, format="WEBP")
        response = MagicMock(content=image_bytes.getvalue())
        session = MagicMock()
        session.get.return_value = response

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "video-poster.jpg"
            downloaded = myfans_dl.download_image_as_jpeg(
                session,
                "https://cdn.example/cover.webp",
                {},
                str(destination),
            )

            self.assertTrue(downloaded)
            with Image.open(destination) as converted:
                self.assertEqual(converted.format, "JPEG")
                self.assertEqual(converted.mode, "RGB")


class SeasonPlanTests(unittest.TestCase):
    def test_oldest_posts_are_grouped_into_first_season(self):
        from scripts.myfans_dl import build_season_plan

        posts = [
            {"id": "new", "published_at": "2024-03-01T00:00:00Z"},
            {"id": "old", "published_at": "2024-01-01T00:00:00Z"},
            {"id": "middle", "published_at": "2024-02-01T00:00:00Z"},
        ]
        plan = build_season_plan(posts, size=2, pattern="S{number:02d}", order="oldest")

        self.assertEqual([item["post"]["id"] for item in plan], ["old", "middle", "new"])
        self.assertEqual([item["folder"] for item in plan], ["S01", "S01", "S02"])

    def test_custom_season_name_adds_number_placeholder(self):
        from scripts.myfans_dl import build_season_plan

        plan = build_season_plan([{"id": "1"}], pattern="第", size=20)
        self.assertEqual(plan[0]["folder"], "第01")


if __name__ == "__main__":
    unittest.main()

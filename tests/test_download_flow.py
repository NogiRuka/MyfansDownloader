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
        ):
            myfans_dl.start_download("", "videos", "all", queue)
        items = []
        while not queue.empty():
            items.append(queue.get_nowait())
        self.assertEqual(items[-1], "DONE")
        self.assertTrue(any("创作者用户名" in str(item) for item in items))


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


if __name__ == "__main__":
    unittest.main()

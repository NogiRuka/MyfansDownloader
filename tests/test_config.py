from __future__ import annotations

import configparser
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.download_state import DownloadState
from scripts.settings_loader import save_config


class ConfigPreserveTests(unittest.TestCase):
    def test_save_does_not_drop_sections(self):
        config = configparser.ConfigParser()
        config["Settings"] = {"output_dir": "downloads", "auth_token": ""}
        config["Filename"] = {"pattern": "{creator}_{date}_{id}", "separator": "_"}
        config["Threads"] = {"threads": "3"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            with patch("scripts.settings_loader.find_config_file", return_value=path):
                save_config(config)
                loaded = configparser.ConfigParser()
                loaded.read(path, encoding="utf-8")
            self.assertEqual(loaded.get("Settings", "output_dir"), "downloads")
            self.assertEqual(loaded.get("Filename", "pattern"), "{creator}_{date}_{id}")
            self.assertEqual(loaded.get("Threads", "threads"), "3")


class DownloadStateTests(unittest.TestCase):
    def test_serializable_snapshot_is_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = DownloadState(state_dir=tmp)
            state.mark_completed("abc")
            snapshot = state.get_serializable_state()
            self.assertIn("abc", snapshot["completed_files"])
            snapshot["completed_files"].append("mutated")
            self.assertNotIn("mutated", state.get_serializable_state()["completed_files"])

    def test_missing_file_resets_completed_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = DownloadState(state_dir=tmp)
            video_path = Path(tmp) / "video.mp4"
            state.add_download("abc", status="in_progress")
            state.mark_completed("abc", video_path)

            state.mark_missing("abc")

            snapshot = state.get_serializable_state()
            self.assertNotIn("abc", snapshot["completed_files"])
            self.assertEqual(snapshot["downloads"]["abc"]["status"], "pending")
            self.assertNotIn("file_path", snapshot["downloads"]["abc"])

    def test_batch_status_update_clears_failed_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = DownloadState(state_dir=tmp)
            state.mark_failed("abc", "下载失败")

            state.set_status_many(["abc", "def", "abc"], "archived")
            archived = state.get_serializable_state()
            self.assertEqual(archived["downloads"]["abc"]["status"], "completed")
            self.assertNotIn("abc", archived["failed_files"])
            self.assertIn("def", archived["completed_files"])

            state.set_status_many(["abc", "def"], "pending")
            pending = state.get_serializable_state()
            self.assertEqual(pending["downloads"]["abc"]["status"], "pending")
            self.assertNotIn("abc", pending["completed_files"])

    def test_progress_updates_total_and_completed_amount(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = DownloadState(state_dir=tmp)
            state.add_download("abc", status="in_progress")

            state.update_progress("abc", 37, 100)

            progress = state.get_progress("abc")
            self.assertEqual(progress["segments_downloaded"], 37)
            self.assertEqual(progress["segments_total"], 100)


if __name__ == "__main__":
    unittest.main()

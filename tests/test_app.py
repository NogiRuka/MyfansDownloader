from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FlaskRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app

        app.config["TESTING"] = True
        cls.client = app.test_client()

    def test_index_ok(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("下载你可以访问的内容", html)
        self.assertIn("myfans-logo.svg", html)

    def test_settings_page_is_html_not_json(self):
        resp = self.client.get("/settings")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("下载设置", resp.get_data(as_text=True))
        self.assertFalse(resp.is_json)

    def test_settings_api_json(self):
        resp = self.client.get("/api/settings")
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertIn("filename_pattern", payload)
        self.assertIn("thread_count", payload)
        self.assertIn("auth_token_set", payload)
        self.assertIn("download_poster", payload)
        self.assertEqual(payload["season_size"], 20)
        self.assertNotIn("auth_token", payload)

    def test_download_requires_target(self):
        resp = self.client.post("/download", json={})
        self.assertEqual(resp.status_code, 400)

    def test_creator_posts_are_sorted_and_grouped(self):
        posts = [
            {"id": "2", "title": "较新", "published_at": "2024-02-01T00:00:00Z", "free": False},
            {"id": "1", "title": "较早", "published_at": "2024-01-01T00:00:00Z", "free": True},
        ]
        with patch("app.read_headers_from_file", return_value={"user-agent": "test"}), patch(
            "app.MyFansClient"
        ), patch("app.downloader.list_creator_posts", return_value=posts):
            resp = self.client.get("/api/creator-posts?username=creator&type=videos")

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertEqual([item["id"] for item in payload["items"]], ["1", "2"])
        self.assertEqual(payload["items"][0]["folder"], "S01")


if __name__ == "__main__":
    unittest.main()

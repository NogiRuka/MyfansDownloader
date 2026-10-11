"""Persistent download progress used by the web UI."""
from __future__ import annotations

import copy
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from scripts.paths import config_dir, ensure_dir


class DownloadState:
    def __init__(self, state_dir: Optional[str] = None):
        directory = Path(state_dir) if state_dir else config_dir()
        ensure_dir(directory)
        self.state_file = str(directory / "download_state.json")
        self._lock = threading.RLock()
        self.state = self._load_state()
        completed = self.state.get("completed_files", [])
        self.state["completed_files"] = set(completed if isinstance(completed, list) else [])

    def _default_state(self) -> Dict[str, Any]:
        return {
            "downloads": {},
            "completed_files": [],
            "failed_files": {},
            "in_progress": {},
        }

    def _load_state(self) -> Dict[str, Any]:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    merged = self._default_state()
                    merged.update(data)
                    return merged
            except (json.JSONDecodeError, OSError):
                return self._default_state()
        return self._default_state()

    def save_state(self) -> None:
        try:
            parent = os.path.dirname(self.state_file) or "."
            ensure_dir(Path(parent))
            with open(self.state_file, "w", encoding="utf-8") as handle:
                json.dump(self.get_serializable_state(), handle)
        except OSError as exc:
            print(f"Error saving state: {exc}")

    def add_download(self, post_id, status="pending", segments_total=0, segments_downloaded=0):
        with self._lock:
            self.state["downloads"][str(post_id)] = {
                "status": status,
                "start_time": datetime.now().isoformat(),
                "segments_total": segments_total,
                "segments_downloaded": segments_downloaded,
                "last_updated": datetime.now().isoformat(),
            }
            self.save_state()

    def update_progress(self, post_id, segments_downloaded, segments_total=None):
        with self._lock:
            entry = self.state["downloads"].get(str(post_id))
            if not entry:
                return
            entry["segments_downloaded"] = segments_downloaded
            if segments_total is not None:
                entry["segments_total"] = segments_total
            entry["last_updated"] = datetime.now().isoformat()
            self.save_state()

    def mark_completed(self, post_id, file_path=None):
        with self._lock:
            post_id = str(post_id)
            if post_id in self.state["downloads"]:
                self.state["downloads"][post_id]["status"] = "completed"
                self.state["downloads"][post_id]["last_updated"] = datetime.now().isoformat()
                self.state["downloads"][post_id].pop("archived", None)
                if file_path:
                    self.state["downloads"][post_id]["file_path"] = os.path.abspath(file_path)
            self.state["completed_files"].add(post_id)
            self.save_state()

    def mark_missing(self, post_id):
        """将作品重新标记为未下载，允许用户再次下载。"""
        self.set_status_many([post_id], "pending")

    def mark_archived(self, post_id):
        """将已经转移到其他存储空间的作品标记为已归档。"""
        self.set_status_many([post_id], "archived")

    def set_status_many(self, post_ids, status):
        """一次更新多个作品的手动状态，避免批量操作反复写入状态文件。"""
        normalized_ids = list(dict.fromkeys(str(post_id).strip() for post_id in post_ids))
        normalized_ids = [post_id for post_id in normalized_ids if post_id]
        if status not in {"archived", "pending"}:
            raise ValueError("状态只能设置为 archived 或 pending。")

        with self._lock:
            now = datetime.now().isoformat()
            for post_id in normalized_ids:
                entry = self.state["downloads"].setdefault(post_id, {})
                entry["last_updated"] = now
                entry.pop("file_path", None)
                if status == "archived":
                    entry["status"] = "completed"
                    entry["archived"] = True
                    self.state["completed_files"].add(post_id)
                    self.state["failed_files"].pop(post_id, None)
                else:
                    entry["status"] = "pending"
                    entry.pop("archived", None)
                    entry.pop("error", None)
                    self.state["completed_files"].discard(post_id)
                    self.state["failed_files"].pop(post_id, None)
            if normalized_ids:
                self.save_state()

    def mark_failed(self, post_id, error):
        with self._lock:
            post_id = str(post_id)
            self.state["downloads"][post_id] = {
                "status": "failed",
                "error": str(error),
                "last_updated": datetime.now().isoformat(),
            }
            self.state["failed_files"][post_id] = str(error)
            self.save_state()

    def is_completed(self, post_id) -> bool:
        with self._lock:
            return str(post_id) in self.state["completed_files"]

    def get_progress(self, post_id) -> Dict[str, Any]:
        return self.state["downloads"].get(str(post_id), {})

    def is_file_exists(self, filename: str) -> bool:
        return filename in self.state["completed_files"]

    def get_serializable_state(self) -> Dict[str, Any]:
        with self._lock:
            completed = self.state.get("completed_files") or []
            if isinstance(completed, set):
                completed = list(completed)
            return {
                "downloads": copy.deepcopy(self.state.get("downloads") or {}),
                "completed_files": list(completed),
                "failed_files": copy.deepcopy(self.state.get("failed_files") or {}),
                "in_progress": copy.deepcopy(self.state.get("in_progress") or {}),
            }

"""一键启动本地网页端，并在服务就绪后打开浏览器。"""

from __future__ import annotations

import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from helpers.deps import check_python_version, install_requirements

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 5000
WEB_URL = f"http://{HOST}:{PORT}"


def server_is_running(url: str = WEB_URL) -> bool:
    """检查目标地址是否已经运行当前网页服务。"""

    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def open_browser_when_ready(url: str = WEB_URL) -> None:
    """等待网页服务可访问后打开默认浏览器。"""

    for _ in range(40):
        if server_is_running(url):
            webbrowser.open(url)
            return
        time.sleep(0.25)
    print(f"服务已启动，但没有自动打开浏览器。请手动访问：{url}")


def main() -> int:
    """启动网页端；如果服务已运行，则只打开浏览器。"""

    os.chdir(ROOT)
    if not check_python_version():
        print("需要 Python 3.9 或更高版本。")
        return 1
    if not install_requirements():
        print("依赖安装未完成，请先运行 SETUP.bat。")
        return 1

    if server_is_running():
        print("检测到网页端已经运行，正在打开浏览器……")
        webbrowser.open(WEB_URL)
        return 0

    try:
        from app import app
    except ImportError as exc:
        print(f"网页端依赖加载失败：{exc}")
        print("请先运行 SETUP.bat 安装依赖。")
        return 1

    print("=" * 52)
    print("MyFans 下载器网页端")
    print("=" * 52)
    print(f"访问地址：{WEB_URL}")
    print("浏览器将在服务就绪后自动打开。")
    print("关闭本窗口即可停止网页服务。")
    print()

    opener = threading.Thread(target=open_browser_when_ready, daemon=True)
    opener.start()
    try:
        app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
    except OSError as exc:
        print(f"网页服务启动失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n网页服务已停止。")
        sys.exit(0)

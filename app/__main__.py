import argparse
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from .server import serve

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="抖音收藏学习平台")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="启动后不自动打开网页")
    a = ap.parse_args()
    serve(a.port, not a.no_browser)

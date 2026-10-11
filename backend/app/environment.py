"""加载根目录环境文件，已有进程环境变量优先。"""

from pathlib import Path

from dotenv import load_dotenv


def load_environment() -> None:
    """兼容源码运行与 Docker 的 /app/app 布局。"""
    backend_dir = Path(__file__).resolve().parent.parent
    root = backend_dir.parent if backend_dir.name == "backend" else backend_dir
    load_dotenv(root / ".env", override=False)

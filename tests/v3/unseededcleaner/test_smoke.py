"""引导冒烟：后端可导入、配置目录已隔离。"""
from app.runtime.config import settings


def test_backend_importable() -> None:
    """MoviePilot 后端在 sys.path 中可导入。"""
    assert settings is not None

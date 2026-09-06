"""引导冒烟：后端可导入、配置目录已隔离、本仓 plugins.v3 已注入。"""
import tempfile

import app.plugins
from app.runtime.config import settings


def test_backend_importable() -> None:
    """MoviePilot 后端在 sys.path 中可导入，配置目录已隔离到临时目录。"""
    assert str(settings.CONFIG_PATH).startswith(
        tempfile.gettempdir()), f"CONFIG_PATH 未隔离: {settings.CONFIG_PATH}"


def test_plugin_source_injected() -> None:
    """本仓 plugins.v3 已前置注入 app.plugins 命名空间。"""
    repo_plugins = "/Users/luanrjones/Code/references/pt/movie-pilot/MoviePilot-Plugins-fork/plugins.v3"
    assert any(str(p).startswith(repo_plugins) for p in app.plugins.__path__), \
        f"plugins.v3 未注入: {list(app.plugins.__path__)}"

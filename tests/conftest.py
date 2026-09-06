"""pytest 全局引导：本仓仅 v3 插件，导入即注册网络守卫并准备 v3 后端。"""
from __future__ import annotations

from ._bootstrap import (  # noqa: F401  导入即注册主程序共享 autouse 网络守卫
    block_real_network,
    prepare_v3_backend,
)


def pytest_configure(config) -> None:
    """收集用例前隔离 CONFIG_DIR、建表并注入本仓 plugins.v3。"""
    prepare_v3_backend()

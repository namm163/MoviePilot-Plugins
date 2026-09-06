"""未做种清理插件。

扫描指定下载根目录，对比 Transmission 全量种子路径，找出磁盘上已不做种的内容，
页面查看 + 两步确认删除，释放磁盘空间。
"""
import os
import threading
from datetime import datetime
from typing import Any

from app.plugins import _PluginBase
from app.sdk.config import settings
from app.sdk.logging import logger
from app.sdk.string import StringUtils

# 内置硬排除：Tr 未完成目录 / 回收站 / 系统目录名（小写包含匹配）
BUILTIN_EXCLUDES = ("incomplete", ".trash", "#recycle", "@eadir")
# 删除记录保留条数（防膨胀）
DELETE_LOG_LIMIT = 500

# 插件持久化数据键
STATUS_KEY = "action_status"      # 动作状态（页面重开恢复显示）
SCAN_KEY = "last_scan"            # 最近扫描结果
PENDING_KEY = "pending_delete"    # 两步删除已标记路径列表
LOG_KEY = "delete_log"            # 删除记录


def normalize_path(path: str) -> str:
    """路径规范化：去首尾空白、尾斜杠、消解点段。"""
    return os.path.normpath(str(path).strip())


def build_protected_paths(torrents: list) -> set:
    """种子对象列表 → 保护路径集合（download_dir + name）。缺字段的种子跳过。"""
    protected = set()
    for t in torrents:
        download_dir = getattr(t, "download_dir", None)
        name = getattr(t, "name", None)
        if download_dir and name:
            protected.add(normalize_path(os.path.join(download_dir, name)))
    return protected


def is_unit_protected(unit_path: str, protected_paths: set) -> bool:
    """双向前缀匹配：单元内有种子，或单元位于种子路径内部，均受保护。

    量级：几百单元 × 几千种子 ≈ 百万级 startswith，秒级内完成，无需优化。
    分隔符边界：/d/a 不是 /d/abc 的前缀保护。
    """
    for p in protected_paths:
        if p == unit_path:
            return True
        if p.startswith(unit_path + os.sep):
            return True
        if unit_path.startswith(p + os.sep):
            return True
    return False


def get_path_size(path: str) -> int:
    """统计文件/目录树总字节数；不可读项按 0 跳过，不抛异常。"""
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                continue
    return total


def format_size(size) -> str:
    """字节数格式化为可读格式（如 42.1G）；空值返回空串。"""
    if size in (None, ""):
        return ""
    try:
        return StringUtils.str_filesize(size)
    except Exception:
        return str(size)

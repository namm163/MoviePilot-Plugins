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
    入参须已经 normalize_path 规范化（带尾斜杠的单元路径会导致前缀匹配失效）。
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


class UnseededCleaner(_PluginBase):
    """扫描下载根目录中未做种内容，支持两步确认删除。"""

    plugin_name = "未做种清理"
    plugin_desc = "扫描下载根目录中已不在 Transmission 做种的内容，查看与删除释放空间。"
    plugin_icon = "https://raw.githubusercontent.com/namm163/MoviePilot-Plugins/main/icons/unseededcleaner.png"
    plugin_version = "1.0.0"
    plugin_author = "namm163"
    author_url = "https://github.com/namm163/MoviePilot-Plugins"
    plugin_config_prefix = "unseededcleaner_"
    plugin_order = 61
    auth_level = 1

    # 扫描/删除互斥锁（类级：配置重载不丢锁状态）
    _lock = threading.Lock()

    _scan_dirs: list = []
    _exclude_keywords: list = []
    _notify = False

    # ---------- 生命周期 ----------

    def init_plugin(self, config: dict | None = None) -> None:
        """读取配置：目录/关键字文本按行解析。"""
        config = config or {}
        self._scan_dirs = self._parse_lines(config.get("scan_dirs"))
        self._exclude_keywords = self._parse_lines(config.get("exclude_keywords"))
        self._notify = bool(config.get("notify"))

    @staticmethod
    def _parse_lines(text) -> list:
        """多行文本解析为非空行列表（忽略注释与首尾空白）。"""
        if not text:
            return []
        return [line.strip() for line in str(text).splitlines()
                if line.strip() and not line.strip().startswith("#")]

    def get_state(self) -> bool:
        """手动工具插件，安装即就绪。"""
        return True

    @staticmethod
    def get_command() -> list:
        """不注册远程命令。"""
        return []

    def stop_service(self) -> None:
        """无后台常驻资源。"""

    def _is_excluded(self, name: str) -> bool:
        """单元名命中内置排除或用户关键字（小写包含匹配）。"""
        lower = name.lower()
        if any(k in lower for k in BUILTIN_EXCLUDES):
            return True
        return any(k and k.lower() in lower for k in self._exclude_keywords)

    # ---------- 配置页 ----------

    def get_form(self) -> tuple[list[dict], dict[str, Any]]:
        """配置页：下载根目录 + 排除关键字 + 通知开关。"""
        form = {
            "component": "VForm",
            "content": [
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12}, "content": [
                        {"component": "VTextarea", "props": {
                            "model": "scan_dirs", "label": "下载根目录", "rows": 4,
                            "placeholder": "每行一个路径，须配到种子直接存放的那一层\n如 /media4/9kg、/education"}}]},
                ]},
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 8}, "content": [
                        {"component": "VTextarea", "props": {
                            "model": "exclude_keywords", "label": "额外排除关键字", "rows": 2,
                            "placeholder": "每行一个，单元名包含即跳过（内置 incomplete/.trash/#recycle/@eaDir）"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                        {"component": "VSwitch", "props": {
                            "model": "notify", "label": "扫描完成通知"}}]},
                ]},
            ],
        }
        return [form], {"scan_dirs": "", "exclude_keywords": "", "notify": False}

    @staticmethod
    def get_render_mode() -> tuple:
        """Vuetify 拼装模式。"""
        return "vuetify", None

    def get_page(self) -> list:
        """详情页（任务 6 实现）。"""
        return [{"component": "div", "text": "暂无数据"}]

    # ---------- 下载器 ----------

    def _get_transmission(self):
        """取已配置启用的 Transmission 实例（多实例取第一个）。"""
        from app.application.downloader import DownloaderHelper
        services = DownloaderHelper().get_services(type_filter="transmission")
        if not services:
            return None
        return next(iter(services.values())).instance

    # ---------- 扫描 ----------

    def _set_status(self, action: str, status: str, progress: str = "", message: str = "") -> None:
        """更新并持久化动作状态（页面重开恢复显示）。"""
        self.save_data(STATUS_KEY, {
            "action": action, "status": status,
            "progress": progress, "message": message,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    def _run_scan(self) -> None:
        """两段式扫描：第一段秒级判定名单并落库，第二段渐进统计大小。"""
        try:
            self._set_status("scan", "running", "正在获取 Transmission 种子列表…")
            tr = self._get_transmission()
            if not tr:
                self._set_status("scan", "error", message="未找到已启用的 Transmission 下载器")
                return
            torrents, error = tr.get_torrents()
            if error:
                # 宁可失败不可误判：RPC 出错绝不产出结果
                self._set_status("scan", "error", message="获取种子列表失败，本次扫描中止")
                return
            protected = build_protected_paths(torrents)
            roots = [{"root": normalize_path(d), **self._collect_unseeded(d, protected)}
                     for d in self._scan_dirs]
            scan = {
                "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "tr_torrent_count": len(torrents),
                "roots": roots,
            }
            self.save_data(SCAN_KEY, scan)     # 第一段产出：名单立即可见
            self._size_units(scan)             # 第二段：后台补大小
            total = sum(len(r["units"]) for r in roots)
            sized = sum(1 for r in roots for u in r["units"] if u.get("sized"))
            self._set_status("scan", "done", f"扫描完成：{total} 项未做种（大小统计 {sized}/{total}）")
            self._notify_done(total)
        except Exception as e:
            logger.error(f"未做种扫描失败：{e}")
            self._set_status("scan", "error", message=f"扫描异常：{e}")

    def _collect_unseeded(self, scan_dir: str, protected: set) -> dict:
        """收集扫描根下未做种的一级子项（大小暂不统计）。"""
        scan_dir = normalize_path(scan_dir)
        if not os.path.isdir(scan_dir):
            logger.warn(f"扫描根目录不存在：{scan_dir}")
            return {"missing": True, "units": []}
        units = []
        for entry in os.scandir(scan_dir):
            if self._is_excluded(entry.name):
                continue
            unit_path = normalize_path(entry.path)
            try:
                if is_unit_protected(unit_path, protected):
                    continue
                units.append({
                    "path": unit_path, "name": entry.name,
                    "type": "dir" if entry.is_dir(follow_symlinks=False) else "file",
                    "size": None, "sized": False,
                })
            except OSError as e:
                logger.warn(f"无法读取 {entry.path}：{e}")
        return {"units": units}

    def _size_units(self, scan: dict) -> None:
        """第二段：逐单元统计大小，每 5 项落库一次（页面可中途看到进度）。"""
        units = [u for r in scan["roots"] for u in r["units"]]
        for i, unit in enumerate(units, 1):
            try:
                unit["size"] = get_path_size(unit["path"])
            except OSError as e:
                logger.warn(f"统计大小失败 {unit['path']}：{e}")
            unit["sized"] = True
            if i % 5 == 0 or i == len(units):
                self.save_data(SCAN_KEY, scan)
                self._set_status("scan", "running", f"正在统计大小 {i}/{len(units)}…")

    def _notify_done(self, total_units: int) -> None:
        """扫描完成后按配置发送通知。"""
        if not self._notify or total_units <= 0:
            return
        from app.schemas.types import MessageType
        scan = self.get_data(SCAN_KEY) or {}
        total_size = sum(u.get("size") or 0
                         for r in scan.get("roots", []) for u in r["units"])
        self.post_message(
            mtype=MessageType.Plugin, title="未做种清理",
            text=f"扫描完成：{total_units} 项未做种，可释放约 {format_size(total_size)}",
        )

    # ---------- 插件 API ----------

    def get_api(self) -> list:
        """注册扫描/刷新/标记/删除接口。"""
        return [
            {"path": "/scan", "endpoint": self.api_scan,
             "methods": ["GET"], "summary": "立即扫描"},
            {"path": "/refresh", "endpoint": self.api_refresh,
             "methods": ["GET"], "summary": "刷新页面数据"},
            {"path": "/mark", "endpoint": self.api_mark,
             "methods": ["GET"], "summary": "标记/取消待删除"},
            {"path": "/delete", "endpoint": self.api_delete,
             "methods": ["GET"], "summary": "确认删除"},
        ]

    @staticmethod
    def _check_apikey(apikey: str):
        """API 密钥校验，失败返回错误 Response，成功返回 None。"""
        from app import schemas
        if apikey != settings.API_TOKEN:
            return schemas.Response(success=False, message="API密钥错误")
        return None

    def api_scan(self, apikey: str):
        """触发后台扫描线程（锁互斥）。"""
        err = self._check_apikey(apikey)
        if err:
            return err
        from app import schemas
        if not self._scan_dirs:
            return schemas.Response(success=False, message="请先在配置中设置下载根目录")
        if not self._lock.acquire(blocking=False):
            return schemas.Response(success=False, message="已有扫描/删除正在进行中")
        threading.Thread(target=self._guarded_scan, daemon=True).start()
        from app import schemas
        return schemas.Response(success=True, message="扫描已启动，稍后刷新查看结果")

    def _guarded_scan(self) -> None:
        """带锁执行扫描（锁由 api_scan 获取，此处释放）。"""
        try:
            self._run_scan()
        finally:
            self._lock.release()

    def api_refresh(self, apikey: str):
        """空操作：仅用于触发前端自动刷新页面数据。"""
        err = self._check_apikey(apikey)
        if err:
            return err
        from app import schemas
        return schemas.Response(success=True)

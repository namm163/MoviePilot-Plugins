"""未做种清理插件。

扫描指定下载根目录，对比 Transmission 全量种子路径，找出磁盘上已不做种的内容，
页面查看 + 两步确认删除，释放磁盘空间。
"""
import os
import threading
from datetime import datetime
from typing import Any, Optional

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


def build_seeded_ancestors(protected_paths: set) -> set:
    """保护路径集合 → 各级父目录集合（用于下钻导航：子树内有种子的目录）。

    例：保护路径 /m/series/国漫/作品A → seeded_ancestors 含 /m/series/国漫、/m/series、/m。
    """
    ancestors = set()
    for p in protected_paths:
        d = os.path.dirname(p)
        while d and d != os.sep:
            ancestors.add(d)
            d = os.path.dirname(d)
    return ancestors


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
    """统计文件/目录树的实际磁盘占用（st_blocks 口径，与 du 默认一致）。

    压缩卷/稀疏文件不虚高；不可读项按 0 跳过，不抛异常。
    """
    if os.path.isfile(path):
        try:
            return os.stat(path).st_blocks * 512
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                total += os.stat(os.path.join(root, f)).st_blocks * 512
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
    plugin_version = "1.0.2"
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
    _auto_drill = True

    # ---------- 生命周期 ----------

    def init_plugin(self, config: dict | None = None) -> None:
        """读取配置：目录/关键字文本按行解析。"""
        config = config or {}
        self._scan_dirs = self._parse_lines(config.get("scan_dirs"))
        self._exclude_keywords = self._parse_lines(config.get("exclude_keywords"))
        self._notify = bool(config.get("notify"))
        self._auto_drill = bool(config.get("auto_drill", True))

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
        """单元名命中隐藏目录、内置排除或用户关键字（小写包含匹配）。"""
        if name.startswith((".", "@")):
            # 隐藏目录/文件（.@upload_cache）与 NAS 系统目录（@Recently-Snapshot）不参与扫描
            return True
        lower = name.lower()
        if any(k in lower for k in BUILTIN_EXCLUDES):
            return True
        return any(k and k.lower() in lower for k in self._exclude_keywords)

    # ---------- 配置页 ----------

    def get_form(self) -> tuple[list[dict], dict[str, Any]]:
        """配置页：下载根目录 + 排除关键字 + 通知开关 + 下钻开关。"""
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
                            "placeholder": "每行一个，目录名包含即跳过（作用于每一层；内置 incomplete/.trash/#recycle/@eaDir 及 . / @ 开头）"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                        {"component": "VSwitch", "props": {
                            "model": "notify", "label": "扫描完成通知"}}]},
                ]},
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 8}, "content": [
                        {"component": "VSwitch", "props": {
                            "model": "auto_drill", "label": "自动下钻找种子层"}}]},
                ]},
            ],
        }
        return [form], {"scan_dirs": "", "exclude_keywords": "", "notify": False, "auto_drill": True}

    @staticmethod
    def get_render_mode() -> tuple:
        """Vuetify 拼装模式。"""
        return "vuetify", None

    # ---------- 详情页 ----------

    def get_page(self) -> list:
        """详情页：状态卡片 + 操作按钮 + 按根分组清单 + 删除记录。"""
        scan = self.get_data(SCAN_KEY) or {}
        status = self.get_data(STATUS_KEY) or {}
        pending = set(self.get_data(PENDING_KEY) or [])
        content = [self._status_card(status, scan), self._action_bar()]
        for root in scan.get("roots", []):
            content.append(self._root_card(root, pending))
        content.append(self._log_panel())
        return content

    def _status_card(self, status: dict, scan: dict) -> dict:
        """顶部状态卡片：进行中显示进度，完成显示摘要，出错红色提示。"""
        inner = []
        state = status.get("status")
        if state == "running":
            inner.append({"component": "VProgressLinear", "props": {"indeterminate": True}})
            inner.append({"component": "VCardText",
                          "text": status.get("progress") or "处理中…"})
        elif state == "error":
            inner.append({"component": "VCardText", "props": {"class": "text-error"},
                          "text": f"出错：{status.get('message') or '未知错误'}"})
        else:
            units = [u for r in scan.get("roots", []) for u in r["units"]]
            sized = sum(u.get("size") or 0 for u in units if u.get("sized"))
            text = (f"上次扫描 {scan.get('scan_time', '—')}："
                    f"发现 {len(units)} 项未做种，可释放约 {format_size(sized)}"
                    if scan else "尚未扫描，请配置下载根目录后点击「立即扫描」")
            inner.append({"component": "VCardText", "text": text})
        return {"component": "VCard", "props": {"class": "mb-3"}, "content": inner}

    def _btn(self, text: str, api_path: str, params: dict = None, color: str = "primary") -> dict:
        """生成带 events.click 的按钮（params 自动附带 apikey）。"""
        merged = {"apikey": settings.API_TOKEN, **(params or {})}
        return {
            "component": "VBtn", "props": {"color": color, "size": "small",
                                           "variant": "tonal", "class": "mr-2"},
            "text": text,
            "events": {"click": {"api": f"plugin/UnseededCleaner/{api_path}",
                                 "method": "get", "params": merged}},
        }

    def _action_bar(self) -> dict:
        """操作按钮行：立即扫描 + 刷新。"""
        return {
            "component": "div", "props": {"class": "mb-3"},
            "content": [self._btn("立即扫描", "scan"),
                        self._btn("刷新", "refresh", color="default")],
        }

    def _root_card(self, root: dict, pending: set) -> dict:
        """单个扫描根分组卡片（VWindow 分页，每页 20 项，按大小降序）。"""
        units = sorted(root.get("units", []),
                       key=lambda u: u.get("size") or 0, reverse=True)
        if root.get("missing"):
            title = f"{root['root']}（目录不存在，请检查配置）"
        else:
            sized = sum(u.get("size") or 0 for u in units if u.get("sized"))
            title = f"{root['root']} — {len(units)} 项 / 已统计 {format_size(sized)}"
        pages = [units[i:i + 20] for i in range(0, len(units), 20)] or [[]]
        items = [{
            "component": "VWindowItem",
            "props": {"class": "d-flex flex-column gap-2"},
            "content": [self._unit_card(u, pending) for u in page],
        } for page in pages]
        return {
            "component": "VCard", "props": {"class": "mb-3"},
            "content": [
                {"component": "VCardTitle", "props": {"class": "text-subtitle-1"}, "text": title},
                {"component": "VCardText", "content": [
                    {"component": "VWindow", "props": {"show-arrows": "hover"}, "content": items}]},
            ],
        }

    def _unit_card(self, unit: dict, pending: set) -> dict:
        """单个未做种单元卡片：名称/大小/路径 + 两步删除按钮。"""
        path = unit["path"]
        size_text = format_size(unit.get("size")) if unit.get("sized") else "统计中…"
        marked = path in pending
        if marked:
            buttons = [
                self._btn("取消标记", "mark", {"path": path}, color="default"),
                self._btn("确认删除", "delete", {"path": path}, color="error"),
            ]
        else:
            buttons = [self._btn("删除", "mark", {"path": path})]
        return {
            "component": "VCard", "props": {"variant": "outlined"},
            "content": [
                {"component": "VCardItem", "content": [
                    {"component": "VCardTitle", "props": {"class": "text-body-1"},
                     "text": unit["name"]},
                ]},
                {"component": "VCardText", "props": {"class": "pb-1"},
                 "text": f"{size_text} · {unit['type']} · {path}"},
                {"component": "VCardActions", "content": buttons},
            ],
        }

    def _log_panel(self) -> dict:
        """底部删除记录折叠面板（最近 20 条）。"""
        log = self.get_data(LOG_KEY) or []
        rows = [f"{r['time']}  {format_size(r['size'])}  {r['path']}" for r in log[-20:]]
        return {
            "component": "VExpansionPanels", "props": {"class": "mt-2"},
            "content": [{
                "component": "VExpansionPanel", "content": [
                    {"component": "VExpansionPanelTitle",
                     "text": f"删除记录（最近 {len(log)} 条）"},
                    {"component": "VExpansionPanelText",
                     "text": "\n".join(rows) if rows else "暂无记录"},
                ],
            }],
        }

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
            seeded_ancestors = build_seeded_ancestors(protected)
            roots = [{"root": normalize_path(d),
                      **self._collect_unseeded(d, protected, seeded_ancestors)}
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

    def _collect_unseeded(self, scan_dir: str, protected: set,
                          seeded_ancestors: Optional[set] = None) -> dict:
        """收集扫描根下的未做种内容单元。

        auto_drill 开启：递归下钻子树内有种子的目录，删种遗留的深层目录也能发现；
        整树无种子时退化为一级子项（防海量误报，交人工判别）。
        auto_drill 关闭：仅一级子项（旧行为）。
        """
        root = normalize_path(scan_dir)
        if not os.path.isdir(root):
            logger.warn(f"扫描根目录不存在：{root}")
            return {"missing": True, "units": []}
        if self._auto_drill and seeded_ancestors is not None and root in seeded_ancestors:
            units = self._drill_units(root, protected, seeded_ancestors)
        else:
            units = self._shallow_units(root, protected)
        return {"units": units}

    def _shallow_units(self, root: str, protected: set) -> list:
        """一级子项收集：单元 = 扫描根直接子项，受保护（双向前缀）的跳过。"""
        units = []
        for entry in self._iter_root(root):
            if self._is_excluded(entry.name):
                continue
            path = normalize_path(entry.path)
            if is_unit_protected(path, protected):
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError as e:
                logger.warn(f"无法读取 {entry.path}：{e}")
                continue
            units.append(self._make_unit(entry, path, is_dir))
        return units

    def _drill_units(self, dir_path: str, protected: set, seeded_ancestors: set) -> list:
        """递归下钻：仅展开子树内有种子的目录。

        规则：条目本身是保护路径（做种中）→ 跳过；子树内有种子的目录 → 继续下钻；
        子树内无种子的目录 / 非保护文件 → 删种遗留，列为单元。
        """
        units = []
        for entry in self._iter_root(dir_path):
            if self._is_excluded(entry.name):
                continue
            path = normalize_path(entry.path)
            if path in protected:
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError as e:
                logger.warn(f"无法读取 {entry.path}：{e}")
                continue
            if is_dir and path in seeded_ancestors:
                units.extend(self._drill_units(path, protected, seeded_ancestors))
            else:
                units.append(self._make_unit(entry, path, is_dir))
        return units

    @staticmethod
    def _iter_root(dir_path: str) -> list:
        """列目录条目；不可读返回空并告警（单目录故障不影响全局）。"""
        try:
            return list(os.scandir(dir_path))
        except OSError as e:
            logger.warn(f"目录不可读：{dir_path}：{e}")
            return []

    def _make_unit(self, entry, path: str, is_dir: bool) -> dict:
        """构造单元 dict（entry 已物化，无 IO，不会抛 OSError）。"""
        return {
            "path": path, "name": entry.name,
            "type": "dir" if is_dir else "file",
            "size": None, "sized": False,
        }

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
        # 读取最终落盘版本（含渐进统计完成后的大小）
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

    # ---------- 两步删除 ----------

    def _unit_in_last_scan(self, path: str) -> bool:
        """校验①白名单：路径必须存在于最近扫描结果。"""
        scan = self.get_data(SCAN_KEY) or {}
        return any(u["path"] == path
                   for r in scan.get("roots", []) for u in r["units"])

    def _is_in_scan_dirs(self, path: str) -> bool:
        """校验②范围：路径必须位于某个扫描根目录之下（防逃逸）。"""
        for d in self._scan_dirs:
            root = normalize_path(d)
            if path == root or path.startswith(root + os.sep):
                return True
        return False

    def api_mark(self, apikey: str, path: str = ""):
        """两步删除第一步：标记/取消（须在扫描结果内）。"""
        err = self._check_apikey(apikey)
        if err:
            return err
        from app import schemas
        if not path or not self._unit_in_last_scan(path):
            return schemas.Response(success=False, message="该路径不在扫描结果中")
        pending = set(self.get_data(PENDING_KEY) or [])
        if path in pending:
            pending.discard(path)
            msg = "已取消标记"
        else:
            pending.add(path)
            msg = "已标记，点击红色「确认删除」执行删除"
        self.save_data(PENDING_KEY, sorted(pending))
        return schemas.Response(success=True, message=msg)

    def api_delete(self, apikey: str, path: str = ""):
        """两步删除第二步：前置校验后启动后台删除。"""
        err = self._check_apikey(apikey)
        if err:
            return err
        from app import schemas
        if not path:
            return schemas.Response(success=False, message="参数错误")
        if not self._unit_in_last_scan(path):
            return schemas.Response(success=False, message="该路径不在扫描结果中")
        if path not in set(self.get_data(PENDING_KEY) or []):
            return schemas.Response(success=False, message="请先点击「删除」标记")
        if not self._lock.acquire(blocking=False):
            return schemas.Response(success=False, message="已有扫描/删除正在进行中")
        threading.Thread(target=self._guarded_delete, args=(path,), daemon=True).start()
        return schemas.Response(success=True, message="删除已启动")

    def _guarded_delete(self, path: str) -> None:
        """带锁删除：范围校验 + 实时保护校验 + 删除 + 留痕（锁由调用方释放）。"""
        import shutil
        try:
            name = os.path.basename(path)
            self._set_status("delete", "running", f"正在删除 {name}…")
            if not self._is_in_scan_dirs(path):
                self._set_status("delete", "error", message=f"路径越界，拒绝删除：{path}")
                return
            tr = self._get_transmission()
            if not tr:
                self._set_status("delete", "error", message="Transmission 不可用，中止删除")
                return
            torrents, error = tr.get_torrents()
            if error:
                self._set_status("delete", "error", message="获取种子列表失败，中止删除")
                return
            if is_unit_protected(path, build_protected_paths(torrents)):
                self._set_status("delete", "error",
                                 message=f"该路径正被种子占用，已跳过：{path}")
                return
            size = get_path_size(path)
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            # 注：三步写非原子（进程中途崩溃可能残留 pending 幽灵条目），依赖进程稳定性，风险已知
            self._remove_from_scan(path)
            self._remove_pending(path)
            self._append_delete_log(path, size)
            self._set_status("delete", "done", f"已删除 {name}（{format_size(size)}）")
        except Exception as e:
            logger.error(f"删除失败 {path}：{e}")
            self._set_status("delete", "error", message=f"删除失败：{e}")
        finally:
            self._lock.release()

    def _remove_from_scan(self, path: str) -> None:
        """从扫描结果中移除已删单元。"""
        scan = self.get_data(SCAN_KEY) or {}
        for root in scan.get("roots", []):
            root["units"] = [u for u in root["units"] if u["path"] != path]
        self.save_data(SCAN_KEY, scan)

    def _remove_pending(self, path: str) -> None:
        """清除删除标记。"""
        pending = [p for p in (self.get_data(PENDING_KEY) or []) if p != path]
        self.save_data(PENDING_KEY, pending)

    def _append_delete_log(self, path: str, size: int) -> None:
        """追加删除记录，仅保留最近 DELETE_LOG_LIMIT 条。"""
        log = self.get_data(LOG_KEY) or []
        log.append({"path": path, "size": size,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        self.save_data(LOG_KEY, log[-DELETE_LOG_LIMIT:])

"""完结剧集扫描通知插件。

定期从 PT 站点扫描当天发布的完结连续剧，去重后推送带海报的通知到 Telegram/飞书。
完结判定不靠标题正则，而是用站点自带的完结列表页 path（tag/状态参数）过滤。
"""
from typing import Any, Optional

from apscheduler.triggers.cron import CronTrigger

from app.plugins import _PluginBase
from app.sdk.config import settings
from app.sdk.logging import logger
from app.sdk.string import StringUtils

# 内置三站完结列表页 path（实测），用户可在配置覆盖/补充
DEFAULT_LIST_PATH = {
    "hhanclub.net": "torrents.php?tag_id17=1",
    "hddolby.com": "torrents.php?mystat=complete",
    "pandapt.net": "torrents.php?tag_id=10",
}
# 通知渠道选项
CHANNEL_ITEMS = [
    {"title": "全部已启用渠道", "value": ""},
    {"title": "Telegram", "value": "Telegram"},
    {"title": "飞书", "value": "Feishu"},
]


class EndedTVPackScan(_PluginBase):
    """扫描 PT 站点完结剧集并通知。"""

    plugin_name = "完结剧集扫描通知"
    plugin_desc = "扫描 PT 站点当天发布的完结连续剧，去重后推送带海报与简介的通知。"
    plugin_icon = "https://raw.githubusercontent.com/namm163/MoviePilot-Plugins/main/icons/endedtvpackscan.png"
    plugin_version = "1.1.4"
    plugin_author = "namm163"
    author_url = "https://github.com/namm163/MoviePilot-Plugins"
    plugin_config_prefix = "endedtvpackscan_"
    plugin_order = 60
    auth_level = 1

    NOTIFIED_KEY = "notified"           # 已通知去重键集合
    RECORDS_KEY = "notified_records"     # 详情页展示用记录列表

    _enabled = False
    _onlyonce = False
    _only_free = False
    _cron = "0 8,20 * * *"
    _channel: Optional[str] = None
    _ended_list_path: dict[str, str] = {}
    _retain_days = 7  # 已通知记录保留天数，0=永久

    # ---------- 生命周期 ----------

    def init_plugin(self, config: dict | None = None) -> None:
        """读取配置，建立运行状态。"""
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._onlyonce = bool(config.get("onlyonce"))
        self._only_free = bool(config.get("only_free"))
        self._cron = config.get("cron") or "0 8,20 * * *"
        self._channel = config.get("channel") or ""
        # 记录保留天数（0=永久）
        try:
            self._retain_days = max(0, int(config.get("retain_days") or 7))
        except (TypeError, ValueError):
            self._retain_days = 7
        # 文本配置解析为 dict；用户配了就完全替换默认，没配才用默认
        user_paths = self._parse_list_path(config.get("ended_list_path"))
        self._ended_list_path = user_paths if user_paths else dict(DEFAULT_LIST_PATH)
        logger.info(f"init_plugin: enabled={self._enabled} onlyonce={self._onlyonce} cron={self._cron}")
        # 立即运行一次：起后台线程跑 scan，并复位 onlyonce 开关
        if self._onlyonce:
            self._onlyonce = False
            import threading
            logger.info("onlyonce 已勾选，启动后台线程执行 scan")
            threading.Thread(target=self.scan, daemon=True).start()
            self.update_config(self._build_config())

    def _build_config(self) -> dict:
        """构造当前配置（用于保存/复位开关）。"""
        return {
            "enabled": self._enabled,
            "onlyonce": False,
            "only_free": self._only_free,
            "cron": self._cron,
            "channel": self._channel,
            "retain_days": self._retain_days,
            "ended_list_path": "\n".join(f"{k}={v}" for k, v in self._ended_list_path.items()),
        }

    def get_state(self) -> bool:
        """返回插件是否启用。"""
        return self._enabled

    @staticmethod
    def get_command() -> list[dict[str, Any]]:
        """不注册远程命令。"""
        return []

    def stop_service(self) -> None:
        """释放后台资源（定时任务由宿主管）。"""
        self._enabled = False

    # ---------- 定时服务 ----------

    def get_service(self) -> list[dict[str, Any]]:
        """每天上午、下午各扫描一次。"""
        if not self.get_state() or not self._cron:
            return []
        return [{
            "id": "EndedTVPackScan.Scan",
            "name": "完结剧集扫描",
            "trigger": CronTrigger.from_crontab(self._cron),
            "func": self.scan,
            "kwargs": {},
        }]

    # ---------- 配置页 ----------

    @staticmethod
    def _col_switch(model: str, label: str) -> dict:
        """生成一个开关列。"""
        return {
            "component": "VCol",
            "props": {"cols": 12, "md": 4},
            "content": [{"component": "VSwitch", "props": {"model": model, "label": label}}],
        }

    def get_form(self) -> tuple[list[dict], dict[str, Any]]:
        """返回配置页面与默认配置。"""
        form = {
            "component": "VForm",
            "content": [
                # 开关行
                {"component": "VRow", "content": [
                    self._col_switch("enabled", "启用插件"),
                    self._col_switch("onlyonce", "立即运行一次"),
                    self._col_switch("only_free", "仅通知免费种子"),
                ]},
                # cron + 渠道 + 保留天数
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                        {"component": "VCronField", "props": {
                            "model": "cron", "label": "执行周期",
                            "placeholder": "默认 0 8,20 * * *（每天上午/下午）"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                        {"component": "VSelect", "props": {
                            "model": "channel", "label": "通知渠道",
                            "items": CHANNEL_ITEMS}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                        {"component": "VTextField", "props": {
                            "model": "retain_days", "label": "记录保留天数",
                            "placeholder": "默认 7 天，0=永久保留"}}]},
                ]},
                # 完结列表页 path
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12}, "content": [
                        {"component": "VTextarea", "props": {
                            "model": "ended_list_path", "label": "站点完结列表页 path",
                            "rows": 4,
                            "placeholder": "每行 domain=path"}}]},
                ]},
                # 重置按钮
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12}, "content": [
                        {"component": "VBtn", "props": {
                            "color": "error", "variant": "tonal"},
                         "text": "重置已通知列表",
                         "events": {"click": {
                            "api": "plugin/EndedTVPackScan/reset", "method": "get",
                            "params": {"apikey": settings.API_TOKEN}},
                        }}]},
                ]},
            ],
        }
        default_config = {
            "enabled": False, "onlyonce": False, "only_free": False,
            "cron": "0 8,20 * * *", "channel": "", "retain_days": 7,
            "ended_list_path": "\n".join(f"{k}={v}" for k, v in DEFAULT_LIST_PATH.items()),
        }
        return [form], default_config

    # ---------- 详情页 ----------

    @staticmethod
    def _record_card(r: dict) -> dict:
        """单条已通知记录卡片（右上角 X 删除该记录并解除去重）。"""
        return {
            "component": "VCard",
            "content": [
                {"component": "VDialogCloseBtn",
                 "props": {"innerClass": "absolute top-0 right-0"},
                 "events": {"click": {
                     "api": "plugin/EndedTVPackScan/delete", "method": "get",
                     "params": {"key": r["key"], "apikey": settings.API_TOKEN}}}},
                {"component": "div",
                 "props": {"class": "d-flex justify-space-start flex-nowrap"},
                 "content": [
                     {"component": "div", "content": [
                         {"component": "VImg", "props": {
                             "src": r.get("poster") or "", "height": 120,
                             "width": 80, "cover": True}}]},
                     {"component": "div", "content": [
                         {"component": "VCardTitle",
                          "props": {"class": "pa-1 break-words"}, "text": r["title"]},
                         {"component": "VCardText", "props": {"class": "pa-0 px-2"},
                          "text": f'站点：{r["site"]}  大小：{EndedTVPackScan._format_size(r["size"])}'},
                         {"component": "VCardText", "props": {"class": "pa-0 px-2"},
                          "text": f'促销：{r["volume"]}  剩余免费：{r["freedate_diff"] or "—"}'},
                         {"component": "VCardText", "props": {"class": "pa-0 px-2"},
                          "text": f'通知时间：{r["time"]}'},
                         # 打开种子页（新标签）；旧记录无链接时禁用
                         {"component": "VBtn", "props": {
                             "href": r.get("page_url") or "",
                             "target": "_blank",
                             "rel": "noopener noreferrer",
                             "variant": "tonal", "color": "primary", "size": "small",
                             "prepend-icon": "mdi-open-in-new",
                             "class": "mt-2 text-none",
                             "disabled": not r.get("page_url")},
                          "text": "打开种子页"},
                     ]},
                 ]},
            ],
        }

    def get_page(self) -> list[dict]:
        """展示已通知记录卡片列表（分页，每页 10 条，左右箭头切换）。"""
        records = self.get_data(self.RECORDS_KEY) or []
        if not records:
            return [{"component": "div", "text": "暂无已通知记录",
                     "props": {"class": "text-center"}}]
        records = sorted(records, key=lambda x: x.get("time", ""), reverse=True)
        page_size = 10
        pages = [records[i:i + page_size] for i in range(0, len(records), page_size)]
        page_items = []
        for page_index, page_records in enumerate(pages, start=1):
            page_items.append({
                "component": "VWindowItem",
                "content": [
                    {"component": "div",
                     "props": {"class": "d-flex justify-space-between align-center mb-2 text-caption text-medium-emphasis"},
                     "content": [
                         {"component": "span", "text": f"第 {page_index} / {len(pages)} 页"},
                         {"component": "span", "text": f"本页 {len(page_records)} 条 / 共 {len(records)} 条"},
                     ]},
                    {"component": "div",
                     "props": {"class": "d-flex flex-column gap-3"},
                     "content": [self._record_card(r) for r in page_records]},
                ],
            })
        return [{
            "component": "VWindow",
            "props": {"show-arrows": "hover"},
            "content": page_items,
        }]

    # ---------- 插件 API ----------

    def get_api(self) -> list[dict[str, Any]]:
        """注册重置与删除接口。"""
        return [
            {"path": "/reset", "endpoint": self.reset_notified,
             "methods": ["GET"], "summary": "清空已通知列表"},
            {"path": "/delete", "endpoint": self.delete_record,
             "methods": ["GET"], "summary": "删除单条已通知记录"},
        ]

    def reset_notified(self, apikey: str):
        """清空已通知列表。"""
        from app import schemas
        if apikey != settings.API_TOKEN:
            return schemas.Response(success=False, message="API密钥错误")
        self.save_data(self.NOTIFIED_KEY, [])
        self.save_data(self.RECORDS_KEY, [])
        return schemas.Response(success=True, message="已清空已通知列表")

    def delete_record(self, key: str, apikey: str):
        """删除单条记录，解除去重可重新通知。"""
        from app import schemas
        if apikey != settings.API_TOKEN:
            return schemas.Response(success=False, message="API密钥错误")
        notified = [k for k in (self.get_data(self.NOTIFIED_KEY) or []) if k != key]
        records = [r for r in (self.get_data(self.RECORDS_KEY) or []) if r.get("key") != key]
        self.save_data(self.NOTIFIED_KEY, notified)
        self.save_data(self.RECORDS_KEY, records)
        return schemas.Response(success=True, message="删除成功")

    # ---------- 扫描主流程 ----------

    def scan(self):
        """遍历配置的完结列表页，扫描当天发布的完结剧集。"""
        logger.info("开始扫描完结剧集")
        self._cleanup_old_records()
        from app.application.site.sites import SitesHelper
        from app.modules.indexer import IndexerModule

        helper = SitesHelper()
        indexer = IndexerModule()
        for domain, ended_path in self._ended_list_path.items():
            torrents = self._fetch_ended(helper, indexer, domain, ended_path)
            for t in (torrents or []):
                if not self._is_today(t):
                    continue
                self._handle_hit(t, domain)
        logger.info("完结剧集扫描完成")

    def _fetch_ended(self, helper, indexer, domain: str, ended_path: str) -> list:
        """取站点配置副本注入完结 path，复用索引器解析。"""
        site = helper.get_indexer(domain)
        if not site:
            logger.warn(f"{domain} 未配置站点索引，跳过")
            return []
        # 副本注入完结 path（浏览模式读 browse.path）
        site_copy = dict(site)
        browse = dict(site.get("browse") or {})
        browse["path"] = self._with_page(ended_path)
        site_copy["browse"] = browse
        try:
            torrents = indexer.refresh_torrents(site=site_copy, page=0) or []
            logger.info(f"{domain} 完结种子 {len(torrents)} 个")
            return torrents
        except Exception as e:
            logger.warn(f"{domain} 扫描失败：{e}")
            return []

    @staticmethod
    def _with_page(path: str) -> str:
        """在 path 上补分页占位 {page}。"""
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}p={{page}}"

    def _handle_hit(self, torrent, domain: str):
        """识别、去重、通知单条种子。"""
        from app.chain.media import MediaChain
        from app.domain.metainfo import MetaInfo
        from app.schemas.types import MediaType

        meta = MetaInfo(torrent.title)
        if meta.type != MediaType.TV:
            return
        mediainfo = None
        try:
            mediainfo = MediaChain().recognize_media(meta=meta, mtype=MediaType.TV)
        except Exception as e:
            logger.warn(f"识别失败 {torrent.title}：{e}")

        key = self._dedup_key(mediainfo, torrent.title)
        notified = self.get_data(self.NOTIFIED_KEY) or []
        if key in notified:
            return
        self._notify(torrent, mediainfo, domain)
        notified.append(key)
        self.save_data(self.NOTIFIED_KEY, notified)
        self._append_record(torrent, mediainfo, domain, key)

    def _notify(self, torrent, mediainfo, domain: str):
        """发送带海报的通知。"""
        from app.schemas.types import MessageType, NotificationChannel
        channel = None
        if self._channel == "Telegram":
            channel = NotificationChannel.Telegram
        elif self._channel == "Feishu":
            channel = NotificationChannel.Feishu
        self.post_message(
            mtype=MessageType.Plugin,
            title=self._build_title(mediainfo, torrent),
            text=self._build_text(mediainfo, torrent, domain),
            image=self._poster_url(mediainfo),
            channel=channel,
        )

    # ---------- 辅助方法 ----------

    @staticmethod
    def _format_size(size) -> str:
        """字节大小格式化为紧凑可读格式（如 31.38G），空值/异常返回空串。"""
        if size in (None, ""):
            return ""
        try:
            return StringUtils.str_filesize(size)
        except Exception:
            return str(size)

    def _cleanup_old_records(self):
        """清理超过保留天数的已通知记录，避免数据无限增长。

        被清理种子的 pubdate 早于保留天数，不会再命中"当天发布"过滤，
        因此清理去重键不会导致重复通知。
        """
        from datetime import datetime, timedelta
        if self._retain_days <= 0:
            return  # 0=永久保留
        cutoff = (datetime.now() - timedelta(days=self._retain_days)).strftime(
            "%Y-%m-%d %H:%M:%S")
        records = self.get_data(self.RECORDS_KEY) or []
        kept = [r for r in records if (r.get("time") or "") >= cutoff]
        if len(kept) == len(records):
            return
        kept_keys = {r.get("key") for r in kept}
        removed = len(records) - len(kept)
        notified = [k for k in (self.get_data(self.NOTIFIED_KEY) or []) if k in kept_keys]
        self.save_data(self.RECORDS_KEY, kept)
        self.save_data(self.NOTIFIED_KEY, notified)
        logger.info(f"已清理 {removed} 条超过 {self._retain_days} 天的已通知记录")

    @staticmethod
    def _dedup_key(mediainfo, title: str) -> str:
        """去重键：tmdb_id 优先，失败回退归一化标题。"""
        if mediainfo and getattr(mediainfo, "tmdb_id", None):
            return f"tmdb:{mediainfo.tmdb_id}"
        norm = "".join(c.lower() for c in title if c.isalnum())
        return f"title:{norm}"

    @staticmethod
    def _poster_url(mediainfo) -> Optional[str]:
        """TMDB 海报 URL（poster_path 可能已是完整镜像 URL）。"""
        poster = getattr(mediainfo, "poster_path", None) if mediainfo else None
        if not poster:
            return None
        if poster.startswith("http"):
            return poster
        return f"https://image.tmdb.org/t/p/w500{poster}"

    @staticmethod
    def _build_title(mediainfo, torrent) -> str:
        """通知标题。"""
        name = getattr(mediainfo, "title", None) if mediainfo else None
        return f"【完结】{name or torrent.title}"

    @staticmethod
    def _build_text(mediainfo, torrent, domain: str) -> str:
        """通知正文：简介 + 种子信息 + 免费剩余时间。"""
        overview = ""
        if mediainfo and getattr(mediainfo, "overview", None):
            overview = mediainfo.overview[:200]
        # 免费判定
        factor = getattr(torrent, "downloadvolumefactor", None)
        is_free = factor is not None and float(factor) == 0
        volume = getattr(torrent, "volume_factor", None) or ("Free" if is_free else "")
        freedate = getattr(torrent, "freedate_diff", None) or "—"
        size = EndedTVPackScan._format_size(getattr(torrent, "size", None))
        seeders = getattr(torrent, "seeders", None) or 0
        peers = getattr(torrent, "peers", None) or 0
        return (
            f"{overview}\n\n"
            f"种子：{torrent.title}\n"
            f"站点：{domain}  大小：{size}\n"
            f"做种：{seeders}  下载：{peers}\n"
            f"促销：{volume}  剩余免费：{freedate}\n"
            f"详情：{getattr(torrent, 'page_url', '') or ''}"
        )

    def _append_record(self, torrent, mediainfo, domain: str, key: str):
        """追加一条展示记录。"""
        from datetime import datetime
        records = self.get_data(self.RECORDS_KEY) or []
        records.append({
            "key": key,
            "title": getattr(mediainfo, "title", None) or torrent.title,
            "poster": self._poster_url(mediainfo) or "",
            "site": domain,
            "size": self._format_size(getattr(torrent, "size", None)),
            "page_url": getattr(torrent, "page_url", "") or "",
            "volume": getattr(torrent, "volume_factor", "") or "",
            "freedate_diff": getattr(torrent, "freedate_diff", "") or "",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.save_data(self.RECORDS_KEY, records)

    @staticmethod
    def _is_today(torrent) -> bool:
        """判断种子是否当天发布（优先 pubdate 解析日期，date_elapsed 仅分钟级兜底）。"""
        from datetime import datetime
        today = datetime.now().date()
        # 优先用 pubdate 解析日期比较
        pub = getattr(torrent, "pubdate", None)
        if pub:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    return datetime.strptime(pub.strip(), fmt).date() == today
                except ValueError:
                    continue
        # date_elapsed 兜底：仅分钟级（必然当天）才视为今天；小时级可能跨天不算
        elapsed = (getattr(torrent, "date_elapsed", "") or "").lower()
        if any(k in elapsed for k in ("今天", "今日", "just now", "minute", "分钟")):
            return True
        return False

    @staticmethod
    def _parse_list_path(text: str | None) -> dict[str, str]:
        """文本配置（每行 domain=path）解析为 dict。"""
        result: dict[str, str] = {}
        if not text:
            return result
        for line in str(text).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            domain, _, path = line.partition("=")
            domain, path = domain.strip(), path.strip()
            if domain and path:
                result[domain] = path
        return result

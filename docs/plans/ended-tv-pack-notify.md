# 完结连续剧扫描通知插件 · 实现计划

> 状态：方案设计（v2，已按站点 tag 修正完结判定），待评审
> 日期：2026-08-22
> 参考：`docs/Plugin_Development.md`（V3 主指南）、`MoviePilot/skills/create-moviepilot-plugin`、`docs/faq/01`、`docs/faq/04`、`docs/faq/06`、PT-depiler 站点定义

## 1. 需求

- 定期从 PT 站点扫描**当天发布的完结连续剧种子**。
- 一天扫描 2 次（上午、下午各一次）。
- 一部连续剧**只通知一次**（去重）。
- 推送到 Telegram 或飞书，通知需带：海报图片、剧情简介、种子信息（站点/标题/大小/做种/下载/促销状态）、免费种子的剩余免费时间。

## 2. 关键决策与验证证据

### 2.1 完结判定：用站点完结列表页 URL 过滤，不用标题正则（已验证）

用 CookieCloud 取站点 cookie 实际访问三个目标站点的完结列表页：

| 站点 | 完结列表页 URL | 返回种子数 | 标题含完结字样比例 |
| --- | --- | --- | --- |
| hhanclub | `torrents.php?tag_id17=1` | 179 | **0/101**（`S and x S01...` 全不含） |
| hddolby | `torrents.php?mystat=complete` | 227 | **100/101**（标题带 Complete 是该站发布习惯，非普适） |
| pandapt | `torrents.php?tag_id=10` | 253 | **2/106**（`Mystic Nine 2026 S01...` 几乎全不含） |

**结论**：三个站标题含完结的比例从 0% 到 99% 横跳，标题正则无法统一覆盖。完结只能靠站点的完结列表页 URL（tag/状态参数）过滤。用户直接提供三个完结列表页 URL，插件配置存这些 URL 即可。参数名各站不同（`tag_id17`/`mystat`/`tag_id`），存完整 URL 最省事。

### 2.2 站点 tag 语义（已确认：季完结即通知）

「完结」= **季完结即通知**：不要求全季打包种子，该剧/该季已完结即纳入通知（含单季完结剧，如 `S and x S01`）。hhanclub 的「完结」tag（`tag_id17=1`）标记的正是这类已完结剧。各站点「完结」tag 机制不同，PT-depiler 站点定义是权威映射源（见 2.3）。不额外做季范围（`S01-S05`）标题判断。

### 2.3 各站点完结 tag 映射（来自 PT-depiler + 实测验证）

| 站点 | 完结标记机制 | 注入/获取方式 | 实测 |
| --- | --- | --- | --- |
| hhanclub | `tag_id17=1`（URL 片段） | 注入 browse path `?tag_id17=1` | ✅ 详情页+列表页验证，标题不含完结，tag 链接可过滤 |
| audiences | `span.tags.twj`（纯 span 非链接，文案"完结"） | **无 tag URL** → browse 后按 `labels` 过滤 或 自解析 | ✅ 列表页验证，7 个完结标签均为纯 span，标题不含完结 |
| starspace | `span.tag:contains('完结')` | 列表内文本标签 → browse+labels 过滤 | 待验 |
| hdhome | `value:"wj"` | 搜索参数注入 | 待验 |
| cspt / zhuque / yemapt / azusa / qingwa / sewerpt | 分类/tag id 数值（cspt=`tag_id` 维度 value=9） | URL 参数注入（按各站参数名） | cspt 定义确认 URL 参数型；其余待验 |
| cinemaz / privatehd / avistaz / animez | 动态 `tags.push({name:"完结"})` | 运行时标签 → browse+labels 过滤 | 待验 |

**机制分两类（已实测确认）**：
- **URL/参数型**（hhanclub、cspt 等）：改 browse path 注入 tag 参数，复用 MoviePilot 索引器解析。**主方案支持这类**。
- **列表内标签型**（audiences、starspace 等）：完结是种子行内 `<span>` 标签，**无 URL 过滤入口**，不能注入 path。需 `browse` 全量后按 `TorrentInfo.labels` 含「完结」过滤（前提：该站索引配置抓了 `span.tags.*` 作为 labels），或插件自请求列表页 + lxml 按 `span.tags.twj` 过滤行（§6.6）。**第二方案支持这类**。

### 2.4 其他默认（可配置）

- 扫描范围：默认全部已配置站点；可在配置勾选子集。
- 通知渠道：默认全部已启用渠道；可限定 Telegram / 飞书 / 全部。
- 去重周期：默认永久（同一剧只推一次）；提供 API 重置。
- 免费：全部通知 + 标注；免费种子额外带 `freedate_diff`，非免费显示促销状态。

## 3. 整体流程

```
get_service() (cron "0 8,20 * * *") ──▶ scan()
                                          │
          遍历站点 (SitesHelper.get_indexers)         
                  ▼
          取站点配置副本，注入完结 tag 参数 (如 +tag_id17=1)
                  ▼
          IndexerModule.refresh_torrents(site=副本) ← 复用解析，只拿完结种子
                  ▼
          过滤「当天发布」(pubdate == 今天)                
                  ▼
          媒体识别 MediaChain.recognize_media → TMDB poster_path/overview/tmdb_id
                  ▼
          去重 (tmdb_id ∈ 已通知集合 ? 跳过)              
                  ▼
          组装通知 (海报URL + 简介 + 种子信息 + freedate_diff)
                  ▼
          self.post_message(mtype=Plugin, image=海报URL, ...) 
                  ▼
          save_data(已通知集合)
```

## 4. 关键宿主能力依赖（已核实）

| 能力 | 入口 | 说明 |
| --- | --- | --- |
| 站点索引 | `app.application.site.sites.SitesHelper` | `get_indexers() -> List[dict]`，站点配置含 `domain/cookie/ua/browse/search/fields` |
| 站点配置扩展 | `SitesHelper().add_indexer(domain, indexer)` (faq/06) | 新增/修改站点索引配置 |
| 浏览最新种子 | `IndexerModule.refresh_torrents(site: dict, ...)` (`indexer/__init__.py:594`) | **site 是 dict，可传改过 path 的副本**，复用全部种子解析 |
| 种子对象 | `app.schemas.context.TorrentInfo` (`context.py:346`) | `title/size/seeders/peers/pubdate/date_elapsed/freedate/freedate_diff/uploadvolumefactor/downloadvolumefactor/volume_factor/page_url/site_name/labels` |
| 媒体识别 | `app.chain.media.MediaChain` | `recognize_media(meta=, mtype=MediaType.TV) -> Optional[MediaInfo]`，结果含 `tmdb_id/poster_path/overview` |
| 通知发送 | `_PluginBase.post_message` (`plugins/__init__.py:337`) | `post_message(mtype=, title=, text=, image=<URL>, channel=None)`；image 传 URL，Telegram/飞书自动下载展示 |
| 通知渠道 | `NotificationChannel` (`schemas/types.py:483`) | `Telegram`/`Feishu`；不传 `channel` 走全部 |
| 定时服务 | `_PluginBase.get_service` (`plugins/__init__.py:128`) | 返回 `[{id,name,trigger:CronTrigger,func,kwargs}]` |
| 状态持久化 | `_PluginBase.save_data/get_data` (`plugins/__init__.py:286/318`) | 存已通知 `tmdb_id` 集合 |
| 网络请求 | `app.sdk.network` | 备选方案自请求 tag 列表页 |

**站点 cookie 来源**：MoviePilot 站点配置的 `cookie` 字段（由系统 CookieCloud 同步或用户手填）。插件经 `SitesHelper.get_indexer` 取，**不自己解密 CookieCloud**。homestead 的 pt-agent（`packages/core/pt-agent/src/collector.ts`）有「CookieCloud→cookie→PT-depiler 抓站」现成参考。

**海报 URL**：`https://image.tmdb.org/t/p/w500{poster_path}`。

## 5. 插件结构

```
MoviePilot-Plugins/
├── plugins.v3/
│   └── endedtvpackscan/
│       └── __init__.py           # 主类 EndedTVPackScan
└── package.v3.json               # 追加 EndedTVPackScan 条目
```

主类元数据与 `package.v3.json` 条目按 V3 规范（类名=插件 ID，目录=类名小写，`plugin_version` 与索引 `version` 一致）。

## 6. 核心逻辑设计

### 6.1 入口与定时

```python
from apscheduler.triggers.cron import CronTrigger

def get_service(self) -> list[dict]:
    if not self.get_state() or not self._cron:
        return []
    return [{
        "id": "EndedTVPackScan.Scan",
        "name": "完结剧集扫描",
        "trigger": CronTrigger.from_crontab(self._cron),  # 默认 "0 8,20 * * *"
        "func": self.scan,
        "kwargs": {},
    }]
```

### 6.2 扫描主流程 `scan()`（主方案：注入 tag 参数 + 复用索引器）

```python
def scan(self):
    indexers = SitesHelper().get_indexers() or []
    for site in self._filter_sites(indexers):
        tag_path = self._ended_tag_path(site["domain"])     # 6.3
        if not tag_path:
            continue                                        # 该站未配置完结 tag
        site_copy = self._inject_tag(site, tag_path)        # 配置副本注入 tag
        try:
            torrents = IndexerModule().refresh_torrents(site=site_copy, page=0) or []
        except Exception as e:
            logger.warn(f"站点 {site.get('name')} 扫描失败：{e}")
            continue
        for t in torrents:
            if not self._is_published_today(t):             # 6.4
                continue
            self._handle_hit(t)                             # 6.5
```

> **关键技术风险**：`refresh_torrents` 用站点配置副本注入 tag query 后能否只返完结种子，取决于该站 `browse`/`search` path 格式与 NexusPHP 对 `tag_id` 共存分页的支持。**须先做原型验证**（见 §11）。若不可行，回退备选方案 6.6。

### 6.3 完结列表页路径映射 `_ended_list_path`

按 domain 返回该站完结列表页 path（实测三站作默认值，用户可覆盖/补充）：

```python
ENDED_LIST_PATH = {
    "hhanclub.net":  "torrents.php?tag_id17=1",     # tag 参数
    "hddolby.com":   "torrents.php?mystat=complete", # 状态参数
    "pandapt.net":   "torrents.php?tag_id=10",       # tag 参数
}
# 配置项 ended_list_path：每行 domain=path，覆盖/补充内置
```

仅返回已配置完结 path 的站点；未配置的站点跳过（如 audiences 列表标签型，走 6.6 备选）。

### 6.4 当天发布判断 `_is_published_today`

- 主用 `TorrentInfo.pubdate` 解析为日期比较；格式不确定时容错（`YYYY-MM-DD`、相对时间）。
- 备选 `date_elapsed` 兜底（命中「今天/今日」视为当天）。
- 解析失败默认**不算当天**（宁可漏不可错推）。

### 6.5 命中处理 `_handle_hit`

```python
def _handle_hit(self, torrent: TorrentInfo):
    meta = MetaInfo(torrent.title)
    if meta.type != MediaType.TV:
        return
    mediainfo = MediaChain().recognize_media(meta=meta, mtype=MediaType.TV)
    dedup_key = self._dedup_key(mediainfo, torrent.title)   # tmdb_id 优先，失败用归一化标题

    notified = self.get_data(self.NOTIFIED_KEY) or []
    if dedup_key in notified:
        return

    self.post_message(
        mtype=MessageType.Plugin,
        title=self._build_title(mediainfo, torrent),
        text=self._build_text(mediainfo, torrent),
        image=self._poster_url(mediainfo),
        channel=self._channel,
    )
    notified.append(dedup_key)
    self.save_data(self.NOTIFIED_KEY, notified)
```

`_dedup_key`：识别成功用 `f"tmdb:{mediainfo.tmdb_id}"`；失败回退 `f"title:{norm}"`（小写、去空格符号）。

### 6.6 备选方案：插件自请求 tag 列表页（URL/标签型站点兜底）

当索引器注入 tag path 不可行时，用 `app.sdk.network` 直接请求站点完结 tag 列表页（如 `https://hhanclub.net/torrents.php?tag_id17=1`），用站点 cookie（来自 `SitesHelper`）+ lxml 按 NexusPHP 结构解析，字段映射参考 PT-depiler 该站 `search.selectors`。免费/促销信息需自行解析站点促销图标（NexusPHP `img.pro_*` class）。

> 代价：失去 MoviePilot 统一种子解析，每站需选择器适配。优先尝试主方案 6.2。

## 7. 通知内容格式

- **标题**：`【完结】{剧名}`（识别失败用种子标题）。
- **图片**：`https://image.tmdb.org/t/p/w500{poster_path}`（无则不传）。
- **正文**：

```
{剧名}
{简介（截断 200 字）}

种子：{种子标题}
站点：{site_name}  大小：{size 人读}
做种：{seeders}  下载：{peers}
促销：{volume_factor 或 "Free"}  剩余免费：{freedate_diff 或 "—"}
详情：{page_url}
```

免费判定：`downloadvolumefactor == 0` → 标注「Free」并展示 `freedate_diff`；否则显示 `volume_factor`，剩余免费显示「—」（永久免费显示「永久免费」）。

## 8. 界面设计

### 8.1 配置项速览

| 字段 | 组件 | 默认 | 说明 |
| --- | --- | --- | --- |
| `enabled` | VSwitch | false | 启用 |
| `onlyonce` | VSwitch | false | 保存后立即运行一次 |
| `only_free` | VSwitch | false | 仅通知免费种子（预留） |
| `cron` | VCronField | `0 8,20 * * *` | 每天两次 |
| `channel` | VSelect | 全部 | 全部/Telegram/飞书 |
| `ended_list_path` | VTextarea | 内置三站 | 站点完结列表页 path，每行 `domain=path`，**同时决定扫描站点范围** |

> 扫描站点范围由 `ended_list_path` 的 domain 决定，无需单独选站点——配了哪个 domain 就扫哪个站。

### 8.2 配置页 `get_form`

```python
def get_form(self) -> tuple[list[dict], dict]:
    return [
        {
            "component": "VForm",
            "content": [
                # 开关行
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                        {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                        {"component": "VSwitch", "props": {"model": "onlyonce", "label": "立即运行一次"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                        {"component": "VSwitch", "props": {"model": "only_free", "label": "仅通知免费种子"}}]},
                ]},
                # cron + 渠道
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VCronField", "props": {
                            "model": "cron", "label": "执行周期",
                            "placeholder": "默认 0 8,20 * * *（每天上午/下午）"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VSelect", "props": {
                            "model": "channel", "label": "通知渠道",
                            "items": [
                                {"title": "全部已启用渠道", "value": ""},
                                {"title": "Telegram", "value": "Telegram"},
                                {"title": "飞书", "value": "Feishu"}]}}]},
                ]},
                # 完结列表页 path 映射
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12}, "content": [
                        {"component": "VTextarea", "props": {
                            "model": "ended_list_path", "label": "站点完结列表页 path",
                            "rows": 4,
                            "placeholder": "每行 domain=path\nhhanclub.net=torrents.php?tag_id17=1\nhddolby.com=torrents.php?mystat=complete\npandapt.net=torrents.php?tag_id=10"}}]},
                ]},
                # 重置按钮
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12}, "content": [
                        {"component": "VBtn", "props": {"color": "error", "variant": "tonal", "text": "重置已通知列表"},
                         "events": {"click": {
                            "api": "plugin/EndedTVPackScan/reset", "method": "post",
                            "params": {"apikey": "{{ settings.API_TOKEN }}"}}}}]},
                ]},
            ],
        }
    ], {
        "enabled": False, "onlyonce": False, "only_free": False,
        "cron": "0 8,20 * * *", "channel": "",
        "ended_list_path":
            "hhanclub.net=torrents.php?tag_id17=1\n"
            "hddolby.com=torrents.php?mystat=complete\n"
            "pandapt.net=torrents.php?tag_id=10",
    }
```

### 8.3 详情页 `get_page`（已通知记录卡片列表）

读取 `notified_records`，按通知时间倒序，每条一张海报卡片（参考 rsssubscribe 的 VCard+VImg 风格）：

```python
def get_page(self) -> list[dict]:
    records = self.get_data("notified_records") or []
    if not records:
        return [{"component": "div", "text": "暂无已通知记录", "props": {"class": "text-center"}}]
    records = sorted(records, key=lambda x: x.get("time", ""), reverse=True)
    contents = []
    for r in records:
        contents.append({
            "component": "VCard",
            "content": [
                # 右上角删除按钮（调 /delete API 删单条，解除去重可重新通知）
                {"component": "VDialogCloseBtn", "props": {"innerClass": "absolute top-0 right-0"},
                 "events": {"click": {"api": "plugin/EndedTVPackScan/delete", "method": "get",
                                      "params": {"key": r["key"], "apikey": settings.API_TOKEN}}}},
                {"component": "div", "props": {"class": "d-flex justify-space-start flex-nowrap"},
                 "content": [
                    # 海报
                    {"component": "div", "content": [
                        {"component": "VImg", "props": {
                            "src": r.get("poster") or "", "height": 120, "width": 80, "cover": True}}]},
                    # 文本
                    {"component": "div", "content": [
                        {"component": "VCardTitle", "props": {"class": "pa-1 break-words"}, "text": r["title"]},
                        {"component": "VCardText", "props": {"class": "pa-0 px-2"},
                         "text": f'站点：{r["site"]}  大小：{r["size"]}'},
                        {"component": "VCardText", "props": {"class": "pa-0 px-2"},
                         "text": f'促销：{r["volume"]}  剩余免费：{r["freedate_diff"] or "—"}'},
                        {"component": "VCardText", "props": {"class": "pa-0 px-2"},
                         "text": f'通知时间：{r["time"]}'},
                    ]},
                ]},
            ],
        })
    return contents
```

`notified_records` 每条结构：`{key, title, poster, site, size, volume, freedate_diff, time}`，`key` 即去重键（tmdb_id 优先），通知时同时写入该列表供详情页展示。

### 8.4 配套 API `get_api`

```python
def get_api(self) -> list[dict]:
    return [
        {"path": "/reset", "endpoint": self.reset_notified,
         "methods": ["POST"], "auth": "bear", "summary": "清空已通知列表"},
        {"path": "/delete", "endpoint": self.delete_record,
         "methods": ["GET"], "auth": "bear", "summary": "删除单条已通知记录"},
    ]

def reset_notified(self):
    self.save_data(self.NOTIFIED_KEY, [])
    self.save_data("notified_records", [])
    return {"success": True}

def delete_record(self, key: str):
    notified = [k for k in (self.get_data(self.NOTIFIED_KEY) or []) if k != key]
    records = [r for r in (self.get_data("notified_records") or []) if r.get("key") != key]
    self.save_data(self.NOTIFIED_KEY, notified)
    self.save_data("notified_records", records)
    return {"success": True}
```

## 9. 插件 API

见 §8.4，提供 `/reset`（清空已通知）与 `/delete`（删单条记录）两个接口，分别供配置页重置按钮、详情页卡片删除按钮调用。

## 10. 生命周期与清理

```python
def init_plugin(self, config: dict | None = None) -> None:
    config = config or {}
    self._enabled = bool(config.get("enabled"))
    self._onlyonce = bool(config.get("onlyonce"))      # 保存后立即跑一次
    self._only_free = bool(config.get("only_free"))
    self._cron = config.get("cron") or "0 8,20 * * *"
    self._channel = self._parse_channel(config.get("channel"))
    # ended_list_path 文本（每行 domain=path）解析为 dict，与内置默认合并
    self._ended_list_path = {**self.DEFAULT_LIST_PATH, **self._parse_list_path(config.get("ended_list_path"))}

def stop_service(self) -> None:
    self._enabled = False   # APScheduler 任务由宿主管，无自建线程
```

## 11. 原型验证（开发第一步，已部分通过）

已用认证 + CookieCloud 脚本验证的关键点：

- [x] **认证**：环境变量 `AUTH_SITE=hdfans` + `HDFANS_UID`/`HDFANS_PASSKEY` + `GITHUB_TOKEN` → `check_user()` 返回 `True`，站点功能解锁。本地 dev 设这些环境变量即可认证。
- [x] **配置获取**：`SitesHelper().get_indexer("hhanclub.net")` 返回配置（含 `search`/`torrents.fields`），`torrents.fields` 含 `title/seeders/leechers/date_elapsed/date/freedate/labels/downloadvolumefactor` —— labels 与 freedate 字段齐全。
- [x] **注入方式**：给副本新建 `browse={"path": "torrents.php?tag_id17=1&p={page}"}` 后，`refresh_torrents` 让 SiteSpider **正确请求了完结 URL**（日志：`开始请求：https://hhanclub.net/torrents.php?tag_id17=1&p=0`）。主方案注入路径成立。
- [x] **完整解析**：`refresh_torrents(site=副本)` 成功解析完结页返回 `TorrentInfo`（样本：`S and x S01 2026...`，seeders=136/pubdate=`2026-08-21 14:53:34`/date_elapsed=`23时33分钟`/downloadvolumefactor=0.0/labels=`['官方','完结','中字']`）。健康统计服务只在裸脚本报错，完整 app 已注册不影响。
- [x] **站点 cookie**：站点导入后 `get_indexer` 返回的配置含 `cookie`/`ua`（CookieCloud 同步），插件 scan 可直接用。
- [x] **labels 含「完结」**：`TorrentInfo.labels` 抓到了站点 tag，可二次校验完结。
- [x] **pubdate 格式**：`2026-08-21 14:53:34`（`YYYY-MM-DD HH:MM:SS`），`_is_today` 可解析；`date_elapsed` 如 `23时33分钟` 兜底。

原型验证全部通过，主方案可行。下一步在 dev 实例加载插件跑 `onlyonce` 验证完整 scan 流程（当天过滤→识别→去重→通知）。

## 12. 边界与错误处理

- 识别失败：仍通知（种子标题当剧名、无海报），按归一化标题去重。
- 单站点扫描异常：记 warn 继续，不影响其他站。
- 当天判断容错：解析失败默认不算当天。
- 标签型站点（audiences 等）：v1 暂不支持或走 6.6 备选；主方案先覆盖 URL/参数型站点。
- 性能：`refresh_torrents` 有 TTL 缓存；一天两次开销可控。
- 无额外 Python 依赖（均用宿主 SDK），不需 `pyproject.toml`。

## 13. 发布前清单

- [ ] `../MoviePilot/.venv/bin/python -m compileall plugins.v3/endedtvpackscan`
- [ ] `plugin_version` 与 `package.v3.json` 一致
- [ ] V3 宿主加载无旧导入警告（`DEBUG=true`）
- [ ] 启用/禁用/重载无残留后台资源
- [ ] 手动触发 `/reset` 可清空已通知列表
- [ ] 当天完结种子 → 收到带海报通知；同剧二次扫描不再推
- [ ] 非当天种子被过滤

## 14. 待确认

- 标签型站点（audiences/starspace）的完结判定走「browse + labels 过滤」还是「6.6 自解析」？v1 先不覆盖。
- 去重是否需要「每天重置」模式？当前默认永久。
- 多站点同剧种子是否合并为一条通知（列出所有命中种子）？当前一剧一条。

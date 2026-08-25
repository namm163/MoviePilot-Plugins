# MoviePilot-Plugins-fork

我的 [MoviePilot](https://github.com/jxxghp/MoviePilot) 个人插件仓库（fork 自 [jxxghp/MoviePilot-Plugins](https://github.com/jxxghp/MoviePilot-Plugins) 后精简为个人插件专用）。

官方插件请从 MoviePilot 插件市场安装，本仓库只放我自己开发的插件。

## 插件列表

| 插件 | 说明 |
| --- | --- |
| [EndedTVPackScan](plugins.v3/endedtvpackscan/) | 完结剧集扫描通知：扫描 PT 站点当天发布的完结连续剧（基于站点完结列表页 path 过滤，如 hhanclub `?tag_id17=1`、hddolby `?mystat=complete`、pandapt `?tag_id=10`），TMDB 识别后按剧去重，推送带海报与简介的通知到 Telegram/飞书。设计文档见 [docs/plans/ended-tv-pack-notify.md](docs/plans/ended-tv-pack-notify.md)。 |

## 仓库结构

```text
├── plugins.v3/            # V3 插件源码（每个插件一个目录）
│   └── <plugin_id小写>/
│       └── __init__.py    # 主类，类名 = 插件 ID
├── icons/                 # 插件图标（package.v3.json 的 icon 字段引用文件名）
├── package.v3.json        # 插件市场索引（元数据/版本/history）
└── docs/plans/            # 插件设计文档
```

## 新增插件流程

1. 在 `plugins.v3/<插件ID小写>/__init__.py` 写插件主类（继承 `_PluginBase`，类名 = 插件 ID）。
2. 图标放 `icons/<图标名>.png`。
3. 在 `package.v3.json` 加条目（`version` 与插件类 `plugin_version` 保持一致，`history` 当前版本置顶）。
4. 设计文档放 `docs/plans/<插件名>.md`。

开发规范参考官方文档：[MoviePilot-Plugins/docs/Plugin_Development.md](https://github.com/jxxghp/MoviePilot-Plugins/blob/main/docs/Plugin_Development.md)。

## 本地开发调试

MoviePilot 实例配置环境变量指向本仓库，插件市场即可发现本地插件：

```sh
export PLUGIN_LOCAL_REPO_PATHS=/path/to/MoviePilot-Plugins-fork
export PLUGIN_AUTO_RELOAD=true   # 源码变更自动重载
```

最小检查（版本一致 + 语法编译，用 MoviePilot 的 venv）：

```sh
../MoviePilot/.venv/bin/python -m compileall plugins.v3/<插件ID小写>
```

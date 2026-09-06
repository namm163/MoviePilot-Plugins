"""插件骨架与配置解析测试。"""
from app.plugins.unseededcleaner import UnseededCleaner


def make_plugin() -> UnseededCleaner:
    """绕过 __init__ 构造插件实例（官方 tvdbdiscover 同款模式）。"""
    return object.__new__(UnseededCleaner)


class TestSkeleton:
    def test_class_attributes(self):
        assert UnseededCleaner.plugin_name == "未做种清理"
        assert UnseededCleaner.plugin_config_prefix == "unseededcleaner_"
        assert UnseededCleaner.plugin_version == "1.0.0"

    def test_get_state_always_true(self):
        """手动工具插件，无后台逻辑，安装即就绪。"""
        assert make_plugin().get_state() is True

    def test_get_form_returns_vuetify(self):
        forms, default = make_plugin().get_form()
        assert default == {"scan_dirs": "", "exclude_keywords": "", "notify": False}
        assert forms[0]["component"] == "VForm"


class TestParseLines:
    def test_parse_scan_dirs(self):
        plugin = make_plugin()
        plugin.init_plugin({"scan_dirs": "/media4/9kg\n/education \n\n# 注释\n"})
        assert plugin._scan_dirs == ["/media4/9kg", "/education"]

    def test_parse_exclude_keywords(self):
        plugin = make_plugin()
        plugin.init_plugin({"exclude_keywords": "sample\n\n"})
        assert plugin._exclude_keywords == ["sample"]

    def test_parse_crlf_and_blank_lines(self):
        plugin = make_plugin()
        plugin.init_plugin({"scan_dirs": "/media4/9kg\r\n   \r\n/education\r\n"})
        assert plugin._scan_dirs == ["/media4/9kg", "/education"]

    def test_empty_config(self):
        plugin = make_plugin()
        plugin.init_plugin(None)
        assert plugin._scan_dirs == []
        assert plugin._exclude_keywords == []
        assert plugin._notify is False


class TestIsExcluded:
    def test_builtin_excludes(self):
        plugin = make_plugin()
        plugin._exclude_keywords = []
        assert plugin._is_excluded("incomplete")
        assert plugin._is_excluded(".Trash-1000")

    def test_user_keywords(self):
        plugin = make_plugin()
        plugin._exclude_keywords = ["sample"]
        assert plugin._is_excluded("xxx-Sample-Pack")

    def test_normal_name_not_excluded(self):
        plugin = make_plugin()
        plugin._exclude_keywords = ["sample"]
        assert not plugin._is_excluded("某剧.S01.1080p")


# ---------- 扫描流程 ----------
import os
from types import SimpleNamespace

from app.plugins.unseededcleaner import SCAN_KEY, STATUS_KEY


class FakeTr:
    """Transmission 实例替身。"""

    def __init__(self, torrents=None, error=False):
        self.torrents, self.error = torrents or [], error

    def get_torrents(self, **kwargs):
        return self.torrents, self.error


def make_scanned_plugin(tmp_path, monkeypatch, torrents=None, error=False):
    """构造带内存存储与 FakeTr 的插件，扫描根指向 tmp_path。"""
    plugin = make_plugin()
    store = {}
    plugin.get_data = lambda key, *a, **k: store.get(key)
    plugin.save_data = lambda key, value, *a, **k: store.__setitem__(key, value)
    plugin._get_transmission = lambda: FakeTr(torrents, error)
    plugin._scan_dirs = [str(tmp_path)]
    plugin._exclude_keywords = []
    return plugin, store


class TestScan:
    def test_unseeded_units_found(self, tmp_path, monkeypatch):
        # 造 2 个单元：一个做种（保护）、一个未做种
        seeded = tmp_path / "seeded-pack"; seeded.mkdir()
        (seeded / "a.mkv").write_bytes(b"x" * 10)
        orphan = tmp_path / "orphan-pack"; orphan.mkdir()
        (orphan / "b.mkv").write_bytes(b"x" * 20)
        torrents = [SimpleNamespace(download_dir=str(tmp_path), name="seeded-pack")]
        plugin, store = make_scanned_plugin(tmp_path, monkeypatch, torrents)
        plugin._run_scan()

        scan = store[SCAN_KEY]
        assert scan["tr_torrent_count"] == 1
        assert len(scan["roots"]) == 1
        units = scan["roots"][0]["units"]
        assert [u["name"] for u in units] == ["orphan-pack"]
        assert units[0]["size"] == 20 and units[0]["sized"] is True

    def test_rpc_error_aborts_without_result(self, tmp_path, monkeypatch):
        (tmp_path / "orphan").mkdir()
        plugin, store = make_scanned_plugin(tmp_path, monkeypatch, error=True)
        plugin._run_scan()
        assert SCAN_KEY not in store          # 不产出结果
        assert store[STATUS_KEY]["status"] == "error"

    def test_no_transmission(self, tmp_path, monkeypatch):
        plugin, store = make_scanned_plugin(tmp_path, monkeypatch)
        plugin._get_transmission = lambda: None
        plugin._run_scan()
        assert store[STATUS_KEY]["status"] == "error"

    def test_excluded_unit_not_listed(self, tmp_path, monkeypatch):
        (tmp_path / "normal").mkdir()
        (tmp_path / "incomplete").mkdir()     # 内置排除
        (tmp_path / "skip-me").mkdir()        # 用户关键字排除
        plugin, store = make_scanned_plugin(tmp_path, monkeypatch)
        plugin._exclude_keywords = ["skip"]
        plugin._run_scan()
        names = [u["name"] for u in store[SCAN_KEY]["roots"][0]["units"]]
        assert names == ["normal"]

    def test_seed_deeper_inside_unit_protects_it(self, tmp_path, monkeypatch):
        # 单元目录内部有种子（download_dir 更深一层）→ 整单元保护
        unit = tmp_path / "tv-pack"; unit.mkdir()
        (unit / "s01e01").mkdir()
        torrents = [SimpleNamespace(download_dir=str(unit), name="s01e01")]
        plugin, store = make_scanned_plugin(tmp_path, monkeypatch, torrents)
        plugin._run_scan()
        assert store[SCAN_KEY]["roots"][0]["units"] == []

    def test_scan_root_missing(self, tmp_path, monkeypatch):
        plugin, store = make_scanned_plugin(tmp_path, monkeypatch)
        plugin._scan_dirs = [str(tmp_path / "no-such-dir")]
        plugin._run_scan()
        scan = store[SCAN_KEY]
        assert scan["roots"][0].get("missing") is True
        assert scan["roots"][0]["units"] == []


class TestScanApi:
    def test_api_scan_requires_dirs(self, monkeypatch):
        plugin = make_plugin()
        plugin._scan_dirs = []
        resp = plugin.api_scan(apikey="wrong")
        assert resp.success is False

    def test_api_scan_auth(self, monkeypatch):
        plugin = make_plugin()
        plugin._scan_dirs = ["/tmp"]
        resp = plugin.api_scan(apikey="wrong")
        assert resp.success is False and "密钥" in resp.message

    def test_api_scan_starts_thread(self, tmp_path, monkeypatch):
        from app.runtime.config import settings
        monkeypatch.setattr(settings, "API_TOKEN", "test-token", raising=False)
        plugin, _ = make_scanned_plugin(tmp_path, monkeypatch)
        resp = plugin.api_scan(apikey="test-token")
        # 扫描线程已启动即视为成功（线程瞬间完成或仍在跑均可）
        assert resp.success is True

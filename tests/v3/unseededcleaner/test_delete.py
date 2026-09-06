"""两步删除与四道校验测试。"""
from types import SimpleNamespace

from app.plugins.unseededcleaner import (
    LOG_KEY,
    PENDING_KEY,
    SCAN_KEY,
    STATUS_KEY,
    UnseededCleaner,
)
from tests.v3.unseededcleaner.test_scan import FakeTr, make_scanned_plugin


class TestMark:
    def test_mark_requires_scan_result(self, tmp_path, monkeypatch):
        plugin, _ = make_scanned_plugin(tmp_path, monkeypatch)
        resp = plugin.api_mark(apikey="wrong", path="/any")
        assert resp.success is False

    def test_mark_toggle(self, tmp_path, monkeypatch):
        plugin, store = make_scanned_plugin(tmp_path, monkeypatch)
        (tmp_path / "orphan").mkdir()
        plugin._run_scan()
        path = store[SCAN_KEY]["roots"][0]["units"][0]["path"]

        resp = plugin.api_mark(apikey="wrong", path=path)
        assert resp.success is False                      # 密钥错
        from app.runtime.config import settings
        monkeypatch.setattr(settings, "API_TOKEN", "t", raising=False)
        resp = plugin.api_mark(apikey="t", path=path)
        assert resp.success is True and path in store[PENDING_KEY]
        resp = plugin.api_mark(apikey="t", path=path)
        assert resp.success is True and path not in store[PENDING_KEY]   # 再点取消

    def test_mark_rejects_path_not_in_scan(self, tmp_path, monkeypatch):
        plugin, store = make_scanned_plugin(tmp_path, monkeypatch)
        (tmp_path / "orphan").mkdir()
        plugin._run_scan()
        from app.runtime.config import settings
        monkeypatch.setattr(settings, "API_TOKEN", "t", raising=False)
        resp = plugin.api_mark(apikey="t", path="/not/in/scan")
        assert resp.success is False


class TestDeleteGuards:
    def _prepared(self, tmp_path, monkeypatch, torrents=None):
        """构造已扫描且已标记一个孤儿单元的插件（模拟 api_delete 占锁）。"""
        plugin, store = make_scanned_plugin(tmp_path, monkeypatch, torrents)
        (tmp_path / "orphan").mkdir()
        (tmp_path / "orphan" / "f.mkv").write_bytes(b"x" * 30)
        plugin._run_scan()
        from app.runtime.config import settings
        monkeypatch.setattr(settings, "API_TOKEN", "t", raising=False)
        path = store[SCAN_KEY]["roots"][0]["units"][0]["path"]
        plugin.api_mark(apikey="t", path=path)
        plugin._lock.acquire()   # 模拟 api_delete 占锁，_guarded_delete 的 finally 会释放
        return plugin, store, path

    def test_delete_requires_mark(self, tmp_path, monkeypatch):
        plugin, store = make_scanned_plugin(tmp_path, monkeypatch)
        (tmp_path / "orphan").mkdir()
        plugin._run_scan()
        path = store[SCAN_KEY]["roots"][0]["units"][0]["path"]
        resp = plugin.api_delete(apikey="t", path=path)   # 未标记直接删 → 拒绝
        assert resp.success is False

    def test_delete_outside_scan_dirs_rejected(self, tmp_path, monkeypatch):
        """校验②：路径逃逸（不在扫描根下）拒绝。"""
        plugin, store, path = self._prepared(tmp_path, monkeypatch)
        plugin._scan_dirs = [str(tmp_path / "other")]     # 改掉扫描根
        plugin._guarded_delete(path)
        assert store[STATUS_KEY]["status"] == "error"
        assert "越界" in store[STATUS_KEY]["message"]

    def test_delete_now_protected_rejected(self, tmp_path, monkeypatch):
        """校验③：删除时该路径已有新种子占用 → 跳过。"""
        torrents = []                                      # 扫描时无种子
        plugin, store, path = self._prepared(tmp_path, monkeypatch, torrents)
        # 删除前冒出新种子占住该路径
        plugin._get_transmission = lambda: FakeTr(
            [SimpleNamespace(download_dir=str(tmp_path), name="orphan")])
        plugin._guarded_delete(path)
        assert store[STATUS_KEY]["status"] == "error"
        assert "占用" in store[STATUS_KEY]["message"]

    def test_delete_success(self, tmp_path, monkeypatch):
        """成功删除：文件消失、结果移除、标记清除、日志留痕。"""
        import os
        plugin, store, path = self._prepared(tmp_path, monkeypatch)
        plugin._guarded_delete(path)
        assert not os.path.exists(path)                    # 文件真删了
        assert store[STATUS_KEY]["status"] == "done"
        units = store[SCAN_KEY]["roots"][0]["units"]
        assert all(u["path"] != path for u in units)       # 结果中移除
        assert path not in (store.get(PENDING_KEY) or [])  # 标记清除
        assert store[LOG_KEY][0]["path"] == path           # 日志留痕
        assert store[LOG_KEY][0]["size"] == 30

    def test_delete_rpc_error_aborts(self, tmp_path, monkeypatch):
        """Tr 不可用时整体中止，不删。"""
        import os
        plugin, store, path = self._prepared(tmp_path, monkeypatch)
        plugin._get_transmission = lambda: FakeTr(error=True)
        plugin._guarded_delete(path)
        assert os.path.exists(path)
        assert store[STATUS_KEY]["status"] == "error"

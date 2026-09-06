"""详情页拼装结构测试。"""
from app.plugins.unseededcleaner import SCAN_KEY, STATUS_KEY, PENDING_KEY
from tests.v3.unseededcleaner.test_scan import make_scanned_plugin


def find_components(tree, name, acc=None):
    """递归收集指定组件名节点。"""
    acc = [] if acc is None else acc
    if isinstance(tree, dict):
        if tree.get("component") == name:
            acc.append(tree)
        for c in tree.get("content", []) or []:
            find_components(c, name, acc)
    elif isinstance(tree, list):
        for c in tree:
            find_components(c, name, acc)
    return acc


def find_buttons(tree, acc=None):
    """递归收集所有带 events.click 的节点。"""
    acc = [] if acc is None else acc
    if isinstance(tree, dict):
        if "click" in (tree.get("events") or {}):
            acc.append(tree)
        for c in tree.get("content", []) or []:
            find_buttons(c, acc)
    elif isinstance(tree, list):
        for c in tree:
            find_buttons(c, acc)
    return acc


class TestGetPage:
    def test_empty_state(self, tmp_path, monkeypatch):
        plugin, _ = make_scanned_plugin(tmp_path, monkeypatch)
        page = plugin.get_page()
        assert page[0]["component"] == "VCard"      # 状态卡片始终存在

    def test_scan_result_rendered(self, tmp_path, monkeypatch):
        plugin, store = make_scanned_plugin(tmp_path, monkeypatch)
        (tmp_path / "orphan-pack").mkdir()
        plugin._run_scan()
        page = plugin.get_page()
        text = str(page)
        assert "orphan-pack" in text
        # 分组卡片：每组一张 VCard，含根路径
        assert str(tmp_path) in text

    def test_buttons_hit_registered_apis(self, tmp_path, monkeypatch):
        plugin, _ = make_scanned_plugin(tmp_path, monkeypatch)
        (tmp_path / "orphan-pack").mkdir()
        plugin._run_scan()
        page = plugin.get_page()
        apis = {b["events"]["click"]["api"] for b in find_buttons(page)}
        assert "plugin/UnseededCleaner/scan" in apis
        assert "plugin/UnseededCleaner/refresh" in apis
        assert "plugin/UnseededCleaner/mark" in apis

    def test_marked_unit_shows_confirm_delete(self, tmp_path, monkeypatch):
        plugin, store = make_scanned_plugin(tmp_path, monkeypatch)
        (tmp_path / "orphan-pack").mkdir()
        plugin._run_scan()
        from app.runtime.config import settings
        monkeypatch.setattr(settings, "API_TOKEN", "t", raising=False)
        path = store[SCAN_KEY]["roots"][0]["units"][0]["path"]
        plugin.api_mark(apikey="t", path=path)
        page = plugin.get_page()
        apis = {b["events"]["click"]["api"] for b in find_buttons(page)}
        assert "plugin/UnseededCleaner/delete" in apis      # 确认删除按钮出现
        text = str(page)
        assert "确认删除" in text and "取消标记" in text

    def test_marked_unit_pinned_to_pending_card(self, tmp_path, monkeypatch):
        """已标记单元集中置顶「待确认删除」卡片，并从分组清单移除（免滚动寻找）。"""
        plugin, store = make_scanned_plugin(tmp_path, monkeypatch)
        (tmp_path / "orphan-pack").mkdir()
        plugin._run_scan()
        from app.runtime.config import settings
        monkeypatch.setattr(settings, "API_TOKEN", "t", raising=False)
        path = store[SCAN_KEY]["roots"][0]["units"][0]["path"]
        plugin.api_mark(apikey="t", path=path)
        page = plugin.get_page()
        text = str(page)
        assert "待确认删除" in text                       # 置顶卡出现
        assert "1 项" in text                             # 置顶卡含 1 项
        assert "0 项" in text                             # 分组清单已无该项

    def test_status_card_progress(self, tmp_path, monkeypatch):
        plugin, _ = make_scanned_plugin(tmp_path, monkeypatch)
        plugin.save_data(STATUS_KEY, {
            "action": "scan", "status": "running",
            "progress": "正在统计大小 3/10…", "message": "", "time": "x"})
        page = plugin.get_page()
        assert "3/10" in str(page)

    def test_delete_log_panel(self, tmp_path, monkeypatch):
        from app.plugins.unseededcleaner import LOG_KEY
        plugin, _ = make_scanned_plugin(tmp_path, monkeypatch)
        plugin.save_data(LOG_KEY, [{"path": "/x/a", "size": 100, "time": "2026-09-06"}])
        page = plugin.get_page()
        assert "/x/a" in str(page)

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

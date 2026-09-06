"""核心纯函数测试：路径规范化、保护集合、双向前缀匹配、大小统计。"""
import os

from types import SimpleNamespace

from app.plugins.unseededcleaner import (
    build_protected_paths,
    format_size,
    get_path_size,
    is_unit_protected,
    normalize_path,
)


class TestNormalizePath:
    def test_strips_trailing_slash(self):
        assert normalize_path("/media4/9kg/") == "/media4/9kg"

    def test_collapses_dot_segments(self):
        assert normalize_path("/media4/9kg/../9kg/a") == "/media4/9kg/a"


class TestBuildProtectedPaths:
    def test_joins_download_dir_and_name(self):
        torrents = [SimpleNamespace(download_dir="/downloads", name="种子A")]
        assert build_protected_paths(torrents) == {"/downloads/种子A"}

    def test_skips_torrent_with_missing_fields(self):
        torrents = [SimpleNamespace(download_dir=None, name="x"),
                    SimpleNamespace(download_dir="/d", name=None)]
        assert build_protected_paths(torrents) == set()

    def test_empty_list(self):
        assert build_protected_paths([]) == set()


class TestIsUnitProtected:
    def test_exact_match(self):
        assert is_unit_protected("/d/a", {"/d/a"})

    def test_seed_inside_unit(self):
        # 种子路径在单元内部 → 保护
        assert is_unit_protected("/d/a", {"/d/a/sub-torrent"})

    def test_unit_inside_seed(self):
        # 单元在种子路径内部 → 保护
        assert is_unit_protected("/d/a/part", {"/d/a"})

    def test_unrelated(self):
        assert not is_unit_protected("/d/b", {"/d/a"})

    def test_prefix_without_separator_not_matched(self):
        # /d/abc 不因 /d/a 前缀而误保护（分隔符边界）
        assert not is_unit_protected("/d/abc", {"/d/a"})

    def test_matches_second_element_in_set(self):
        # 命中集合中第二个元素（防实现只查第一个）
        assert is_unit_protected("/d/b", {"/x/other", "/d/b"})

    def test_no_match_in_multi_element_set(self):
        assert not is_unit_protected("/d/c", {"/d/a", "/d/b", "/e/f"})


class TestFormatSize:
    def test_empty_values_return_empty_string(self):
        assert format_size(None) == ""
        assert format_size("") == ""

    def test_formats_bytes(self):
        result = format_size(1024 * 1024 * 1024)
        assert "G" in result and "1" in result


class TestGetPathSize:
    def test_file(self, tmp_path):
        f = tmp_path / "movie.mkv"
        f.write_bytes(b"x" * 1024)
        assert get_path_size(str(f)) == 1024

    def test_directory_tree(self, tmp_path):
        d = tmp_path / "pack"
        d.mkdir()
        (d / "a.mkv").write_bytes(b"x" * 100)
        sub = d / "sub"
        sub.mkdir()
        (sub / "b.mkv").write_bytes(b"x" * 50)
        assert get_path_size(str(d)) == 150

    def test_missing_path_returns_zero(self, tmp_path):
        assert get_path_size(str(tmp_path / "no-such")) == 0

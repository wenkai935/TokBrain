"""媒体路径约定（NAS ⇄ Windows）的单测。

这套逻辑是「大肥鱼在 NAS 上下载、Windows 上导入」能不能成立的唯一支点，
所以每个分支都单独钉住。
"""

from __future__ import annotations

import pytest

from tokbrain_control import audit, commands, paths, upload


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(paths.ENV_ROOTS, raising=False)


def _set(monkeypatch, value: str) -> None:
    monkeypatch.setenv(paths.ENV_ROOTS, value)


def test_parse_specs_exact(monkeypatch):
    _set(monkeypatch, r"Z:\videos|/vol1/1000/media/videos ; D:\本地视频\ ")
    specs = paths.specs()
    assert specs[0] == ("Z:\\videos", "/vol1/1000/media/videos")
    assert specs[1] == ("D:\\本地视频", None)  # 尾部反斜杠被去掉
    assert len(specs) == 2


def test_unconfigured_keeps_original(monkeypatch):
    assert paths.specs() == []
    assert paths.candidates(r"D:\media\a.mp4") == [r"D:\media\a.mp4"]
    # 未配置时相对路径没有 root 可拼，只剩原样
    assert paths.candidates("a.mp4") == ["a.mp4"]


def test_nas_path_translates(monkeypatch, tmp_path):
    media = tmp_path / "videos"
    media.mkdir()
    (media / "a.mp4").write_bytes(b"x")
    _set(monkeypatch, f"{media}|/vol1/1000/media/videos")

    info = upload.probe_path("/vol1/1000/media/videos/a.mp4")
    assert info["exists"] is True
    assert info["name"] == "a.mp4"
    assert info["translated_from"] == "/vol1/1000/media/videos/a.mp4"


def test_bare_filename_resolves_under_root(monkeypatch, tmp_path):
    media = tmp_path / "videos"
    media.mkdir()
    (media / "b.mkv").write_bytes(b"x")
    _set(monkeypatch, f"{media}|/vol1/1000/media/videos")

    info = upload.probe_path("b.mkv")
    assert info["exists"] is True
    assert info["translated_from"] == "b.mkv"


def test_windows_absolute_wins_without_translation(monkeypatch, tmp_path):
    media = tmp_path / "videos"
    media.mkdir()
    target = media / "c.mp4"
    target.write_bytes(b"x")
    _set(monkeypatch, f"{tmp_path}|/vol1/1000/media/videos")

    info = upload.probe_path(str(target))
    assert info["exists"] is True
    assert "translated_from" not in info


def test_dir_traversal_rejected(monkeypatch, tmp_path):
    _set(monkeypatch, f"{tmp_path}|/vol1/1000/media/videos")
    assert paths.candidates("../../Windows/win.ini") == []
    info = upload.probe_path("../../Windows/win.ini")
    assert info["exists"] is False


def test_missing_path_lists_roots(monkeypatch, tmp_path):
    media = tmp_path / "videos"
    media.mkdir()
    _set(monkeypatch, f"{media}|/vol1/1000/media/videos")

    info = upload.probe_path("/vol1/1000/media/videos/无此文件.mp4")
    assert info["exists"] is False
    assert info["attempts"]
    assert "tb_fs_roots" in info["hint"]


def test_describe_unconfigured_has_hint():
    data = paths.describe()
    assert data["configured"] is False
    assert data["usable_roots"] == 0
    assert paths.ENV_ROOTS in data["hint"]


def test_describe_lists_samples(monkeypatch, tmp_path):
    media = tmp_path / "videos"
    media.mkdir()
    (media / "z.mp4").write_bytes(b"x")
    (media / "notes.txt").write_bytes(b"x")
    _set(monkeypatch, f"{media}|/vol1/1000/media/videos")

    data = paths.describe()
    assert data["configured"] is True
    assert data["usable_roots"] == 1
    root = data["roots"][0]
    assert root["nas_prefix"] == "/vol1/1000/media/videos"
    names = [item["name"] for item in root["sample_files"]]
    assert "z.mp4" in names
    assert "notes.txt" not in names  # 非媒体后缀不进样例


def test_describe_flags_offline_root(monkeypatch, tmp_path):
    missing = tmp_path / "not-mounted"
    _set(monkeypatch, f"{missing}|/vol1/1000/media/videos")

    data = paths.describe()
    assert data["usable_roots"] == 0
    assert "映射盘" in data["hint"] or "不可访问" in data["hint"]


# --------------------------------------------------------------- 工具层集成


@pytest.fixture
def isolated_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "DEFAULT_LOG_DIR", tmp_path / "logs", raising=False)
    return tmp_path


def test_fs_roots_envelope(isolated_audit, monkeypatch, tmp_path):
    media = tmp_path / "videos"
    media.mkdir()
    _set(monkeypatch, f"{media}|/vol1/1000/media/videos")

    env = commands.fs_roots()
    assert env["ok"] is True
    assert env["tool"] == "tb_fs_roots"
    assert env["data"]["usable_roots"] == 1
    assert env["data"]["roots"][0]["nas_prefix"] == "/vol1/1000/media/videos"


def test_import_files_hint_points_to_roots_when_configured(isolated_audit, monkeypatch):
    # 这条同时钉住一个踩过的坑：commands.import_files 的参数也叫 paths，
    # 曾把模块 paths 遮蔽掉，导致所有路径都被误报成「控制面内部异常」。
    _set(monkeypatch, r"D:\definitely-missing|/vol1/1000/media/videos")

    env = commands.import_files(paths=["/vol1/1000/media/videos/a.mp4"])
    assert env["ok"] is False
    assert env["error"]["code"] == "path_not_visible"
    assert "tb_fs_roots" in env["error"]["hint"]
    # 翻译尝试要留下痕迹，方便判断是「没映射」还是「前缀写错」
    attempts = env["error"]["detail"]["skipped"][0]["info"]["attempts"]
    assert attempts


def test_import_files_hint_when_unconfigured(isolated_audit, monkeypatch):
    env = commands.import_files(paths=["a.mp4"])
    assert env["ok"] is False
    assert env["error"]["code"] == "path_not_visible"
    assert "control.env" in env["error"]["hint"]

"""Shared pytest bootstrap.

Production entry points patch ``PATH`` at boot so ``shutil.which("ffmpeg")``
resolves even when ffmpeg is installed outside ``PATH``
(see ``app.main._ensure_media_runtime_on_path``). Pytest is usually started
straight from a shell whose ``PATH`` never went through that patch, so tests
that guard on ``shutil.which("ffmpeg")`` — e.g. the real-ffmpeg smoke tests in
``test_local_external_imports.py`` — used to skip even on machines where ffmpeg
is installed and working.

Mirror the production behaviour once at collection time. This runs before any
test module is imported, which matters because ``pytest.mark.skipif`` is
evaluated at decoration (import) time.

The probe is intentionally best-effort: if importing the app fails (partial
environment, missing optional deps) we leave ``PATH`` untouched and let the
existing skip guards do their job.
"""

from __future__ import annotations


def _patch_media_runtime_path() -> None:
    try:
        from app.main import _ensure_media_runtime_on_path
    except Exception:  # pragma: no cover - app not importable in this env
        return
    try:
        _ensure_media_runtime_on_path()
    except Exception:  # pragma: no cover - never block collection
        return


_patch_media_runtime_path()


def _isolate_cookie_directory() -> None:
    """测试绝不读/写真实的 ``data/cookies``——那里是用户登录态。

    指向一次性临时目录后，任何一层的 Cookie 读取都不会拿到开发者本机的 cookie，
    也让"文件优先、旧 Secret 兜底"这类分支在测试里保持确定。
    """

    import os
    import tempfile

    if os.environ.get("TOKBRAIN_COOKIE_DIR"):
        return
    os.environ["TOKBRAIN_COOKIE_DIR"] = tempfile.mkdtemp(prefix="tokbrain-test-cookies-")


_isolate_cookie_directory()

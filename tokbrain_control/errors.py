"""错误模型与退出码。

设计要点：

* 每个失败都带一个稳定的 ``code``（给程序判断）与一句人话 ``message``；
* ``kind`` 决定退出码与是否可重试，是 CLI/MCP 唯一的分类依据；
* ``hint`` 面向模型/用户，写清"下一步该调哪个工具"，这样 Hermes 不用猜。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------- 退出码

EXIT_OK = 0
EXIT_FAIL = 1  # 业务失败：后端在线但拒绝了，或任务终态非成功
EXIT_USAGE = 2  # 参数错（与 argparse 的默认退出码对齐）
EXIT_OFFLINE = 3  # 连不上后端

_KIND_TO_EXIT = {
    "usage": EXIT_USAGE,
    "offline": EXIT_OFFLINE,
    "business": EXIT_FAIL,
    "server": EXIT_FAIL,
    "local": EXIT_FAIL,
}


# ---------------------------------------------------------------- 错误码常量
#
# 命名规则：``<domain>_<problem>``。后端原样透出的错误码优先，不要二次编造。

# 传输层
CODE_BACKEND_OFFLINE = "backend_offline"
CODE_BACKEND_TIMEOUT = "backend_timeout"
CODE_HOST_REJECTED = "host_rejected"
CODE_BACKEND_CONTRACT_MISMATCH = "backend_contract_mismatch"

# 鉴权 / 权限
CODE_REJECTED = "rejected"
CODE_DESTRUCTIVE_DISABLED = "destructive_disabled"

# 通用资源
CODE_NOT_FOUND = "not_found"
CODE_JOB_NOT_FOUND = "job_not_found"
CODE_BATCH_NOT_FOUND = "batch_not_found"
CODE_CONFLICT = "conflict"

# 入参校验（本地）
CODE_INVALID_ARGUMENT = "invalid_argument"
CODE_CONFIRM_REQUIRED = "confirm_required"
CODE_TITLE_MISMATCH = "title_mismatch"
CODE_PATH_NOT_VISIBLE = "path_not_visible"
CODE_IS_DIRECTORY = "is_directory"
CODE_NO_READ_PERMISSION = "no_read_permission"
CODE_FILE_TOO_LARGE = "file_too_large"
CODE_TOO_MANY_FILES = "too_many_files"

# 检索
CODE_SEARCH_UNAVAILABLE = "search_unavailable"

# 服务生命周期
CODE_ALREADY_RUNNING = "already_running"
CODE_NOT_RUNNING = "not_running"
CODE_START_FAILED = "start_failed"
CODE_STOP_INCOMPLETE = "stop_incomplete"
CODE_STOP_REFUSED = "stop_refused_identity_mismatch"
CODE_PORT_CONFLICT_UNTRACKED = "port_conflict_untracked"
CODE_RUNTIME_MISSING_BUT_PORT_BUSY = "runtime_missing_but_port_busy"
CODE_SERVICE_BUSY = "service_busy"
CODE_POWERSHELL_MISSING = "powershell_missing"

# 并发/重试提示
CODE_ALREADY_QUEUED = "already_queued"


class ControlError(Exception):
    """控制面统一的失败表示。

    ``kind`` 的语义：

    ``usage``     调用方参数不对，改参数再来
    ``offline``   连不上后端（PC 关机 / 服务没起 / 防火墙）
    ``business``  后端在线但拒绝了（422/409/404...）
    ``server``    后端 5xx
    ``local``     本地动作失败（文件不可见、启动超时、powershell 缺失）
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        kind: str = "business",
        retryable: bool = False,
        http_status: int | None = None,
        hint: str | None = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.kind = kind
        self.retryable = retryable
        self.http_status = http_status
        self.hint = hint
        self.detail = detail

    # -- 派生 ---------------------------------------------------------

    @property
    def exit_code(self) -> int:
        return _KIND_TO_EXIT.get(self.kind, EXIT_FAIL)

    @property
    def offline(self) -> bool:
        return self.kind == "offline"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "http_status": self.http_status,
            "hint": self.hint,
            "detail": self.detail,
        }

    def __str__(self) -> str:  # pragma: no cover - 只是便于调试
        tail = f"（{self.code}）" if self.code else ""
        return f"{self.message}{tail}"


# ---------------------------------------------------------------- 便捷构造


def offline_error(exc: BaseException | None = None, *, base: str = "") -> ControlError:
    target = f" {base}" if base else ""
    return ControlError(
        CODE_BACKEND_OFFLINE,
        f"连不上 TokBrain 后端{target}",
        kind="offline",
        retryable=True,
        hint="PC 可能休眠/关机，或服务未启动。可用 tb_service_status 查看，"
        "必要时 tb_service_start；Hermes 不要用离线快照冒充操作结果。",
        detail=repr(exc) if exc else None,
    )


def timeout_error(exc: BaseException | None = None, *, path: str = "") -> ControlError:
    return ControlError(
        CODE_BACKEND_TIMEOUT,
        f"后端响应超时{f'（{path}）' if path else ''}",
        kind="offline",
        retryable=True,
        hint="单 worker 串行处理中，长任务请改用 tb_job_wait 分段轮询，不要重复提交。",
        detail=repr(exc) if exc else None,
    )


def usage_error(code: str, message: str, *, hint: str | None = None) -> ControlError:
    return ControlError(code, message, kind="usage", hint=hint)

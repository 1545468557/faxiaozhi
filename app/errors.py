"""统一错误体与错误码（PRD 7、底线 3：不泄露堆栈）。"""

from __future__ import annotations

from typing import Any

HTTP_STATUS: dict[str, int] = {
    "session_not_found": 404,
    "run_not_found": 404,
    "not_found": 404,
    "empty_input": 422,
    "empty_sample": 422,
    "unsupported_file": 422,
    "file_too_large": 422,
    "type_mismatch": 422,
    "schema_invalid": 422,
    "checkpoint_mismatch": 409,
    "sample_not_locked": 409,
    "export_blocked": 409,
    "permission_denied": 403,
    "path_escape": 403,
    "model_unavailable": 502,
    "mcp_unavailable": 502,
    "concurrency_queued": 429,
    "workflow_step_limit": 500,
    "internal_error": 500,
    "invalid_request": 400,
    "run_expired": 409,
    "auth_required": 401,
    "auth_failed": 401,
    "user_disabled": 403,
    "forbidden": 403,
    "rate_limited": 429,
    "invite_invalid": 422,
    "username_taken": 409,
    "invalid_username": 422,
    "password_weak": 422,
    "no_failed_step": 409,
    "session_busy": 409,
    "retry_exhausted": 429,
    "invalid_source": 422,
    # ---- 2-4：材料主路径 ----
    "supplement_exhausted": 429,
    "not_conflict": 409,
    "too_many_files": 422,
    "empty_material": 422,
    "material_not_recoverable": 409,
    # ---- 2-5：法律咨询 ----
    "consult_not_recoverable": 409,
    "rounds_exhausted": 409,
    # ---- 2-6：合同审查 ----
    "contract_parse_failed": 422,
    "contract_not_recoverable": 409,
}

# 用户可见文案（PRD 第七部分降级矩阵）
MESSAGES: dict[str, str] = {
    "model_unavailable": "模型服务暂时不可用，已重试多次。以上内容未产出，请稍后再试。",
    "mcp_unavailable": (
        "检索接口调用失败，本次未获得可核验依据。"
        "这不等于「无相关案例」。可点击重试。"
    ),
    "sample_not_locked": "样本尚未确认，无法生成对比矩阵。",
    "export_blocked": "存在未通过核验的引用 / 已排除样本残留 / 越界表述，无法导出。",
    "empty_input": "请先输入要研究的议题。",
    "concurrency_queued": "当前同时在跑的研究已达上限（4 个），请稍等片刻再点一次。",
    "empty_sample": "请至少确认 1 篇样本（生成综合结论需要至少 2 篇）。",
    "unsupported_file": "不支持的文件类型。请上传 .docx / .pdf / .txt / .md。",
    "file_too_large": "文件超出大小限制（20MB）。",
    "type_mismatch": "文件真实类型与扩展名不符，已拒绝。",
    "path_escape": "禁止写入导出目录之外的路径。",
    "workflow_step_limit": "任务超出最大步数，已中止。已完成部分已保存。",
    "internal_error": "服务内部错误，请稍后重试。",
    "no_failed_step": "当前没有失败的步骤，无需重试。",
    "session_busy": "当前任务仍在运行中，请稍候再试。",
    "retry_exhausted": "重试次数已达上限，请重新发起研究。",
    "invalid_source": "source 只能是 real / test / unknown。",
    # ---- 2-4 ----
    "supplement_exhausted": "本次研究的补充检索已达上限，请改用已经取得的材料与来源。",
    "not_conflict": "该来源不是冲突来源，无需人工裁决。",
    "too_many_files": "单次上传文件数超出上限，请分批上传。",
    "empty_material": "未选择要上传的材料文件。",
    "material_not_recoverable": (
        "本次研究使用了你上传的材料；材料原文不落盘，服务重启后无法继续。请重新上传材料后重新发起研究。"
    ),
    # ---- 2-5 ----
    "consult_not_recoverable": (
        "咨询问题里可能含当事人具体事实；按 C-12 原文不落盘，服务重启后无法继续。请重新提问。"
    ),
    "rounds_exhausted": "追问轮数已达上限，已按现有信息与假设作答。",
    # ---- 2-6 ----
    "contract_parse_failed": (
        "这份合同没有可解析的正文。请提供可复制文字的版本（docx / pdf / txt / md）；扫描件暂不支持。"
    ),
    "contract_not_recoverable": (
        "合同原文按 C-12 仅当前会话临时处理、不落盘，服务重启后无法继续。请重新上传合同后重新发起审查。"
    ),
}


class ApiError(Exception):
    """业务错误。message 之外不携带任何内部细节（不泄露堆栈）。"""

    def __init__(self, code: str, message: str | None = None, **extra: Any) -> None:
        self.code = code
        self.message = message or MESSAGES.get(code, code)
        self.extra = extra
        self.http_status = HTTP_STATUS.get(code, 500)
        super().__init__(self.message)

    def body(self) -> dict:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.extra:
            err["detail"] = self.extra
        return {"error": err}

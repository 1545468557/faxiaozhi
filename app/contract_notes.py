"""Validate human edits independently of model risks and citation verification."""
from .errors import ApiError


def validate_review_notes(value, risks):
    if not isinstance(value, dict) or len(value) > 200:
        raise ApiError("invalid_request", "审查处理记录格式不正确。")
    allowed = {str(r.get("riskId")) for r in risks}
    out = {}
    for key, note in value.items():
        if key not in allowed or not isinstance(note, dict):
            raise ApiError("invalid_request", "处理记录不属于当前审查结果，请刷新后重试。")
        status = note.get("status")
        suggestion = note.get("suggestion")
        if status not in ("accept", "skip", "pending") or not isinstance(suggestion, str) or len(suggestion) > 10000:
            raise ApiError("invalid_request", "处理状态或修改意见无效。")
        if status == "accept" and not suggestion.strip():
            raise ApiError("invalid_request", "采纳前请填写修改意见。")
        out[key] = {"status": status, "suggestion": suggestion.strip()}
    return out

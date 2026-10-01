"""短信通道（可插拔）。

为什么默认是「模拟发码」而不是直接调服务商
------------------------------------------------
国内短信必须过服务商（阿里云 / 腾讯云 …），且**签名与模板需要主体报备**，
按条计费。本地部署阶段没有密钥、也不该拿真实号段试发 —— 所以：

- `SMS_PROVIDER=mock`（默认）：**不发送**，把验证码写进服务端日志与
  `data/sms-outbox.jsonl`，本地开发能完整跑通「发码 → 校验」全流程。
- `SMS_PROVIDER=aliyun|tencent`：**已留好接缝但未实现**，未配置时抛出明确的
  `SmsNotConfigured`。故意不写「看起来能用但没验证过」的签名代码 ——
  签名算错会表现为「短信没到」，而这种静默失败最难查。

要接真实短信：在 `_send_aliyun` / `_send_tencent` 里补上请求构造，
需要哪些密钥见下面的错误文案。**换通道不需要改业务代码。**
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SmsNotConfigured(RuntimeError):
    """通道没配好 —— 说清楚缺什么，不要让它变成「短信没到」这种静默失败。"""


def _provider() -> str:
    return (os.environ.get("SMS_PROVIDER", "mock") or "mock").strip().lower()


def provider_name() -> str:
    """给接口/界面用的通道标识（界面据此提示「当前为模拟发码」）。"""
    return _provider()


def is_mock() -> bool:
    return _provider() == "mock"


def _outbox_path() -> Path:
    configured = os.environ.get("SMS_OUTBOX", "").strip()
    path = Path(configured).expanduser() if configured else ROOT / "data" / "sms-outbox.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _log_and_store(phone: str, code: str, purpose: str) -> None:
    """模拟发码：日志 + 追加式 outbox 文件（便于 `tail` 查看，不用翻大日志）。"""
    print(f"[sms:mock] purpose={purpose} phone={phone} code={code}", flush=True)
    record = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "purpose": purpose,
        "phone": phone,
        "code": code,
    }
    with _outbox_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


_ALIYUN_KEYS = (
    "SMS_ALIYUN_ACCESS_KEY_ID",
    "SMS_ALIYUN_ACCESS_KEY_SECRET",
    "SMS_SIGN_NAME",
    "SMS_TEMPLATE_CODE",
)

_TENCENT_KEYS = (
    "SMS_TENCENT_SECRET_ID",
    "SMS_TENCENT_SECRET_KEY",
    "SMS_TENCENT_SDK_APP_ID",
    "SMS_SIGN_NAME",
    "SMS_TEMPLATE_ID",
)


def _missing(keys: tuple[str, ...]) -> list[str]:
    return [key for key in keys if not os.environ.get(key, "").strip()]


def _send_aliyun(phone: str, code: str, purpose: str) -> None:
    """阿里云短信 —— 接缝。

    需要的环境变量：SMS_ALIYUN_ACCESS_KEY_ID / SMS_ALIYUN_ACCESS_KEY_SECRET /
    SMS_SIGN_NAME（已报备的签名） / SMS_TEMPLATE_CODE（已报备的模板 CODE）；
    模板里验证码占位符名默认 "code"，可用 SMS_TEMPLATE_PARAM 改。
    """
    missing = _missing(_ALIYUN_KEYS)
    if missing:
        raise SmsNotConfigured("阿里云短信未配置，缺少：" + "、".join(missing))
    raise SmsNotConfigured(
        "阿里云短信通道尚未实现（只留了接缝）。补 `_send_aliyun` 的请求构造即可，业务代码不用动；"
        "签名算法请以官方文档为准 —— 这里刻意不写未经验证的签名实现。"
    )


def _send_tencent(phone: str, code: str, purpose: str) -> None:
    """腾讯云短信 —— 接缝。

    需要的环境变量：SMS_TENCENT_SECRET_ID / SMS_TENCENT_SECRET_KEY /
    SMS_TENCENT_SDK_APP_ID / SMS_SIGN_NAME / SMS_TEMPLATE_ID。
    """
    missing = _missing(_TENCENT_KEYS)
    if missing:
        raise SmsNotConfigured("腾讯云短信未配置，缺少：" + "、".join(missing))
    raise SmsNotConfigured(
        "腾讯云短信通道尚未实现（只留了接缝）。补 `_send_tencent` 的请求构造即可，业务代码不用动；"
        "TC3-HMAC-SHA256 签名请以官方文档为准 —— 这里刻意不写未经验证的签名实现。"
    )


_CHANNELS = {"mock": _log_and_store, "aliyun": _send_aliyun, "tencent": _send_tencent}


def send_code(phone: str, code: str, purpose: str) -> str:
    """发一条验证码，返回实际使用的通道名。

    未知通道名直接报错，不静默回退到 mock —— 否则「配了真实通道却没生效」会被
    当成「短信延迟」，白查半天。
    """
    name = _provider()
    channel = _CHANNELS.get(name)
    if channel is None:
        raise SmsNotConfigured(f"未知的 SMS_PROVIDER={name!r}，可选：{', '.join(sorted(_CHANNELS))}")
    channel(phone, code, purpose)
    return name

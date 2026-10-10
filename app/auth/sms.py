"""短信通道（可插拔）。

为什么默认是「模拟发码」而不是直接调服务商
------------------------------------------------
国内短信必须过服务商（阿里云 / 腾讯云 …），且**签名与模板需要主体报备**，
按条计费。本地部署阶段没有密钥、也不该拿真实号段试发 —— 所以：

- `SMS_PROVIDER=mock`（默认）：**不发送**，把验证码写进服务端日志与
  `data/sms-outbox.jsonl`，本地开发能完整跑通「发码 → 校验」全流程。
- `SMS_PROVIDER=aliyun`：**已实现**（2026-10-10）—— 阿里云短信 Dysmsapi 2017-05-25
  `SendSms`（RPC 风格 + HMAC-SHA1 签名）。需要 RAM AccessKey 与**已报备**的签名名称、模板 CODE。
  验证码由**我们**生成、我们存哈希、我们比对；签名可以是自己的品牌名。
- `SMS_PROVIDER=pnvs`：**已实现**（2026-10-10）—— 阿里云号码认证服务 Dypnsapi 2017-05-25
  `SendSmsVerifyCode` / `CheckSmsVerifyCode`。**免资质、免签名、免模板**，个人实名账号即可用，
  是个人账号唯一能真发短信的通道；代价见下面「模型是反的」那一段。
- `SMS_PROVIDER=tencent`：**仍只留接缝**，未配置时抛 `SmsNotConfigured`。

**两条真实通道的模型是反的 —— 这是本文件最要紧的一件事**：

    |                | 谁生成码 | 谁保管码     | 谁比对 |
    | mock / aliyun  | 我们     | 我们（哈希） | 我们   |
    | pnvs           | 阿里云   | 阿里云       | 阿里云 |

所以 `pnvs` 下 `send_code()` 的 `code` 参数**刻意不用**（阿里云按 `CodeType` 自己生成），
校验要走 `verify_code()` 去问阿里云。`remote_verify()` 是这个区别的开关，`core.py` 据此分流；
**调用方不要自己去判断通道名**，否则以后加通道一定会漏改某处。

`pnvs` 的硬约束：署名**只能是阿里云赠送的签名，不支持自定义**（控制台明确「赠送签名必须
搭配赠送模板使用」）。也就是说短信里的发件人**不是**自己的品牌名。要有自己的署名，只能
走企业主体 + `aliyun` 通道 —— 两条路随时可切，业务代码一行不用动。

两个自检开关（都是「宁可大声失败，不要静默失败」）：
- `SMS_DRY_RUN=1`：**构造请求但不发送**，日志只打印手机号打码、模板、签名长度，
  **不打验证码**（不复制 mock 的明文泄漏）。刚拿到密钥时用它确认签名与模板参数对不对，
  既不发真实短信也不花钱。
- 服务商业务错误一律抛 `SmsSendFailed`（带服务商错误码 + 可执行提示：签名没报备、
  模板没报备、余额不足、被限流……），由 server 映射成 502 `sms_unavailable`。

**换通道不需要改业务代码。**
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[2]


class SmsNotConfigured(RuntimeError):
    """通道没配好 —— 说清楚缺什么，不要让它变成「短信没到」这种静默失败。"""


class SmsSendFailed(RuntimeError):
    """服务商明确拒绝了这次发送。

    `code` 是服务商返回的错误码（如 `isv.SMS_SIGNATURE_ILLEGAL`），
    调用方据此给出可执行的提示 —— 不要只说「发送失败」，那等于让人去猜。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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


# ---------------------------------------------------------------- 阿里云短信
# 接口：Dysmsapi 2017-05-25 SendSms（RPC 风格）。签名口径（官方文档）：
#   1. 参数按键名字典序排列，键与值都按 RFC3986 编码，拼成规范化查询串；
#   2. StringToSign = "POST" + "&" + 编码("/") + "&" + 编码(规范化查询串)；
#   3. Signature = Base64(HMAC-SHA1(AccessKeySecret + "&", StringToSign))；
#   4. Signature 也作为一个参数随表单一并提交。

_ALIYUN_ENDPOINT = "https://dysmsapi.aliyuncs.com/"
_ALIYUN_VERSION = "2017-05-25"
_ALIYUN_TIMEOUT = 8.0

#: 服务商错误码 → 可执行提示。命中不了就回退服务商原文，绝不只说「发送失败」。
_ALIYUN_HINTS: dict[str, str] = {
    "SignatureDoesNotMatch": "签名校验失败：AccessKeySecret 不对，或服务器系统时间偏差过大（校准时间后重试）",
    "InvalidAccessKeyId": "AccessKeyId 不存在或已停用，核对短信控制台里的 RAM 用户",
    # 2026-10-10 实测：真实接口对不存在的 AccessKey 回的就是带后缀的这个码
    "InvalidAccessKeyId.NotFound": "AccessKeyId 不存在（Specified access key is not found），核对 SMS_ALIYUN_ACCESS_KEY_ID",
    "InvalidTimeStamp.Expired": "请求时间戳过期：服务器系统时间与标准时间偏差过大（校准时间）",
    "Forbidden.RAM": "这个 AccessKey 没有短信发送权限（在 RAM 里授予 AliyunDysmsFullAccess）",
    "Forbidden": "无权调用（检查 RAM 授权、账号欠费状态）",
    "isv.SMS_SIGNATURE_ILLEGAL": "短信签名未报备、或与模板不匹配（核对 SMS_SIGN_NAME）",
    "isv.SMS_TEMPLATE_ILLEGAL": "短信模板未报备或已删除（核对 SMS_TEMPLATE_CODE）",
    "isv.SMS_CONTENT_ILLEGAL": "短信内容含敏感词（改模板文案后重新报备）",
    "isv.MOBILE_NUMBER_ILLEGAL": "手机号不被服务商接受（核对号码本身与号段）",
    "isv.AMOUNT_NOT_ENOUGH": "短信套餐余额不足，需充值",
    "isv.BUSINESS_LIMIT_CONTROL": "触发服务商限流（同一号码/模板发送过于频繁，等一会儿再试）",
    "isv.DAY_LIMIT_CONTROL": "触发服务商日发送上限",
    "isv.ACCOUNT_NOT_EXISTS": "账号不存在或未开通短信服务",
    # 实名制报备类（2026-10-10 补）。报备没走完时这是**最常见的失败**，
    # 不退化成英文原文 —— 否则线上只看到一句「服务商未给出可识别的原因」，白查半天。
    "PORT_NOT_REGISTERED": (
        "签名对应的发送端口还没完成运营商实名制报备（报备平均 5–7 个工作日、部分需 7–10 个；"
        "在短信服务控制台签名管理页看「运营商报备结果」列）"
    ),
}


def _mask_phone(phone: str) -> str:
    """给日志用的打码手机号。"""
    return f"{phone[:3]}****{phone[-4:]}" if len(phone) == 11 else "***"


def _percent_encode(value: object) -> str:
    """RFC3986 编码：只放行 A-Za-z0-9 与 -_.~，空格编码成 %20（不是 +）。"""
    return quote(str(value), safe="-_.~")


def _aliyun_canonical(params: dict[str, str]) -> str:
    return "&".join(f"{_percent_encode(key)}={_percent_encode(value)}" for key, value in sorted(params.items()))


def _aliyun_string_to_sign(params: dict[str, str], method: str = "POST") -> str:
    return f"{method}&{_percent_encode('/')}&{_percent_encode(_aliyun_canonical(params))}"


def _aliyun_signature(params: dict[str, str], secret: str, method: str = "POST") -> str:
    digest = hmac.new(
        (secret + "&").encode("utf-8"),
        _aliyun_string_to_sign(params, method).encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _rpc_post(endpoint: str, payload: dict[str, str], secret: str) -> dict:
    """签好名、POST 出去、解析出 JSON —— dysmsapi 与 dypnsapi 共用这条。

    只负责「把请求送出去并拿到结构化响应」；**响应里的业务码由调用方解释** ——
    两个接口对「OK」的定义不一样（`SendSms` 的 OK 是发送成功，而
    `CheckSmsVerifyCode` 的 OK **只是接口调通、不代表验证码正确**），
    混在一起处理必出事故。
    """
    signed = dict(payload)
    signed["Signature"] = _aliyun_signature(signed, secret)
    try:
        response = httpx.post(endpoint, data=signed, timeout=_ALIYUN_TIMEOUT)
    except httpx.HTTPError as exc:
        raise SmsSendFailed("network", f"连不上阿里云短信服务（{exc.__class__.__name__}），稍后重试") from exc
    try:
        return response.json()
    except ValueError:
        raise SmsSendFailed(
            f"http_{response.status_code}", f"阿里云返回了非 JSON 响应（HTTP {response.status_code}）"
        ) from None


def _template_param_name() -> str:
    """模板里的验证码占位符名，默认 "code"。"""
    return (os.environ.get("SMS_TEMPLATE_PARAM", "code") or "code").strip()


def _aliyun_payload(phone: str, code: str) -> dict[str, str]:
    """构造 SendSms 的完整参数表（**不含 Signature**）。"""
    return {
        "AccessKeyId": os.environ.get("SMS_ALIYUN_ACCESS_KEY_ID", "").strip(),
        "Action": "SendSms",
        "Format": "JSON",
        "PhoneNumbers": phone,
        "RegionId": (os.environ.get("SMS_ALIYUN_REGION_ID", "") or "cn-hangzhou").strip(),
        "SignName": os.environ.get("SMS_SIGN_NAME", "").strip(),
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "TemplateCode": os.environ.get("SMS_TEMPLATE_CODE", "").strip(),
        "TemplateParam": json.dumps({_template_param_name(): code}, ensure_ascii=False),
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": _ALIYUN_VERSION,
    }


def _dry_run() -> bool:
    return os.environ.get("SMS_DRY_RUN", "").strip().lower() in {"1", "true", "yes", "on"}


def _send_aliyun(phone: str, code: str, purpose: str) -> None:
    """阿里云短信发送。缺密钥 → SmsNotConfigured；服务商拒绝 → SmsSendFailed。

    需要的环境变量：SMS_ALIYUN_ACCESS_KEY_ID / SMS_ALIYUN_ACCESS_KEY_SECRET /
    SMS_SIGN_NAME（已报备的签名） / SMS_TEMPLATE_CODE（已报备的模板 CODE）；
    模板里验证码占位符名默认 "code"，可用 SMS_TEMPLATE_PARAM 改。

    注意：这是同步调用（与 `send_code` 的同步签名一致），单次网络往返约 100~300ms。
    发码接口本身有频率限制，这个开销可以接受；真要高并发再改异步。
    """
    missing = _missing(_ALIYUN_KEYS)
    if missing:
        raise SmsNotConfigured("阿里云短信未配置，缺少：" + "、".join(missing))

    payload = _aliyun_payload(phone, code)
    secret = os.environ.get("SMS_ALIYUN_ACCESS_KEY_SECRET", "").strip()

    if _dry_run():
        # 只证明「请求构造得出来」。**日志里不出现验证码**，也不发包。
        signature = _aliyun_signature(payload, secret)
        print(
            "[sms:aliyun] dry_run "
            f"purpose={purpose} phone={_mask_phone(phone)} "
            f"sign_name={payload['SignName']} template={payload['TemplateCode']} "
            f"param_name={_template_param_name()} timestamp={payload['Timestamp']} "
            f"signature_len={len(signature)}",
            flush=True,
        )
        return

    body = _rpc_post(_ALIYUN_ENDPOINT, payload, secret)
    provider_code = str(body.get("Code", ""))
    if provider_code == "OK":
        return
    hint = _ALIYUN_HINTS.get(provider_code) or str(body.get("Message") or "服务商未给出可识别的原因")
    raise SmsSendFailed(
        provider_code or "unknown",
        f"阿里云短信发送失败（{provider_code or '未知错误'}）：{hint}",
    )


# ------------------------------------------------------- 阿里云号码认证服务
# 接口：Dypnsapi 2017-05-25 SendSmsVerifyCode / CheckSmsVerifyCode（同样是 RPC 风格）。
# 这条路**免资质、免签名、免模板**，个人实名账号就能真发验证码 —— 因为验证码由阿里云
# 自己生成与保管，我们不碰，于是「签名报备」这道坎在我们这里不存在。
#
# 三点必须守住（都是踩了就**静默出事**的地方）：
#   1. `ReturnVerifyCode` **绝不能传 true** —— 传了会把验证码回传给我们，
#      于是「我们从头到尾拿不到明文」这个安全前提当场消失。默认 false，显式写死。
#   2. `CodeLength` / `ValidTime` / `Interval` 必须和 core.py 的本地策略一致，
#      否则会出现「我们以为还在有效期内，服务商已经拒了」。Interval(60s) 与
#      ValidTime(300s) 的默认值恰好一致，**但 CodeLength 不同** —— PNVS 默认 4，我们默认 6。
#   3. `CheckSmsVerifyCode` 的 `Code == "OK"` **只代表接口调通，不代表验证码对**；
#      真正的判定是 `Model.VerifyResult == "PASS"`。判错这一条 = 任意验证码都能登录。

_PNVS_ENDPOINT = "https://dypnsapi.aliyuncs.com/"
_PNVS_VERSION = "2017-05-25"

_PNVS_KEYS = (
    "SMS_ALIYUN_ACCESS_KEY_ID",
    "SMS_ALIYUN_ACCESS_KEY_SECRET",
    "SMS_PNVS_SIGN_NAME",
    "SMS_PNVS_TEMPLATE_CODE",
)

#: 号码认证服务的错误码 → 可执行提示。
_PNVS_HINTS: dict[str, str] = {
    "FUNCTION_NOT_OPENED": "号码认证服务的「短信认证」功能还没开通（去号码认证服务控制台开通）",
    "MOBILE_NUMBER_ILLEGAL": "手机号格式不被服务商接受（只支持中国大陆 11 位手机号）",
    "BUSINESS_LIMIT_CONTROL": "这个号码今天发得太多了，触发了服务商天级流控",
    "FREQUENCY_FAIL": "触发服务商频控（同一号码发得太频繁，等一会儿再试）",
    "INVALID_PARAMETERS": (
        "参数不合法：多半是赠送签名与赠送模板不配套，或模板占位符名不是 code"
        "（赠送签名必须搭配赠送模板使用）"
    ),
    "SignatureDoesNotMatch": "签名校验失败：AccessKeySecret 不对，或服务器系统时间偏差过大",
    "InvalidAccessKeyId.NotFound": "AccessKeyId 不存在，核对 SMS_ALIYUN_ACCESS_KEY_ID",
    "InvalidTimeStamp.Expired": "请求时间戳过期（校准服务器系统时间）",
    "Forbidden.RAM": "这个 AccessKey 没有号码认证服务权限（RAM 里授予 AliyunDypnsFullAccess）",
}


def _pnvs_code_length() -> int:
    """与 `core._code_length()` **读同一个环境变量、同默认值**，有测试守着不许漂。"""
    return int(os.environ.get("AUTH_CODE_LENGTH", "6") or 6)


def _pnvs_valid_time() -> int:
    """与 `core._code_ttl()` 对齐。"""
    return int(os.environ.get("AUTH_CODE_TTL_SECONDS", "300") or 300)


def _pnvs_interval() -> int:
    """与 `core._code_min_interval()` 对齐。"""
    return int(os.environ.get("AUTH_CODE_MIN_INTERVAL", "60") or 60)


def _pnvs_base(action: str, phone: str, purpose: str) -> dict[str, str]:
    """两个动作共用的骨架（**不含 Signature**）。"""
    return {
        "AccessKeyId": os.environ.get("SMS_ALIYUN_ACCESS_KEY_ID", "").strip(),
        "Action": action,
        "CountryCode": "86",
        "Format": "JSON",
        "OutId": purpose,
        "PhoneNumber": phone,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": _PNVS_VERSION,
    }


def _pnvs_send_payload(phone: str, purpose: str) -> dict[str, str]:
    """`SendSmsVerifyCode` 的参数表（不含 Signature）。"""
    payload = _pnvs_base("SendSmsVerifyCode", phone, purpose)
    payload.update(
        {
            "SignName": os.environ.get("SMS_PNVS_SIGN_NAME", "").strip(),
            "TemplateCode": os.environ.get("SMS_PNVS_TEMPLATE_CODE", "").strip(),
            # `##code##` 是**占位符**：阿里云按 CodeType 生成真码后再替换进去。
            # 若传具体数字（如 "123456"），官方明确「阿里云接口无法校验」——
            # 等于自己把校验闭环掐断，只剩个发得出去的摆设。
            "TemplateParam": json.dumps({_template_param_name(): "##code##"}, ensure_ascii=False),
            "CodeType": "1",  # 1 = 纯数字
            "CodeLength": str(_pnvs_code_length()),
            "ValidTime": str(_pnvs_valid_time()),
            "Interval": str(_pnvs_interval()),
            # 1 = 覆盖：同号同场景重发时旧码立即失效。
            # 这正是本地通道靠 `UPDATE phone_codes SET consumed_at` 手做的事 ——
            # pnvs 下码在阿里云手里我们改不了，只能靠这个参数，**必须显式传**：
            # 默认值恰好也是 1，但依赖默认值意味着哪天默认变了我们会静默丢掉这层防护。
            "DuplicatePolicy": "1",
            "ReturnVerifyCode": "false",  # 见文件里第 1 条注释，改它等于自毁安全前提
            "AutoRetry": "1",  # 运营商明确失败时允许换签名/换运营商重试，提升到达率
        }
    )
    return payload


def _pnvs_check_payload(phone: str, code: str, purpose: str) -> dict[str, str]:
    """`CheckSmsVerifyCode` 的参数表（不含 Signature）。"""
    payload = _pnvs_base("CheckSmsVerifyCode", phone, purpose)
    payload["VerifyCode"] = code
    return payload


def _pnvs_guard() -> str:
    """缺什么就说清楚缺什么；返回 AccessKeySecret。"""
    missing = _missing(_PNVS_KEYS)
    if missing:
        raise SmsNotConfigured("号码认证服务未配置，缺少：" + "、".join(missing))
    return os.environ.get("SMS_ALIYUN_ACCESS_KEY_SECRET", "").strip()


def _pnvs_call(action: str, payload: dict[str, str]) -> dict:
    """发一次请求并解释业务码。非 OK 一律抛 `SmsSendFailed`（带可执行提示）。"""
    body = _rpc_post(_PNVS_ENDPOINT, payload, _pnvs_guard())
    provider_code = str(body.get("Code", ""))
    if provider_code == "OK":
        return body
    hint = _PNVS_HINTS.get(provider_code) or str(body.get("Message") or "服务商未给出可识别的原因")
    raise SmsSendFailed(
        provider_code or "unknown",
        f"号码认证服务 {action} 失败（{provider_code or '未知错误'}）：{hint}",
    )


def _send_pnvs(phone: str, code: str | None, purpose: str) -> None:
    """号码认证服务发码。

    需要的环境变量：SMS_ALIYUN_ACCESS_KEY_ID / SMS_ALIYUN_ACCESS_KEY_SECRET /
    SMS_PNVS_SIGN_NAME（控制台的**赠送签名**名称）/ SMS_PNVS_TEMPLATE_CODE（**赠送模板**编号）。
    """
    # `code` **刻意不使用**：验证码由阿里云按 CodeType 生成，我们的代码拿不到明文。
    del code
    _pnvs_guard()
    payload = _pnvs_send_payload(phone, purpose)

    if _dry_run():
        # 日志只证明「请求构造得出来」。这里本来也没有验证码可泄漏。
        print(
            "[sms:pnvs] dry_run "
            f"purpose={purpose} phone={_mask_phone(phone)} "
            f"sign_name={payload['SignName']} template={payload['TemplateCode']} "
            f"code_length={payload['CodeLength']} valid_time={payload['ValidTime']} "
            f"interval={payload['Interval']} duplicate_policy={payload['DuplicatePolicy']} "
            f"return_verify_code={payload['ReturnVerifyCode']}",
            flush=True,
        )
        return

    _pnvs_call("SendSmsVerifyCode", payload)


def _verify_pnvs(phone: str, code: str, purpose: str) -> bool:
    """问阿里云「这个码对不对」。True=通过，False=**码不对**（不是故障）。"""
    body = _pnvs_call("CheckSmsVerifyCode", _pnvs_check_payload(phone, code, purpose))
    model = body.get("Model")
    result = str(model.get("VerifyResult", "") or "") if isinstance(model, dict) else ""
    # 只有明确的 PASS 才算通过（fail-closed）。万一阿里云以后加了别的取值，
    # 宁可判「不通过」让用户重试，也绝不能把未知状态当成功放人进来。
    return result.upper() == "PASS"


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


_CHANNELS = {
    "mock": _log_and_store,
    "aliyun": _send_aliyun,
    "pnvs": _send_pnvs,
    "tencent": _send_tencent,
}

#: 这些通道的验证码由服务商生成并保管 —— 我们**既生不出也校验不了**，
#: 校验必须走 `verify_code()` 去问服务商。`core.py` 用 `remote_verify()` 判断。
_REMOTE_VERIFY_PROVIDERS = frozenset({"pnvs"})


def remote_verify() -> bool:
    """当前通道是否「验证码归服务商管」。

    为 True 时调用方**不要**再自己生成验证码，校验也要改为询问服务商。
    见文件头那张模型对照表。
    """
    return _provider() in _REMOTE_VERIFY_PROVIDERS


def send_code(phone: str, code: str | None, purpose: str) -> str:
    """发一条验证码，返回实际使用的通道名。

    `code` 在 `remote_verify()` 为 True 的通道上会被忽略（传 `None` 即可）。

    未知通道名直接报错，不静默回退到 mock —— 否则「配了真实通道却没生效」会被
    当成「短信延迟」，白查半天。
    """
    name = _provider()
    channel = _CHANNELS.get(name)
    if channel is None:
        raise SmsNotConfigured(f"未知的 SMS_PROVIDER={name!r}，可选：{', '.join(sorted(_CHANNELS))}")
    channel(phone, code, purpose)
    return name


def verify_code(phone: str, code: str, purpose: str) -> bool:
    """向服务商校验验证码（**只在 `remote_verify()` 为 True 的通道上调用**）。

    返回 True=通过、False=**验证码不对**。这两个必须和「通道故障」分清：

    - 验证码不对 → 返回 False（接口层 → 400，是用户打错了一位）
    - 通道故障   → 抛 `SmsSendFailed`（接口层 → 502，可以重试）

    把前者当后者，用户会对着「服务暂时不可用」发呆，而其实他只需要重打一遍。
    """
    name = _provider()
    if name not in _REMOTE_VERIFY_PROVIDERS:
        raise SmsNotConfigured(
            f"通道 {name!r} 的验证码由我们本地保管，不该走 verify_code()；"
            "请走 core._check_code 的本地比对路径。"
        )
    if name == "pnvs":
        return _verify_pnvs(phone, code, purpose)
    raise SmsNotConfigured(f"通道 {name!r} 还没实现 verify_code()")  # pragma: no cover

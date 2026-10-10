"""阿里云短信通道（2026-10-10 实现）。

三条约定：

1. **测试绝不真发短信** —— 一律 monkeypatch 掉 `httpx.post`。真发=花钱 + 打扰真实号码。
2. **重点测签名与编码口径**，因为这里错了以后**不会有报错**，只表现为「短信没到」。
   规范化串 / 待签串 / 签名三段都断言，并冻结一个特征值防止以后被无意改口径。
3. **日志与错误信息里不许出现验证码原文** —— mock 通道把码写进日志（那是本地开发特例），
   真实通道若也这样，等于把「谁都能登录任意手机号」的坑搬到了生产。
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from app.auth import sms

PHONE = "13800000000"
CODE = "864209"
#: 模板 CODE 刻意**不含数字**：否则「日志里没有验证码」这条断言会因为
#: 模板码里恰好包含那几个数字而假绿（第一版就踩到了：CODE=123456 撞上 SMS_123456）。
TEMPLATE_CODE = "SMS_TEST_TEMPLATE"


def _configure(monkeypatch, **extra: str) -> None:
    monkeypatch.setenv("SMS_PROVIDER", "aliyun")
    monkeypatch.setenv("SMS_ALIYUN_ACCESS_KEY_ID", "LTAI-test-id")
    monkeypatch.setenv("SMS_ALIYUN_ACCESS_KEY_SECRET", "test-secret")
    monkeypatch.setenv("SMS_SIGN_NAME", "法小智")
    monkeypatch.setenv("SMS_TEMPLATE_CODE", TEMPLATE_CODE)
    monkeypatch.delenv("SMS_DRY_RUN", raising=False)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)


class _FakeResponse:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._payload, self.status_code = payload, status

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


# ------------------------------------------------------------------ 编码与签名

def test_percent_encode_follows_rfc3986() -> None:
    """阿里云要求：空格是 %20（不是 +）、`*` 必须编码、`~` 不编码。"""
    assert sms._percent_encode("a b") == "a%20b"
    assert sms._percent_encode("a+b") == "a%2Bb"
    assert sms._percent_encode("a*b") == "a%2Ab"
    assert sms._percent_encode("a~b") == "a~b"
    assert sms._percent_encode("/") == "%2F"
    assert sms._percent_encode("a!b'c(d)e") == "a%21b%27c%28d%29e"
    assert sms._percent_encode("中") == "%E4%B8%AD"


def test_canonical_string_is_sorted_and_encoded() -> None:
    params = {"b": "2", "a": "1 x", "C": "3"}
    assert sms._aliyun_canonical(params) == "C=3&a=1%20x&b=2"


def test_string_to_sign_shape() -> None:
    string_to_sign = sms._aliyun_string_to_sign({"Action": "SendSms"})
    assert string_to_sign.startswith("POST&%2F&")
    assert string_to_sign == "POST&%2F&Action%3DSendSms"


def test_signature_is_deterministic_and_sensitive() -> None:
    params = {"Action": "SendSms", "PhoneNumbers": PHONE}
    signature = sms._aliyun_signature(params, "secret")
    assert sms._aliyun_signature(dict(params), "secret") == signature
    assert len(signature) == 28 and signature.endswith("=")      # Base64(HMAC-SHA1) 定长
    assert sms._aliyun_signature({**params, "PhoneNumbers": "13900000000"}, "secret") != signature
    assert sms._aliyun_signature(params, "other-secret") != signature


def test_signature_golden_value() -> None:
    """特征值冻结：口径一旦被改（哪怕只改一个字符的编码方式）这条就会红。

    这个值不是「我们自己算出来就当真」—— 它由一份**独立实现**（在测试外按官方文档
    重新写了一遍规范化 + 待签串 + HMAC-SHA1）交叉验证过，两者一致才写进来。
    """
    params = {
        "Action": "SendSms",
        "PhoneNumbers": PHONE,
        "TemplateParam": json.dumps({"code": CODE}, ensure_ascii=False, separators=(",", ":")),
        "Timestamp": "2026-10-10T09:00:00Z",
    }
    assert sms._aliyun_signature(params, "test-secret") == "GONXY7FevynvVcOcFFp+q4zQTQc="


# ------------------------------------------------------------------ 请求构造

def test_payload_shape(monkeypatch) -> None:
    _configure(monkeypatch)
    payload = sms._aliyun_payload(PHONE, CODE)
    assert payload["Action"] == "SendSms"
    assert payload["Version"] == "2017-05-25"
    assert payload["SignatureMethod"] == "HMAC-SHA1"
    assert payload["SignatureVersion"] == "1.0"
    assert payload["PhoneNumbers"] == PHONE
    assert payload["SignName"] == "法小智"
    assert payload["TemplateCode"] == TEMPLATE_CODE
    assert payload["RegionId"] == "cn-hangzhou"
    assert json.loads(payload["TemplateParam"]) == {"code": CODE}
    assert payload["Timestamp"].endswith("Z") and payload["Timestamp"].count(":") == 2
    assert "Signature" not in payload                            # 签名由调用处补
    assert sms._aliyun_payload(PHONE, CODE)["SignatureNonce"] != payload["SignatureNonce"]


def test_template_param_name_configurable(monkeypatch) -> None:
    _configure(monkeypatch, SMS_TEMPLATE_PARAM="mycode")
    assert json.loads(sms._aliyun_payload(PHONE, CODE)["TemplateParam"]) == {"mycode": CODE}


def test_missing_keys_reports_which_ones_without_echoing_values(monkeypatch) -> None:
    monkeypatch.setenv("SMS_PROVIDER", "aliyun")
    for key in sms._ALIYUN_KEYS:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(sms.SmsNotConfigured) as error:
        sms.send_code(PHONE, CODE, "login")
    message = str(error.value)
    assert "SMS_SIGN_NAME" in message and "SMS_TEMPLATE_CODE" in message


# ------------------------------------------------------------------ 发送

def test_dry_run_makes_no_request_and_hides_code(monkeypatch, capsys) -> None:
    _configure(monkeypatch, SMS_DRY_RUN="1")

    def _boom(*args, **kwargs):                                   # noqa: ANN002, ANN003
        raise AssertionError("dry-run 不该发起网络请求")

    monkeypatch.setattr(httpx, "post", _boom)
    assert sms.send_code(PHONE, CODE, "login") == "aliyun"
    out = capsys.readouterr().out
    assert "dry_run" in out
    assert PHONE not in out and "138****0000" in out              # 手机号打码
    assert CODE not in out                                        # 验证码绝不进日志
    assert "test-secret" not in out


def test_success_path_posts_signed_form(monkeypatch) -> None:
    _configure(monkeypatch)
    seen: dict[str, object] = {}

    def _fake_post(url, data=None, timeout=None):                  # noqa: ANN001
        seen.update({"url": url, "data": data, "timeout": timeout})
        return _FakeResponse({"Code": "OK", "Message": "OK", "BizId": "1"})

    monkeypatch.setattr(httpx, "post", _fake_post)
    assert sms.send_code(PHONE, CODE, "login") == "aliyun"
    assert seen["url"] == sms._ALIYUN_ENDPOINT
    data = seen["data"]
    assert data["Signature"] and data["SignatureNonce"]
    assert data["PhoneNumbers"] == PHONE


def test_provider_error_becomes_actionable_hint(monkeypatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **k: _FakeResponse({"Code": "isv.SMS_SIGNATURE_ILLEGAL", "Message": "SIGNATURE_ILLEGAL"}),
    )
    with pytest.raises(sms.SmsSendFailed) as error:
        sms.send_code(PHONE, CODE, "login")
    assert error.value.code == "isv.SMS_SIGNATURE_ILLEGAL"
    assert "签名" in str(error.value)
    assert CODE not in str(error.value)                            # 错误信息也不带验证码


def test_unknown_provider_code_keeps_provider_message(monkeypatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **k: _FakeResponse({"Code": "isv.BRAND_NEW_ISSUE", "Message": "服务商新原因"}),
    )
    with pytest.raises(sms.SmsSendFailed) as error:
        sms.send_code(PHONE, CODE, "login")
    assert error.value.code == "isv.BRAND_NEW_ISSUE"
    assert "服务商新原因" in str(error.value)


def test_network_failure_is_not_silent(monkeypatch) -> None:
    _configure(monkeypatch)

    def _boom(*args, **kwargs):                                    # noqa: ANN002, ANN003
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _boom)
    with pytest.raises(sms.SmsSendFailed) as error:
        sms.send_code(PHONE, CODE, "login")
    assert error.value.code == "network"
    assert "稍后重试" in str(error.value)


def test_non_json_response_is_reported_with_status(monkeypatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(ValueError("not json"), status=502))
    with pytest.raises(sms.SmsSendFailed) as error:
        sms.send_code(PHONE, CODE, "login")
    assert error.value.code == "http_502"
    assert "502" in str(error.value)


def test_unknown_provider_does_not_fall_back_to_mock(monkeypatch) -> None:
    """配错通道名必须报错：静默回退到 mock 会被当成「短信延迟」白查半天。"""
    monkeypatch.setenv("SMS_PROVIDER", "ali-yun")
    with pytest.raises(sms.SmsNotConfigured) as error:
        sms.send_code(PHONE, CODE, "login")
    assert "ali-yun" in str(error.value)


# ------------------------------------------------------------------ 接口层：通道真的换了

def test_api_reports_aliyun_channel_and_leaves_mock_outbox_alone(monkeypatch, tmp_path) -> None:
    """走一遍 `/api/auth/code`：返回的通道应是 aliyun、mock=false，
    并且**不碰 mock 的 outbox 文件**（否则就是静默回退到 mock 了）。"""
    import importlib

    outbox = tmp_path / "sms-outbox.jsonl"
    monkeypatch.setenv("AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("SMS_OUTBOX", str(outbox))
    monkeypatch.setenv("AUTH_CODE_MIN_INTERVAL", "0")
    _configure(monkeypatch, SMS_DRY_RUN="1")                      # dry-run：不发真实短信

    import app.auth.core as core

    importlib.reload(core)
    from app import server

    importlib.reload(server)
    from fastapi.testclient import TestClient

    response = TestClient(server.app).post("/api/auth/code", json={"phone": PHONE, "purpose": "login"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["channel"] == "aliyun"
    assert payload["mock"] is False
    assert not outbox.exists(), "配了 aliyun 却写了 mock 的 outbox，说明通道没真正切换"


def test_api_maps_provider_failure_to_sms_unavailable(monkeypatch, tmp_path) -> None:
    """服务商拒绝时，接口要给 502 `sms_unavailable` + 可执行提示，不是 500。"""
    import importlib

    monkeypatch.setenv("AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("SMS_OUTBOX", str(tmp_path / "sms-outbox.jsonl"))
    monkeypatch.setenv("AUTH_CODE_MIN_INTERVAL", "0")
    _configure(monkeypatch)
    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **k: _FakeResponse({"Code": "isv.SMS_TEMPLATE_ILLEGAL", "Message": "TEMPLATE_ILLEGAL"}),
    )

    import app.auth.core as core

    importlib.reload(core)
    from app import server

    importlib.reload(server)
    from fastapi.testclient import TestClient

    response = TestClient(server.app).post("/api/auth/code", json={"phone": PHONE, "purpose": "login"})
    assert response.status_code == 502, response.text
    error = response.json()["error"]
    assert error["code"] == "sms_unavailable"
    assert "模板" in error["message"]


# ------------------------------------------------------------------ 可选：真实接口形状校验

@pytest.mark.skipif(
    os.environ.get("FAXIAOZHI_SMS_SHAPE_CHECK") != "1",
    reason="需要出网访问 dysmsapi.aliyuncs.com，默认跳过；设 FAXIAOZHI_SMS_SHAPE_CHECK=1 手动跑",
)
def test_request_shape_is_accepted_by_aliyun(monkeypatch) -> None:
    """用**假密钥**打真实接口，这是没有真密钥时能做的最强验证。

    期望服务端回「鉴权类」错误（说明请求结构被接受、只是凭据不对）。
    若回 `MissingParameter` / `InvalidParameter`，说明我们的参数构造错了。

    2026-10-10 实测：真实接口回的是 `InvalidAccessKeyId.NotFound`
    （Specified access key is not found）—— 正是期望中的「结构被接受」。
    这个码后来也补进了 `_ALIYUN_HINTS`，避免线上只看到一句英文原文。
    """
    _configure(monkeypatch)
    with pytest.raises(sms.SmsSendFailed) as error:
        sms.send_code(PHONE, CODE, "login")
    accepted_shape_codes = {
        "InvalidAccessKeyId",
        "InvalidAccessKeyId.NotFound",
        "SignatureDoesNotMatch",
        "Forbidden.RAM",
        "Forbidden",
    }
    assert error.value.code in accepted_shape_codes, (
        f"服务端回了 {error.value.code}，说明请求结构可能不对：{error.value}"
    )

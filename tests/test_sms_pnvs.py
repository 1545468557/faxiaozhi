"""阿里云号码认证服务（pnvs）通道（2026-10-10 实现）。

这个通道和 mock / aliyun 的模型是**反的**：验证码由阿里云生成并保管，
我们的代码从头到尾拿不到明文。于是多出两条别处没有的、**错了会静默出事**的护栏：

1. `ReturnVerifyCode` 必须是 `false`。传 `true` 会把验证码回传给我们，
   「我们拿不到明文」这个安全前提当场消失，十有八九还会跟着写进日志。
2. `CheckSmsVerifyCode` 的 `Code == "OK"` **只代表接口调通，不代表验证码正确**。
   真正的判定是 `Model.VerifyResult == "PASS"`。照 `Code` 判成功 =
   **任何人输入任意验证码都能登录进去**。这条用参数化测试钉死，口径取 fail-closed。

外加一条接口边界：**「验证码不对」必须和「通道故障」分开** ——
前者 400（用户打错一位），后者 502（可以重试）。混了用户会对着
「服务暂时不可用」一直重打，而问题在他自己的手指上。

约定与 aliyun 那批测试一致：**绝不真发短信**，一律 monkeypatch 掉 `httpx.post`。
"""

from __future__ import annotations

import importlib
import json
import os

import httpx
import pytest

from app.auth import core, sms

PHONE = "13800000000"
#: 刻意选一个**不是**模板 CODE 子串的验证码（PNVS 赠送模板编号真实长相是 "100001"
#: 这类纯数字），免得「日志里没有验证码」这类断言因为撞串而假绿 ——
#: 这个坑在 aliyun 那批测试里已经踩过一次（CODE=123456 撞上 SMS_123456）。
CODE = "732914"
PNVS_SIGN = "速通互联验证码"
PNVS_TEMPLATE = "100001"


def _configure_pnvs(monkeypatch, **extra: str) -> None:
    monkeypatch.setenv("SMS_PROVIDER", "pnvs")
    monkeypatch.setenv("SMS_ALIYUN_ACCESS_KEY_ID", "LTAI-test-id")
    monkeypatch.setenv("SMS_ALIYUN_ACCESS_KEY_SECRET", "test-secret")
    monkeypatch.setenv("SMS_PNVS_SIGN_NAME", PNVS_SIGN)
    monkeypatch.setenv("SMS_PNVS_TEMPLATE_CODE", PNVS_TEMPLATE)
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


def _ok(**extra: object) -> dict[str, object]:
    return {"Code": "OK", "Message": "成功", "Success": True, **extra}


# ------------------------------------------------------------------ 通道标识

@pytest.mark.parametrize(
    ("provider", "expected"),
    [("mock", False), ("aliyun", False), ("pnvs", True)],
)
def test_remote_verify_flag(monkeypatch, provider: str, expected: bool) -> None:
    """`remote_verify()` 是 core 分流的唯一依据，不能靠猜通道名。"""
    monkeypatch.setenv("SMS_PROVIDER", provider)
    assert sms.remote_verify() is expected


def test_verify_code_refuses_local_channels(monkeypatch) -> None:
    """本地通道的码在我们自己手里，误调 `verify_code` 是编程错误 —— 要响亮报错，
    不能静默返回 False 把「写错了」伪装成「用户输错了」。"""
    monkeypatch.setenv("SMS_PROVIDER", "mock")
    with pytest.raises(sms.SmsNotConfigured) as error:
        sms.verify_code(PHONE, CODE, "login")
    assert "本地" in str(error.value)


# ------------------------------------------------------------------ 请求构造

def test_pnvs_send_payload_shape(monkeypatch) -> None:
    _configure_pnvs(monkeypatch)
    payload = sms._pnvs_send_payload(PHONE, "login")
    assert payload["Action"] == "SendSmsVerifyCode"
    assert payload["Version"] == "2017-05-25"
    assert payload["SignatureMethod"] == "HMAC-SHA1"
    assert payload["SignatureVersion"] == "1.0"
    assert payload["CountryCode"] == "86"
    assert payload["PhoneNumber"] == PHONE
    assert payload["SignName"] == PNVS_SIGN
    assert payload["TemplateCode"] == PNVS_TEMPLATE
    assert payload["OutId"] == "login"
    assert payload["CodeType"] == "1"  # 1 = 纯数字
    assert payload["Timestamp"].endswith("Z")
    assert "Signature" not in payload  # 签名由 _rpc_post 补
    assert sms._pnvs_send_payload(PHONE, "login")["SignatureNonce"] != payload["SignatureNonce"]


def test_pnvs_passes_code_as_placeholder_not_a_real_value(monkeypatch) -> None:
    """必须传 `##code##` 占位符。直接传具体数字的话，官方明确「阿里云接口无法校验」，
    等于自己把校验闭环掐断 —— 短信还发得出去，但登录永远失败。"""
    _configure_pnvs(monkeypatch)
    assert json.loads(sms._pnvs_send_payload(PHONE, "login")["TemplateParam"]) == {"code": "##code##"}


def test_pnvs_never_asks_provider_to_return_the_code(monkeypatch) -> None:
    """`ReturnVerifyCode` 必须显式 false。

    官方默认就是 false，但这条不能靠默认值：一旦被传成 true，验证码会随响应回到
    我们这里，「代码从来看不到明文」这个安全前提就没了。
    """
    _configure_pnvs(monkeypatch)
    assert sms._pnvs_send_payload(PHONE, "login")["ReturnVerifyCode"] == "false"


def test_pnvs_duplicate_policy_is_explicit(monkeypatch) -> None:
    """1 = 覆盖：同号同场景重发时旧码立即失效。

    这是本地通道靠 `UPDATE phone_codes SET consumed_at` 手做的那层防护；pnvs 下码在
    阿里云手里我们改不了，只能靠这个参数。官方默认恰好也是 1，但**不能依赖默认值** ——
    哪天默认变了，我们会静默丢掉「上一轮验证码还能用」这层保护。
    """
    _configure_pnvs(monkeypatch)
    assert sms._pnvs_send_payload(PHONE, "login")["DuplicatePolicy"] == "1"


def test_pnvs_policy_defaults_match_core(monkeypatch) -> None:
    """本地策略与服务商下发的策略必须是**同一套数字**。

    `sms.py` 不能 import `core.py`（循环依赖），所以两边各读一次同名环境变量 ——
    这就有漂移风险：改了 core 的默认值却忘了改 sms，会变成「我们以为码 6 位、
    服务商只发 4 位」或者有效期两边对不上。这条测试就是那个守卫，
    默认值与覆盖值两种情况都断言。
    """
    for key in ("AUTH_CODE_LENGTH", "AUTH_CODE_TTL_SECONDS", "AUTH_CODE_MIN_INTERVAL"):
        monkeypatch.delenv(key, raising=False)
    assert sms._pnvs_code_length() == core._code_length()
    assert sms._pnvs_valid_time() == core._code_ttl()
    assert sms._pnvs_interval() == core._code_min_interval()

    monkeypatch.setenv("AUTH_CODE_LENGTH", "4")
    monkeypatch.setenv("AUTH_CODE_TTL_SECONDS", "120")
    monkeypatch.setenv("AUTH_CODE_MIN_INTERVAL", "30")
    assert sms._pnvs_code_length() == core._code_length() == 4
    assert sms._pnvs_valid_time() == core._code_ttl() == 120
    assert sms._pnvs_interval() == core._code_min_interval() == 30


def test_pnvs_check_payload_shape(monkeypatch) -> None:
    _configure_pnvs(monkeypatch)
    payload = sms._pnvs_check_payload(PHONE, CODE, "login")
    assert payload["Action"] == "CheckSmsVerifyCode"
    assert payload["PhoneNumber"] == PHONE
    assert payload["VerifyCode"] == CODE
    assert payload["OutId"] == "login"


# ------------------------------------------------------------------ 校验判定（最要紧的一段）

@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ({"VerifyResult": "PASS"}, True),
        ({"VerifyResult": "pass"}, True),          # 大小写不敏感
        ({"VerifyResult": "FAIL"}, False),
        ({"VerifyResult": "UNKNOWN"}, False),
        ({"VerifyResult": ""}, False),
        ({"VerifyResult": None}, False),
        ({}, False),                                # Model 在、字段缺
        ({"OtherField": "PASS"}, False),            # 字段名不对也算不通过
        ("not-a-dict", False),                      # Model 结构异常
    ],
)
def test_only_explicit_pass_is_success(monkeypatch, model: object, expected: bool) -> None:
    """**本次改动最要紧的一条。**

    `Code == "OK"` 只代表接口调通。这里刻意把 `Code` 固定成 "OK"，
    再让 `VerifyResult` 五花八门 —— 照 `Code` 判成功的实现会在这里全绿，
    而实际效果是**任何人输入任意验证码都能登录**。口径取 fail-closed：
    只有明确的 PASS 才算通过，未知状态一律当「不通过」。
    """
    _configure_pnvs(monkeypatch)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(_ok(Model=model)))
    assert sms.verify_code(PHONE, CODE, "login") is expected


def test_missing_model_entirely_is_not_success(monkeypatch) -> None:
    """连 `Model` 都不返回时也必须判「不通过」—— 缺失 ≠ 通过。"""
    _configure_pnvs(monkeypatch)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(_ok()))
    assert sms.verify_code(PHONE, CODE, "login") is False


def test_verify_channel_failure_raises_instead_of_returning_false(monkeypatch) -> None:
    """通道故障必须抛异常（→ 502），**不能**返回 False（→ 400）。

    返回 False 的话，服务商挂了的时候用户看到的是「验证码不对」，会一直重打。
    """
    _configure_pnvs(monkeypatch)
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse({"Code": "FREQUENCY_FAIL", "Message": "Check frequency fail."})
    )
    with pytest.raises(sms.SmsSendFailed) as error:
        sms.verify_code(PHONE, CODE, "login")
    assert error.value.code == "FREQUENCY_FAIL"


# ------------------------------------------------------------------ 发送与错误提示

def test_dry_run_makes_no_request_and_leaks_nothing(monkeypatch, capsys) -> None:
    _configure_pnvs(monkeypatch, SMS_DRY_RUN="1")

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("dry-run 不该发起网络请求")

    monkeypatch.setattr(httpx, "post", _boom)
    assert sms.send_code(PHONE, None, "login") == "pnvs"
    out = capsys.readouterr().out
    assert "dry_run" in out
    assert PHONE not in out and "138****0000" in out
    assert "test-secret" not in out
    assert "return_verify_code=false" in out


def test_send_error_becomes_actionable_hint(monkeypatch) -> None:
    """`FUNCTION_NOT_OPENED` = 还没在控制台开通短信认证 —— 最可能遇到的第一个错，
    必须给出「去哪儿开通」，不能只回英文原文。"""
    _configure_pnvs(monkeypatch)
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            {"Code": "FUNCTION_NOT_OPENED", "Message": "You have not opened this function."}
        ),
    )
    with pytest.raises(sms.SmsSendFailed) as error:
        sms.send_code(PHONE, None, "login")
    assert error.value.code == "FUNCTION_NOT_OPENED"
    assert "开通" in str(error.value)


def test_signature_mismatch_explains_toplevel_cause(monkeypatch) -> None:
    """签名与模板不配套是赠送资源的典型错误，提示要指到那个方向上。"""
    _configure_pnvs(monkeypatch)
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse({"Code": "INVALID_PARAMETERS", "Message": "parameter is not valid."})
    )
    with pytest.raises(sms.SmsSendFailed) as error:
        sms.send_code(PHONE, None, "login")
    assert "赠送签名" in str(error.value)


def test_unknown_provider_code_keeps_provider_message(monkeypatch) -> None:
    _configure_pnvs(monkeypatch)
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse({"Code": "BRAND_NEW_ISSUE", "Message": "服务商新原因"})
    )
    with pytest.raises(sms.SmsSendFailed) as error:
        sms.send_code(PHONE, None, "login")
    assert error.value.code == "BRAND_NEW_ISSUE"
    assert "服务商新原因" in str(error.value)


def test_missing_keys_reports_which_ones(monkeypatch) -> None:
    monkeypatch.setenv("SMS_PROVIDER", "pnvs")
    for key in sms._PNVS_KEYS:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(sms.SmsNotConfigured) as error:
        sms.send_code(PHONE, None, "login")
    message = str(error.value)
    assert "SMS_PNVS_SIGN_NAME" in message and "SMS_PNVS_TEMPLATE_CODE" in message


def test_network_failure_is_not_silent(monkeypatch) -> None:
    _configure_pnvs(monkeypatch)

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _boom)
    with pytest.raises(sms.SmsSendFailed) as error:
        sms.send_code(PHONE, None, "login")
    assert error.value.code == "network"


# ------------------------------------------------------------------ 本地不留明文

def test_issue_code_stores_sentinel_not_a_hash(monkeypatch, tmp_path) -> None:
    """断言**结构**：库里存的是哨兵值，本地根本没有验证码哈希可存。"""
    monkeypatch.setenv("AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("AUTH_CODE_MIN_INTERVAL", "0")
    _configure_pnvs(monkeypatch, SMS_DRY_RUN="1")
    importlib.reload(core)

    result = core.issue_code(PHONE, "login")
    assert result["channel"] == "pnvs"
    assert result["mock"] is False
    assert "code" not in result  # 返回体里绝不含验证码

    with core.connect() as conn:
        row = conn.execute("SELECT code_hash FROM phone_codes ORDER BY id DESC LIMIT 1").fetchone()
    assert row["code_hash"] == core._REMOTE_CODE_SENTINEL


# ------------------------------------------------------------------ 接口层

def _client(monkeypatch, tmp_path, *, max_attempts: str | None = None):
    """起一个装了 pnvs 通道的 TestClient；发码走 dry-run，不发真实短信。"""
    monkeypatch.setenv("AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("SMS_OUTBOX", str(tmp_path / "sms-outbox.jsonl"))
    monkeypatch.setenv("AUTH_CODE_MIN_INTERVAL", "0")
    extra = {"AUTH_CODE_MAX_ATTEMPTS": max_attempts} if max_attempts else {}
    _configure_pnvs(monkeypatch, SMS_DRY_RUN="1", **extra)

    importlib.reload(core)
    from app import server

    importlib.reload(server)
    from fastapi.testclient import TestClient

    return TestClient(server.app), tmp_path / "sms-outbox.jsonl"


def test_api_wrong_code_is_422_not_502(monkeypatch, tmp_path) -> None:
    """**接口层的核心边界**：远端说「码不对」→ 422 `code_invalid`（校验类错误）。

    重点其实是它**不是 502**。502 的含义是「我们的通道挂了，等会儿再试」，
    套在「用户打错一位」上会让人对着屏幕一直重试。
    """
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(_ok(Model={"VerifyResult": "FAIL"}))
    )
    client, outbox = _client(monkeypatch, tmp_path)

    assert client.post("/api/auth/code", json={"phone": PHONE, "purpose": "login"}).status_code == 200
    response = client.post("/api/auth/login", json={"phone": PHONE, "code": CODE})
    assert response.status_code == 422, response.text
    # 再把「不是通道故障」单独写死一遍：这条边界光靠测试名表达不够，
    # 将来真红了要一眼看出是这一类回归，而不是继续调状态码数字。
    assert response.json()["error"]["code"] != "sms_unavailable"
    assert response.json()["error"]["code"] == "code_invalid"
    assert not outbox.exists(), "配了 pnvs 却写了 mock 的 outbox，说明通道没真正切换"


def test_api_channel_failure_is_502(monkeypatch, tmp_path) -> None:
    """而「服务商挂了」→ 502 `sms_unavailable`（可重试），不是 500。"""
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse({"Code": "FUNCTION_NOT_OPENED", "Message": "not opened"}),
    )
    client, _ = _client(monkeypatch, tmp_path)

    assert client.post("/api/auth/code", json={"phone": PHONE, "purpose": "login"}).status_code == 200
    response = client.post("/api/auth/login", json={"phone": PHONE, "code": CODE})
    assert response.status_code == 502, response.text
    error = response.json()["error"]
    assert error["code"] == "sms_unavailable"
    assert "开通" in error["message"]


def test_api_wrong_code_still_counts_attempts_locally(monkeypatch, tmp_path) -> None:
    """远端校验**不能把本地限次架空**。

    不数次数的话，`AUTH_CODE_MAX_ATTEMPTS` 配了等于没配，而且「无限次试码」的
    请求全部打给服务商 —— 成本由我们买单，防护却归零。
    """
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(_ok(Model={"VerifyResult": "FAIL"}))
    )
    client, _ = _client(monkeypatch, tmp_path, max_attempts="2")

    assert client.post("/api/auth/code", json={"phone": PHONE, "purpose": "login"}).status_code == 200
    codes = [
        client.post("/api/auth/login", json={"phone": PHONE, "code": CODE}).json()["error"]["code"]
        for _ in range(3)
    ]
    assert codes == ["code_invalid", "code_invalid", "code_attempts_exceeded"]


def test_api_passing_code_reaches_user_lookup(monkeypatch, tmp_path) -> None:
    """真通过时要能往下走到「这个号还没注册」——证明校验分支真的放行了。

    没有这条，前面那些「不通过」的断言全都可能因为别的原因不通过而假绿。
    """
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(_ok(Model={"VerifyResult": "PASS"}))
    )
    client, _ = _client(monkeypatch, tmp_path)

    assert client.post("/api/auth/code", json={"phone": PHONE, "purpose": "login"}).status_code == 200
    response = client.post("/api/auth/login", json={"phone": PHONE, "code": CODE})
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "auth_failed"


# ------------------------------------------------------------------ 可选：真实接口形状校验

@pytest.mark.skipif(
    os.environ.get("FAXIAOZHI_SMS_SHAPE_CHECK") != "1",
    reason="需要出网访问 dypnsapi.aliyuncs.com，默认跳过；设 FAXIAOZHI_SMS_SHAPE_CHECK=1 手动跑",
)
def test_pnvs_request_shape_is_accepted_by_aliyun(monkeypatch) -> None:
    """用**假密钥**打真实接口 —— 没有真密钥时能做的最强验证。

    期望服务端回「鉴权类」错误（说明请求结构被接受、只是凭据不对）。
    若回 `MissingParameter` / `InvalidParameter`，说明参数构造错了。
    """
    _configure_pnvs(monkeypatch)
    with pytest.raises(sms.SmsSendFailed) as error:
        sms.send_code(PHONE, None, "login")
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

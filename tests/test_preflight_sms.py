"""preflight 的短信通道检查（2026-10-10 补）。

这两条检查存在的唯一理由：`SMS_PROVIDER=mock` 在公网上**不报错**，
它只是安静地把明文验证码写进日志。所以必须有一个显式的闸门把它拦下来 ——
而闸门本身也需要守卫，否则哪天被顺手改成恒真就没人发现。

这里只测 `_sms_checks` 的判定逻辑（喂一个假的 cfg），
「这些键有没有真的并进 inspect() 的结果」由一条独立命令实跑 preflight 来验证。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_preflight():
    """按路径加载，不依赖目录名大小写（仓库里是 `Scripts/`，别处可能写成 `scripts/`）。"""
    for candidate in (ROOT / "scripts" / "preflight_public.py", ROOT / "Scripts" / "preflight_public.py"):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location("preflight_public_under_test", candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise FileNotFoundError("找不到 preflight_public.py")


preflight = _load_preflight()

ALIYUN_KEYS = {
    "SMS_ALIYUN_ACCESS_KEY_ID": "LTAI-test",
    "SMS_ALIYUN_ACCESS_KEY_SECRET": "secret",
    "SMS_SIGN_NAME": "法小智",
    "SMS_TEMPLATE_CODE": "SMS_TEST",
}
PNVS_KEYS = {
    "SMS_ALIYUN_ACCESS_KEY_ID": "LTAI-test",
    "SMS_ALIYUN_ACCESS_KEY_SECRET": "secret",
    "SMS_PNVS_SIGN_NAME": "速通互联验证码",
    "SMS_PNVS_TEMPLATE_CODE": "100001",
}


class _Cfg:
    """最小替身：只要有个 `env()` 就能喂给 `_sms_checks`。"""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def env(self, key: str) -> str:
        return self._values.get(key, "")


def test_mock_is_rejected() -> None:
    """本地开发长期停在 mock，上线时最容易忘的就是这一行。"""
    checks = preflight._sms_checks(_Cfg({"SMS_PROVIDER": "mock"}))
    assert checks["sms_channel_is_real"] is False
    assert checks["sms_channel_configured"] is False


def test_unset_provider_is_rejected() -> None:
    """没设也算不通过 —— `sms.py` 里没设时默认就是 mock，不能靠「没设」蒙过去。"""
    assert preflight._sms_checks(_Cfg({}))["sms_channel_is_real"] is False


@pytest.mark.parametrize(("provider", "keys"), [("aliyun", ALIYUN_KEYS), ("pnvs", PNVS_KEYS)])
def test_real_channel_with_keys_passes(provider: str, keys: dict[str, str]) -> None:
    checks = preflight._sms_checks(_Cfg({"SMS_PROVIDER": provider, **keys}))
    assert checks["sms_channel_is_real"] is True
    assert checks["sms_channel_configured"] is True


@pytest.mark.parametrize("provider", ["aliyun", "pnvs"])
def test_real_channel_without_keys_is_rejected(provider: str) -> None:
    """切了通道却没配密钥 = 每个发码请求都 502，同样要红。"""
    checks = preflight._sms_checks(_Cfg({"SMS_PROVIDER": provider}))
    assert checks["sms_channel_is_real"] is True
    assert checks["sms_channel_configured"] is False


def test_pnvs_does_not_accept_the_dysms_key_names() -> None:
    """两个通道的密钥名不能混：pnvs 用 `SMS_PNVS_SIGN_NAME`/`SMS_PNVS_TEMPLATE_CODE`。

    写成 dysms 那套的话，pnvs 通道会「检查通过但发不出去」—— 正好是闸门最该拦住的情形。
    """
    checks = preflight._sms_checks(_Cfg({"SMS_PROVIDER": "pnvs", **ALIYUN_KEYS}))
    assert checks["sms_channel_is_real"] is True
    assert checks["sms_channel_configured"] is False


@pytest.mark.parametrize("raw", ["MOCK", " mock ", "Mock", "mOcK"])
def test_case_and_whitespace_cannot_slip_through(raw: str) -> None:
    """口径必须和 `sms.py` 的 `strip().lower()` 一致，否则大小写就能绕过闸门。"""
    assert preflight._sms_checks(_Cfg({"SMS_PROVIDER": raw}))["sms_channel_is_real"] is False


def test_unknown_provider_is_not_treated_as_real() -> None:
    """配错通道名（写错一个字）也不能过 —— 那种状态下发码会直接报错。"""
    checks = preflight._sms_checks(_Cfg({"SMS_PROVIDER": "ali-yun", **ALIYUN_KEYS}))
    assert checks["sms_channel_is_real"] is True   # 它不是 mock
    assert checks["sms_channel_configured"] is False  # 但没有已知的密钥清单 → 拦住


# --------------------------------------------------------------- 该不该拦住上线
#
# 上面测的是「短信配得对不对」；下面测的是「配不对时该不该拦住上线」。
# 两者的答案不一样：**访客模式下发码接口根本不可达**，拦住上线就是把关把错了地方
# （2026-10-10 实测：跑 --local 彩排时，明明不发短信的部署也被堵在 Caddy 外面）。


def _force_guest_mode(monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(preflight.guard, "guest_mode_enabled", lambda: enabled)


def test_login_mode_keeps_sms_as_hard_check(monkeypatch) -> None:
    """登录模式下短信没配好 = 上线即坏，必须硬拦。"""
    _force_guest_mode(monkeypatch, False)
    checks, warnings = preflight._sms_gate(_Cfg({"SMS_PROVIDER": "mock"}))
    assert checks["sms_channel_is_real"] is False
    assert checks["sms_channel_configured"] is False
    assert warnings == []


def test_guest_mode_downgrades_missing_sms_to_warning(monkeypatch) -> None:
    """访客模式下不发短信，所以「没配」只记警告，不阻止开公网入口。"""
    _force_guest_mode(monkeypatch, True)
    checks, warnings = preflight._sms_gate(_Cfg({"SMS_PROVIDER": "mock"}))
    assert checks == {}                      # 不并进 checks ⇒ 不影响 ok
    assert len(warnings) == 2
    assert all("AUTH_GUEST_MODE=1" in w for w in warnings)


def test_guest_mode_with_configured_sms_has_no_warning(monkeypatch) -> None:
    """配好了就不该有噪音 —— 否则「有警告」会变成没人看的东西。"""
    _force_guest_mode(monkeypatch, True)
    checks, warnings = preflight._sms_gate(_Cfg({"SMS_PROVIDER": "pnvs", **PNVS_KEYS}))
    assert checks == {}
    assert warnings == []


def test_flipping_back_to_login_mode_blocks_again(monkeypatch) -> None:
    """同一个配置，从访客模式切回登录模式后必须**立刻**变回硬拦。

    这条是「警告不是免责声明」的守卫：访客模式下放过的短信缺口，
    在登录模式里是一颗雷（mock 会把明文验证码写进日志）。
    """
    cfg = _Cfg({"SMS_PROVIDER": "mock"})
    _force_guest_mode(monkeypatch, True)
    assert preflight._sms_gate(cfg)[0] == {}

    _force_guest_mode(monkeypatch, False)
    checks, _ = preflight._sms_gate(cfg)
    assert checks["sms_channel_is_real"] is False
    assert checks["sms_channel_configured"] is False

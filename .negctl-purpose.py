"""负对照：证明「验证码用途隔离」测试真的会咬人。

做法：**同时**拆掉两层隔离，让注册码能被登录用：
  1) `_check_code` 的 SQL 不再按 purpose 过滤；
  2) 验证码哈希不再绑定 purpose。
只拆一层是不会让测试变红的 —— 两层是独立的，这本身也是结论的一部分。

安全措施（照铁律）：
  - 每处替换断言**命中恰好 1 处**，命中 0/2 处直接中止且文件不动；
  - `try/finally` 保证还原；跑完比对 SHA-256 确认逐字节回到原状。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

TARGET = pathlib.Path("app/auth/core.py")

PAIRS = [
    (
        'message = f"{phone}:{purpose}:{code}"',
        'message = f"{phone}::{code}"',
    ),
    (
        "SELECT * FROM phone_codes WHERE phone = ? AND purpose = ? AND consumed_at IS NULL ORDER BY id DESC LIMIT 1",
        "SELECT * FROM phone_codes WHERE phone = ? AND (? IS NOT NULL) AND consumed_at IS NULL ORDER BY id DESC LIMIT 1",
    ),
]

original = TARGET.read_text(encoding="utf-8")
sha_before = hashlib.sha256(original.encode("utf-8")).hexdigest()

patched = original
for old, new in PAIRS:
    hits = patched.count(old)
    if hits != 1:
        print(f"[中止] 期望命中恰好 1 处，实际 {hits} 处：{old!r}")
        sys.exit(2)
    patched = patched.replace(old, new)

try:
    TARGET.write_text(patched, encoding="utf-8")
    print("[负对照] 已注入：SQL 去掉 purpose 过滤 + 哈希不绑定 purpose")
    result = subprocess.run(
        ["uv", "run", "pytest", "tests/test_auth.py::test_code_purpose_is_isolated", "-q", "--basetemp=/tmp/fxz-pytest-neg"],
        capture_output=True,
        text=True,
    )
    print("[负对照] pytest 退出码:", result.returncode, "（期望非 0 —— 测试应该变红）")
    tail = (result.stdout or "").strip().splitlines()[-6:]
    print("\n".join(tail))
finally:
    TARGET.write_text(original, encoding="utf-8")
    sha_after = hashlib.sha256(TARGET.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    print("[还原] SHA-256 一致:", sha_before == sha_after)
    print("[还原]", sha_after)

#!/usr/bin/env python3
"""从本机 `.env` 生成服务器要用的 `.env.production`（**只搬值，不打印值**）。

为什么需要它：容器里的应用拿不到仓库的 `.env`（`.env` 不进镜像、也不进 Git），
线上唯一的配置来源就是 `deploy/compose.yaml` 里那句 `env_file: ../.env.production`。
而这份文件**必须手写一个**才容易出事 —— 典型事故是「照着
`deploy/production.env.example` 抄，只抄了 4 个 `PKULAW_URL_*`，
剩下 10 个静默消失，线上检索少了一半工具却不报错」。

所以本脚本做两件事：
1. 把本机已经验证可用的模型密钥、北大法宝 Token、**全部** `PKULAW_URL_*` 搬过去；
2. **对照 `config.yaml` 的 `mcp.endpoints` 逐个点名**，少一个就拒绝生成
   （`--allow-missing-endpoints` 可降级为只警告）。

用法：
    python3 deploy/render_env.py --site-host law.example.com
    python3 deploy/render_env.py --site-host 1.2.3.4.sslip.io --out /tmp/.env.production
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 原样搬过去的键。前四个是模型，后一个是北大法宝鉴权。
CARRY_EXACT = (
    "MODEL_PROVIDER",
    "MODEL_BASE_URL",
    "MODEL_ID",
    "MODEL_API_KEY",
    "PKULAW_MCP_TOKEN",
)
#: 本机有多少个就搬多少个（`config.yaml` 按名字逐个引用它们）
CARRY_PREFIX = ("PKULAW_URL_",)

#: 公开部署的固定策略。改这里的每一个值都要问「公网上这样安全吗」。
PUBLIC_BLOCK: tuple[tuple[str, str], ...] = (
    ("AUTH_REQUIRE_LOGIN", "1"),
    ("AUTH_GUEST_MODE", "1"),
    ("AUTH_COOKIE_SECURE", "1"),
    ("GUEST_DAILY_LIMIT", "5"),
    ("GUEST_GLOBAL_DAILY_LIMIT", "30"),
    ("AUTH_SESSION_DAYS", "7"),
    ("AUTH_LOGIN_MAX_PER_MINUTE", "5"),
)

#: 短信通道的必需键。切了通道却没配齐 = 每个发码请求都 502。
SMS_CHANNEL_KEYS: dict[str, tuple[str, ...]] = {
    "aliyun": ("SMS_SIGN_NAME", "SMS_TEMPLATE_CODE"),
    "pnvs": ("SMS_PNVS_SIGN_NAME", "SMS_PNVS_TEMPLATE_CODE"),
}
#: 同一组 AccessKey 两条通道都用（都是阿里云账号密钥）
SMS_SHARED_KEYS = ("SMS_ALIYUN_ACCESS_KEY_ID", "SMS_ALIYUN_ACCESS_KEY_SECRET")


def _parse_env(path: Path) -> dict[str, str]:
    """极简解析，口径与 `app/config.py:load_dotenv` 一致（不剥行内注释）。"""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            out[key] = value
    return out


def _referenced_endpoints() -> list[str]:
    """`config.yaml` 的 `mcp.endpoints` 里点过名的环境变量，去重且保持首次出现顺序。

    优先用 PyYAML 精确解析；没有 PyYAML 时退回正则扫描整个文件 ——
    `PKULAW_URL_*` 这个名字只出现在 `mcp.endpoints` 里（`tool_names` / `field_map` 都不含），
    所以扫描全文不会误伤，而这个脚本就有可能在「没装依赖的服务器」上被直接执行。
    """
    text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        names: list[str] = []
        for name in re.findall(r"\bPKULAW_URL_[A-Z0-9_]+", text):
            if name not in names:
                names.append(name)
        return names

    raw = yaml.safe_load(text) or {}
    table = ((raw.get("mcp") or {}).get("endpoints")) or {}
    names = []
    for _logical, env_names in table.items():
        for name in env_names or []:
            if name not in names:
                names.append(name)
    return names


def _required_missing(env: dict[str, str]) -> list[str]:
    return [name for name in _referenced_endpoints() if not env.get(name, "").strip()]


def build(source: dict[str, str], site_host: str, sms_provider: str, sms_dry_run: bool,
          guest_secret: str) -> str:
    carried = {key: source.get(key, "") for key in CARRY_EXACT}
    prefix_keys = [key for key in source if key.startswith(CARRY_PREFIX)]

    lines: list[str] = [
        "# 由 deploy/render_env.py 生成 —— 不要手改，下次生成会覆盖。",
        "# 本文件含真实密钥，已被 .gitignore 的 `.env*` 规则排除，绝不进仓库。",
        "# 生成命令：python3 deploy/render_env.py --site-host <域名>",
        "",
        "# ---- 站点（Caddy 用它申请证书；ALLOWED_HOSTS 是前端 BFF 的主机白名单）----",
        f"SITE_HOST={site_host}",
        f"ALLOWED_HOSTS={site_host}",
        "",
        "# ---- 公开访问模式 ----",
        "# AUTH_GUEST_MODE=1：不上登录页，每位访客自动拿一个独立 HttpOnly Cookie；",
        "# AUTH_COOKIE_SECURE=1：只走 HTTPS（Caddy 提供）。",
    ]
    lines += [f"{key}={value}" for key, value in PUBLIC_BLOCK]
    lines.append(f"GUEST_COOKIE_SECRET={guest_secret}")
    lines.append("# ↑ 首次生成时随机产生，之后重复生成会**保持不变**（轮换它 = 所有访客身份重置）。")

    lines += [
        "",
        "# ---- 模型 ----",
        "# MODEL_PROVIDER 必须写 auto 或 deepseek：写成空会让 Config 回落到 auto，行为一致但意图不明。",
    ]
    lines += [f"{key}={carried[key]}" for key in CARRY_EXACT]
    if not carried.get("MODEL_API_KEY", "").strip():
        lines.append("# ⚠️ 本机 .env 里 MODEL_API_KEY 是空的 —— 线上会跑在 stub 模式（不调模型）。")

    lines += ["", "# ---- 北大法宝 MCP ----", f"# 本机共搬来 {len(prefix_keys)} 个地址槽位。"]
    lines += [f"{key}={source[key]}" for key in prefix_keys]

    lines += [
        "",
        "# ---- 短信验证码通道 ----",
        "# ⚠️ 绝不能是 mock：mock 把明文验证码写进服务端日志与 data/sms-outbox.jsonl，",
        "#    等于「拿到日志的人能登录成任意手机号」。preflight 会拦住 mock。",
        f"SMS_PROVIDER={sms_provider}",
    ]
    lines += [f"{key}={source.get(key, '')}" for key in SMS_SHARED_KEYS]
    if sms_provider in SMS_CHANNEL_KEYS:
        lines += [f"{key}={source.get(key, '')}" for key in SMS_CHANNEL_KEYS[sms_provider]]
    lines.append(f"SMS_DRY_RUN={'1' if sms_dry_run else '0'}")
    if sms_provider == "pnvs":
        lines += [
            "# 赠送签名 + 赠送模板**必须配套**（阿里云不允许赠送签名配自定义模板）；",
            "# 只支持中国大陆手机号（CountryCode=86）。核验免费，只收发送费。",
            "# 签名名与模板编号从号码认证服务控制台「赠送签名/赠送模板配置」页原样抄。",
        ]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site-host", required=True, help="公开域名；没有域名时用 <公网IP>.sslip.io")
    parser.add_argument("--source", type=Path, default=ROOT / ".env", help="取值来源，默认本机 .env")
    parser.add_argument("--out", type=Path, default=ROOT / ".env.production")
    parser.add_argument("--sms-provider", default="pnvs", choices=sorted(SMS_CHANNEL_KEYS))
    parser.add_argument("--sms-dry-run", action="store_true", help="只构造请求不真发（核对接签名/模板参数用）")
    parser.add_argument("--allow-missing-endpoints", action="store_true", help="缺 MCP 端点时只警告不中止")
    parser.add_argument("--rotate-guest-secret", action="store_true", help="强制换掉 GUEST_COOKIE_SECRET（所有访客身份重置）")
    args = parser.parse_args(argv)

    source = _parse_env(args.source)
    if not source:
        print(f"读不到任何配置：{args.source} 不存在或为空", file=sys.stderr)
        return 2

    existing = _parse_env(args.out)
    guest_secret = existing.get("GUEST_COOKIE_SECRET", "").strip()
    if args.rotate_guest_secret or len(guest_secret) < 32:
        guest_secret = secrets.token_urlsafe(36)
        kept = False
    else:
        kept = True

    missing = _required_missing(source)
    if missing and not args.allow_missing_endpoints:
        print("本机配置里缺少 config.yaml 点名要用的 MCP 端点，拒绝生成：", file=sys.stderr)
        for name in missing:
            print(f"  - {name}", file=sys.stderr)
        print("\n逐个补齐后再跑；确实不用那个工具就加 --allow-missing-endpoints。", file=sys.stderr)
        return 3

    content = build(source, args.site_host.strip(), args.sms_provider, args.sms_dry_run, guest_secret)
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", args.site_host.strip()):
        print(f"SITE_HOST 形状可疑：{args.site_host!r}", file=sys.stderr)
        return 4

    args.out.write_text(content, encoding="utf-8")
    os.chmod(args.out, 0o600)

    # 回读断言：写进去了、且环境名与 config.yaml 对得上。
    written = _parse_env(args.out)
    bad = [name for name in _referenced_endpoints() if not written.get(name, "").strip()]
    tail = "（其中有缺失，已按 --allow-missing-endpoints 放行）" if bad else ""
    print(f"已生成 {args.out}")
    print(f"  站点：{args.site_host}")
    print(f"  搬来的键：{len(written)} 个；MCP 端点 {len(_referenced_endpoints())} 个点名项全齐{tail}")
    print(f"  短信通道：{args.sms_provider}，dry-run={'开' if args.sms_dry_run else '关'}")
    print(f"  访客密钥：{'沿用原有的（未轮换）' if kept else '本次新生成'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

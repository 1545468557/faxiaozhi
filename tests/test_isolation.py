"""多人隔离（2026-09-27 改造）：登录门 + 数据归属。

这里覆盖的每一件事，改造前都**真实存在**：
1. 8010（旧后端）~25 个业务接口零鉴权，`/api/exports` 能列出并下载**全部**导出文件；
2. 8011（v3 问答）**整段没有鉴权** —— 经 BFF 谁都能用、不用登录；
3. v3 的 `ensure_session` 认下任意 sid（拿到别人的会话 id 就能读记录、往里追加消息），
   `list_sessions` 返回**所有人**的会话。

约定：
- 用临时库（`AUTH_DB` / `V3_DB` / `SMS_OUTBOX` 全指向 tmp），不碰真实数据；
- 验证码从 mock 通道的 outbox 文件里读（测真实链路，不是调内部函数）；
- **本文件显式把登录门打开**（`AUTH_REQUIRE_LOGIN=1`）。conftest 默认是关的，
  原因见那里的注释：既有用例测的是业务逻辑，不是登录门。
"""

from __future__ import annotations

import importlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.auth import guard
from app.config import get_config
from app.errors import ApiError

PHONE_A = "13812345678"
PHONE_B = "13900001111"

COOKIE = "fzx_session"


@pytest.fixture()
def boxes(tmp_path, monkeypatch):
    """两套 app（8010 / 8011）+ 账号底座，全部用临时库；**登录门打开**。"""
    monkeypatch.setenv("AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("SMS_OUTBOX", str(tmp_path / "sms-outbox.jsonl"))
    monkeypatch.setenv("SMS_PROVIDER", "mock")
    monkeypatch.setenv("AUTH_CODE_MIN_INTERVAL", "0")
    monkeypatch.setenv("AUTH_CODE_MAX_PER_HOUR", "100")
    monkeypatch.setenv("AUTH_CODE_MAX_PER_DAY", "100")
    monkeypatch.setenv("V3_DB", str(tmp_path / "v3.sqlite3"))
    monkeypatch.setenv("AUTH_REQUIRE_LOGIN", "1")

    import app.auth.core as core

    importlib.reload(core)

    from app import server as legacy

    importlib.reload(legacy)

    import app_v3.main as v3

    importlib.reload(v3)  # reload 会把模块级 _store 重置为 None，从而用上 V3_DB

    return legacy, v3, core, tmp_path


# ------------------------------------------------------------------ 小工具


def _latest_code(tmp_path, purpose: str) -> str:
    path = tmp_path / "sms-outbox.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    matched = [r for r in records if r["purpose"] == purpose]
    assert matched, f"没有发出 purpose={purpose} 的验证码"
    return matched[-1]["code"]


def _sign_in(legacy, core, tmp_path, phone: str) -> str:
    """在 8010 上注册一个账号，返回它的 `fzx_session` cookie 值。

    v3 进程里**没有** `/api/auth/*`，所以登录只能在 8010 做，再把 cookie 种到 v3 的客户端上。
    这也顺带说明了 cookie 是跨这两个进程唯一的登录凭据。
    """
    http = TestClient(legacy.app)
    invite = core.generate_invite("隔离用例")["code"]
    sent = http.post("/api/auth/code", json={"phone": phone, "purpose": "register"})
    assert sent.status_code == 200, sent.text
    created = http.post(
        "/api/auth/register",
        json={"invite_code": invite, "phone": phone, "code": _latest_code(tmp_path, "register")},
    )
    assert created.status_code == 200, created.text
    token = http.cookies.get(COOKIE)
    assert token, "注册没有下发 fzx_session"
    return str(token)


def _with_cookie(app, token: str) -> TestClient:
    client = TestClient(app)
    client.cookies.set(COOKIE, token)
    return client


def _sse_session_id(body: str) -> str:
    """从 SSE 文本里取 `session_id`（meta 事件里带）。"""
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("session_id"):
                return str(data["session_id"])
    raise AssertionError("SSE 里没有 session_id：" + body[:300])


def _ask(client: TestClient, message: str, sid: str | None = None) -> str:
    payload: dict[str, object] = {"message": message}
    if sid:
        payload["session_id"] = sid
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200, response.text
    return _sse_session_id(response.text)


def _sids(client: TestClient) -> set[str]:
    response = client.get("/api/sessions")
    assert response.status_code == 200, response.text
    return {str(item["session_id"]) for item in response.json()["sessions"]}


# ------------------------------------------------------------------ 1. 8010 登录门


def test_legacy_blocks_anonymous(boxes):
    """改造前 `/api/session` 与 `/api/exports` 都是零鉴权，任何人都能建会话、列全部导出。"""
    legacy, _v3, _core, _tmp = boxes
    anon = TestClient(legacy.app)

    assert anon.post("/api/session", json={}).status_code == 401
    assert anon.get("/api/exports").status_code == 401
    assert anon.get("/api/metrics").status_code == 401
    assert anon.get("/api/statutes/status").status_code == 401

    # 白名单：登录本身 + 探活 / 能力声明
    assert anon.get("/api/bootstrap").status_code == 200
    assert anon.post("/api/auth/code", json={"phone": PHONE_A, "purpose": "login"}).status_code == 200


def test_legacy_gate_body_is_a_clean_401(boxes):
    """未登录的响应要说人话，且**不能**顺手把内部细节带出去。"""
    legacy, _v3, _core, _tmp = boxes
    body = TestClient(legacy.app).get("/api/exports").json()
    assert body["error"]["code"] == "auth_required"
    assert "api/auth" not in json.dumps(body)


# ------------------------------------------------------------------ 2. 8011 登录门


def test_v3_blocks_anonymous(boxes):
    """改造前 v3 整段没有鉴权 —— 连登录都不用就能问答。"""
    _legacy, v3, _core, _tmp = boxes
    anon = TestClient(v3.app)

    assert anon.post("/api/chat", json={"message": "你好"}).status_code == 401
    assert anon.get("/api/sessions").status_code == 401
    assert anon.get("/api/chat/whatever").status_code == 401
    assert anon.delete("/api/session/whatever").status_code == 401

    # 免登录白名单保持可用
    assert anon.get("/api/health").status_code == 200
    assert anon.get("/api/bootstrap").status_code == 200


# ------------------------------------------------------------------ 3. v3 会话归属


def test_v3_sessions_are_invisible_across_accounts(boxes):
    legacy, v3, core, tmp_path = boxes
    a = _with_cookie(v3.app, _sign_in(legacy, core, tmp_path, PHONE_A))
    b = _with_cookie(v3.app, _sign_in(legacy, core, tmp_path, PHONE_B))

    sid = _ask(a, "A 的问题：租房押金不退怎么办？")

    # A 自己看得见
    assert sid in _sids(a)
    assert a.get(f"/api/chat/{sid}").status_code == 200

    # B 看不见 A 的会话：列表里没有、直取 404、删不掉
    assert sid not in _sids(b)
    assert b.get(f"/api/chat/{sid}").status_code == 404
    assert b.delete(f"/api/session/{sid}").json()["deleted"] is False

    # **最关键的回归**：B 拿 A 的 sid 发消息，不能被"接管"
    hijack = b.post("/api/chat", json={"message": "蹭一下", "session_id": sid})
    assert hijack.status_code == 404, "改造前这里会成功，等于接管别人的会话"

    # 而且 A 的记录没有被动过
    timeline = a.get(f"/api/chat/{sid}").json()["messages"]
    assert [m["role"] for m in timeline] == ["user", "assistant"]
    assert "蹭一下" not in json.dumps(timeline, ensure_ascii=False)

    # B 自己开新会话是正常的，两边互不干扰
    b_sid = _ask(b, "B 的问题：买卖合同违约金怎么算？")
    assert b_sid != sid
    assert b_sid in _sids(b) and b_sid not in _sids(a)


def test_v3_delete_only_touches_own_session(boxes):
    legacy, v3, core, tmp_path = boxes
    a = _with_cookie(v3.app, _sign_in(legacy, core, tmp_path, PHONE_A))
    b = _with_cookie(v3.app, _sign_in(legacy, core, tmp_path, PHONE_B))

    sid = _ask(a, "A 的会话")
    assert b.delete(f"/api/session/{sid}").json()["deleted"] is False
    assert a.get(f"/api/chat/{sid}").status_code == 200  # A 的还在
    assert a.delete(f"/api/session/{sid}").json()["deleted"] is True
    assert a.get(f"/api/chat/{sid}").status_code == 404


# ------------------------------------------------------------------ 4. 8010 会话归属


def test_legacy_sessions_are_invisible_across_accounts(boxes):
    """8010 的会话是纯内存态，但归属同样要在 `STORE.get()` 上校验。"""
    legacy, _v3, core, tmp_path = boxes
    a = _with_cookie(legacy.app, _sign_in(legacy, core, tmp_path, PHONE_A))
    b = _with_cookie(legacy.app, _sign_in(legacy, core, tmp_path, PHONE_B))

    created = a.post("/api/session", json={"branch": "research"})
    assert created.status_code == 200, created.text
    sid = created.json()["session_id"]

    assert a.get(f"/api/session/{sid}/state").status_code == 200

    denied = b.get(f"/api/session/{sid}/state")
    assert denied.status_code == 404, "改造前这里会 200，把别人的会话整个交出去"
    assert denied.json()["error"]["code"] == "session_not_found"

    # 归属不符要用统一的「不存在」，不能出现「存在但不是你的」这种回答（那本身就是信息泄漏）
    missing = b.get("/api/session/s_does_not_exist/state")
    assert missing.status_code == denied.status_code
    assert missing.json() == denied.json()


# ------------------------------------------------------------------ 5. store 层（不经过 HTTP）


def test_v3_ensure_session_refuses_foreign_sid(tmp_path):
    from app_v3.store import SessionOwnedByOther, Store

    store = Store(tmp_path / "v3.sqlite3")
    mine = store.create_session("我的会话", user_id="1")

    # 自己的 sid 照常复用
    assert store.ensure_session(mine["session_id"], user_id="1")["session_id"] == mine["session_id"]
    # 别人的 sid 一律拒
    with pytest.raises(SessionOwnedByOther):
        store.ensure_session(mine["session_id"], user_id="2")
    # 不存在的 sid 仍然能建（前端自己生成 id 的场景）
    fresh = store.ensure_session("brand_new_sid", user_id="1")
    assert fresh["session_id"] == "brand_new_sid" and store.user_of("brand_new_sid") == "1"


def test_v3_queries_are_filtered_by_owner(tmp_path):
    from app_v3.store import Store

    store = Store(tmp_path / "v3.sqlite3")
    a = store.create_session("A", user_id="1")
    b = store.create_session("B", user_id="2")
    store.append_message(a["session_id"], "user", "A 的正文", user_id="1")

    assert [s["session_id"] for s in store.list_sessions(user_id="1")] == [a["session_id"]]
    assert [s["session_id"] for s in store.list_sessions(user_id="2")] == [b["session_id"]]
    # 别人的消息读出来是空的（不抛、也不确认存在性）
    assert store.messages(a["session_id"], user_id="2") == []
    assert [m["content"] for m in store.messages(a["session_id"], user_id="1")] == ["A 的正文"]
    # 删不掉别人的
    assert store.delete_session(a["session_id"], user_id="2") is False
    assert store.user_of(a["session_id"]) == "1"


def test_store_migrates_legacy_db_without_owner_column(tmp_path):
    """改造前建好的 `v3.sqlite3` 没有 `user_id` 列：要能补上，且**不猜**历史会话归谁。"""
    from app_v3.store import ANON, Store

    path = tmp_path / "legacy-v3.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
          created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE messages (
          id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,
          content TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]',
          created_at REAL NOT NULL, seq INTEGER NOT NULL
        );
        """
    )
    conn.execute("INSERT INTO sessions (id, title, created_at, updated_at) VALUES ('old','改造前的会话',1.0,1.0)")
    conn.commit()
    conn.close()

    store = Store(path)
    assert store.user_of("old") == ANON, "存量无主会话应落 _anonymous，不该被猜给某个人"
    assert store.get_session("old")["title"] == "改造前的会话"


# ------------------------------------------------------------------ 6. 导出文件归属


def _owner_of(client: TestClient) -> str:
    user = client.get("/api/auth/me").json()["user"]
    return guard.owner_key(user)


def _plant_export(owner: str, name: str, text: str = "内容") -> None:
    """直接在某个归属的导出子目录里放一个文件（不必真跑一遍研究）。"""
    directory = guard.owner_dir_for(get_config().exports_dir, owner)
    (directory / name).write_text(text, encoding="utf-8")


def test_exports_download_requires_login(boxes):
    """`/exports/{name}` 挂在 `/api` 之外 —— 改造前它在登录门之外，知道文件名就能下。"""
    legacy, _v3, _core, _tmp = boxes
    anon = TestClient(legacy.app)
    assert anon.get("/exports/whatever.docx").status_code == 401
    assert anon.get("/api/exports").status_code == 401


def test_exports_are_isolated_per_account(boxes):
    legacy, _v3, core, tmp_path = boxes
    a = _with_cookie(legacy.app, _sign_in(legacy, core, tmp_path, PHONE_A))
    b = _with_cookie(legacy.app, _sign_in(legacy, core, tmp_path, PHONE_B))

    owner_a, owner_b = _owner_of(a), _owner_of(b)
    assert owner_a != owner_b
    _plant_export(owner_a, "A的研究报告.docx")
    _plant_export(owner_b, "B的研究报告.docx")

    # 列表只看得到自己的
    assert [i["name"] for i in a.get("/api/exports").json()["items"]] == ["A的研究报告.docx"]
    assert [i["name"] for i in b.get("/api/exports").json()["items"]] == ["B的研究报告.docx"]

    # 下载：自己的能下，别人的 404（改造前这里会直接下到别人的报告）
    assert a.get("/exports/A的研究报告.docx").status_code == 200
    assert b.get("/exports/A的研究报告.docx").status_code == 404
    # 「不是自己的」与「不存在」回同一个 404，不确认别人导出过什么
    assert b.get("/exports/A的研究报告.docx").json() == b.get("/exports/nope.docx").json()


async def test_exports_download_rejects_traversal(boxes):
    """越界必须被挡住。断言的是**目录外的文件拿不到**，不是某个特定状态码/错误体 ——
    因为 HTTP 客户端与框架会先把 `..` 归一化掉，用「响应长什么样」当判据会假绿。
    """
    legacy, _v3, core, tmp_path = boxes
    a = _with_cookie(legacy.app, _sign_in(legacy, core, tmp_path, PHONE_A))
    owner = _owner_of(a)

    # 在**归属子目录之外**放一个文件，它就是不该被拿到的东西
    (get_config().exports_dir / "secret.docx").write_text("不该被下载到", encoding="utf-8")

    # 直接打处理函数：不经过 HTTP 层的路径归一化，测的是我们自己的解析
    from app import server as server_module

    previous = guard.current_owner()
    guard.set_current_owner(owner)
    try:
        with pytest.raises(ApiError) as caught:
            await server_module.download("../secret.docx")
        assert caught.value.code == "path_escape"
    finally:
        guard.set_current_owner(previous)

    # 走 HTTP 也不能把目录外的内容吐出来（换几种写法各试一遍）
    for evil in ("../secret.docx", "%2e%2e%2fsecret.docx", "....//secret.docx", "..%5Csecret.docx"):
        body = a.get(f"/exports/{evil}").text
        assert "不该被下载到" not in body, f"{evil} 把目录外的文件读出来了"

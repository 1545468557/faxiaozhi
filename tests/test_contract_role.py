"""材料角色白名单：**合同角色必须能显式指定**（阶段 3-4 修掉契约与实现的差异）。

背景（3-1 全局一览时发现）：交接契约写「上传 `role` 支持 case / statute / contract」，
但后端上传与改角色接口只放行前两个。合同材料当时只能靠正文自动识别兜住——
一旦识别成「案例」（例如标题里没有"合同"字样），用户**没有任何办法改回合同角色**，
审查会直接失败在「请先上传合同」。本文件锁住修复后的行为：

- 上传时可以显式 `role=contract`；
- 改角色接口接受 `surface: case / statute / contract`；
- 非法角色仍然拒绝（422，统一错误结构）；
- 改成合同角色后，该材料不再出现在类案研究的候选池里（候选只收案例角色）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.server import STORE, app
from tests.test_contract import CONTRACT_TEXT
from tests.test_materials import CASE_TEXT

client = TestClient(app)


def _create(branch: str = "contract") -> tuple[str, str]:
    created = client.post("/api/session", json={"branch": branch}).json()
    return created["session_id"], ""


def test_upload_accepts_explicit_contract_role():
    sid, _ = _create()
    response = client.post(
        f"/api/session/{sid}/upload",
        files=[("files", ("租赁合同.txt", CONTRACT_TEXT.encode(), "text/plain"))],
        data={"role": "contract"},
    )
    body = response.json()

    assert response.status_code == 200, body
    assert body["materials"], "合同材料应被收录"
    material = body["materials"][0]
    assert material["role"] == "contract"
    assert material["role_source"] == "manual"

    snapshot = client.get(f"/api/session/{sid}/state").json()
    contract_sources = [s for s in snapshot["sources"] if s["kind"] == "contract"]
    assert contract_sources, "依据池里必须有一条合同角色的材料"


def test_upload_contract_role_keeps_it_out_of_case_candidates():
    """合同不是案例：不得混进类案研究的候选池。"""
    sid, _ = _create(branch="research")
    client.post(
        f"/api/session/{sid}/upload",
        files=[("files", ("租赁合同.txt", CONTRACT_TEXT.encode(), "text/plain"))],
        data={"role": "contract"},
    )
    session = STORE.get(sid)
    from app.workflows.research import assemble_candidates

    assert assemble_candidates(session, 30) == []


def test_role_endpoint_can_switch_material_to_contract():
    """自动识别成案例的合同，必须能手动改成合同角色（改角色会作废原核验）。"""
    sid, _ = _create(branch="contract")
    uploaded = client.post(
        f"/api/session/{sid}/upload",
        files=[("files", ("合同正文.txt", CONTRACT_TEXT.encode(), "text/plain"))],
        data={"role": "case"},
    ).json()
    source_id = uploaded["materials"][0]["source_id"]

    client.post(f"/api/session/{sid}/material/{source_id}/verify")
    assert STORE.get(sid).source_pool[source_id].user_verified is True

    response = client.post(
        f"/api/session/{sid}/material/{source_id}/role", json={"role": "contract"}
    )
    assert response.status_code == 200, response.text
    source = STORE.get(sid).source_pool[source_id]
    assert source.kind == "contract"
    assert source.user_verified is False, "改角色必须作废原核验（避免改完直接引用）"


def test_role_endpoint_rejects_unknown_role_with_unified_error():
    sid, _ = _create()
    uploaded = client.post(
        f"/api/session/{sid}/upload",
        files=[("files", ("材料.txt", CASE_TEXT.encode(), "text/plain"))],
    ).json()
    source_id = uploaded["materials"][0]["source_id"]

    response = client.post(
        f"/api/session/{sid}/material/{source_id}/role", json={"role": "something_else"}
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "invalid_request"
    assert "contract" in body["error"]["message"]

"""HTTP 层：REST + SSE（PRD 8.4 契约）。错误统一 `{"error":{"code","message"}}`。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .config import get_config
from .errors import ApiError
from .knowledge import KnowledgeBase
from .llm import ToolCall
from .loop import install_default_hooks, user_prompt_submit
from .materials import extract_identifier, mark_material_corroboration
from .models import Status
from .observability import get_observability
from .session import STORE
from .tools import dispatch_tool
from .workflows import WORKFLOW_FOR_BRANCH, launch
from .workflows.research import assemble_candidates
from .workflows.runtime import STANCES

LOG = logging.getLogger("faxiaozhi.server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

KB = KnowledgeBase(get_config())


@asynccontextmanager
async def lifespan(app: FastAPI):
    install_default_hooks()
    cfg = get_config()
    cfg.self_check()
    STORE.max_concurrent = int(cfg.get("limits.max_concurrent_sessions", 4))
    STORE.ttl_seconds = int(cfg.get("limits.session_ttl_seconds", 1800))
    LOG.info("会话上限 %s，空闲 %s 秒自动释放", STORE.max_concurrent, STORE.ttl_seconds)
    yield


app = FastAPI(title="法小智", version="0.1.0", lifespan=lifespan)


def _err(exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.body())


@app.exception_handler(ApiError)
async def _api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return _err(exc)


@app.exception_handler(Exception)
async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
    LOG.exception("未处理异常")
    return _err(ApiError("internal_error"))


# ------------------------------------------------------------------ 基础


@app.get("/api/bootstrap")
async def bootstrap() -> dict[str, Any]:
    cfg = get_config()
    check = cfg.self_check()
    return {
        "agent": {"name": cfg.get("agent.name", "法小智"), "id": cfg.get("agent.id", "")},
        "modules": [
            {"id": "research", "name": "类案检索与研究", "enabled": True},
            {"id": "consult", "name": "法律咨询", "enabled": True},
            {"id": "contract", "name": "合同审查", "enabled": True},
        ],
        "materials": {
            "max_files_per_upload": int(cfg.get("materials.max_files_per_upload", 20)),
            "role_default": str(cfg.get("materials.role_default", "case")),
            "max_supplement_rounds": int(
                cfg.get("sources.supplement.max_supplement_rounds", 2)
            ),
        },
        "model": check["model"],
        "mcp": check["mcp"],
        "policy": check["policy"],
        "limits": cfg.get("limits", {}),
        "disclosure": cfg.get("agent.disclosure", ""),
    }


@app.get("/api/library")
async def library_status() -> dict[str, Any]:
    """本地依据库状态（2-3 新增）。**不返回任何地址、密钥或条目正文**。"""
    from .library import get_library

    library = get_library()
    stats = library.stats()
    stats.pop("dir", None)                     # 连目录路径也不对外暴露
    cfg = get_config()
    providers: list[dict[str, Any]] = []
    for kind in ("case", "statute"):
        for name in cfg.get(f"sources.{kind}.providers", []) or []:
            if name == "pkulaw_mcp":
                available = bool(cfg.mcp_endpoints().get(f"search_{kind}s"))
            else:
                available = True
            providers.append(
                {"kind": kind, "name": name, "enabled": available}
            )
    return {**stats, "providers": providers}


@app.get("/api/metrics")
async def metrics(source: str | None = None) -> dict[str, Any]:
    """source=real 只统计真实运行；source=test 只统计自动化测试；不传取全部。

    2-2 新增来源维度：实测发现跑测试会污染真实指标文件，导致北极星指标
    （引用可溯源率等）不可信。看真实成绩请用 /api/metrics?source=real。
    """
    if source is not None and source not in {"real", "test", "unknown"}:
        raise ApiError("invalid_source", "source 只能是 real / test / unknown。")
    return get_observability().summary(source=source)


# ------------------------------------------------------------------ 会话


@app.post("/api/session")
async def create_session(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = payload or {}
    session = STORE.create(branch=body.get("branch", "research"), purpose=body.get("purpose"))
    get_observability().metric(event="session_created", session_id=session.id, branch=session.branch)
    if session.queued:
        return {
            "session_id": session.id,
            "queued": True,
            "status": "queued",
            "message": "当前同时在跑的会话已达上限，已排队，请稍后重试。",
        }
    return {"session_id": session.id, "queued": False, "created_at": time.time()}


@app.get("/api/session/{session_id}/state")
async def session_state(session_id: str) -> dict[str, Any]:
    return STORE.get(session_id).snapshot()


@app.post("/api/session/{session_id}/message")
async def send_message(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    session = STORE.get(session_id)
    STORE.sweep()
    if STORE.active_count() > STORE.max_concurrent:
        raise ApiError("concurrency_queued")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ApiError("empty_input")
    # 补充事实保存在 consult.facts；检索主题保留原问题，不能被最后一句补充替换。
    if session.branch != "consult" or not session.consult.get("awaiting"):
        session.topic = text
    session.conditions = dict(payload.get("conditions") or {})
    session.purpose = payload.get("purpose") or session.purpose
    user_prompt_submit(session, text)

    # 2-5：分支决定 workflow（PRD 3.5.1）。咨询把问题原文放在 args 里，
    # 但 save_state 会把它脱敏——问题里可能含当事人具体事实（C-12）。
    workflow = WORKFLOW_FOR_BRANCH.get(session.branch, "legal-research")
    if session.branch == "consult":
        args = {"question": text, "consult": True}
    else:
        args = {"topic": text, "conditions": session.conditions}
    cfg = get_config()
    scenario = str(payload.get("scenario") or "clean")
    task = asyncio.create_task(
        launch(session, cfg, KB, args, workflow=workflow, scenario=scenario)
    )
    session.run_tasks = getattr(session, "run_tasks", [])
    session.run_tasks.append(task)          # 保持强引用，避免被回收
    await asyncio.sleep(0)                  # 让任务先跑起来，尽快发首帧
    return {"run_id": session.run_id, "status": "accepted", "session_id": session.id}


def _resume_workflow(state: dict[str, Any], branch: str | None = None) -> tuple[str, str]:
    """由存档/原会话确定业务分支，禁止恢复时意外进入默认研究流程。"""
    saved = state.get("workflow")
    if saved:
        stored_branch = next(
            (name for name, workflow in WORKFLOW_FOR_BRANCH.items() if workflow == saved), None
        )
        if stored_branch is None:
            raise ApiError("run_expired", "该任务的业务类型不受支持，请重新发起。")
        if branch and branch != stored_branch:
            raise ApiError("invalid_request", "恢复的业务类型与原任务不一致。")
        return stored_branch, str(saved)
    branch = branch or "research"
    if branch not in WORKFLOW_FOR_BRANCH:
        raise ApiError("invalid_request", "未知的业务类型。")
    return branch, WORKFLOW_FOR_BRANCH[branch]


@app.post("/api/session/{session_id}/resume")
async def resume_session(session_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    session = STORE.get(session_id)
    run_id = str((payload or {}).get("run_id") or session.run_id or "")
    if not run_id:
        raise ApiError("run_not_found")
    state_file = get_config().runs_dir / f"{run_id}.json"
    if not state_file.exists():
        raise ApiError("run_not_found")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    _, workflow = _resume_workflow(state, session.branch)
    args = state.get("args", {})
    task = asyncio.create_task(
        launch(session, get_config(), KB, args, workflow=workflow, run_id=run_id)
    )
    session.run_tasks = getattr(session, "run_tasks", [])
    session.run_tasks.append(task)
    await asyncio.sleep(0)
    return {"run_id": run_id, "status": "accepted", "session_id": session.id}


@app.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """服务重启后按 run_id 恢复：新开会话 + 从 journal 重放（已完成步骤不重跑）。"""
    state_file = get_config().runs_dir / f"{run_id}.json"
    if not state_file.exists():
        raise ApiError("run_not_found")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    branch, workflow = _resume_workflow(state, (payload or {}).get("branch"))
    session = STORE.create(branch=branch)
    session.run_id = run_id
    session.topic = str((state.get("args") or {}).get("topic", ""))
    session.conditions = dict((state.get("args") or {}).get("conditions") or {})
    args = dict(state.get("args") or {"topic": session.topic, "conditions": session.conditions})
    task = asyncio.create_task(
        launch(session, get_config(), KB, args, workflow=workflow, run_id=run_id)
    )
    session.run_tasks = getattr(session, "run_tasks", [])
    session.run_tasks.append(task)
    await asyncio.sleep(0)
    return {"session_id": session.id, "run_id": run_id, "status": "resumed"}


@app.get("/api/session/{session_id}/degradation")
async def degradation(session_id: str) -> dict[str, Any]:
    """本次会话的降级记录（2-2 新增）。界面据此告知用户「哪一步、什么原因、能否重试」。

    隐私：`endpoint_alias` 是脱敏别名，真实地址与 Token 永不出现。
    """
    session = STORE.get(session_id)
    return {
        "session_id": session.id,
        "summary": session.degradation_summary(),
        "degradations": [d.event() for d in session.degradations],
        "failed_step": session.failed_step,
        "failed_reason": session.failed_reason,
        "retry_count": session.retry_count,
        "can_retry": session.can_retry,
    }


@app.post("/api/session/{session_id}/retry")
async def retry_session(session_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """从失败步骤继续（2-2 新增，兑现失败文案里的「可点击重试」）。

    **已完成的步骤命中 journal 存档，不重跑、不重复计费**（有回归测试）。
    不接受前端指定「从哪一步开始」，一律由服务端按 failed_step 决定（防越权跳步）。
    """
    session = STORE.get(session_id)
    if not session.failed_step:
        raise ApiError("no_failed_step")
    run_id = session.run_id or ""
    if not run_id:
        raise ApiError("run_not_found")
    state_file = get_config().runs_dir / f"{run_id}.json"
    if not state_file.exists():
        raise ApiError("run_not_found")
    tasks = getattr(session, "run_tasks", [])
    if any(not task.done() for task in tasks):
        raise ApiError("session_busy")
    max_retries = int(get_config().get("limits.max_retries_per_run", 3))
    if session.retry_count >= max_retries:
        raise ApiError("retry_exhausted")

    state = json.loads(state_file.read_text(encoding="utf-8"))
    _, workflow = _resume_workflow(state, session.branch)
    args = state.get("args", {})
    resumed_from = session.failed_step
    session.retry_count += 1
    get_observability().metric(
        event="retry",
        session_id=session.id,
        run_id=run_id,
        step=resumed_from,
        attempt=session.retry_count,
    )
    task = asyncio.create_task(
        launch(session, get_config(), KB, args, workflow=workflow, run_id=run_id)
    )
    session.run_tasks = tasks
    session.run_tasks.append(task)
    await asyncio.sleep(0)
    return {
        "session_id": session.id,
        "run_id": run_id,
        "status": "accepted",
        "resumed_from": resumed_from,
        "attempt": session.retry_count,
        "max_attempts": max_retries,
    }


@app.post("/api/session/{session_id}/checkpoint")
async def submit_checkpoint(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    session = STORE.get(session_id)
    kind = str(payload.get("kind") or "")
    value = dict(payload.get("payload") or {})
    if session.checkpoint_kind != kind:
        raise ApiError("checkpoint_mismatch", "当前没有等待该确认点。")

    # 2-6：立场确认（与样本确认是**不同**的人工门，不得混用同一套逻辑）
    if kind == "stance_confirm":
        stance = str(value.get("stance") or "").strip()
        if stance not in STANCES:
            raise ApiError(
                "invalid_request", "立场只能是 party_a（甲方）/ party_b（乙方）/ neutral（其他）。"
            )
        session.checkpoint_value = {"stance": stance}
        session.checkpoint_waiter.set()
        get_observability().metric(
            event="stance_confirmed",
            session_id=session.id,
            run_id=session.run_id,
            stance=stance,
        )
        return {"status": "accepted", "kind": kind, "stance": stance}

    if kind != "sample_confirm":
        raise ApiError("invalid_request", f"未知的确认点：{kind}")

    confirmed = [str(x) for x in value.get("confirmed", [])]
    excluded = [str(x) for x in value.get("excluded", [])]
    confirmed = [c for c in confirmed if c in session.sample.candidates]
    if not confirmed and not excluded:
        raise ApiError("empty_sample")
    session.checkpoint_value = {"confirmed": confirmed, "excluded": excluded}
    session.checkpoint_waiter.set()
    get_observability().metric(
        event="sample_change",
        session_id=session.id,
        run_id=session.run_id,
        count=len(confirmed),
    )
    return {"status": "accepted", "confirmed": len(confirmed), "excluded": len(excluded)}


@app.get("/api/session/{session_id}/events")
async def session_events(session_id: str) -> StreamingResponse:
    session = STORE.get(session_id)
    cfg = get_config()
    check = cfg.self_check()

    async def stream():
        meta = {
            "session_id": session.id,
            "run_id": session.run_id,
            "branch": session.branch,
            "model": {"provider": check["model"]["provider"], "is_stub": check["model"]["is_stub"]},
            "mcp_available": check["mcp"]["configured"],
            "demo_mode": check["policy"]["allow_synthetic"],
            "warning": _warning(check),
        }
        yield _sse("meta", meta)
        while True:
            try:
                item = await asyncio.wait_for(session.events.get(), timeout=15)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            yield _sse(item["event"], item["data"])
            if item["event"] in {"done", "error"}:
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ------------------------------------------------------------------ 材料


@app.post("/api/session/{session_id}/upload")
async def upload(
    session_id: str,
    file: Annotated[UploadFile | None, File()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
    role: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """上传材料（2-4：支持**一次多个文件**；单文件旧用法保持兼容）。

    - 逐个解析、逐个核验；**单个失败不阻断其余**，失败项单独列出原因（验收 C3）；
    - **全部失败**才返回 422（沿用统一错误结构）；
    - 内容仅在本会话临时处理，不落盘（C-12）。
    """
    session = STORE.get(session_id)
    incoming: list[UploadFile] = list(files or [])
    if file is not None:
        incoming.insert(0, file)
    if not incoming:
        raise ApiError("empty_input", "请先选择要上传的材料文件。")
    cfg = get_config()
    max_files = int(cfg.get("materials.max_files_per_upload", 20))
    if len(incoming) > max_files:
        raise ApiError(
            "too_many_files",
            f"单次最多上传 {max_files} 个文件（本次 {len(incoming)} 个）。请分批上传。",
        )
    # 角色白名单与 materials.ROLES 对齐（含 contract，阶段 3-4 修掉契约与实现的差异）
    requested_role = role if role in {"case", "statute", "contract"} else ""
    obs = get_observability()
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for item in incoming:
        name = item.filename or "material"
        data = await item.read()
        material_id = "m" + uuid.uuid4().hex[:8]
        session.materials[material_id] = (data, name)
        arguments: dict[str, Any] = {"filename": name, "material_id": material_id}
        if requested_role:
            arguments["role"] = requested_role
        result = dispatch_tool(
            session, ToolCall(id=f"call_parse_{material_id}", name="parse_document", arguments=arguments)
        )
        if result.status is not Status.OK or not result.sources:
            session.materials.pop(material_id, None)          # 失败不留原始字节
            reason = result.error_kind or "unsupported_file"
            rejected.append({"filename": name, "error": reason, "message": result.detail})
            obs.metric(event="material_rejected", session_id=session.id, reason=reason)
            continue
        source = result.sources[0]
        obs.register_material(source.text_fingerprint)
        obs.metric(
            event="material_uploaded",
            session_id=session.id,
            source_id=source.source_id,
            chars=len(source.quote),
            kind=source.kind,
        )
        accepted.append(
            {
                "material_id": material_id,
                "source_id": source.source_id,
                "filename": result.meta.get("filename") or name,
                "role": result.meta.get("role"),
                "role_label": result.meta.get("role_label"),
                "role_source": result.meta.get("role_source"),
                "identifier": source.identifier,
                "identifier_source": result.meta.get("identifier_source"),
                "identifier_missing": bool(result.meta.get("identifier_missing")),
                "chars": len(source.quote),
                "needs_review": True,
                "verified": False,
                "superseded": result.meta.get("superseded") or [],
                "locator_index": result.meta.get("locator_index") or {},
                "detail": result.detail,
            }
        )

    if not accepted:
        first = rejected[0]
        raise ApiError(first["error"], first["message"])

    response: dict[str, Any] = {
        "materials": accepted,
        "rejected": rejected,
        "note": "内容仅在本会话临时处理，不会保存。作为引用依据前需你确认「本人已核验」。",
    }
    if len(accepted) == 1 and not rejected:
        # 向后兼容：单文件上传仍返回旧字段（阶段 2-1 的界面与回归测试依赖）
        one = accepted[0]
        response.update(
            {
                "material_id": one["material_id"],
                "source_id": one["source_id"],
                "identifier": one["identifier"],
                "chars": one["chars"],
                "needs_review": True,
            }
        )
    return response


@app.post("/api/session/{session_id}/supplement")
async def supplement(session_id: str) -> dict[str, Any]:
    """补充检索（2-4）：**只补充，不替换**——用户材料仍是候选池主体。

    - 受 `sources.supplement.max_supplement_rounds` 限制，超限返回 `supplement_exhausted`；
    - 新增来源单独标记为「补充来源」，不改变用户材料的主体地位；
    - 按当前议题检索，不需要模型编排（确定性、不花模型费）。
    """
    session = STORE.get(session_id)
    cfg = get_config()
    max_rounds = int(cfg.get("sources.supplement.max_supplement_rounds", 2))
    session.max_supplement_rounds = max_rounds
    if session.supplement_rounds >= max_rounds:
        raise ApiError(
            "supplement_exhausted",
            f"本次研究的补充检索已达上限（{max_rounds} 次）。请改用已经取得的材料与来源。",
        )
    if not session.topic:
        raise ApiError("empty_input", "请先输入议题并发起研究，再补充检索。")

    before = set(session.source_pool)
    limit = int(cfg.get("limits.max_candidates", 30))
    result = dispatch_tool(
        session,
        ToolCall(
            id="call_supplement",
            name="search_cases",
            arguments={"expression": session.topic, "limit": limit},
        ),
    )
    added = 0
    for source_id, source in session.source_pool.items():
        if source_id in before or source.is_user_material:
            continue
        source.supplement = True
        added += 1

    failed = result.status in (Status.INTERFACE_ERROR, Status.PARSE_ERROR)
    if not failed:
        session.supplement_rounds += 1            # 接口失败不消耗轮数（没拿到数据）
    conflicts = mark_material_corroboration(session.source_pool)
    session.material_primary = bool(session.material_primary)
    candidates = assemble_candidates(session, limit)
    session.candidates = candidates
    if not session.sample.locked:
        session.sample.set_candidates([str(c["source_id"]) for c in candidates])
    get_observability().metric(
        event="supplement",
        session_id=session.id,
        run_id=session.run_id,
        count=added,
        status=result.status.value,
        attempt=session.supplement_rounds,
    )
    return {
        "status": "accepted" if not failed else "failed",
        "run_id": session.run_id,
        "sources_added": added,
        "conflicts": conflicts,
        "candidates": len(candidates),
        "detail": result.detail,
        "supplement": {
            "rounds_used": session.supplement_rounds,
            "max_rounds": max_rounds,
            "remaining": max(0, max_rounds - session.supplement_rounds),
            "material_primary": session.material_primary,
        },
        "note": "补充来源已单独标注，不改变你上传材料的主体地位（矩阵仍以材料为主）。",
    }


@app.post("/api/session/{session_id}/conflict/resolve")
async def resolve_conflict(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """来源冲突的人工裁决（2-4，人工门）。

    只支持 `decision=prefer_user_material`（**不提供「自动以用户材料为准」**，底线 5）；
    裁决不绕过门禁 R3：未核验的材料仍然不得引用。
    """
    session = STORE.get(session_id)
    source_id = str(payload.get("source_id") or "")
    decision = str(payload.get("decision") or "")
    if decision == "use_user_material":
        decision = "prefer_user_material"
    if decision not in {"prefer_user_material", "exclude_source"}:
        raise ApiError(
            "invalid_request",
            "只支持 decision=prefer_user_material；本系统不提供自动放行，也不由模型裁决。",
        )
    if decision == "exclude_source":
        record = session.exclude_conflict(source_id)
    else:
        source = session.source_pool.get(source_id)
        if source is not None and not source.is_user_material:
            raise ApiError("invalid_request", "这不是用户上传材料，不能采用用户材料确认方式。")
        record = session.resolve_conflict(source_id, decision)
    get_observability().metric(
        event="conflict_resolved",
        session_id=session.id,
        run_id=session.run_id,
        source_id=source_id,
        reason=decision,
    )
    if session.pending_claims or session.structured_output:
        from .gates.runner import apply_gates

        apply_gates(session)
    return {
        "status": "resolved",
        "source_id": source_id,
        "identifier": record.get("identifier"),
        "decision": decision,
        "export_blockers": session.export_blockers(),
        "note": "已排除该依据及依赖结论，并重新核验剩余内容。" if decision == "exclude_source" else "依据用户上传材料，未经法宝印证；冲突已由人工确认（导出文档会带此标注）。",
    }


@app.post("/api/session/{session_id}/material/{source_id}/verify")
async def verify_material(session_id: str, source_id: str) -> dict[str, Any]:
    session = STORE.get(session_id)
    source = session.source_pool.get(source_id)
    if source is None or not source.is_user_material or source.superseded:
        raise ApiError("not_found", "材料不存在或已被新版本取代。")
    # 核验同时写回登记表（2-4：单个材料的核验状态要能在清单里看到）
    record = session.verify_material(source_id) or session.material_registry.by_source(source_id)
    get_observability().metric(
        event="material_verified", session_id=session.id, source_id=source_id, kind=source.kind
    )
    if session.pending_claims:
        from .gates.runner import apply_gates

        apply_gates(session)
    return {
        "status": "verified",
        "source_id": source_id,
        "verified": True,
        "identifier": source.identifier,
        "identifier_missing": source.identifier_missing,
        "role": source.kind,
        "version": getattr(record, "version", 1),
        "export_blockers": session.export_blockers(),
    }


@app.post("/api/session/{session_id}/material/{source_id}/role")
async def set_material_role(
    session_id: str, source_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """改材料角色（2-4 Q1：自动推断 + **可改**）。

    角色一变，标识重抽、效力状态重定，**原核验标记作废**（避免「改完角色直接引用」）；
    重新计算与法宝的印证状态，并重装候选池。
    """
    session = STORE.get(session_id)
    source = session.source_pool.get(source_id)
    if source is None or not source.is_user_material or source.superseded:
        raise ApiError("not_found", "材料不存在或已被新版本取代。")
    role = str(payload.get("role") or "")
    if role not in {"case", "statute", "contract"}:
        raise ApiError(
            "invalid_request", "role 只能是 case（案例）/ statute（法条）/ contract（合同）。"
        )
    record = session.material_registry.by_source(source_id)
    filename = str(getattr(record, "filename", "") or source.identifier)
    identifier, ident_source, missing = extract_identifier(source.quote or "", filename, role)
    source.kind = role
    source.identifier = identifier
    source.identifier_missing = missing
    source.effective_status = "不适用（裁判文书）" if role == "case" else "unknown"
    source.user_verified = False                      # 角色变了，原核验不再适用
    source.corroboration = "not_applicable"
    if record is not None:
        record.role = role
        record.role_source = "manual"
        record.identifier = identifier
        record.identifier_source = ident_source
        record.identifier_missing = missing
        record.verified = False
        record.status = "pending_review"
    mark_material_corroboration(session.source_pool)
    limit = int(get_config().get("limits.max_candidates", 30))
    session.candidates = assemble_candidates(session, limit)
    if not session.sample.locked:
        session.sample.set_candidates([str(c["source_id"]) for c in session.candidates])
    get_observability().metric(
        event="material_role_changed", session_id=session.id, source_id=source_id, kind=role
    )
    return {
        "status": "updated",
        "source_id": source_id,
        "role": role,
        "identifier": identifier,
        "identifier_source": ident_source,
        "identifier_missing": missing,
        "user_verified": False,
        "note": "角色已改为手动指定；原核验标记已作废，需重新点「本人已核验」。",
        "export_blockers": session.export_blockers(),
    }


@app.get("/api/session/{session_id}/materials")
async def materials(session_id: str) -> dict[str, Any]:
    """材料清单（2-4）：角色 / 标识 / 核验状态 / 版本 / 定位索引 / 失败原因。不含原文。"""
    session = STORE.get(session_id)
    return {
        "session_id": session.id,
        "materials": session.material_snapshot(),
        "counts": {
            "total": len(session.material_registry.records),
            "active": len(session.material_registry.active()),
            "superseded": len(session.material_registry.superseded()),
            "case": len(session.material_registry.active_case_materials()),
        },
    }


@app.get("/api/session/{session_id}/evidence")
async def evidence(session_id: str) -> dict[str, Any]:
    session = STORE.get(session_id)
    return {
        "sources": [s.brief() for s in session.source_pool.values()],
        "materials": session.material_snapshot(),
        "conflicts": session.conflict_snapshot(),
        "supplement": {
            "rounds_used": session.supplement_rounds,
            "max_rounds": session.max_supplement_rounds,
            "material_primary": session.material_primary,
        },
        "gate_report": session.gate_report.event() if session.gate_report else None,
        "gaps": [g.__dict__ for g in session.gaps],
        "expression_hits": session.expression_hits,
        "export": {"ready": session.export_ready(), "blockers": session.export_blockers()},
    }


@app.post("/api/session/{session_id}/export")
async def export(session_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    session = STORE.get(session_id)
    blockers = session.export_blockers()
    if blockers:
        raise ApiError("export_blocked", "；".join(blockers), reasons=blockers)
    result = dispatch_tool(
        session,
        ToolCall(id="call_export", name="export_docx", arguments=dict(payload or {})),
    )
    if result.status is not Status.OK:
        raise ApiError("export_blocked", result.detail)
    get_observability().metric(event="export", session_id=session.id, run_id=session.run_id, ok=True)
    return {
        "ok": True,
        "filename": result.meta.get("filename"),
        "download_url": result.meta.get("download_url"),
        "detail": result.detail,
    }


@app.post("/api/session/{session_id}/consult/answer")
async def consult_answer(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """回答当前单题，或主动跳过剩余追问。expected_round 防止旧页面重复提交。"""
    session = STORE.get(session_id)
    if session.branch != "consult":
        raise ApiError("invalid_request", "该会话不是法律咨询分支。")
    skip = payload.get("skip_clarification", False)
    if type(skip) is not bool:
        raise ApiError("invalid_request", "skip_clarification 必须是布尔值。")
    raw_facts = payload.get("facts", payload.get("text", ""))
    if not isinstance(raw_facts, str):
        raise ApiError("invalid_request", "facts 必须是文字。")
    text = raw_facts.strip()
    if "expected_round" in payload:
        expected_round = payload["expected_round"]
        if type(expected_round) is not int or expected_round < 1:
            raise ApiError("invalid_request", "expected_round 必须是正整数。")
        if expected_round != session.consult.get("rounds"):
            raise ApiError("invalid_request", "追问轮次已变化，请刷新后回答当前问题。")
    if not (session.consult or {}).get("awaiting"):
        raise ApiError("invalid_request", "当前没有待回答的追问；请直接提交新的问题。")
    tasks = getattr(session, "run_tasks", [])
    if any(not task.done() for task in tasks):
        raise ApiError("session_busy")
    if not skip:
        if not text:
            raise ApiError("empty_input", "请先输入补充的事实，或选择跳过剩余追问。")
        return await send_message(session_id, {"text": text})

    STORE.sweep()
    if STORE.active_count() > STORE.max_concurrent:
        raise ApiError("concurrency_queued")
    # 跳过是控制信号，不伪装成用户事实；可把尚未提交的输入一并交给解答步骤。
    if text:
        user_prompt_submit(session, text)
    task = asyncio.create_task(launch(
        session, get_config(), KB,
        {"question": text, "consult": True, "skip_clarification": True},
        workflow="legal-consult",
    ))
    session.run_tasks = tasks
    session.run_tasks.append(task)
    await asyncio.sleep(0)
    return {"run_id": session.run_id, "status": "accepted", "session_id": session.id}


@app.post("/api/session/{session_id}/consult/export")
async def consult_export(session_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """导出「咨询备忘」（2-5）。导出由**产品层按钮**触发，不经过模型工具池（PRD §3.2.6）。"""
    session = STORE.get(session_id)
    if session.branch != "consult":
        raise ApiError("invalid_request", "该会话不是法律咨询分支。")
    return await export(session_id, payload)


@app.post("/api/session/{session_id}/contract/review")
async def contract_review(session_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """发起合同审查（2-6）。合同通过「上传材料（角色=合同）」进入，不在请求体里传文本。"""
    session = STORE.get(session_id)
    if session.branch != "contract":
        raise ApiError("invalid_request", "该会话不是合同审查分支。")
    STORE.sweep()
    if STORE.active_count() > STORE.max_concurrent:
        raise ApiError("concurrency_queued")
    requirements = (payload or {}).get("requirements", "")
    if not isinstance(requirements, str) or len(requirements) > 2000:
        raise ApiError("invalid_request", "补充审查要求须为不超过2000字的文字。")
    session.contract["requirements"] = requirements.strip()
    cfg = get_config()
    task = asyncio.create_task(
        launch(session, cfg, KB, {"contract": True}, workflow="legal-contract")
    )
    session.run_tasks = getattr(session, "run_tasks", [])
    session.run_tasks.append(task)          # 保持强引用
    await asyncio.sleep(0)
    return {"run_id": session.run_id, "status": "accepted", "session_id": session.id}


@app.post("/api/session/{session_id}/contract/export")
async def contract_export(session_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """导出《合同审查报告》（2-6）。"""
    session = STORE.get(session_id)
    if session.branch != "contract":
        raise ApiError("invalid_request", "该会话不是合同审查分支。")
    from .contract_notes import validate_review_notes
    from .render.docx import render_contract_report
    data = dict(payload or {})
    review = validate_review_notes(data.pop("review", {}), session.contract.get("risks") or [])
    result = await export(session_id, data)
    if review and result.get("filename"):
        render_contract_report(session, get_config().exports_dir / result["filename"], review=review)
    return result


# ---------------------------------------------------------------- 本地法规库（迭代 3）


# ---------------------------------------------------------------- 账号与登录（迭代 2-1）


def _cookie_token(request: Request) -> str | None:
    raw = request.headers.get("cookie") or ""
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name == "fzx_session":
            return value or None
    return None


def _with_cookie(payload: dict[str, Any], token: str) -> JSONResponse:
    from .auth import core as auth

    response = JSONResponse(content=payload)
    response.headers.append("set-cookie", f"fzx_session={token}; {auth.cookie_flags()}")
    return response


@app.post("/api/auth/register")
async def auth_register(request: Request) -> JSONResponse:
    """邀请码注册（密码用 scrypt 存储；库里只存邀请码哈希）。"""
    from .auth import core as auth

    body = await request.json()
    try:
        user = auth.register(str(body.get("invite_code", "")), str(body.get("username", "")), str(body.get("password", "")))
    except auth.AuthError as exc:
        raise ApiError(exc.code, exc.message) from exc
    _, _, token = auth.login(user["username"], str(body.get("password", "")))
    return _with_cookie({"user": user}, token)


@app.post("/api/auth/login")
async def auth_login(request: Request) -> JSONResponse:
    from .auth import core as auth

    body = await request.json()
    try:
        user, _ttl, token = auth.login(str(body.get("username", "")), str(body.get("password", "")))
    except auth.AuthError as exc:
        raise ApiError(exc.code, exc.message) from exc
    return _with_cookie({"user": user}, token)


@app.post("/api/auth/logout")
async def auth_logout(request: Request) -> JSONResponse:
    from .auth import core as auth

    auth.logout(_cookie_token(request))
    response = JSONResponse(content={"ok": True})
    response.headers.append("set-cookie", "fzx_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
    return response


@app.post("/api/auth/password")
async def auth_change_password(request: Request) -> dict[str, Any]:
    """改密码（作废其它设备的登录态，当前这条保留）。"""
    from .auth import core as auth

    body = await request.json()
    try:
        auth.change_password(_cookie_token(request), str(body.get("old_password", "")), str(body.get("new_password", "")))
    except auth.AuthError as exc:
        raise ApiError(exc.code, exc.message) from exc
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(request: Request) -> dict[str, Any]:
    from .auth import core as auth

    try:
        return {"user": auth.me(_cookie_token(request))}
    except auth.AuthError as exc:
        raise ApiError(exc.code, exc.message) from exc


@app.post("/api/admin/invites")
async def admin_invite_create(request: Request) -> dict[str, Any]:
    """生成一次性邀请码（仅 owner）。**明文只返回这一次**。"""
    from .auth import core as auth

    user = auth.session_from_request(_cookie_token(request))
    if user is None:
        raise ApiError("auth_required", "请先登录。")
    if user["role"] != "owner":
        raise ApiError("forbidden", "只有管理员可以生成邀请码。")
    body = await request.json() if await request.body() else {}
    return auth.generate_invite(str(body.get("note", "")), int(body.get("expires_days", 30) or 30))


@app.get("/api/admin/invites")
async def admin_invite_list(request: Request) -> dict[str, Any]:
    """列出邀请码（**不含码原文**，仅 owner）。"""
    from .auth import core as auth

    user = auth.session_from_request(_cookie_token(request))
    if user is None:
        raise ApiError("auth_required", "请先登录。")
    if user["role"] != "owner":
        raise ApiError("forbidden", "只有管理员可以查看邀请码。")
    return {"items": auth.list_invites()}


@app.get("/api/statutes/status")
async def statutes_status() -> dict[str, Any]:
    """本地法规库的规模与状态分布（界面用来标注"默认只出有效条文"的依据）。"""
    from .statutes import status_summary

    return status_summary()


@app.get("/api/statutes/search")
async def statutes_search(q: str = "", status: str = "valid", limit: int = 10) -> dict[str, Any]:
    """法规检索。**默认只出「现行有效」**；`status=any` 才包含已修改/已废止。

    支持「法规名 + 条号」精准查：条号可写 577 / 第577条 / 第五百七十七条，并支持「第X条之一」。
    """
    from .statutes import search

    query = (q or "").strip()
    if not query:
        raise ApiError("empty_input", "请输入要查的法规名、条号或关键词。")
    if len(query) < 2:
        raise ApiError("empty_input", "至少输入两个字。")
    capped = max(1, min(int(limit or 10), 50))
    return search(query, include_invalid=(status == "any"), limit=capped)


@app.get("/api/statutes/{bbbs}")
async def statutes_detail(bbbs: str) -> dict[str, Any]:
    """取一份法规的元数据与全部条文。"""
    from .statutes import open_statute

    found = open_statute(bbbs)
    if found is None:
        raise ApiError("not_found", "本地法规库没有这份法规。")
    return found


@app.get("/api/exports")
async def exports_list() -> dict[str, Any]:
    """列出导出目录里的文件（**只读**）。

    2026-09-21 迭代 3 追加：「我的」页要显示"我导出的文件"并能重新下载，但此前只有
    "按文件名下载"的接口。这里只列 文件名 / 大小 / 时间（倒序），不改任何现有逻辑。

    **如实说明**：现在没有账号体系，所以列出的是**本机导出目录里的全部文件**；
    接入账号后必须改成按用户过滤（底线 7：用户只能看自己的数据）。
    """
    export_dir = get_config().exports_dir
    items: list[dict[str, Any]] = []
    if export_dir.exists():
        for path in sorted(export_dir.glob("*"), key=lambda item: item.stat().st_mtime, reverse=True):
            if not path.is_file() or path.name.startswith("."):
                continue
            stat = path.stat()
            items.append(
                {
                    "name": path.name,
                    "bytes": stat.st_size,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stat.st_mtime)),
                    "url": f"/exports/{quote(path.name)}",
                }
            )
    return {"total": len(items), "items": items[:100]}


@app.get("/exports/{name}")
async def download(name: str) -> FileResponse:
    export_dir = get_config().exports_dir.resolve()
    target = (export_dir / name).resolve()
    if not target.is_relative_to(export_dir) or not target.exists():
        raise ApiError("path_escape")
    return FileResponse(target, filename=target.name)


@app.get("/")
async def index() -> FileResponse:
    # 单页验收界面在开发迭代中变动频繁；不设 no-store 时浏览器会继续用缓存里的旧页面
    # （表现为“新加的按钮点不了”——实际是旧 DOM）。这里强制不缓存。
    return FileResponse(
        Path(__file__).resolve().parent.parent / "web" / "index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


# ------------------------------------------------------------------ 辅助


def _warning(check: dict[str, Any]) -> str:
    if check["policy"]["allow_synthetic"]:
        return "⚠ 演示模式：当前引用的是合成夹具数据，不是真实检索结果。"
    if check["model"]["is_stub"]:
        return "当前为离线模式：模型为 stub，未调用真实模型。"
    if not check["mcp"]["configured"]:
        return "当前未配置北大法宝 MCP 地址，检索将返回接口失败（不等于没有相关案例）。"
    return ""


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def main() -> None:                                     # pragma: no cover
    import uvicorn

    cfg = get_config()
    host = cfg.env("APP_HOST") or "127.0.0.1"
    port = int(cfg.env("APP_PORT") or 8010)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":                              # pragma: no cover
    main()

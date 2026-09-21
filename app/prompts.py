"""提示词（PRD 第八节）。知识包只放口径与规则，**不放法条**。"""

from __future__ import annotations

from typing import Any

from .session import Session


def build_retrieval_prompt(
    topic: str,
    conditions: dict[str, Any],
    limit: int = 30,
    material_primary: bool = False,
) -> str:
    lines = [
        "请为下面的议题完成一次类案检索，并汇总候选案例池。",
        "",
        f"议题：{topic}",
    ]
    if material_primary:
        lines += [
            "",
            "⚠ 本次以**用户提供材料为主**；仅在需要补充时才调用检索工具。"
            "材料已能支撑结论时不要习惯性去检索。",
        ]
    for key, label in (
        ("cause", "案由"),
        ("court", "法院"),
        ("level", "层级"),
        ("region", "地域"),
        ("since", "起始日期"),
    ):
        value = conditions.get(key)
        if value:
            lines.append(f"{label}：{value}")
    lines += [
        "",
        f"返回上限：{limit} 条。",
        "",
        "要求：",
        "1. 只能调用检索工具获取案例与法条，不得凭记忆编造案号或条文；",
        "2. 工具返回为空或失败时，必须在 conditions_echo 中如实回显，不得解释为「不存在案例」；",
        "3. 候选清单由系统从工具返回值直接装配，**你不需要抄写候选案例**；",
        "4. 最后输出 JSON（json 格式），字段：queries（你用过的检索式）、"
        "conditions_echo{applied,ignored}（哪些条件真正生效了）、notes（可选的一句话说明）。",
    ]
    return "\n".join(lines)


def build_extract_prompt(session: Session, source_id: str, max_chars: int = 6000) -> str:
    source = session.source_pool.get(source_id)
    if source is None:
        return f"来源编号：{source_id}\n（该来源不存在，请输出 missing_fields 说明）"
    head = [
        "请对下面这一篇案件材料做要素提炼，只处理这一篇。",
        "",
        "【案件材料】",
        f"来源编号：{source.source_id}",
        f"引用标识：{source.identifier}",
    ]
    if source.court:
        head.append(f"法院：{source.court}（{source.level or '层级未知'}）")
    if source.region:
        head.append(f"地域：{source.region}")
    if source.decided_on:
        head.append(f"裁判日期：{source.decided_on}")
    kind_note = "用户上传材料（仅摘要级别引用，需人工核验）" if source.is_user_material else "权威检索来源"
    head.append(f"来源类型：{kind_note}")
    text = (source.quote or "")[:max_chars]

    tail = [
        "",
        "要求：",
        "1. 只依据上面的原文，不得补充外部信息，不得编造案号、条款号或引文；",
        "2. 提取不到的字段写进 missing_fields，不要猜；",
        "3. basis 中每条引用的原文片段必须逐字来自上面的原文；",
        "4. stance 只能取 support / oppose / other / unknown 之一；",
        "5. 输出 JSON（json 格式），字段：case_id、facts、claim、issues、holding、result、basis、stance、"
        "missing_fields；",
        "6. **数组元素一律是字符串**（issues 是 [\"争点一\", \"争点二\"]，basis 是 [\"原文片段\"]），"
        "不要用对象或嵌套结构。",
    ]
    if source.is_user_material:
        if source.kind == "case":
            tail.insert(
                1,
                "（注意：这是用户上传的**案例材料**，已/待人工核验；构述与引用同样必须逐字来自原文，"
                "不得概括后当原文，也不得补充材料之外的信息。）",
            )
        else:
            tail.insert(
                1,
                "（注意：这是用户上传的**法条材料**，效力状态未经核验，本次只能作为线索，不能作为权威依据。）",
            )

    return "\n".join(head + ["原文（逐字）：", "<<<SOURCE", text, "SOURCE>>>"] + tail)


def build_consult_identify_prompt(
    session: Session,
    facts: list[str],
    rounds_used: int,
    max_rounds: int,
    asked: list[str] | None = None,
    max_questions: int = 1,
) -> str:
    """咨询第 1 步：只做「问题识别」，**此时还没有检索，不得写任何法条号或案号**。"""
    lines = [
        "请对下面这个法律咨询问题做**识别**，不要给结论、不要引用任何法条号或案号（此时还没有检索）。",
        "",
        "【用户提供的信息（原始提问 + 历轮补充）】",
    ]
    for index, item in enumerate(facts, start=1):
        lines.append(f"{index}. {item}")
    lines += [
        "",
        f"【追问进度】已追问 {rounds_used} 轮 / 上限 {max_rounds} 轮",
    ]
    if asked:
        lines += ["", "【已经问过的问题（不要重复）】"] + [f"- {q}" for q in asked]
    lines += [
        "",
        "要求：",
        "1. 判断法律关系、争议焦点、用户已给的事实、以及**影响结论但缺失**的事实；",
        "2. missing_facts 只列**会改变结论**的事实（如金额、时间、是否书面、是否已催告、是否已过期限）；",
        "3. 必须结合用户刚补充的回答重新判断：事实已足够作答时立刻 need_clarify=false，"
        "clarify_questions=[]；不必问满轮数，也不能照搬上一轮的问题清单；",
        "4. 仍需追问时：need_clarify=true，clarify_questions 只能有 1 个问题。"
        "按对结论的影响程度排序，只问最关键、最可能改变处理方向的一个未知事实；"
        "每个问题必须能一句话回答，不得重复已经得到回答的事项。"
        "禁止把多个问题用逗号、分号、编号、换行或‘以及/同时/分别’合并进这一项；",
        "5. **不得凭记忆写具体法条号、案号或条文原文**（检索在下一步）；",
        "6. 若你预判到信息可能永远问不到，把你的兜底预判写进 assumptions；",
        "7. 数组元素一律是字符串；",
        "8. 输出 JSON（json 格式），字段：legal_relation、disputes[]、given_facts[]、missing_facts[]、"
        "possible_claims[]、need_clarify、clarify_questions[]、assumptions[]。",
    ]
    return "\n".join(lines)


def _citable_sources(session: Session) -> list[Any]:
    """可引用依据：状态 ok、未被取代、**效力状态可引**、用户材料需已核验、无未裁决冲突。

    与门禁 R2 / R3 / R6 / R7 同一口径——提示词里说的“可引用”必须真的可引用，
    否则会出现“提示词说能引、门禁却拦掉”的自相矛盾。
    2-5 咨询与 2-6 合同审查共用本函数。
    """
    from .config import get_config
    from .gates.citation import effective_status_ok
    from .models import Status

    require_status = bool(get_config().get("policy.require_effective_status", True))
    resolved = session.resolved_conflict_ids()
    out = []
    for source in session.source_pool.values():
        if source.status is not Status.OK or source.superseded:
            continue
        if source.is_user_material and not source.user_verified:
            continue
        if source.corroboration == "conflict" and source.source_id not in resolved:
            continue
        if require_status and not effective_status_ok(source.effective_status):
            continue  # 已废止 / 效力未知 → 不得作为现行依据（R6）
        out.append(source)
    return out


def build_consult_answer_prompt(
    session: Session,
    issue: dict[str, Any] | None = None,
    assumptions: list[str] | None = None,
    max_chars: int = 6000,
) -> str:
    """咨询第 2 步：**只能基于已核验的依据**作答，每条结论必须带逐字引用。"""
    issue = issue or {}
    lines = [
        "请基于下面的**可引用依据**回答用户的法律咨询问题。",
        "",
        f"【用户问题】{session.topic}",
        "",
        "【问题识别】",
        f"- 法律关系：{issue.get('legal_relation', '（未识别）')}",
        f"- 争议焦点：{'；'.join(issue.get('disputes') or []) or '（未识别）'}",
        f"- 已给事实：{'；'.join(issue.get('given_facts') or []) or '（未提供）'}",
        f"- 缺失事实：{'；'.join(issue.get('missing_facts') or []) or '（无）'}",
    ]
    if assumptions:
        lines += [
            "- **以下为假设或尚未确认的事项（必须写进 uncertainties，不得当作既定事实）**："
            + "；".join(assumptions),
        ]
    if session.consult.get("skipped_clarification"):
        lines += [
            "- 用户主动跳过了剩余追问，不要再要求先回答追问才提供解答。"
            "根据已提供事实和可引用依据作条件性分析；未知事项不能补写成事实。"
            "若上轮识别的缺口已被最新补充说明，以最新事实为准；其余影响结论的未知项写进 uncertainties。",
        ]

    citable = _citable_sources(session)
    lines += ["", "【可引用依据（只有下列来源可被引用）】"]
    if not citable:
        lines.append("（无可引用依据）")
    for source in citable:
        mark = ""
        if source.is_user_material:
            mark += "（用户材料，已经人工核验）"
        if source.corroboration == "conflict":
            mark += "（来源冲突，已经人工裁决）"
        lines.append(
            f"- 来源编号：{source.source_id} | 类型：{source.kind} | 引用标识：{source.identifier}"
            f" | 效力状态：{source.effective_status}"
        )
        lines.append(f"  来源标注（由系统标注，不得改写）：{source.origin_text}{mark}")

    lines += ["", "【可引用依据的原文（用于逐字引用）】"]
    for source in citable:
        if not (source.quote or "").strip():
            continue
        lines += [f"<<<SOURCE id={source.source_id}", (source.quote or "")[:max_chars], "SOURCE>>>"]

    from .assemble import render_gaps, render_status_ledger

    ledger = render_status_ledger(session)
    if ledger:
        lines += ["", ledger]
    gaps = render_gaps(session)
    if gaps:
        lines += ["", gaps]

    lines += [
        "",
        "要求：",
        "1. conclusions[] 的每一项必须是对象，正文放在 text（字符串）中，"
        "并给出 citation_source_ids（字符串数组，至少 1 条）与 citations（对象数组）"
        "（source_id / identifier / quote）；quote 必须是上面 SOURCE 块中**逐字连续出现**的片段，"
        "不得改写、不得用省略号拼接；",
        "2. **不得承诺结果**：不得出现「一定能赢」「百分百」「保证胜诉」这类表述；",
        "3. **不得说可以替代律师**，不得生成可直接提交法院的文书；",
        "4. 不得把个案说成「司法实践普遍认为」「各地法院均…」，也不得给出胜诉率类的伪指标；",
        "5. 检索不到直接依据时：conclusions 留空，把说明写进 insufficient"
        "（例如「未检索到直接规定」并给放宽建议），**绝对不得凭记忆编造法条或案号**；",
        "6. uncertainties[]：写风险与不确定（含上面列出的假设）；不得省略；",
        "7. next_steps[]：给可执行的下一步（如补强证据、书面催告、咨询执业律师）；",
        "8. 「来源 / 印证」由系统标注，不得自行编造或改写；",
        "9. 只有 uncertainties[]、next_steps[]、citation_source_ids[] 的元素是字符串；"
        "conclusions[]、citations[] 的元素必须是对象。禁止 conclusions: [\"一段结论\"]，"
        "也禁止用 content、conclusion 等字段替代 text；",
        "10. 输出 JSON（json 格式），必须包含 conclusions、uncertainties、next_steps、insufficient 四个字段。"
        "insufficient 是字符串，无缺口时用空字符串；无结论时 conclusions 用空数组；",
        "11. 优先回答最关键的 3–5 条结论，引用取足够支持该结论的简短原文，避免重复抄写大段材料；",
        '格式示例（仅说明结构，占位文字不能作为真实引用）：'
        '{"conclusions":[{"text":"有依据支持的结论正文",'
        '"citation_source_ids":["材料中的真实来源编号"],'
        '"citations":[{"source_id":"同一真实来源编号","identifier":"材料中的真实引用标识",'
        '"quote":"该来源中逐字连续出现的原文"}]}],'
        '"uncertainties":["尚需确认的事实"],"next_steps":["可执行的下一步"],"insufficient":""}',
    ]
    return "\n".join(lines)


def build_contract_clauses_prompt(clauses_text: str, head: str = "") -> str:
    """合同第 1 步：给代码切好的条款块补标题与摘要，并提取甲乙方名称。

    **切块以代码为准**，模型不得增删条款、不得改写原文。
    """
    lines = [
        "下面是一份合同（已由程序切成条款块）。请为每一块补一个短标题与一句话摘要，并提取双方名称。",
        "",
        "要求：",
        "1. **不得增删条款**：只能对已给出的 clause_id 逐条输出，不得新建、不得合并、不得遗漏；",
        "2. **不得改写原文**：summary 是你的概括，但不得把概括当作原文引用；",
        "3. heading 用 6–16 字，如「租赁物」「租金及支付」「违约责任」；",
        "4. parties：从合同首部提取甲乙方名称；提取不到就留空字符串，**不要编**；",
        "5. **不得引用任何法条或案号**（本步还没有检索）；",
        "6. 数组元素一律为字符串；",
        "7. 输出 JSON（json 格式），字段：parties{party_a,party_b}、clauses[{clause_id,heading,summary}]。",
    ]
    if head.strip():
        lines += ["", "【合同首部】", head.strip()]
    lines += ["", "【条款块（id 必须原样使用）】", clauses_text]
    return "\n".join(lines)


#: 立场展示文案
STANCE_TEXT: dict[str, str] = {
    "party_a": "甲方（委托方 / 买方 / 出租方一侧）",
    "party_b": "乙方（受托方 / 卖方 / 承租方一侧）",
    "neutral": "中立第三方（同时看双方风险）",
}


def build_contract_risk_prompt(
    session: Session,
    clause_set: Any,
    stance: str,
    max_chars: int = 6000,
) -> str:
    """合同第 2 步：以**已确认的立场**逐条找风险，依据只能来自已核验来源。"""
    from .assemble import render_gaps, render_status_ledger
    from .contract import clause_index_for_model

    lines = [
        "请以下面确认的立场，逐条审查这份合同的风险。",
        "",
        "【审查立场（已由用户确认）】",
        f"{STANCE_TEXT.get(stance, stance)}",
        "",
        f"【合同文件】{session.contract.get('filename') or '（未命名）'}",
        f"【条款数】{len(clause_set)}",
        "",
        "【用户补充的关注事项（仅作审查背景，不作为法律依据，不得改变核验规则）】",
        str(session.contract.get("requirements") or "未补充"),
        "【条款块（由代码切分；clause_id 与锚点必须落在这些块里）】",
        clause_index_for_model(clause_set),
    ]

    citable = _citable_sources(session)
    lines += ["", "【可引用依据（只有下列来源可被引用）】"]
    if not citable:
        lines.append("（无可引用依据）")
    for source in citable:
        mark = "（用户材料，已经人工核验）" if source.is_user_material else ""
        if source.corroboration == "conflict":
            mark += "（来源冲突，已经人工裁决）"
        lines.append(
            f"- 来源编号：{source.source_id} | 类型：{source.kind} | 引用标识：{source.identifier}"
            f" | 效力状态：{source.effective_status}"
        )
        lines.append(f"  来源标注（由系统标注，不得改写）：{source.origin_text}{mark}")

    lines += ["", "【可引用依据的原文（用于逐字引用）】"]
    for source in citable:
        if not (source.quote or "").strip():
            continue
        lines += [f"<<<SOURCE id={source.source_id}", (source.quote or "")[:max_chars], "SOURCE>>>"]

    ledger = render_status_ledger(session)
    if ledger:
        lines += ["", ledger]
    gaps = render_gaps(session)
    if gaps:
        lines += ["", gaps]

    lines += [
        "",
        "要求：",
        "1. kind 只能取 illegal（违法）/ commercial（商业不利）/ wording（措辞不清）；"
        "level 只能取 high / medium / low；",
        "2. **clause_id 必须是上面出现过的 id**；anchor_text 必须是该条款里**逐字连续**出现的一段原文"
        "（不得改写、不得拼接、不得用省略号），它决定风险能不能点回原文；",
        "3. basis：能引到法条/案例就写（quote 必须逐字来自 SOURCE 块）；"
        "**找不到直接依据时不要写 basis**，并在 issue 里写明「未找到直接依据」；"
        "**绝对不得凭记忆编造法条、案号或条文原文**；",
        "4. suggestion：给**示例表述**，必须包含「须经律师审定」字样；"
        "不得出现「可直接签署」「可直接提交法院」「无需律师审阅」这类表述；",
        "5. 不得输出「不存在任何风险」这类断言：没发现风险就返回空的 risks 数组；",
        "6. issue 用客观第三方语气，不得替律师下结论、不得承诺结果；",
        "7. 只描述你能在条款里看到的内容，不得假设合同之外的事实；",
        "8. 「来源 / 印证」由系统标注，不得自行编造或改写；",
        "9. 数组元素一律为字符串，basis 的每个元素必须是对象 {source_id, identifier, quote}；",
        "10. 输出 JSON（json 格式），字段："
        "risks[{kind,level,clause_id,anchor_text,issue,basis,suggestion,confidence}]。",
    ]
    return "\n".join(lines)


def build_synthesis_prompt(session: Session, max_chars: int = 6000) -> str:
    lines = [
        "请基于下面由程序计算的矩阵与观点分布，做跨案归纳与解释。",
        "",
        f"议题：{session.topic}",
        "",
        "【对比矩阵（代码计算，仅含已确认样本）】",
    ]
    if session.matrix:
        for row in session.matrix:
            lines.append(
                "- "
                + " | ".join(f"{key}:{row.get(key, '')}" for key in row)
            )
    else:
        lines.append("（空）")

    lines += ["", "【观点分布（代码计算）】", session.distribution.get("text", "（空）")]

    lines += ["", "【可引用来源（只有下列来源可被引用）】"]
    for source_id in session.sample.confirmed:
        source = session.source_pool.get(source_id)
        if source is None:
            continue
        mark = ""
        if source.is_user_material and not source.user_verified:
            mark += "（未经人工核验，不得引用）"
        if source.identifier_missing:
            mark += "（正文未识别到案号，此标识为文件名）"
        if source.corroboration == "conflict":
            mark += "（来源冲突，已被拦住，除经人工裁决外不得引用）"
        # 「来源 / 印证」由系统标注：另起一行写，不得混进引用标识（避免模型把标注当标识抄）
        lines.append(f"- 来源编号：{source_id} | 引用标识：{source.identifier}")
        lines.append(f"  来源标注（由系统标注，不得改写）：{source.origin_text}{mark}")

    if any(
        (session.source_pool[sid].is_user_material if sid in session.source_pool else False)
        for sid in session.sample.confirmed
    ):
        lines.append(
            "（含用户上传材料：已核验的**案例**材料可作为引用依据；法条材料与未核验材料不得引用。）"
        )

    lines += [
        "",
        "【可引用来源的原文（用于逐字引用）】",
    ]
    for source_id in session.sample.confirmed:
        source = session.source_pool.get(source_id)
        if source is None or not (source.quote or "").strip():
            continue
        lines += [
            f"<<<SOURCE id={source_id}",
            (source.quote or "")[:max_chars],
            "SOURCE>>>",
        ]

    lines += [
        "",
        "要求：",
        "1. 每条结论必须给出 citation_source_ids（至少 1 条）与 citations"
        "（source_id / identifier / quote）；quote 必须是上面 SOURCE 块中逐字出现的片段，不得改写或编造；",
        "2. 不得输出总体化推断（全国/全部/绝大多数）、胜诉率支持率等伪指标、无来源的百分比、"
        "确定性承诺，也不得断言某类案例不存在；",
        "3. 只描述本次确认样本范围内的情况；",
        "4. quote 必须是从上面 SOURCE 块中**连续复制**的一段原文，不允许改写、不允许用省略号拼接两段"
        "不相邻的文字；宁可只引一小段，也不要凑长句；",
        "5. 结论正文中提到案件或法条时，必须写**案号 / 法规名+条号**，禁止出现内部编号"
        "（形如 mcp_case_3 这种），系统会自动替换但请直接写可读名称；",
        "6. 来源（用户材料 / 法宝 / 补充来源）与印证状态由系统标注，不得自行编造或改写；",
        "7. 数组元素一律是字符串（key_variables 是 [\"变量一\", \"变量二\"]）；",
        "8. 输出 JSON（json 格式），字段：conclusions[]、opposing_paths[]、key_variables[]。",
    ]
    return "\n".join(lines)

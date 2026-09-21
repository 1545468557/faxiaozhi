/**
 * 后端契约类型（阶段 3-1）
 *
 * 权威来源：后端 `app/server.py`、`app/models.py`、`app/session.py`（可用 GET /docs 查看 OpenAPI）。
 * 本文件只描述前端**实际用到**的字段；后端多返回的字段一律忽略，不做假设。
 *
 * 3-2 之后再补齐 materials / conflicts / matrix / synthesis 的完整类型。
 */

export type Module = "research" | "consult" | "contract";

/** 与后端 Status 枚举一致（models.py） */
export type SourceStatus =
  | "ok"
  | "no_match"
  | "insufficient"
  | "abstract_only"
  | "parse_error"
  | "interface_error";

export type Bootstrap = {
  agent: { name: string; id: string };
  modules: { id: Module; name: string; enabled: boolean }[];
  materials: { max_files_per_upload: number; role_default: string; max_supplement_rounds: number };
  model: { provider: string; is_stub: boolean; model_id: string; key_present: boolean };
  mcp: { configured: boolean; token_present: boolean; available: boolean | null };
  policy: Record<string, unknown>;
  limits: Record<string, number>;
  disclosure: string;
};

/** 候选池条目（research 分支的 candidates，由后端代码生成） */
export type Candidate = {
  source_id: string;
  identifier: string;
  title: string;
  court: string;
  level: string;
  region: string;
  decided_on: string;
  status: SourceStatus;
  origin: string;
  origin_text: string;
  supplement: boolean;
  user_verified: boolean;
  identifier_missing: boolean;
  corroboration: string;
};

/** 依据池摘要（sources，用户材料不回显原文） */
export type SourceBrief = Omit<Candidate, "source_id"> & {
  source_id: string;
  kind: "statute" | "case" | "user_material";
  status_text: string;
  effective_status: string;
  uri: string | null;
  local_hit: boolean;
  corroboration_text: string;
  superseded: boolean;
  manual_override: boolean;
  locator: Record<string, unknown>;
  quote: string;
  quote_len?: number;
  note?: string;
  synthetic: boolean;
};

export type Rejection = {
  /** 规则号：R0（无引用）/ R1..R7 */
  rule: string;
  reason: string;
  identifier?: string;
  quote?: string;
};

/**
 * 门禁报告。**注意字段口径**：
 * - `rejected` / `guard_hits` 是**数量**，明细在 `details`；
 * - `degraded_texts` 是**被降级（未通过核验）的结论原文** —— 红线 4 要求界面
 *   只展示通过核验的结论，前端必须用它把对应结论移到「依据缺口」。
 */
export type GateReport = {
  accepted: number;
  rejected: number;
  guard_hits: number;
  coverage: number;
  demo_mode: boolean;
  details: Rejection[];
  gaps: string[];
  degraded_texts: string[];
};

/** 依据缺口（后端 Gap：kind = citation | sample | expression | evidence） */
export type Gap = { kind: string; detail: string };

export type ExportState = { ready: boolean; blockers: string[] };

export type DegradationItem = {
  step?: string;
  tool?: string;
  status?: string;
  error_kind?: string;
  retryable?: boolean;
  status_text?: string;
  endpoint_alias?: string;
};

/** 会话全量快照（GET /api/session/{sid}/state） */
export type SessionState = {
  session_id: string;
  branch: Module;
  topic: string;
  conditions: Record<string, string>;
  phase: string;
  status: string;
  run_id: string | null;
  queued: boolean;
  first_response_ms: number | null;
  total_ms: number | null;
  candidates: Candidate[];
  sample: { locked: boolean; confirmed: string[]; excluded: string[] };
  cases: CaseElement[];
  matrix: MatrixRow[];
  distribution: Distribution | Record<string, never>;
  synthesis: Synthesis | null;
  limitations: string[];
  gate_report: GateReport | null;
  expression_hits: string[];
  gaps: Gap[];
  sources: SourceBrief[];
  materials: MaterialItem[];
  conflicts: ConflictItem[];
  supplement: {
    rounds_used: number;
    max_rounds: number;
    material_primary: boolean;
    remaining: number;
  };
  export: ExportState;
  consult: ConsultSnapshot;
  contract: ContractSnapshot;
  degradations: DegradationItem[];
  degradation_summary: Record<string, unknown>;
  failed_step: string | null;
  failed_reason: string | null;
  retry_count: number;
  can_retry: boolean;
};

/** SSE 事件（后端 _sse()：event + data 两个字段） */
export type EventName =
  | "meta"
  | "phase"
  | "progress"
  | "tool"
  | "gate"
  | "degrade"
  | "checkpoint_reached"
  | "clarify"
  | "conflict"
  | "retry"
  | "done"
  | "error";

export type RunEvent = {
  name: EventName;
  data: Record<string, unknown>;
};

export type ErrorBody = { error: { code: string; message: string; detail?: Record<string, unknown> } };

// ---------------------------------------------------------------- 研究产物

/** 单案要素（CASE_ELEMENT_SCHEMA，由模型提炼、case_id 由代码对齐） */
export type CaseElement = {
  case_id: string;
  facts?: string;
  claim?: string;
  issues?: string[];
  holding?: string;
  result?: string;
  basis?: string[];
  stance?: "support" | "oppose" | "other" | "unknown";
  missing_fields?: string[];
};

/** 对比矩阵行：MATRIX_COLUMNS + 代码写入的「来源」列 */
export type MatrixRow = Record<string, string> & {
  case_id?: string;
  identifier?: string;
  来源?: string;
};

/** 观点分布：分母与口径声明由代码生成 */
export type Distribution = {
  counts: { support: number; oppose: number; other: number; unknown: number };
  denominator: number;
  labels: Record<string, string>;
  text: string;
};

export type SynthesisCitation = { source_id: string; identifier: string; quote: string };

export type Conclusion = {
  text: string;
  citation_source_ids: string[];
  citations?: SynthesisCitation[];
  similarities?: string[];
  differences?: string[];
};

export type Synthesis = {
  conclusions?: Conclusion[];
  opposing_paths?: Record<string, unknown>[];
  key_variables?: string[];
};

/** 会话材料清单条目（用户材料不回显原文） */
export type MaterialItem = {
  material_id: string;
  /** 依据池里的来源编号（**核验/改角色接口要的是它**，不是 material_id） */
  source_id: string;
  filename: string;
  role: string;
  role_label: string;
  role_source: string;
  identifier: string;
  identifier_source: string;
  identifier_missing: boolean;
  chars: number;
  verified: boolean;
  status: string;
  version_label: string;
  superseded: boolean;
  superseded_by?: string | null;
  fail_reason?: string | null;
};

export type ConflictItem = {
  decision?: string;
  source_id: string;
  identifier: string;
  status_text?: string;
  reason?: string;
  resolved?: boolean;
};

/** 咨询分支快照（state.consult） */
export type ConsultIssueSnapshot = {
  legal_relation?: string;
  disputes?: string[];
  given_facts?: string[];
  missing_facts?: string[];
  possible_claims?: string[];
  need_clarify?: boolean;
  clarify_questions?: string[];
  assumptions?: string[];
};

export type ConsultAnswerSnapshot = {
  conclusions?: {
    text: string;
    citation_source_ids?: string[];
    citations?: { source_id: string; identifier: string; quote: string }[];
  }[];
  uncertainties?: string[];
  next_steps?: string[];
  insufficient?: string;
};

export type ConsultSnapshot = {
  facts?: string[];
  asked?: string[];
  /** 已用追问轮数（**代码计数**，界面不许自己估） */
  rounds?: number;
  max_rounds?: number;
  status?: string;
  /** true = 正在等待用户回答追问（要显示追问卡，不能显示成加载中） */
  awaiting?: boolean;
  /** 用户主动跳过剩余追问；不代表信息已经充分。 */
  skipped_clarification?: boolean;
  issue?: ConsultIssueSnapshot;
  /** 后端最终采用的假设（可由 missing_facts 回退生成）。 */
  assumptions?: string[];
  answer?: ConsultAnswerSnapshot;
  /** 通过门禁的结论原文（红线 4：只展示这批） */
  passed?: string[];
};

// ---------------------------------------------------------------- 合同审查

/** 合同条款块（后端 `split_clauses` 切块结果，含行号与原文偏移） */
export type ContractClause = {
  clause_id: string;
  heading: string;
  text: string;
  summary: string;
  start: number;
  end: number;
  line: number;
};

/** 合同风险条目（后端 `_normalize_risks` 产出；`start/end` 用于原文联动） */
export type ContractRisk = {
  riskId: string;
  kind: string;
  level: string;
  clauseId: string;
  clauseHeading: string;
  anchorText: string;
  /** 原文偏移；定位失败时为 -1 */
  start: number;
  end: number;
  locateOk: boolean;
  issue: string;
  basis: { source_id: string; identifier: string; quote: string }[];
  /** verified = 依据通过核验；no_basis = 未找到直接依据；rejected = 依据未通过核验（只能作提示） */
  basisStatus: "verified" | "no_basis" | "rejected" | "pending";
  suggestion: string;
  confidence: string;
};

export type ContractCounts = {
  total?: number;
  by_kind?: Record<string, number>;
  by_level?: Record<string, number>;
  verified?: number;
  no_basis?: number;
  locate_failed?: number;
};

export type ContractSnapshot = {
  filename?: string;
  /** 合同原文：**仅当前会话内存**，界面用它做原文↔风险联动 */
  text?: string;
  stance?: string;
  stanceLabel?: string;
  parties?: { party_a?: string; party_b?: string };
  clauses?: ContractClause[];
  clauses_count?: number;
  risks?: ContractRisk[];
  counts?: ContractCounts;
  status?: string;
};

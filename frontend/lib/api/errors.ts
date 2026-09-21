/**
 * 错误码 → 用户文案与行为（阶段 3-1，对应交接契约 §4.2 与后端 app/errors.py）
 *
 * 规则（五条红线第 3 条）：
 * 1. **优先使用后端返回的 message**，本地表只作兜底（后端换了文案不用改前端）；
 * 2. 「接口失败」绝不能显示成「没有相关规定」——`interface_error` / `mcp_unavailable`
 *    必须带 `mcpWarning: true`，界面要显式写出"这不等于没有相关规定"；
 * 3. 是否需要「重试」按钮由 `retryable` 决定，不靠文案猜。
 */

export type Tone = "info" | "warn" | "error";

export type ErrorInfo = {
  code: string;
  /** 兜底文案；后端给了 message 时以后端为准 */
  fallback: string;
  /** 是否给「重试」按钮（走 POST /retry 或重发本动作） */
  retryable: boolean;
  tone: Tone;
  /** 是否为「检索失败」类：必须在界面上强调"不等于没有相关规定" */
  mcpWarning?: boolean;
  /** 是否需要"重新发起"（内容不落盘，无法续跑） */
  restartRequired?: boolean;
};

const TABLE: Record<string, Omit<ErrorInfo, "code">> = {
  // ---- 会话 / 请求 ----
  session_not_found: { fallback: "会话已过期，正在重新建立会话。", retryable: true, tone: "warn" },
  empty_input: { fallback: "请先输入要研究的议题。", retryable: false, tone: "warn" },
  empty_material: { fallback: "请先上传材料（合同审查需先上传合同）。", retryable: false, tone: "warn" },
  empty_sample: { fallback: "请至少确认 1 篇样本（生成综合结论需要至少 2 篇）。", retryable: false, tone: "warn" },
  invalid_request: { fallback: "请求格式不正确，请刷新页面后重试。", retryable: false, tone: "error" },
  schema_invalid: { fallback: "模型输出未通过结构校验，本次未产出。可重试一次。", retryable: true, tone: "warn" },
  checkpoint_mismatch: { fallback: "当前没有等待该确认点（通常是点了两次）。", retryable: false, tone: "info" },
  sample_not_locked: { fallback: "样本尚未确认，无法生成对比矩阵。", retryable: false, tone: "warn" },
  session_busy: { fallback: "当前任务仍在运行中，请稍候再试。", retryable: true, tone: "info" },
  concurrency_queued: { fallback: "同时在跑的任务已达上限，请稍后重试。", retryable: true, tone: "warn" },
  not_found: { fallback: "找不到对应的内容。", retryable: false, tone: "error" },
  run_not_found: { fallback: "找不到这次运行的记录。", retryable: false, tone: "error" },
  run_expired: { fallback: "这次运行的存档已过期。", retryable: false, tone: "warn" },
  workflow_step_limit: { fallback: "任务超出最大步数，已中止。已完成部分已保存。", retryable: true, tone: "warn" },

  // ---- 检索 / 模型（红线 3 的重点）----
  mcp_unavailable: {
    fallback: "检索接口调用失败，本次未获得可核验依据。这不等于「无相关案例」。可点击重试。",
    retryable: true,
    tone: "error",
    mcpWarning: true,
  },
  model_unavailable: { fallback: "模型服务暂时不可用，已重试多次。请稍后再试。", retryable: true, tone: "error" },
  parse_error: { fallback: "返回内容无法识别，未能完成本次处理。", retryable: true, tone: "warn" },

  // ---- 重试 ----
  no_failed_step: { fallback: "当前没有失败的步骤，无需重试。", retryable: false, tone: "info" },
  retry_exhausted: { fallback: "重试次数已达上限，请重新发起研究。", retryable: false, tone: "error" },
  can_not_retry: { fallback: "当前状态不支持重试。", retryable: false, tone: "info" },

  // ---- 导出 / 门禁 ----
  export_blocked: {
    fallback: "存在未通过核验的引用 / 已排除样本残留 / 越界表述，无法导出。",
    retryable: false,
    tone: "error",
  },
  permission_denied: { fallback: "没有权限执行该操作。", retryable: false, tone: "error" },
  path_escape: { fallback: "禁止写入导出目录之外的路径。", retryable: false, tone: "error" },

  // ---- 材料 ----
  unsupported_file: { fallback: "不支持的文件类型。请上传 .docx / .pdf / .txt / .md。", retryable: false, tone: "warn" },
  file_too_large: { fallback: "文件超出大小限制（20MB）。", retryable: false, tone: "warn" },
  type_mismatch: { fallback: "文件真实类型与扩展名不符，已拒绝。", retryable: false, tone: "warn" },
  too_many_files: { fallback: "单次上传文件数超出上限，请分批上传。", retryable: false, tone: "warn" },
  invalid_source: { fallback: "source 只能是 real / test / unknown。", retryable: false, tone: "error" },
  not_conflict: { fallback: "该来源不是冲突来源，无需人工裁决。", retryable: false, tone: "info" },
  supplement_exhausted: { fallback: "本次研究的补充检索已达上限，请改用已取得的材料与来源。", retryable: false, tone: "warn" },

  // ---- 不可恢复（隐私：原文不落盘）----
  material_not_recoverable: {
    fallback: "本次研究使用了你上传的材料；材料原文不落盘，服务重启后无法继续。请重新上传材料后重新发起研究。",
    retryable: false,
    tone: "error",
    restartRequired: true,
  },
  consult_not_recoverable: {
    fallback: "咨询问题里可能含当事人具体事实；原文不落盘，服务重启后无法继续。请重新提问。",
    retryable: false,
    tone: "error",
    restartRequired: true,
  },
  contract_not_recoverable: {
    fallback: "合同原文仅当前会话临时处理、不落盘，服务重启后无法继续。请重新上传合同后重新发起审查。",
    retryable: false,
    tone: "error",
    restartRequired: true,
  },

  // ---- 咨询 / 合同 ----
  rounds_exhausted: { fallback: "追问轮数已达上限，已按现有信息与假设作答。", retryable: false, tone: "info" },
  contract_parse_failed: {
    fallback: "这份合同没有可解析的正文。请提供可复制文字的版本（docx / pdf / txt / md）；扫描件暂不支持。",
    retryable: false,
    tone: "warn",
  },

  // ---- 其它 ----
  internal_error: { fallback: "服务内部错误，请稍后重试。", retryable: true, tone: "error" },

  // ---- 传输层（前端自己产生，后端不会返回）----
  backend_unreachable: {
    fallback: "连不上本机后端服务。请确认后端已启动（127.0.0.1:8010）。",
    retryable: true,
    tone: "error",
  },
  backend_blocked: { fallback: "当前只允许在本机使用。", retryable: false, tone: "error" },
  stream_interrupted: { fallback: "实时进度连接中断，正在用最新快照恢复。", retryable: true, tone: "warn" },
  download_failed: { fallback: "导出文件下载失败。", retryable: true, tone: "error" },
};

export const KNOWN_ERROR_CODES = Object.keys(TABLE).sort();

/** 把后端错误体解析成界面要用的结构化信息 */
export function describeError(
  code: string | undefined,
  backendMessage?: string,
): ErrorInfo {
  const key = code && TABLE[code] ? code : "internal_error";
  const row = TABLE[key];
  return {
    code: key,
    // 后端文案优先（后端是唯一权威），本表只兜底
    fallback: backendMessage?.trim() ? backendMessage : row.fallback,
    retryable: row.retryable,
    tone: row.tone,
    mcpWarning: row.mcpWarning,
    restartRequired: row.restartRequired,
  };
}

/** 界面展示用文案：后端 message 优先 */
export function errorText(code: string | undefined, backendMessage?: string): string {
  const info = describeError(code, backendMessage);
  return backendMessage?.trim() ? backendMessage : info.fallback;
}

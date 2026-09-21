/**
 * 合同审查的"该做哪一步"（迭代 1-1）
 *
 * 单独抽成纯函数，方便单测锁住行为：**人工门没通过就不许跳到下一步**。
 * 界面只负责按返回值渲染，不在组件里自己判断。
 */

export const CONTRACT_STEPS = [
  { id: 1, label: "上传合同" },
  { id: 2, label: "确认立场" },
  { id: 3, label: "审查中" },
  { id: 4, label: "看结果并导出" },
] as const;

export type ContractStep = 1 | 2 | 3 | 4;

export function contractStep(input: {
  /** 合同材料是否已点「本人已核验」 */
  materialVerified: boolean;
  /**
   * 后端**真的**在等立场确认。
   * 注意：不能只看"当前没有立场"——还没开始审查时也没有立场，
   * 那会把用户直接从上传那一步踢到立场页（2026-09-20 实测踩到的缺陷）。
   */
  awaitingStance: boolean;
  /** 是否已产出结果（status = reviewed / insufficient，且立场已确认） */
  finished: boolean;
  /** 后端记录下来的失败步骤（有值说明这次没跑完） */
  failedStep?: string | null;
  /** 运行状态机当前阶段 */
  phase: "idle" | "running" | "awaiting" | "done" | "error";
  /** 这个会话是否已经发起过审查（后端返回的运行 id） */
  runStarted: boolean;
}): ContractStep {
  if (!input.materialVerified) return 1;
  if (input.awaitingStance) return 2;
  if (input.finished || Boolean(input.failedStep) || input.phase === "error" || input.phase === "done") return 4;
  if (input.phase === "running" || input.phase === "awaiting" || input.runStarted) return 3;
  // 已核验但还没开始审查：留在第一步，按钮就在眼前
  return 1;
}

/** 允许用户回看的最远步骤：不能越过还没通过的人工门 */
export function canJumpTo(step: number, current: ContractStep): boolean {
  return step <= current;
}

/**
 * 后端是否**真的**还在等立场确认。
 *
 * 两个坑（都是 2026-09-20 实测踩到的）：
 * 1. 不能只看"当前没有立场"——还没开始审查时也没有立场，会把用户从上传页踢到立场页；
 * 2. 不能只看 `status === "awaiting_stance"`——实测后端在"立场已确认、下一步失败"时
 *    仍把状态留成 `awaiting_stance`，只看状态会让界面卡在立场页，看不到失败原因。
 * 因此：**已经选了立场就一律不算在等**。
 */
export function isAwaitingStance(input: {
  /** 快照里已记录的立场 */
  stance?: string | null;
  /** 事件流里明确在等 stance_confirm */
  eventAwaiting: boolean;
  /** 快照里的合同状态 */
  status?: string | null;
}): boolean {
  if (input.stance) return false;
  return input.eventAwaiting || input.status === "awaiting_stance";
}

/**
 * 合同审查四步走的判定（迭代 1-1）
 *
 * 锁住一条底线：**人工门没过就不许跳到下一步**。
 * 纯函数，不依赖 DOM，也不调用后端。
 */

import { describe, expect, it } from "vitest";
import { canJumpTo, contractStep, isAwaitingStance } from "@/components/contract/steps";

const base = {
  materialVerified: true,
  awaitingStance: false,
  finished: false,
  failedStep: null,
  phase: "idle" as const,
  runStarted: false,
};

describe("合同审查该做哪一步", () => {
  it("没上传 / 没核验 → 停在第一步", () => {
    expect(contractStep({ ...base, materialVerified: false })).toBe(1);
    // 即使后端已经在等立场，只要材料没核验也先回第一步
    expect(contractStep({ ...base, materialVerified: false, awaitingStance: true })).toBe(1);
  });

  it("已核验但立场未确认 → 第二步（人工门）", () => {
    expect(contractStep({ ...base, awaitingStance: true })).toBe(2);
  });

  it("立场已确认、还在跑 → 第三步", () => {
    expect(contractStep({ ...base, phase: "running" })).toBe(3);
    expect(contractStep({ ...base, runStarted: true })).toBe(3);
  });

  it("已核验、还没开始审查 → 留在第一步（不能直接跳到立场页）", () => {
    // 这是 2026-09-20 实测踩到的缺陷：只看"当前没有立场"会把用户踢到立场页
    expect(contractStep({ ...base })).toBe(1);
  });

  it("已出结果 / 这次没跑完 / 运行报错 → 第四步（都要能看到结果与失败原因）", () => {
    expect(contractStep({ ...base, finished: true })).toBe(4);
    expect(contractStep({ ...base, failedStep: "依据检索" })).toBe(4);
    expect(contractStep({ ...base, phase: "error" })).toBe(4);
  });

  it("只能回看，不能越过还没通过的人工门往前点", () => {
    expect(canJumpTo(1, 3)).toBe(true);
    expect(canJumpTo(3, 3)).toBe(true);
    expect(canJumpTo(4, 2)).toBe(false);
  });
});

describe("是不是真的还在等立场确认", () => {
  it("还没开始审查（无立场、无事件、无状态）→ 不算在等", () => {
    expect(isAwaitingStance({ stance: undefined, eventAwaiting: false, status: undefined })).toBe(false);
  });

  it("后端在等立场（事件或状态任一）→ 算在等", () => {
    expect(isAwaitingStance({ stance: undefined, eventAwaiting: true, status: "awaiting_stance" })).toBe(true);
    expect(isAwaitingStance({ stance: undefined, eventAwaiting: false, status: "awaiting_stance" })).toBe(true);
  });

  it("已经选了立场，就一律不算在等（否则会卡在立场页看不到结果）", () => {
    expect(isAwaitingStance({ stance: "party_b", eventAwaiting: true, status: "awaiting_stance" })).toBe(false);
  });
});

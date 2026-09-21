/**
 * 会话生命周期（阶段 3-1）
 *
 * 事实来源在后端（内存会话 + journal），前端只保存 `session_id`：
 * - 存 `sessionStorage`：**同一标签页刷新后仍能恢复**，关掉标签页即清（台账 D6：材料不落盘）；
 * - 收到 `session_not_found` → **静默重开一次会话并重试**（契约 §4.2）；
 * - `*_not_recoverable` → 不给重试，直接提示"服务重启后无法继续，请重新发起"。
 */

import { ApiError, api } from "./client";
import type { Module, SessionState } from "./types";

const PREFIX = "fzx:sid:";

function storage(): Storage | null {
  try {
    if (typeof window === "undefined") return null;
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function readSessionId(branch: Module): string | null {
  return storage()?.getItem(PREFIX + branch) ?? null;
}

export function writeSessionId(branch: Module, sid: string): void {
  storage()?.setItem(PREFIX + branch, sid);
}

export function forgetSession(branch: Module): void {
  storage()?.removeItem(PREFIX + branch);
}

/** 建立（或复用）一个可用会话 */
export async function createSession(branch: Module): Promise<string> {
  const created = await api.createSession(branch);
  const sid = String(created.session_id);
  writeSessionId(branch, sid);
  return sid;
}

/**
 * 取当前会话的状态快照；会话丢了就静默重建。
 * `onRecreated` 让调用方能在界面上说明"会话已重建"，而不是假装无事发生。
 */
export async function loadState(
  branch: Module,
  options: { onRecreated?: () => void; sid?: string | null } = {},
): Promise<{ sid: string; state: SessionState }> {
  let sid = options.sid ?? readSessionId(branch);
  if (!sid) sid = await createSession(branch);

  try {
    return { sid, state: await api.state(sid) };
  } catch (error) {
    if (error instanceof ApiError && error.code === "session_not_found") {
      const fresh = await createSession(branch);
      options.onRecreated?.();
      return { sid: fresh, state: await api.state(fresh) };
    }
    throw error;
  }
}

/** 把「内容不落盘、重启后无法继续」这类错误标出来，界面据此提示重新发起 */
export function isRestartRequired(error: unknown): boolean {
  return error instanceof ApiError && error.info.restartRequired === true;
}

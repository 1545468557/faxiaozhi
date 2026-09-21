/**
 * 「我的」页的数据（迭代 3 追加）
 *
 * 现在还没有账号体系，所以这一页只呈现**本机范围内**的东西：会话、会话内上传的材料、
 * 本机导出目录里的文件、真实运行统计。跨设备/跨重启的历史要等账号（迭代 2）。
 */

import { BFF, api } from "./client";
import type { SessionState } from "./types";

export type ExportFile = { name: string; bytes: number; created_at: string; url: string };

export type BranchKey = "consult" | "research" | "contract";

export const BRANCH_LABEL: Record<BranchKey, string> = {
  consult: "法律问答（完整版）",
  research: "类案检索",
  contract: "合同审查",
};

/** 本地保存的三条链路会话 id（sessionStorage；关掉标签页即清） */
export function localSessionIds(): Record<BranchKey, string | null> {
  const read = (branch: BranchKey) => {
    try {
      return window.sessionStorage.getItem(`fzx:sid:${branch}`);
    } catch {
      return null;
    }
  };
  return { consult: read("consult"), research: read("research"), contract: read("contract") };
}

/** 首页快速问答（v3 后端）的会话 id 在 localStorage */
export function quickChatSessionId(): string | null {
  try {
    return window.localStorage.getItem("faxiaozhi:v3:sid");
  } catch {
    return null;
  }
}

/** 清空本机保存的会话与草稿（不动服务器上的任何东西） */
export function clearLocalData(): number {
  const keys = ["faxiaozhi:v3:sid", "fzx:sid:consult", "fzx:sid:research", "fzx:sid:contract", "consult:draft",
    "research:topic", "research:purpose", "research:conditions", "research:selection", "contract:stance"];
  let removed = 0;
  for (const key of keys) {
    for (const store of [window.sessionStorage, window.localStorage]) {
      try {
        if (store.getItem(key) !== null) {
          store.removeItem(key);
          removed += 1;
        }
      } catch {
        /* 隐私模式写不进也读不到，忽略 */
      }
    }
  }
  return removed;
}

export async function exportsList(): Promise<ExportFile[]> {
  const response = await fetch(`${BFF}/exports`, { headers: { accept: "application/json" } });
  if (!response.ok) throw new Error("读取导出文件列表失败。");
  const data = (await response.json()) as { items?: ExportFile[] };
  return data.items ?? [];
}

/** 某一分支的会话状态（会话不存在返回 null，不抛错） */
export async function branchState(branch: BranchKey, sid: string | null): Promise<SessionState | null> {
  if (!sid) return null;
  try {
    return await api.state(sid);
  } catch {
    return null;
  }
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

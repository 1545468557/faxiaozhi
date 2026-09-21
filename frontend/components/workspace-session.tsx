"use client";

/**
 * 跨页面会话状态（阶段 3-1 精简版）
 *
 * 一个浏览器文档一份内存 Map：换页保留输入草稿，刷新即清（台账 D6：不写 localStorage、
 * 不把问题正文写进网址）。业务事实来源始终是后端快照，这里只放"还没提交的输入"。
 */

import { createContext, useCallback, useContext, useState, useSyncExternalStore, type ReactNode, type SetStateAction } from "react";
import { useRouter } from "next/navigation";

export type WorkspaceModule = "research" | "consult" | "contract";
export type WorkspaceEntry = { module: WorkspaceModule; query?: string };

function createSession() {
  const values = new Map<string, unknown>();
  const listeners = new Set<() => void>();
  return {
    values,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    set(key: string, value: unknown) {
      values.set(key, value);
      listeners.forEach(listener => listener());
    },
  };
}

const SessionContext = createContext<ReturnType<typeof createSession> | null>(null);

export function WorkspaceSessionProvider({ children }: { children: ReactNode }) {
  const [session] = useState(createSession);
  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;
}

function useSession() {
  const session = useContext(SessionContext);
  if (!session) throw new Error("WorkspaceSessionProvider is required");
  return session;
}

export function useSessionState<T>(key: string, initial: T): [T, (value: SetStateAction<T>) => void] {
  const session = useSession();
  const [fallback] = useState(() => initial);
  const read = useCallback(() => (session.values.has(key) ? (session.values.get(key) as T) : fallback), [session, key, fallback]);
  const serverRead = useCallback(() => fallback, [fallback]);
  const value = useSyncExternalStore(session.subscribe, read, serverRead);
  const update = useCallback(
    (next: SetStateAction<T>) => {
      session.set(key, typeof next === "function" ? (next as (previous: T) => T)(read()) : next);
    },
    [session, key, read],
  );
  return [value, update];
}

export function useWorkspaceNavigation() {
  const session = useSession();
  const router = useRouter();
  const openWorkspace = useCallback(
    (entry: WorkspaceEntry) => {
      if (entry.query) {
        // 草稿：研究议题 / 咨询问题各存一份，进入页面后由用户自己点提交（不自动触发付费调用）
        session.set(entry.module === "consult" ? "consult:question" : "research:topic", entry.query);
      }
      router.push(`/${entry.module}`);
    },
    [session, router],
  );
  return { openWorkspace };
}

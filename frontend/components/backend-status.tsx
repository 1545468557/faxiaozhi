"use client";

/**
 * 后端连接状态（阶段 3-1）
 *
 * 数据来自 `GET /api/bootstrap`（经 BFF）。它同时承担一件事：
 * **把「离线模式」显示出来**——后端在 stub 模式下运行时，界面必须明确标注，
 * 不能让离线结果看起来像真实结果（AGENTS.md 底线 10 + 红线 5）。
 */

import { useCallback, useEffect, useState } from "react";
import { api, describeError, type Bootstrap } from "@/lib/api";

export type BackendState = {
  loading: boolean;
  bootstrap: Bootstrap | null;
  /** 后端连不上时的可读原因 */
  error: string | null;
  reload: () => void;
};

export function useBackend(poll = false): BackendState {
  const [loading, setLoading] = useState(true);
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const data = await api.bootstrap();
        if (!alive) return;
        setBootstrap(data);
        setError(null);
      } catch (cause) {
        if (!alive) return;
        setBootstrap(null);
        const info = describeError(
          cause && typeof cause === "object" && "code" in cause ? String((cause as { code: string }).code) : undefined,
          cause instanceof Error ? cause.message : undefined,
        );
        setError(info.fallback);
      } finally {
        if (alive) setLoading(false);
      }
    };
    void load();
    if (!poll) return () => {
      alive = false;
    };
    const timer = setInterval(() => void load(), 15000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [nonce, poll]);

  const reload = useCallback(() => {
    setLoading(true);
    setNonce(value => value + 1);
  }, []);

  return { loading, bootstrap, error, reload };
}

export function BackendStatus({ className = "" }: { className?: string }) {
  const { loading, bootstrap, error, reload } = useBackend();

  // 正常状态不占用办案工作区；只在需要用户采取行动或知悉限制时提示。
  if (loading && !bootstrap && !error) return null;

  if (error || !bootstrap) {
    return (
      <div className={`fzx-status ${className}`} data-tone="error" role="alert">
        <span className="fzx-dot" aria-hidden="true" />
        <div className="fzx-status-text">
          暂时无法使用服务，请重新检查；若仍未恢复，请联系服务管理员。
          {error && (
            <details className="fzx-status-meta">
              <summary>查看连接详情</summary>
              <p>{error}</p>
            </details>
          )}
        </div>
        <button type="button" onClick={reload} disabled={loading}>{loading ? "检查中…" : "重新检查"}</button>
      </div>
    );
  }

  const notices: string[] = [];
  if (bootstrap.model.is_stub) {
    notices.push("当前为离线演示，分析内容为示例结果，不可用于办案。");
  } else if (!bootstrap.model.key_present) {
    notices.push("智能分析服务尚未就绪，请联系服务管理员完成开通后重新检查。");
  }

  if (!bootstrap.mcp.configured || !bootstrap.mcp.token_present) {
    notices.push("法条与案例检索尚未就绪，外部依据核验可能受限。请联系服务管理员完成开通后重新检查。");
  } else if (bootstrap.mcp.available === false) {
    notices.push("法条与案例检索暂不可用，请稍后重新检查。未取得依据不代表没有相关规定。");
  }

  // 配置存在不等于检索成功，更不代表结果已通过核验。
  if (notices.length === 0) return null;

  return (
    <div className={`fzx-status ${className}`} data-tone="warn" role="status">
      <span className="fzx-dot" aria-hidden="true" />
      <span className="fzx-status-text">
        {notices.join(" ")}
      </span>
      <button type="button" onClick={reload} disabled={loading}>{loading ? "检查中…" : "重新检查"}</button>
    </div>
  );
}

"use client";

/**
 * 「我的」（迭代 3 追加）
 *
 * 现在<b>没有账号体系</b>，所以这一页是"本机工作台"：把你这台电脑上的东西集中到一页。
 * 四个区块都能显示真实数据：
 *   ① 我的会话（三条链路 + 首页快速问答，含"空闲 30 分钟自动结束"的说明，可一键清空本机数据）
 *   ② 我上传的材料（<b>当前会话内</b>；并如实写明原文不落盘）
 *   ③ 我导出的文件（可重新下载；现在列的是<b>本机全部导出</b>，有账号后必须按用户过滤）
 *   ④ 这台机器的真实使用情况（不含离线演示与测试数据）
 * 末尾一段"登录后可用"，只作说明，<b>不做假入口</b>。
 */

import { useCallback, useEffect, useState } from "react";
import { Download, FileText, FolderOpen, Info, KeyRound, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import {
  BRANCH_LABEL,
  branchState,
  clearLocalData,
  exportsList,
  formatBytes,
  localSessionIds,
  quickChatSessionId,
  type BranchKey,
  type ExportFile,
} from "@/lib/api/mine";
import type { MaterialItem, SessionState } from "@/lib/api/types";

type BranchRow = { branch: BranchKey; sid: string | null; state: SessionState | null };

export function MineDashboard() {
  const [rows, setRows] = useState<BranchRow[]>([]);
  const [quickChat, setQuickChat] = useState<string | null>(null);
  const [files, setFiles] = useState<ExportFile[]>([]);
  const [metrics, setMetrics] = useState<Record<string, unknown> | null>(null);
  const [notice, setNotice] = useState("");
  const [confirming, setConfirming] = useState(false);

  const load = useCallback(async () => {
    const ids = localSessionIds();
    setQuickChat(quickChatSessionId());
    const loaded = await Promise.all(
      (Object.keys(ids) as BranchKey[]).map(async branch => ({
        branch,
        sid: ids[branch],
        state: await branchState(branch, ids[branch]),
      })),
    );
    setRows(loaded);
    try {
      setFiles(await exportsList());
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "读取导出文件失败。");
    }
    try {
      setMetrics(await api.metrics("real"));
    } catch {
      setMetrics(null);
    }
  }, []);

  useEffect(() => {
    // 只在挂载时拉一次本机数据。这里的数据全部来自浏览器本地存储与后端只读接口，
    // 属于"订阅外部数据源"的正当用法；load 内部都是 await 之后的 setState。
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 挂载时拉取本机数据
    void load();
  }, [load]);

  const materials: { branch: BranchKey; item: MaterialItem }[] = [];
  for (const row of rows) {
    for (const item of (row.state?.materials ?? []) as MaterialItem[]) {
      materials.push({ branch: row.branch, item });
    }
  }

  const statusText = (row: BranchRow) => {
    if (!row.sid) return "没有会话（还没用过）";
    if (!row.state) return "会话已失效（服务重启或超过 30 分钟空闲）";
    if (row.state.failed_step) return `上次没跑完：卡在「${row.state.failed_step}」`;
    if (row.state.status === "active") return "正在运行中";
    if (row.state.sample?.locked) return `已完成（确认了 ${row.state.sample.confirmed?.length ?? 0} 篇样本）`;
    return "已完成，可以继续看结果";
  };

  const onClear = () => {
    const removed = clearLocalData();
    setConfirming(false);
    setNotice(`已清空本机保存的 ${removed} 项数据（浏览器里的会话与草稿）。服务器上的导出文件不受影响。`);
    void load();
  };

  const callStats = (metrics?.by_event ?? {}) as Record<string, number>;
  const citations = (metrics?.citations ?? {}) as Record<string, number>;

  return (
    <div className="ct-wrap">
      <header className="ct-head">
        <h1>我的</h1>
        <p>这一页显示你这台电脑上的东西：会话、上传的材料、导出的文件、用量。<b>暂不需要登录</b>；接入账号后这里会变成你自己的记录。</p>
      </header>

      {notice && (
        <p className="ct-blocked" role="status" style={{ marginBottom: 14 }}>
          <Info size={14} aria-hidden="true" />
          {notice}
        </p>
      )}

      {/* ① 我的会话 */}
      <section className="ct-card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, marginBottom: 8 }}>我的会话</h2>
        <p className="ct-mini" style={{ marginBottom: 10 }}>
          会话是临时的：<b>空闲 30 分钟自动结束</b>，服务重启也会失效；材料原文全程只在内存里。
        </p>
        <ul className="ct-range" style={{ paddingLeft: 0, listStyle: "none" }}>
          <li style={{ marginBottom: 6 }}>
            <b>法律问答（首页快速问答）</b>：{quickChat ? "有会话，可以继续聊" : "没有会话（还没用过）"}
          </li>
          {rows.map(row => (
            <li key={row.branch} style={{ marginBottom: 6 }}>
              <b>{BRANCH_LABEL[row.branch]}</b>：{statusText(row)}
            </li>
          ))}
        </ul>
        <div className="ct-actions">
          <button type="button" className="ct-btn" onClick={() => setConfirming(true)}>
            <Trash2 size={14} aria-hidden="true" />
            清空本机数据
          </button>
        </div>
        {confirming && (
          <div className="ct-more" style={{ marginTop: 10 }}>
            <p className="ct-mini">
              会清掉浏览器里保存的会话 id 与草稿（三条链路 + 首页问答）。<b>不会</b>删除服务器上的导出文件，
              也<b>不会</b>删除任何法规库内容。确认清空吗？
            </p>
            <div className="ct-actions">
              <button type="button" className="ct-btn ct-btn-primary" onClick={onClear}>
                确认清空
              </button>
              <button type="button" className="ct-btn ct-btn-ghost" onClick={() => setConfirming(false)}>
                取消
              </button>
            </div>
          </div>
        )}
      </section>

      {/* ② 我上传的材料 */}
      <section className="ct-card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, marginBottom: 8 }}>我上传的材料（本次会话内）</h2>
        {materials.length === 0 ? (
          <p className="ct-mini">当前没有上传过的材料。</p>
        ) : (
          <div>
            {materials.map(({ branch, item }) => (
              <div className="ct-file" key={`${branch}-${item.source_id}`} style={{ marginBottom: 8 }}>
                <FileText size={18} aria-hidden="true" />
                <div>
                  <strong>{String(item.filename ?? "未命名")}</strong>
                  <span>
                    {BRANCH_LABEL[branch]} · {String(item.role_label ?? "")} ·{" "}
                    {item.verified ? "已核验" : "未核验"} {item.superseded ? "· 已被新版本取代" : ""}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
        <p className="ct-mini" style={{ marginTop: 8 }}>
          <b>如实说明</b>：这里只显示材料的登记信息（文件名、角色、核验状态）；<b>原文不落盘</b>，
          服务重启或会话过期后原文就无法再取回。
        </p>
      </section>

      {/* ③ 我导出的文件 */}
      <section className="ct-card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, marginBottom: 8 }}>我导出的文件</h2>
        {files.length === 0 ? (
          <p className="ct-mini">还没有导出过文件。</p>
        ) : (
          <>
            <ul className="ct-range" style={{ paddingLeft: 0, listStyle: "none" }}>
              {files.slice(0, 10).map(file => (
                <li key={file.name} style={{ marginBottom: 8, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <FolderOpen size={15} aria-hidden="true" />
                  <span style={{ flex: 1, minWidth: 200 }}>{file.name}</span>
                  <span className="ct-mini">
                    {formatBytes(file.bytes)} · {file.created_at.slice(0, 16).replace("T", " ")}
                  </span>
                  <a className="ct-btn ct-btn-ghost" href={`/api/bff${file.url}`} download>
                    <Download size={14} aria-hidden="true" />
                    重新下载
                  </a>
                </li>
              ))}
            </ul>
            <p className="ct-mini" style={{ marginTop: 8 }}>
              共 {files.length} 个，这里显示最近 10 个。<b>如实说明</b>：现在还没有账号，所以列出的是
              <b>这台电脑上的全部导出文件</b>；接入账号后会改成只看自己的。
            </p>
          </>
        )}
      </section>

      {/* ④ 使用情况 */}
      <section className="ct-card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, marginBottom: 8 }}>这台机器的使用情况</h2>
        {metrics ? (
          <ul className="ct-range" style={{ paddingLeft: 0, listStyle: "none" }}>
            <li>真实运行记录：{String(metrics.events ?? 0)} 条（<b>只统计真实运行</b>，不含离线演示与测试）</li>
            <li>检索调用：{callStats.tool_call ?? 0} 次 · 门禁检查：{callStats.gate ?? 0} 次 · 导出：{callStats.export ?? 0} 次</li>
            <li>
              引用核验：通过 {citations.accepted ?? 0} 条 · 拦截 {citations.rejected ?? 0} 条 · 可溯源率{" "}
              {citations.traceable_rate ? `${Math.round(Number(citations.traceable_rate) * 100)}%` : "—"}
            </li>
            <li>材料上传：{callStats.material_uploaded ?? 0} 次 · 本人核验：{callStats.material_verified ?? 0} 次</li>
          </ul>
        ) : (
          <p className="ct-mini">暂时取不到统计。</p>
        )}
      </section>

      {/* 还没做完的（说清"跟登录无关，是功能还没做"） */}
      <section className="ct-card">
        <h2 style={{ fontSize: 18, marginBottom: 8 }}>
          <KeyRound size={16} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />
          还没做完的（跟「有没有登录」无关，是功能还没开发）
        </h2>
        <p className="ct-mini" style={{ marginBottom: 8 }}>
          你已经登录了（当前账号在左下角「设置」里能看到）。下面这几项**不是登录的问题**，是功能还没做：
        </p>
        <ul className="ct-range">
          <li>
            <b>历史记录</b>：跨设备、重启后还能看到问过什么、审过什么 —— 需要把结果**落库**（下一步 2-3）
          </li>
          <li>
            <b>导出文件按账号隔离</b>：现在这一页列的是**这台电脑上的全部导出文件**；
            要改成「只看到自己的」，需要给会话/运行/导出加**归属**（下一步 2-2，这是底线要求）
          </li>
          <li>
            <b>材料长期保存</b>：现在的口径是「原文不落盘」，要长期保存得先解决加密与合规
          </li>
          <li>
            <b>按人的用量明细</b>：现在的统计是「这台机器的」，不是「你的」
          </li>
        </ul>
        <p className="ct-mini" style={{ marginTop: 8 }}>
          登录现在能干的事：未登录不能使用 · 在「设置」里改密码、退出登录 · 管理员生成邀请码。
        </p>
      </section>
    </div>
  );
}

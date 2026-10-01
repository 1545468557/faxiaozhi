"use client";

import { useState } from "react";
import { Info, Trash2 } from "lucide-react";
import { clearLocalData } from "@/lib/api/mine";
import { BackendStatus } from "@/components/backend-status";

export function SettingsPanel() {
  const [notice, setNotice] = useState("");

  return (
    <div className="ct-wrap">
      <header className="ct-head">
        <h1>设置</h1>
        <p>连接状态与本机数据。</p>
      </header>
      <BackendStatus />

      {notice && (
        <p className="ct-blocked" role="status" style={{ marginBottom: 14 }}>
          <Info size={14} aria-hidden="true" />
          {notice}
        </p>
      )}

      <section className="ct-card">
        <h2 style={{ fontSize: 18, marginBottom: 8 }}>
          <Trash2 size={16} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />
          本机数据
        </h2>
        <p className="ct-mini">清掉浏览器里保存的会话与草稿，不会删除服务器上的历史对话或导出文件。</p>
        <div className="ct-actions">
          <button type="button" className="ct-btn" onClick={() => setNotice(`已清空本机保存的 ${clearLocalData()} 项数据。`)}>
            清空本机数据
          </button>
        </div>
      </section>
    </div>
  );
}

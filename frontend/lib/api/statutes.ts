/**
 * 本地法规库客户端（迭代 3）
 *
 * 三条口径与后端一致：
 * 1. **默认只出「现行有效」**（`status=any` 才含已修改/已废止）；
 * 2. 支持「法规名 + 条号」精准查（后端已做数字归一化与「第X条之一」）；
 * 3. 界面不显示"数据截至时间"（产品经理明确要求）；该字段仍在响应里，供排查使用。
 */

import { BFF, ApiError } from "./client";

const BASE = `${BFF}/statutes`;

export type StatuteHit = {
  bbbs: string;
  title: string;
  organ: string;
  status_code: string;
  status_text: string;
  publish_date: string;
  effective_date: string;
  no: string;
  text: string;
  rank: number;
};

export type StatuteSearchResult = {
  input: string;
  parsed: { name: string; article: string; keyword: string };
  data_as_of: string;
  total: number;
  items: StatuteHit[];
};

export type StatuteDetail = {
  bbbs: string;
  title: string;
  category: string;
  organ: string;
  status_code: string;
  status_text: string;
  publish_date: string;
  effective_date: string;
  article_count: number;
  articles: { no: string; text: string }[];
};

export type StatutesStatus = {
  statutes: number;
  articles: number;
  by_status: Record<string, number>;
  data_as_of: string;
};

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { headers: { accept: "application/json" } });
  const payload = (await response.json().catch(() => null)) as
    | (T & { error?: { code?: string; message?: string } })
    | null;
  if (!response.ok) {
    const code = payload?.error?.code ?? "request_failed";
    throw new ApiError(code, payload?.error?.message ?? "法规库请求失败。", response.status);
  }
  return payload as T;
}

export const statutesApi = {
  status: () => get<StatutesStatus>("/status"),
  search: (query: string, options: { includeInvalid?: boolean; limit?: number } = {}) => {
    const params = new URLSearchParams({ q: query, status: options.includeInvalid ? "any" : "valid" });
    params.set("limit", String(options.limit ?? 10));
    return get<StatuteSearchResult>(`/search?${params.toString()}`);
  },
  detail: (bbbs: string) => get<StatuteDetail>(`/detail?bbbs=${encodeURIComponent(bbbs)}`),
};

/** 复制到剪贴板的引用格式：统一《法规名》第X条 + 效力状态 */
export function citationOf(hit: { title: string; no: string; status_text: string }): string {
  return `《${hit.title}》${hit.no}（${hit.status_text}）`;
}

/**
 * 把回答文本里的法规/案例名，对回本次检索到的材料，做成**行内**引用。
 *
 * 为什么要做：v3 放弃了「单独一个引用区 + 逐条核验」。依据就在说话的句子里，
 * 点一下能看原文 —— 而不是让用户在正文和引用列表之间来回对照。
 *
 * 两条保守规则（宁可少链、不可错链）：
 * 1. **同一个词对应多条材料时不链**：四份材料都来自《民法典》，那「民法典」三个字
 *    指哪一条并不确定，链上去就是误导。此时只有写全「《中华人民共和国民法典》第七百二十五条」才链。
 * 2. **长词优先**：先匹配最长的标识，避免「民法典」抢先命中「《中华人民共和国民法典》第七百二十五条」的前半截。
 */

import type { V3Source } from "@/lib/api/v3";

export type CitePart = {
  text: string;
  /** 命中材料时的下标；未命中为 undefined */
  index?: number;
};

/** 一份材料在正文里可能被写成的几种样子 */
function anchorsFor(source: V3Source): string[] {
  const found = new Set<string>();
  for (const raw of [source.identifier, source.title]) {
    const text = (raw || "").trim();
    if (!text) continue;
    found.add(text);

    // 《中华人民共和国民法典》→《民法典》/ 中华人民共和国民法典 / 民法典
    const names: string[] = [];
    for (const match of text.matchAll(/《([^》]+)》/g)) {
      const inner = match[1].trim();
      if (!inner) continue;
      found.add(`《${inner}》`);
      found.add(inner);
      names.push(`《${inner}》`, inner);
      const short = inner.replace(/^中华人民共和国/, "");
      if (short !== inner && short.length >= 2) {
        found.add(`《${short}》`);
        found.add(short);
        names.push(`《${short}》`, short);
      }
    }

    // 模型通常写「《民法典》第七百二十五条」，而材料标识是全称：
    // 把法名变体与条号后缀拼起来，这些组合各自唯一，能安全地链到同一条材料。
    const tail = text.includes("》") ? text.slice(text.lastIndexOf("》") + 1).trim() : "";
    if (tail) {
      for (const name of names) {
        found.add(`${name}${tail}`);
        found.add(`${name} ${tail}`);
      }
    }
  }
  // 太短的词（如「法」「条例」）不参与匹配，否则满屏乱链
  return [...found].filter(key => key.length >= 2);
}

const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function splitByCitations(text: string, sources: V3Source[]): CitePart[] {
  if (!text || !sources.length) return [{ text }];

  const owner = new Map<string, number>();
  const ambiguous = new Set<string>();
  sources.forEach((source, index) => {
    for (const key of anchorsFor(source)) {
      if (owner.has(key)) {
        ambiguous.add(key);
        continue;
      }
      owner.set(key, index);
    }
  });

  const keys = [...owner.keys()].filter(key => !ambiguous.has(key)).sort((a, b) => b.length - a.length);
  if (!keys.length) return [{ text }];

  const pattern = new RegExp(keys.map(escape).join("|"), "g");
  const parts: CitePart[] = [];
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > cursor) parts.push({ text: text.slice(cursor, start) });
    parts.push({ text: match[0], index: owner.get(match[0]) });
    cursor = start + match[0].length;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor) });
  return parts.filter(part => part.text);
}

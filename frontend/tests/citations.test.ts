/**
 * 行内引用的匹配规则测试
 *
 * 这里测的是「宁可少链、不可错链」这条口径真的被实现了：
 * 一个词对应多条材料时不许链（链接指错地方比不链接更糟）。
 */

import { describe, expect, it } from "vitest";
import { splitByCitations } from "@/components/consult/citations";
import type { V3Source } from "@/lib/api/v3";

const source = (over: Partial<V3Source>): V3Source => ({
  kind: "statute",
  title: "",
  identifier: "",
  court: "",
  decided_on: "",
  uri: "",
  origin_text: "",
  quote: "",
  ...over,
});

const linkedTexts = (text: string, sources: V3Source[]) =>
  splitByCitations(text, sources)
    .filter(part => part.index !== undefined)
    .map(part => part.text);

const LINK = source({
  identifier: "《中华人民共和国民法典》第七百二十五条",
  title: "中华人民共和国民法典",
});

describe("splitByCitations", () => {
  it("没有材料时原样返回，不做任何切割", () => {
    const text = "《民法典》第七百二十五条规定了买卖不破租赁。";
    expect(splitByCitations(text, [])).toEqual([{ text }]);
  });

  it("写全称时整段命中的是一个链接，不会被拆成两截", () => {
    const text = "依据《中华人民共和国民法典》第七百二十五条，你可以继续住。";
    const parts = splitByCitations(text, [LINK]);
    expect(linkedTexts(text, [LINK])).toEqual(["《中华人民共和国民法典》第七百二十五条"]);
    // 拼接回去必须和原文一字不差（不能吞字、不能重复）
    expect(parts.map(part => part.text).join("")).toBe(text);
    expect(parts.find(part => part.index !== undefined)?.index).toBe(0);
  });

  it("模型写简称 + 条号时也能链上", () => {
    const text = "按《民法典》第七百二十五条，买卖不破租赁。";
    expect(linkedTexts(text, [LINK])).toEqual(["《民法典》第七百二十五条"]);
  });

  it("同一部法的不同条号不会互相抢链", () => {
    const other = source({ identifier: "《中华人民共和国民法典》第七百三十四条", title: "中华人民共和国民法典" });
    const text = "《民法典》第七百二十五条与《民法典》第七百三十四条都相关。";
    const parts = splitByCitations(text, [LINK, other]);
    expect(parts.filter(part => part.index !== undefined).map(part => [part.text, part.index])).toEqual([
      ["《民法典》第七百二十五条", 0],
      ["《民法典》第七百三十四条", 1],
    ]);
  });

  it("只写「民法典」而存在多条同法材料时，不链（避免指错条）", () => {
    const other = source({ identifier: "《中华人民共和国民法典》第七百三十四条", title: "中华人民共和国民法典" });
    const text = "民法典管这件事。";
    expect(linkedTexts(text, [LINK, other])).toEqual([]);
    // 负对照：只有一条民法典材料时，同样的字必须链上 —— 证明上面那个空数组不是匹配逻辑整体失灵
    expect(linkedTexts(text, [LINK])).toEqual(["民法典"]);
  });

  it("判例的案号也能链", () => {
    const judgment = source({ kind: "case", identifier: "（2021）京01民终1234号", title: "某某与某某租赁合同纠纷" });
    const text = "可参考（2021）京01民终1234号的裁判思路。";
    expect(linkedTexts(text, [judgment])).toEqual(["（2021）京01民终1234号"]);
  });

  it("正文里没出现的材料不会被凭空造出链接", () => {
    const text = "这只是一段没有引用的说明。";
    const parts = splitByCitations(text, [LINK]);
    expect(parts).toEqual([{ text }]);
  });
});

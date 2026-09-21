/**
 * 示例合同（合成）的底线（迭代 1-1）
 *
 * 产品经理验收时提出「没有合同示例」——手上没合同的人试不了这个功能，于是加了内置示例。
 * 这里锁住两条硬要求：
 * 1. 文件名与正文都必须写明是**合成示例**（不能让人误当成真合同）；
 * 2. 正文要有真实内容（够长、够条款），否则后端会当"内容过少"拒收。
 */

import { describe, expect, it } from "vitest";
import { SAMPLE_CONTRACT_NAME, SAMPLE_CONTRACT_TEXT } from "@/components/contract/sample-contract";

describe("示例合同（合成）", () => {
  it("文件名写明是合成示例", () => {
    expect(SAMPLE_CONTRACT_NAME).toContain("示例");
    expect(SAMPLE_CONTRACT_NAME).toContain("合成");
  });

  it("正文首尾都声明虚构、不构成真实法律关系", () => {
    expect(SAMPLE_CONTRACT_TEXT).toContain("合成示例");
    expect(SAMPLE_CONTRACT_TEXT).toContain("虚构");
    expect(SAMPLE_CONTRACT_TEXT).toContain("不构成任何真实法律关系");
  });

  it("含多名条款且足够长（否则后端会以内容过少拒收）", () => {
    expect(SAMPLE_CONTRACT_TEXT.length).toBeGreaterThan(400);
    for (const clause of ["第一条", "第二条", "第三条", "第七条"]) {
      expect(SAMPLE_CONTRACT_TEXT).toContain(clause);
    }
  });

  it("不含任何真实可识别的个人信息/机构名（只用示例称呼）", () => {
    expect(SAMPLE_CONTRACT_TEXT).toContain("示例出租方");
    expect(SAMPLE_CONTRACT_TEXT).not.toMatch(/[\u4e00-\u9fa5]{2,3}身份证|统一社会信用代码/);
  });
});

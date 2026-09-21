/**
 * 错误码文案与行为（阶段 3-1）
 *
 * 覆盖交接契约 §4.2 的 34 条错误码 + 前端自己产生的 5 条传输层错误。
 * 重点断言红线 3：「接口失败」不能显示成「没有相关规定」。
 */

import { describe, expect, it } from "vitest";
import { describeError, errorText, KNOWN_ERROR_CODES } from "@/lib/api/errors";

/** 交接契约 §4.2 列出的错误码（逐条必须存在） */
const CONTRACT_CODES = [
  "session_not_found",
  "empty_input",
  "empty_material",
  "empty_sample",
  "checkpoint_mismatch",
  "export_blocked",
  "mcp_unavailable",
  "model_unavailable",
  "concurrency_queued",
  "supplement_exhausted",
  "rounds_exhausted",
  "material_not_recoverable",
  "consult_not_recoverable",
  "contract_not_recoverable",
  "unsupported_file",
  "file_too_large",
  "type_mismatch",
  "permission_denied",
  "internal_error",
  "run_not_found",
  "not_found",
  "run_expired",
  "schema_invalid",
  "invalid_request",
  "sample_not_locked",
  "session_busy",
  "no_failed_step",
  "retry_exhausted",
  "invalid_source",
  "not_conflict",
  "too_many_files",
  "workflow_step_limit",
  "parse_error",
  "path_escape",
  "contract_parse_failed",
];

const LOCAL_CODES = ["backend_unreachable", "backend_blocked", "stream_interrupted", "download_failed"];

describe("错误码表", () => {
  it("契约里的每条错误码都有本地兜底文案", () => {
    const missing = CONTRACT_CODES.filter(code => !KNOWN_ERROR_CODES.includes(code));
    expect(missing).toEqual([]);
  });

  it("每条错误码的兜底文案都非空", () => {
    for (const code of KNOWN_ERROR_CODES) {
      const info = describeError(code);
      expect(info.fallback.length, code).toBeGreaterThan(4);
    }
  });

  it("前端传输层错误码也在表里", () => {
    for (const code of LOCAL_CODES) expect(KNOWN_ERROR_CODES).toContain(code);
  });
});

describe("后端文案优先", () => {
  it("后端给了 message 就用后端的", () => {
    const info = describeError("empty_sample", "后端自定义：请至少选 2 篇。");
    expect(info.fallback).toBe("后端自定义：请至少选 2 篇。");
  });

  it("后端 message 为空才用本地兜底", () => {
    const info = describeError("empty_sample", "   ");
    expect(info.fallback).toContain("请至少确认 1 篇样本");
  });

  it("errorText 与 describeError 口径一致", () => {
    expect(errorText("mcp_unavailable", "短文")).toBe("短文");
    expect(errorText("mcp_unavailable")).toContain("检索接口调用失败");
  });

  it("未知错误码降级为 internal_error", () => {
    const info = describeError("some_new_code_from_backend_v2");
    expect(info.code).toBe("internal_error");
    expect(info.retryable).toBe(true);
  });

  it("没有错误码时也降级为 internal_error", () => {
    expect(describeError(undefined).code).toBe("internal_error");
  });
});

describe("红线 3：接口失败 ≠ 没有相关规定", () => {
  it("mcp_unavailable 标记为需要强调「不等于没有相关规定」", () => {
    const info = describeError("mcp_unavailable");
    expect(info.mcpWarning).toBe(true);
    expect(info.fallback).toContain("这不等于");
    expect(info.fallback).toContain("可点击重试");
  });

  it("只有检索失败这一类才带 mcpWarning", () => {
    for (const code of ["empty_sample", "export_blocked", "rounds_exhausted", "internal_error"]) {
      expect(describeError(code).mcpWarning, code).toBeFalsy();
    }
  });

  it("mcp_unavailable 文案里不出现「没有相关规定」这种误导说法", () => {
    const text = describeError("mcp_unavailable").fallback;
    expect(text).not.toMatch(/无相关案例。$/);
    expect(text).toContain("未获得可核验依据");
  });
});

describe("可重试与不可重试", () => {
  it("检索/模型失败可重试", () => {
    expect(describeError("mcp_unavailable").retryable).toBe(true);
    expect(describeError("model_unavailable").retryable).toBe(true);
  });

  it("导出被拦不可重试（要先去解决门禁）", () => {
    expect(describeError("export_blocked").retryable).toBe(false);
  });

  it("并发排队可重试", () => {
    expect(describeError("concurrency_queued").retryable).toBe(true);
  });

  it("追问轮数到上限是正常收尾，不是错误", () => {
    const info = describeError("rounds_exhausted");
    expect(info.retryable).toBe(false);
    expect(info.tone).toBe("info");
  });
});

describe("隐私：内容不落盘的不可恢复错误", () => {
  it("三种 *_not_recoverable 都要求重新发起", () => {
    for (const code of ["material_not_recoverable", "consult_not_recoverable", "contract_not_recoverable"]) {
      expect(describeError(code).restartRequired, code).toBe(true);
    }
  });

  it("普通错误不要求重新发起", () => {
    expect(describeError("export_blocked").restartRequired).toBeFalsy();
    expect(describeError("session_not_found").restartRequired).toBeFalsy();
  });
});

describe("后端未启动的处理", () => {
  it("backend_unreachable 给出可执行提示且可重试", () => {
    const info = describeError("backend_unreachable");
    expect(info.fallback).toContain("后端");
    expect(info.retryable).toBe(true);
    expect(info.tone).toBe("error");
  });

  it("本机限制被触发时是权限问题，不给重试", () => {
    expect(describeError("backend_blocked").retryable).toBe(false);
    expect(describeError("permission_denied").retryable).toBe(false);
  });
});

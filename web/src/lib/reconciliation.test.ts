import { describe, expect, it } from "vitest";

import {
  evidenceArtifactFromState,
  parseEvidenceArtifact,
  publicNoticeFromState,
} from "./reconciliation";

describe("parseEvidenceArtifact", () => {
  it("accepts a versioned citation", () => {
    const result = parseEvidenceArtifact({
      caseId: "CASE-1001",
      status: "ANALYSIS_READY",
      citations: [
        {
          chunkId: "rule-alipay-v1-amount",
          source: "alipay-v1.md",
          section: "金额差异",
          ruleVersion: "RULE-ALIPAY-V1",
          content: "金额必须完全一致。",
        },
      ],
    });

    expect(result.citations[0].ruleVersion).toBe("RULE-ALIPAY-V1");
  });

  it("maps the graph state into display-safe evidence", () => {
    const result = evidenceArtifactFromState({
      case_id: "CASE-1001",
      collected_facts: {
        case: {
          channel: "ALIPAY",
          result_type: "AMOUNT_MISMATCH",
          status: "OPEN",
          rule_version: "RULE-ALIPAY-V1",
          channel_amount_cents: 10000,
        },
        payments: {
          payments: [{ amount_cents: 9900 }],
        },
      },
      citations: [
        {
          chunk_id: "rule-alipay-v1-amount",
          source: "alipay-v1.md",
          section: "金额差异",
          rule_version: "RULE-ALIPAY-V1",
          content: "金额必须完全一致。",
        },
      ],
      final_response: {
        status: "READY",
        case_id: "CASE-1001",
        summary: "渠道金额与内部支付金额相差 1.00 元。",
      },
    });

    expect(result).toMatchObject({
      caseId: "CASE-1001",
      status: "ANALYSIS_READY",
      summary: "渠道金额与内部支付金额相差 1.00 元。",
    });
    expect(result?.facts).toContainEqual({
      label: "渠道金额",
      value: "100.00 元",
    });
    expect(result?.facts).toEqual(
      expect.arrayContaining([
        { label: "渠道", value: "支付宝" },
        { label: "差异类型", value: "金额差异" },
        { label: "案件状态", value: "待处理" },
      ]),
    );
    expect(result?.citations[0]).toMatchObject({
      chunkId: "rule-alipay-v1-amount",
      ruleVersion: "RULE-ALIPAY-V1",
    });
  });

  it("rejects malformed graph citations without crashing the workspace", () => {
    const result = evidenceArtifactFromState({
      case_id: "CASE-1001",
      citations: [{ source: "alipay-v1.md" }],
      final_response: {
        status: "READY",
        case_id: "CASE-1001",
        summary: "证据格式不完整。",
      },
    });

    expect(result).toBeNull();
  });

  it("keeps validated evidence visible after a rejected resolution", () => {
    const result = evidenceArtifactFromState({
      case_id: "CASE-1001",
      citations: [
        {
          chunk_id: "rule-alipay-v1-amount",
          source: "alipay-v1.md",
          section: "金额差异",
          rule_version: "RULE-ALIPAY-V1",
          content: "金额必须完全一致。",
        },
      ],
      final_response: {
        status: "REJECTED",
        case_id: "CASE-1001",
        summary: "用户已拒绝处理建议，案件保持未处理状态。",
      },
    });

    expect(result).toMatchObject({
      status: "ANALYSIS_READY",
      summary: "用户已拒绝处理建议，案件保持未处理状态。",
    });
    expect(result?.citations).toHaveLength(1);
  });

  it("shows a failed run as insufficient evidence", () => {
    const result = evidenceArtifactFromState({
      case_id: "CASE-1001",
      final_response: {
        status: "FAILED",
        case_id: "CASE-1001",
        error: {
          code: "DATABASE_UNAVAILABLE",
          message: "数据库暂时不可用，请稍后重试。",
        },
      },
    });

    expect(result).toMatchObject({
      status: "INSUFFICIENT_EVIDENCE",
      summary: "数据库暂时不可用，请稍后重试。",
    });
  });

  it("surfaces an invalid request without inventing a case ID", () => {
    const state = {
      case_id: null,
      final_response: {
        status: "INVALID_REQUEST",
        case_id: null,
        summary: "请求中缺少有效案件编号，格式应为 CASE-四位数字。",
      },
    };

    expect(evidenceArtifactFromState(state)).toBeNull();
    expect(publicNoticeFromState(state)).toEqual({
      tone: "warning",
      summary: "请求中缺少有效案件编号，格式应为 CASE-四位数字。",
    });
  });
});

import { z } from "zod";

export const evidenceArtifactSchema = z.object({
  caseId: z.string().regex(/^CASE-[0-9]{4}$/),
  status: z.enum(["ANALYSIS_READY", "INSUFFICIENT_EVIDENCE", "RESOLVED"]),
  summary: z.string().default(""),
  facts: z
    .array(z.object({ label: z.string(), value: z.string() }))
    .default([]),
  citations: z
    .array(
      z.object({
        chunkId: z.string(),
        source: z.string(),
        section: z.string(),
        ruleVersion: z.string(),
        content: z.string(),
      }),
    )
    .default([]),
});

export type EvidenceArtifact = z.infer<typeof evidenceArtifactSchema>;

const graphStateSchema = z.object({
  case_id: z
    .string()
    .regex(/^CASE-[0-9]{4}$/)
    .nullable()
    .optional(),
  collected_facts: z.record(z.string(), z.unknown()).default({}),
  citations: z.array(z.record(z.string(), z.unknown())).default([]),
  final_response: z.record(z.string(), z.unknown()).optional(),
});

const statusMap = {
  READY: "ANALYSIS_READY",
  INSUFFICIENT_EVIDENCE: "INSUFFICIENT_EVIDENCE",
  RESOLVED: "RESOLVED",
  REJECTED: "ANALYSIS_READY",
  FAILED: "INSUFFICIENT_EVIDENCE",
} as const;

const channelCopy: Record<string, string> = {
  ALIPAY: "支付宝",
  WECHAT: "微信支付",
  UNIONPAY: "银联",
};

const resultTypeCopy: Record<string, string> = {
  AMOUNT_MISMATCH: "金额差异",
  CHANNEL_ONLY: "渠道单边",
  INTERNAL_ONLY: "内部单边",
};

const caseStatusCopy: Record<string, string> = {
  OPEN: "待处理",
  RESOLVED: "已解决",
};

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function formatAmount(value: unknown): string | null {
  return typeof value === "number" ? `${(value / 100).toFixed(2)} 元` : null;
}

function displayEnum(
  value: unknown,
  copy: Record<string, string>,
): string | null {
  return typeof value === "string" ? (copy[value] ?? value) : null;
}

export function parseEvidenceArtifact(value: unknown): EvidenceArtifact {
  return evidenceArtifactSchema.parse(value);
}

export function shouldUseResolutionConfirmation(
  actionName: string | undefined,
  allowedDecisions: readonly string[],
  actionCount: number,
): boolean {
  return (
    actionCount === 1 &&
    actionName === "resolve_case" &&
    allowedDecisions.includes("approve") &&
    allowedDecisions.includes("reject")
  );
}

export function publicNoticeFromState(value: unknown): {
  tone: "error" | "success" | "warning";
  summary: string;
  detail?: string;
} | null {
  const state = graphStateSchema.safeParse(value);
  if (!state.success || !state.data.final_response) return null;

  const response = state.data.final_response;
  if (response.status === "READY") return null;

  if (response.status === "REJECTED") {
    return {
      tone: "warning",
      summary: "已取消，本次未修改案件",
      detail: "案件状态：待处理",
    };
  }

  if (response.status === "RESOLVED") {
    const resolution = asRecord(response.resolution);
    const auditEventId = resolution.audit_event_id;
    return {
      tone: "success",
      summary: "案件已解决",
      ...(typeof auditEventId === "number"
        ? { detail: `审计事件编号：${auditEventId}` }
        : {}),
    };
  }

  const responseError = asRecord(response.error);
  const summary =
    typeof response.summary === "string"
      ? response.summary
      : typeof responseError.message === "string"
        ? responseError.message
        : null;
  if (!summary) return null;

  return {
    tone:
      response.status === "FAILED"
        ? "error"
        : response.status === "RESOLVED"
          ? "success"
          : "warning",
    summary,
  };
}

export function evidenceArtifactFromState(
  value: unknown,
): EvidenceArtifact | null {
  const state = graphStateSchema.safeParse(value);
  if (!state.success || !state.data.case_id || !state.data.final_response) {
    return null;
  }

  const response = state.data.final_response;
  const responseError = asRecord(response.error);
  const mappedStatus =
    typeof response.status === "string"
      ? statusMap[response.status as keyof typeof statusMap]
      : undefined;
  if (!mappedStatus) return null;

  const caseFacts = asRecord(state.data.collected_facts.case);
  const payments = asRecord(state.data.collected_facts.payments);
  const paymentRows = Array.isArray(payments.payments)
    ? payments.payments.map(asRecord)
    : [];
  const facts = [
    { label: "渠道", value: displayEnum(caseFacts.channel, channelCopy) },
    {
      label: "差异类型",
      value: displayEnum(caseFacts.result_type, resultTypeCopy),
    },
    {
      label: "案件状态",
      value: displayEnum(
        response.status === "RESOLVED" ? "RESOLVED" : caseFacts.status,
        caseStatusCopy,
      ),
    },
    {
      label: "渠道金额",
      value: formatAmount(caseFacts.channel_amount_cents),
    },
    {
      label: "内部支付金额",
      value: formatAmount(paymentRows[0]?.amount_cents),
    },
  ].flatMap(({ label, value }) =>
    typeof value === "string" ? [{ label, value }] : [],
  );

  const artifact = evidenceArtifactSchema.safeParse({
    caseId: state.data.case_id,
    status: mappedStatus,
    summary:
      typeof response.summary === "string"
        ? response.summary
        : typeof responseError.message === "string"
          ? responseError.message
          : "",
    facts,
    citations: state.data.citations.map((citation) => ({
      chunkId: citation.chunk_id,
      source: citation.source,
      section: citation.section,
      ruleVersion: citation.rule_version,
      content: citation.content,
    })),
  });

  return artifact.success ? artifact.data : null;
}

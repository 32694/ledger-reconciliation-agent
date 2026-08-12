import {
  BookOpenText,
  CheckCircle2,
  CircleAlert,
  Database,
  FileSearch,
} from "lucide-react";

import type { EvidenceArtifact } from "@/lib/reconciliation";
import { cn } from "@/lib/utils";

const statusCopy = {
  ANALYSIS_READY: {
    label: "分析完成",
    className: "border-amber-200 bg-amber-50 text-amber-800",
    icon: CircleAlert,
  },
  INSUFFICIENT_EVIDENCE: {
    label: "证据不足",
    className: "border-rose-200 bg-rose-50 text-rose-700",
    icon: FileSearch,
  },
  RESOLVED: {
    label: "已解决",
    className: "border-emerald-200 bg-emerald-50 text-emerald-800",
    icon: CheckCircle2,
  },
} as const;

const evidenceStatusCopy: Record<string, string> = {
  MISSING_CASE: "未读取到案件记录",
  MISSING_STRUCTURED_FACTS: "支付或账本证据不完整",
  MISSING_RULE: "未找到案件锁定的规则版本",
  INCONSISTENT_STRUCTURED_FACTS: "支付与账本结构化证据相互矛盾",
  TOOL_BUDGET_EXHAUSTED: "工具调用预算已耗尽",
  INVALID_MODEL_OUTPUT: "模型输出未通过结构校验",
  UNKNOWN: "依赖服务暂时不可用",
};

export function EvidencePanel({
  artifact,
  evidenceStatus,
}: {
  artifact: EvidenceArtifact | null;
  evidenceStatus?: string;
}) {
  if (!artifact) {
    return (
      <aside className="flex h-full flex-col bg-white">
        <PanelHeading />
        <div className="flex flex-1 flex-col items-center justify-center px-8 text-center">
          <span className="mb-4 flex size-11 items-center justify-center rounded-md border border-zinc-200 bg-zinc-50">
            <Database className="size-5 text-zinc-500" />
          </span>
          <p className="text-sm font-medium text-zinc-700">尚无案件证据</p>
          <p className="mt-1 text-xs leading-5 text-zinc-500">
            完成一次案件分析后，这里会显示结构化事实与规则引用。
          </p>
        </div>
      </aside>
    );
  }

  const status = statusCopy[artifact.status];
  const StatusIcon = status.icon;
  const ruleVersions = [
    ...new Set(artifact.citations.map((citation) => citation.ruleVersion)),
  ];

  return (
    <aside className="flex h-full flex-col overflow-hidden bg-white">
      <PanelHeading caseId={artifact.caseId} />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <section className="border-b border-zinc-200 px-5 py-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-xs font-semibold text-zinc-500">结论状态</h3>
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-xs font-medium",
                status.className,
              )}
            >
              <StatusIcon className="size-3.5" />
              {status.label}
            </span>
          </div>
          <p className="text-sm leading-6 text-zinc-800">{artifact.summary}</p>
          {artifact.status === "INSUFFICIENT_EVIDENCE" && (
            <p className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs leading-5 text-rose-700">
              缺失证据：
              {evidenceStatusCopy[evidenceStatus ?? ""] ?? "无法形成可靠结论"}
            </p>
          )}
        </section>

        <section className="border-b border-zinc-200 px-5 py-4">
          <div className="mb-3 flex items-center gap-2">
            <Database className="size-4 text-zinc-500" />
            <h3 className="text-xs font-semibold text-zinc-500">结构化事实</h3>
          </div>
          {artifact.facts.length > 0 ? (
            <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-2.5 text-sm">
              {artifact.facts.map((fact) => (
                <div
                  key={fact.label}
                  className="contents"
                >
                  <dt className="text-zinc-500">{fact.label}</dt>
                  <dd className="max-w-40 text-right font-medium break-words text-zinc-800">
                    {fact.value}
                  </dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-xs text-zinc-500">暂无可展示的结构化事实。</p>
          )}
        </section>

        <section className="border-b border-zinc-200 px-5 py-4">
          <div className="mb-3 flex items-center gap-2">
            <BookOpenText className="size-4 text-zinc-500" />
            <h3 className="text-xs font-semibold text-zinc-500">命中规则</h3>
          </div>
          {ruleVersions.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {ruleVersions.map((version) => (
                <code
                  key={version}
                  className="rounded bg-zinc-100 px-2 py-1 text-xs font-semibold text-zinc-700"
                >
                  {version}
                </code>
              ))}
            </div>
          ) : (
            <p className="text-xs text-zinc-500">未命中锁定版本的规则。</p>
          )}
        </section>

        <section className="px-5 py-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <FileSearch className="size-4 text-zinc-500" />
              <h3 className="text-xs font-semibold text-zinc-500">证据引用</h3>
            </div>
            <span className="text-[11px] text-zinc-400">
              {artifact.citations.length} 条
            </span>
          </div>
          <div className="space-y-2">
            {artifact.citations.map((citation) => (
              <article
                key={citation.chunkId}
                className="rounded-md border border-zinc-200 p-3"
              >
                <div className="mb-2 flex items-start justify-between gap-2">
                  <span className="text-xs font-semibold text-zinc-800">
                    {citation.section}
                  </span>
                  <code className="max-w-32 truncate text-[10px] text-zinc-400">
                    {citation.chunkId}
                  </code>
                </div>
                <p className="text-xs leading-5 text-zinc-600">
                  {citation.content}
                </p>
                <p className="mt-2 truncate text-[11px] text-zinc-400">
                  来源：{citation.source}
                </p>
              </article>
            ))}
          </div>
        </section>
      </div>
    </aside>
  );
}

function PanelHeading({ caseId }: { caseId?: string }) {
  return (
    <div className="flex h-14 shrink-0 items-center justify-between border-b border-zinc-200 px-5">
      <div>
        <h2 className="text-sm font-semibold text-zinc-900">证据面板</h2>
        <p className="text-[11px] text-zinc-500">仅展示本轮已验证证据</p>
      </div>
      {caseId && (
        <code className="rounded bg-zinc-100 px-2 py-1 text-xs font-semibold text-zinc-600">
          {caseId}
        </code>
      )}
    </div>
  );
}

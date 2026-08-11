import { CheckCircle2, Clock3, ShieldCheck, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";

type ResolutionDecision = "approve" | "reject";

const resolutionCodeCopy: Record<string, string> = {
  INTERNAL_CONFIRMED: "内部账本确认",
  CHANNEL_CONFIRMED: "支付渠道确认",
  IGNORED_TEST_DATA: "忽略测试数据",
  OTHER: "其他处理方式",
};

export function ResolutionConfirmation({
  args,
  isLoading,
  onDecision,
}: {
  args: Record<string, unknown>;
  isLoading: boolean;
  onDecision: (decision: ResolutionDecision) => void;
}) {
  const caseId = typeof args.case_id === "string" ? args.case_id : "-";
  const resolutionCode =
    typeof args.resolution_code === "string" ? args.resolution_code : "";
  const note = typeof args.note === "string" ? args.note : "-";

  return (
    <section className="w-full rounded-lg border border-zinc-200 bg-white shadow-sm">
      <div className="flex min-h-[328px] flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-zinc-200 px-5 py-4">
          <div className="flex items-center gap-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-amber-50 text-amber-700">
              <ShieldCheck className="size-4.5" />
            </span>
            <div>
              <h3 className="text-sm font-semibold text-zinc-900">
                等待人工确认
              </h3>
              <p className="mt-0.5 flex items-center gap-1.5 text-xs text-zinc-500">
                <Clock3 className="size-3.5" />
                10分钟内有效
              </p>
            </div>
          </div>
        </header>

        <dl className="grid flex-1 content-start gap-4 px-5 py-5 text-sm">
          <div className="grid gap-1 sm:grid-cols-[120px_minmax(0,1fr)] sm:items-start sm:gap-4">
            <dt className="text-zinc-500">案件编号</dt>
            <dd className="font-mono font-semibold break-all text-zinc-900">
              {caseId}
            </dd>
          </div>
          <div className="grid gap-1 sm:grid-cols-[120px_minmax(0,1fr)] sm:items-start sm:gap-4">
            <dt className="text-zinc-500">处理方式</dt>
            <dd className="font-medium text-zinc-900">
              {resolutionCodeCopy[resolutionCode] ?? resolutionCode ?? "-"}
            </dd>
          </div>
          <div className="grid gap-1 sm:grid-cols-[120px_minmax(0,1fr)] sm:items-start sm:gap-4">
            <dt className="text-zinc-500">处理说明</dt>
            <dd className="leading-6 break-words whitespace-pre-wrap text-zinc-800">
              {note}
            </dd>
          </div>
        </dl>

        <footer className="grid grid-cols-2 gap-3 border-t border-zinc-200 px-5 py-4">
          <Button
            type="button"
            variant="outline"
            disabled={isLoading}
            onClick={() => onDecision("reject")}
            className="h-10 w-full gap-2 rounded-md border-zinc-300"
          >
            <XCircle className="size-4" />
            拒绝执行
          </Button>
          <Button
            type="button"
            disabled={isLoading}
            onClick={() => onDecision("approve")}
            className="h-10 w-full gap-2 rounded-md bg-emerald-700 text-white hover:bg-emerald-800"
          >
            <CheckCircle2 className="size-4" />
            确认执行
          </Button>
        </footer>
      </div>
    </section>
  );
}

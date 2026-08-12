import { CircleAlert, FileQuestion, Split, WalletCards } from "lucide-react";

import { cn } from "@/lib/utils";

const cases = [
  // 固定演示案件对应 seed 数据，方便面试时重复演示正常、单边和缺规则场景。
  {
    id: "CASE-1001",
    channel: "支付宝",
    issue: "金额差异",
    icon: CircleAlert,
  },
  {
    id: "CASE-1002",
    channel: "微信支付",
    issue: "渠道单边",
    icon: WalletCards,
  },
  {
    id: "CASE-1003",
    channel: "银联",
    issue: "内部单边",
    icon: Split,
  },
  {
    id: "CASE-1004",
    channel: "支付宝",
    issue: "规则版本缺失",
    icon: FileQuestion,
  },
] as const;

export function CaseShortcuts({
  selectedCaseId,
  onSelect,
}: {
  selectedCaseId?: string | null;
  onSelect: (caseId: string) => void;
}) {
  return (
    <section aria-labelledby="case-shortcuts-title">
      <div className="mb-2 flex items-center justify-between px-1">
        <h2
          id="case-shortcuts-title"
          className="text-xs font-semibold text-zinc-500"
        >
          演示案件
        </h2>
        <span className="text-[11px] text-zinc-400">4 个</span>
      </div>
      <div className="space-y-1">
        {cases.map((item) => {
          const Icon = item.icon;
          const active = selectedCaseId === item.id;
          return (
            <button
              key={item.id}
              type="button"
              aria-pressed={active}
              onClick={() => onSelect(item.id)}
              className={cn(
                "group flex min-h-14 w-full items-center gap-3 rounded-md border px-3 py-2 text-left transition-colors",
                active
                  ? "border-emerald-300 bg-emerald-50 text-emerald-950"
                  : "border-transparent text-zinc-700 hover:border-zinc-200 hover:bg-white",
              )}
            >
              <span
                className={cn(
                  "flex size-8 shrink-0 items-center justify-center rounded-md border bg-white",
                  active ? "border-emerald-200" : "border-zinc-200",
                )}
              >
                <Icon className="size-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block font-mono text-xs font-semibold">
                  {item.id}
                </span>
                <span className="block truncate text-xs text-zinc-500">
                  {item.channel} · {item.issue}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

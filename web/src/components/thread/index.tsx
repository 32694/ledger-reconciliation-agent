import type { Checkpoint, Message } from "@langchain/langgraph-sdk";
import {
  ArrowDown,
  Bot,
  History,
  LoaderCircle,
  PanelRight,
  SendHorizontal,
  SquarePen,
} from "lucide-react";
import { parseAsBoolean, useQueryState } from "nuqs";
import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";
import { StickToBottom, useStickToBottomContext } from "use-stick-to-bottom";
import { v4 as uuidv4 } from "uuid";
import { toast } from "sonner";

import { CaseShortcuts } from "@/components/cases/case-shortcuts";
import { EvidencePanel } from "@/components/cases/evidence-panel";
import { GitHubSVG } from "@/components/icons/github";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  DO_NOT_RENDER_ID_PREFIX,
  ensureToolCallsHaveResponses,
} from "@/lib/ensure-tool-responses";
import {
  evidenceArtifactFromState,
  publicNoticeFromState,
} from "@/lib/reconciliation";
import { cn } from "@/lib/utils";
import { useStreamContext } from "@/providers/Stream";

import ThreadHistory from "./history";
import { AssistantMessage, AssistantMessageLoading } from "./messages/ai";
import { HumanMessage } from "./messages/human";
import { TooltipIconButton } from "./tooltip-icon-button";

function StickyToBottomContent({
  content,
  footer,
}: {
  content: ReactNode;
  footer: ReactNode;
}) {
  return (
    <StickToBottom.Content
      scrollClassName="absolute inset-0 overflow-y-auto"
      className="flex min-h-full flex-col"
    >
      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 pt-6 sm:px-6">
        {content}
      </div>
      {footer}
    </StickToBottom.Content>
  );
}

function ScrollToBottom() {
  const { isAtBottom, scrollToBottom } = useStickToBottomContext();
  if (isAtBottom) return null;

  return (
    <TooltipIconButton
      tooltip="滚动到底部"
      variant="outline"
      className="absolute bottom-full left-1/2 mb-3 size-9 -translate-x-1/2 rounded-full bg-white shadow-sm"
      onClick={() => scrollToBottom()}
    >
      <ArrowDown className="size-4" />
    </TooltipIconButton>
  );
}

function GitHubAttribution() {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <a
            href="https://github.com/langchain-ai/agent-chat-ui"
            target="_blank"
            rel="noreferrer"
            className="flex size-9 items-center justify-center rounded-md text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900"
          >
            <GitHubSVG
              width="20"
              height="20"
            />
            <span className="sr-only">查看前端开源项目</span>
          </a>
        </TooltipTrigger>
        <TooltipContent>查看前端开源项目</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function NavigationPanel({
  selectedCaseId,
  onCaseSelect,
  onThreadClick,
}: {
  selectedCaseId?: string | null;
  onCaseSelect: (caseId: string) => void;
  onThreadClick?: () => void;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col gap-6 bg-zinc-50 px-3 py-4">
      <CaseShortcuts
        selectedCaseId={selectedCaseId}
        onSelect={onCaseSelect}
      />
      <ThreadHistory onThreadClick={onThreadClick} />
    </div>
  );
}

function PublicNotice({
  notice,
}: {
  notice: NonNullable<ReturnType<typeof publicNoticeFromState>>;
}) {
  const toneClassName = {
    error: "border-rose-200 bg-rose-50 text-rose-800",
    success: "border-emerald-200 bg-emerald-50 text-emerald-800",
    warning: "border-amber-200 bg-amber-50 text-amber-800",
  }[notice.tone];

  return (
    <div
      className="mr-auto flex w-full items-start gap-3"
      aria-live="polite"
    >
      <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-emerald-700 text-white">
        <Bot className="size-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="mb-1 text-xs font-medium text-zinc-500">系统结论</p>
        <p className={cn("rounded-md border px-3 py-2 text-sm", toneClassName)}>
          {notice.summary}
        </p>
      </div>
    </div>
  );
}

export function Thread() {
  const [, setThreadId] = useQueryState("threadId");
  const [hideToolCalls, setHideToolCalls] = useQueryState(
    "hideToolCalls",
    parseAsBoolean.withDefault(false),
  );
  const [input, setInput] = useState("");
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);
  const [mobileEvidenceOpen, setMobileEvidenceOpen] = useState(false);
  const [firstTokenReceived, setFirstTokenReceived] = useState(false);

  const stream = useStreamContext();
  const messages = stream.messages;
  const isLoading = stream.isLoading;
  const lastError = useRef<string | undefined>(undefined);
  const prevMessageLength = useRef(0);

  const artifact = evidenceArtifactFromState(stream.values);
  const publicNotice = publicNoticeFromState(stream.values);
  const selectedCaseId = stream.values.case_id;
  const evidenceStatus = stream.values.evidence_status;

  useEffect(() => {
    if (!stream.error) {
      lastError.current = undefined;
      return;
    }
    const message = (stream.error as { message?: string }).message;
    if (!message || lastError.current === message) return;
    lastError.current = message;
    toast.error("Agent 运行失败", {
      description: "本次请求未完成，请检查服务状态后重试。",
      richColors: true,
      closeButton: true,
    });
  }, [stream.error]);

  useEffect(() => {
    if (
      messages.length !== prevMessageLength.current &&
      messages.at(-1)?.type === "ai"
    ) {
      setFirstTokenReceived(true);
    }
    prevMessageLength.current = messages.length;
  }, [messages]);

  const submitMessage = (content: string) => {
    if (!content.trim() || isLoading) return;
    setFirstTokenReceived(false);

    const newHumanMessage: Message = {
      id: uuidv4(),
      type: "human",
      content: content.trim(),
    };
    const toolMessages = ensureToolCallsHaveResponses(stream.messages);

    stream.submit(
      { messages: [...toolMessages, newHumanMessage] },
      {
        streamMode: ["values"],
        streamSubgraphs: true,
        streamResumable: true,
        optimisticValues: (previous) => ({
          ...previous,
          messages: [
            ...(previous.messages ?? []),
            ...toolMessages,
            newHumanMessage,
          ],
        }),
      },
    );
    setInput("");
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    submitMessage(input);
  };

  const handleRegenerate = (
    parentCheckpoint: Checkpoint | null | undefined,
  ) => {
    prevMessageLength.current -= 1;
    setFirstTokenReceived(false);
    stream.submit(undefined, {
      checkpoint: parentCheckpoint,
      streamMode: ["values"],
      streamSubgraphs: true,
      streamResumable: true,
    });
  };

  const selectCase = (caseId: string) => {
    setInput(`分析 ${caseId} 的差异原因`);
    setMobileNavigationOpen(false);
  };

  const hasNoAIOrToolMessages = !messages.some(
    (message) => message.type === "ai" || message.type === "tool",
  );
  const connectionCopy = {
    checking: { label: "正在连接", className: "bg-amber-500" },
    connected: { label: "服务正常", className: "bg-emerald-500" },
    disconnected: { label: "服务离线", className: "bg-rose-500" },
  }[stream.connectionStatus];

  return (
    <div className="flex h-dvh min-h-[560px] w-full flex-col overflow-hidden bg-zinc-100 text-zinc-950">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-zinc-200 bg-white px-3 sm:px-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <TooltipIconButton
            tooltip="打开案件与会话"
            className="size-9 lg:hidden"
            onClick={() => setMobileNavigationOpen(true)}
          >
            <History className="size-4" />
          </TooltipIconButton>
          <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-emerald-700 text-white">
            <Bot className="size-4" />
          </span>
          <div className="min-w-0">
            <h1 className="truncate text-sm font-semibold sm:text-base">
              智能对账分析
            </h1>
            <p className="hidden text-[11px] text-zinc-500 sm:block">
              证据驱动的案件研判工作台
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1 sm:gap-2">
          <span className="hidden items-center gap-2 rounded-md border border-zinc-200 px-2.5 py-1.5 text-xs text-zinc-600 sm:flex">
            <span
              className={cn("size-2 rounded-full", connectionCopy.className)}
            />
            {connectionCopy.label}
          </span>
          <GitHubAttribution />
          <TooltipIconButton
            tooltip="新建会话"
            className="size-9"
            onClick={() => setThreadId(null)}
          >
            <SquarePen className="size-4" />
          </TooltipIconButton>
          <TooltipIconButton
            tooltip="查看证据"
            className="size-9 lg:hidden"
            onClick={() => setMobileEvidenceOpen(true)}
          >
            <PanelRight className="size-4" />
          </TooltipIconButton>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 lg:grid-cols-[260px_minmax(0,1fr)_340px]">
        <aside className="hidden min-h-0 border-r border-zinc-200 lg:block">
          <NavigationPanel
            selectedCaseId={selectedCaseId}
            onCaseSelect={selectCase}
          />
        </aside>

        <main className="flex min-h-0 min-w-0 flex-col bg-white">
          <div className="flex h-11 shrink-0 items-center justify-between border-b border-zinc-100 px-4 sm:px-6">
            <div className="flex min-w-0 items-center gap-2">
              <span className="text-xs font-medium text-zinc-500">
                当前会话
              </span>
              {selectedCaseId && (
                <code className="truncate rounded bg-zinc-100 px-2 py-1 text-[11px] font-semibold text-zinc-600">
                  {selectedCaseId}
                </code>
              )}
            </div>
            <span className="flex items-center gap-1.5 text-[11px] text-zinc-400 sm:hidden">
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  connectionCopy.className,
                )}
              />
              {connectionCopy.label}
            </span>
          </div>

          <StickToBottom className="relative min-h-0 flex-1 overflow-hidden">
            <StickyToBottomContent
              content={
                <div className="flex flex-1 flex-col gap-4 pb-8">
                  {messages.length === 0 &&
                  !stream.interrupt &&
                  !publicNotice ? (
                    <div className="flex min-h-80 flex-1 flex-col items-center justify-center text-center">
                      <span className="mb-4 flex size-12 items-center justify-center rounded-md border border-zinc-200 bg-zinc-50">
                        <Bot className="size-5 text-emerald-700" />
                      </span>
                      <h2 className="text-base font-semibold text-zinc-800">
                        等待分析案件
                      </h2>
                      <p className="mt-2 max-w-sm text-sm leading-6 text-zinc-500">
                        选择演示案件，或直接输入案件编号开始核查。
                      </p>
                    </div>
                  ) : (
                    messages
                      .filter(
                        (message) =>
                          !message.id?.startsWith(DO_NOT_RENDER_ID_PREFIX),
                      )
                      .map((message, index) =>
                        message.type === "human" ? (
                          <HumanMessage
                            key={message.id || `${message.type}-${index}`}
                            message={message}
                            isLoading={isLoading}
                          />
                        ) : (
                          <AssistantMessage
                            key={message.id || `${message.type}-${index}`}
                            message={message}
                            isLoading={isLoading}
                            handleRegenerate={handleRegenerate}
                          />
                        ),
                      )
                  )}
                  {publicNotice && <PublicNotice notice={publicNotice} />}
                  {hasNoAIOrToolMessages && !!stream.interrupt && (
                    <AssistantMessage
                      key="interrupt-message"
                      message={undefined}
                      isLoading={isLoading}
                      handleRegenerate={handleRegenerate}
                    />
                  )}
                  {isLoading && !firstTokenReceived && (
                    <AssistantMessageLoading />
                  )}
                </div>
              }
              footer={
                <div className="sticky bottom-0 border-t border-zinc-100 bg-white/95 px-4 py-3 backdrop-blur sm:px-6 sm:py-4">
                  <div className="relative mx-auto max-w-3xl">
                    <ScrollToBottom />
                    <form
                      onSubmit={handleSubmit}
                      className="rounded-lg border border-zinc-300 bg-white p-2 shadow-sm transition-shadow focus-within:border-zinc-400 focus-within:shadow-md"
                    >
                      <textarea
                        value={input}
                        onChange={(event) => setInput(event.target.value)}
                        onKeyDown={(event) => {
                          if (
                            event.key === "Enter" &&
                            !event.shiftKey &&
                            !event.nativeEvent.isComposing
                          ) {
                            event.preventDefault();
                            event.currentTarget.form?.requestSubmit();
                          }
                        }}
                        placeholder="输入案件编号，例如：分析CASE-1001"
                        rows={2}
                        className="max-h-32 min-h-14 w-full resize-none bg-transparent px-2 py-1.5 text-sm leading-6 outline-none placeholder:text-zinc-400"
                      />
                      <div className="flex h-9 items-center justify-between gap-3 px-1">
                        <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-500">
                          <Switch
                            aria-label="显示工具过程"
                            checked={!hideToolCalls}
                            onCheckedChange={(checked) =>
                              setHideToolCalls(!checked)
                            }
                          />
                          <span>显示工具过程</span>
                        </label>
                        <Button
                          type="submit"
                          size="icon"
                          aria-label="发送"
                          disabled={!input.trim() || isLoading}
                          className="size-9 rounded-md bg-emerald-700 text-white hover:bg-emerald-800"
                        >
                          {isLoading ? (
                            <LoaderCircle className="size-4 animate-spin" />
                          ) : (
                            <SendHorizontal className="size-4" />
                          )}
                        </Button>
                      </div>
                    </form>
                    <p className="mt-2 text-center text-[11px] text-zinc-400">
                      Agent 结论必须引用当前案件的锁定规则版本
                    </p>
                  </div>
                </div>
              }
            />
          </StickToBottom>
        </main>

        <div className="hidden min-h-0 border-l border-zinc-200 lg:block">
          <EvidencePanel
            artifact={artifact}
            evidenceStatus={evidenceStatus}
          />
        </div>
      </div>

      <Sheet
        open={mobileNavigationOpen}
        onOpenChange={setMobileNavigationOpen}
      >
        <SheetContent
          side="left"
          className="w-[88vw] max-w-xs p-0"
        >
          <SheetHeader className="sr-only">
            <SheetTitle>案件与历史会话</SheetTitle>
          </SheetHeader>
          <div className="h-full pt-8">
            <NavigationPanel
              selectedCaseId={selectedCaseId}
              onCaseSelect={selectCase}
              onThreadClick={() => setMobileNavigationOpen(false)}
            />
          </div>
        </SheetContent>
      </Sheet>

      <Sheet
        open={mobileEvidenceOpen}
        onOpenChange={setMobileEvidenceOpen}
      >
        <SheetContent
          side="right"
          className="w-[92vw] max-w-sm p-0"
        >
          <SheetHeader className="sr-only">
            <SheetTitle>案件证据</SheetTitle>
          </SheetHeader>
          <div className="h-full pt-8">
            <EvidencePanel
              artifact={artifact}
              evidenceStatus={evidenceStatus}
            />
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

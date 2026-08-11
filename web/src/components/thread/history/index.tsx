import { History, MessageSquareText } from "lucide-react";
import { useQueryState } from "nuqs";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useThreads } from "@/providers/Thread";
import type { Thread } from "@langchain/langgraph-sdk";

import { getContentString } from "../utils";

function threadTitle(thread: Thread): string {
  if (
    typeof thread.values === "object" &&
    thread.values &&
    "messages" in thread.values &&
    Array.isArray(thread.values.messages) &&
    thread.values.messages.length > 0
  ) {
    return getContentString(thread.values.messages[0].content);
  }
  return thread.thread_id;
}

function ThreadList({
  threads,
  onThreadClick,
}: {
  threads: Thread[];
  onThreadClick?: () => void;
}) {
  const [threadId, setThreadId] = useQueryState("threadId");

  if (threads.length === 0) {
    return (
      <div className="flex min-h-28 flex-col items-center justify-center px-4 text-center">
        <MessageSquareText className="mb-2 size-4 text-zinc-400" />
        <p className="text-xs text-zinc-500">暂无历史会话</p>
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
      {threads.map((thread) => (
        <Button
          key={thread.thread_id}
          variant="ghost"
          className="h-auto min-h-10 w-full justify-start rounded-md px-2.5 py-2 text-left font-normal"
          aria-current={thread.thread_id === threadId ? "page" : undefined}
          onClick={() => {
            onThreadClick?.();
            if (thread.thread_id !== threadId) setThreadId(thread.thread_id);
          }}
        >
          <span className="line-clamp-2 text-xs leading-5 text-zinc-600">
            {threadTitle(thread)}
          </span>
        </Button>
      ))}
    </div>
  );
}

function ThreadHistoryLoading() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 4 }).map((_, index) => (
        <Skeleton
          key={index}
          className="h-10 w-full"
        />
      ))}
    </div>
  );
}

export default function ThreadHistory({
  onThreadClick,
}: {
  onThreadClick?: () => void;
}) {
  const { getThreads, threads, setThreads, threadsLoading, setThreadsLoading } =
    useThreads();

  useEffect(() => {
    setThreadsLoading(true);
    getThreads()
      .then(setThreads)
      .catch(console.error)
      .finally(() => setThreadsLoading(false));
  }, [getThreads, setThreads, setThreadsLoading]);

  return (
    <section
      aria-labelledby="thread-history-title"
      className="flex min-h-0 flex-1 flex-col"
    >
      <div className="mb-2 flex items-center gap-2 px-1">
        <History className="size-3.5 text-zinc-400" />
        <h2
          id="thread-history-title"
          className="text-xs font-semibold text-zinc-500"
        >
          历史会话
        </h2>
      </div>
      {threadsLoading ? (
        <ThreadHistoryLoading />
      ) : (
        <ThreadList
          threads={threads}
          onThreadClick={onThreadClick}
        />
      )}
    </section>
  );
}

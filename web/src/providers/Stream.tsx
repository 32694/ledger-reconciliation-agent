import type { Message } from "@langchain/langgraph-sdk";
import { useStream } from "@langchain/langgraph-sdk/react";
import {
  isRemoveUIMessage,
  isUIMessage,
  type RemoveUIMessage,
  type UIMessage,
  uiMessageReducer,
} from "@langchain/langgraph-sdk/react-ui";
import { useQueryState } from "nuqs";
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useState,
} from "react";
import { toast } from "sonner";

import { getApiKey } from "@/lib/api-key";

import { useThreads } from "./Thread";

export type StateType = {
  messages: Message[];
  ui?: UIMessage[];
  case_id?: string | null;
  collected_facts?: Record<string, unknown>;
  citations?: Record<string, unknown>[];
  evidence_status?: string;
  final_response?: Record<string, unknown>;
};

const useTypedStream = useStream<
  StateType,
  {
    UpdateType: {
      messages?: Message[] | Message | string;
      ui?: (UIMessage | RemoveUIMessage)[] | UIMessage | RemoveUIMessage;
      context?: Record<string, unknown>;
    };
    CustomEventType: UIMessage | RemoveUIMessage;
  }
>;

type ConnectionStatus = "checking" | "connected" | "disconnected";
type StreamContextType = ReturnType<typeof useTypedStream> & {
  connectionStatus: ConnectionStatus;
};

const StreamContext = createContext<StreamContextType | undefined>(undefined);

const DEFAULT_API_URL = "http://localhost:2024";
const DEFAULT_ASSISTANT_ID = "reconciliation_agent";

async function sleep(ms = 4000) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function checkGraphStatus(
  apiUrl: string,
  apiKey: string | null,
  authScheme?: string,
): Promise<boolean> {
  try {
    const headers = new Headers();
    if (apiKey) headers.set("X-Api-Key", apiKey);
    if (authScheme) headers.set("X-Auth-Scheme", authScheme);

    const response = await fetch(`${apiUrl}/info`, { headers });
    return response.ok;
  } catch {
    return false;
  }
}

function StreamSession({
  children,
  apiKey,
  apiUrl,
  assistantId,
  authScheme,
}: {
  children: ReactNode;
  apiKey: string | null;
  apiUrl: string;
  assistantId: string;
  authScheme?: string;
}) {
  const [threadId, setThreadId] = useQueryState("threadId");
  const [connectionStatus, setConnectionStatus] =
    useState<ConnectionStatus>("checking");
  const { getThreads, setThreads } = useThreads();
  const streamValue = useTypedStream({
    apiUrl,
    apiKey: apiKey ?? undefined,
    assistantId,
    ...(authScheme && {
      defaultHeaders: { "X-Auth-Scheme": authScheme },
    }),
    threadId: threadId ?? null,
    fetchStateHistory: true,
    onCustomEvent: (event, options) => {
      if (isUIMessage(event) || isRemoveUIMessage(event)) {
        options.mutate((previous) => ({
          ...previous,
          ui: uiMessageReducer(previous.ui ?? [], event),
        }));
      }
    },
    onThreadId: (id) => {
      setThreadId(id);
      sleep().then(() => getThreads().then(setThreads).catch(console.error));
    },
  });

  useEffect(() => {
    let active = true;
    checkGraphStatus(apiUrl, apiKey, authScheme).then((connected) => {
      if (!active) return;
      setConnectionStatus(connected ? "connected" : "disconnected");
      if (!connected) {
        toast.error("无法连接对账 Agent 服务", {
          description: `请确认本地服务已启动：${apiUrl}`,
          duration: 10000,
          richColors: true,
          closeButton: true,
        });
      }
    });
    return () => {
      active = false;
    };
  }, [apiKey, apiUrl, authScheme]);

  return (
    <StreamContext.Provider value={{ ...streamValue, connectionStatus }}>
      {children}
    </StreamContext.Provider>
  );
}

export function StreamProvider({ children }: { children: ReactNode }) {
  const envApiUrl = process.env.NEXT_PUBLIC_API_URL;
  const envAssistantId = process.env.NEXT_PUBLIC_ASSISTANT_ID;
  const envAuthScheme = process.env.NEXT_PUBLIC_AUTH_SCHEME;
  const [apiUrl] = useQueryState("apiUrl", {
    defaultValue: envApiUrl || DEFAULT_API_URL,
  });
  const [assistantId] = useQueryState("assistantId", {
    defaultValue: envAssistantId || DEFAULT_ASSISTANT_ID,
  });
  const [authScheme] = useQueryState("authScheme", {
    defaultValue: envAuthScheme || "",
  });
  const [apiKey] = useState(() => getApiKey() || "");

  return (
    <StreamSession
      apiKey={apiKey}
      apiUrl={apiUrl}
      assistantId={assistantId}
      authScheme={authScheme || undefined}
    >
      {children}
    </StreamSession>
  );
}

export function useStreamContext(): StreamContextType {
  const context = useContext(StreamContext);
  if (!context) {
    throw new Error("useStreamContext must be used within a StreamProvider");
  }
  return context;
}

export default StreamContext;

import type { ThreadEvent, ThreadItem, Usage } from "@openai/codex-sdk";

import {
  createTurnOutcomeState,
  noteTurnCompleted,
  noteTurnFailure,
  terminalTurnFailureMessage,
} from "./turn_outcome.js";

export type SidecarEmitPayload = {
  text?: string;
  delta?: string;
  item_type?: string;
  status?: string;
  payload?: Record<string, unknown>;
};

export type SidecarEmit = (kind: string, payload?: SidecarEmitPayload) => Promise<void>;
export type TerminalTurnEventKind = "turn.completed" | "turn.failed";

export type ConsumedTurn = {
  threadId: string | null;
  finalResponse: string;
  usage: Usage | null;
  itemCount: number;
  failureMessage: string | null;
};

function diffText(previous: string, current: string): string {
  if (!current) {
    return "";
  }
  if (!previous) {
    return current;
  }
  if (current.startsWith(previous)) {
    return current.slice(previous.length);
  }
  return current;
}

function summarizeItem(item: ThreadItem): { text?: string; payload?: Record<string, unknown> } {
  if (item.type === "command_execution") {
    return {
      text: item.command,
      payload: {
        command: item.command,
        exit_code: item.exit_code,
        status: item.status,
      },
    };
  }
  if (item.type === "mcp_tool_call") {
    return {
      text: `${item.server}.${item.tool}`,
      payload: {
        server: item.server,
        tool: item.tool,
        status: item.status,
      },
    };
  }
  if (item.type === "web_search") {
    return {
      text: item.query,
      payload: { query: item.query },
    };
  }
  if (item.type === "file_change") {
    return {
      text: `${item.changes.length} file change(s)`,
      payload: { changes: item.changes, status: item.status },
    };
  }
  if (item.type === "error") {
    return {
      text: item.message,
      payload: { message: item.message },
    };
  }
  if (item.type === "todo_list") {
    return {
      text: `${item.items.length} todo item(s)`,
      payload: { count: item.items.length },
    };
  }
  if (item.type === "reasoning") {
    return {
      text: item.text,
      payload: { message: item.text },
    };
  }
  return {};
}

export async function consumeTurnEvents(args: {
  events: AsyncIterable<ThreadEvent>;
  initialThreadId: string | null;
  emit: SidecarEmit;
  onTerminalEvent?: (kind: TerminalTurnEventKind) => Promise<void> | void;
}): Promise<ConsumedTurn> {
  const iterator = args.events[Symbol.asyncIterator]();
  const completedItems: ThreadItem[] = [];
  const previousAgentTextById = new Map<string, string>();
  let finalResponse = "";
  let lastMeaningfulAssistantText = "";
  let usage: Usage | null = null;
  let sdkThreadId = args.initialThreadId;
  let turnOutcome = createTurnOutcomeState();
  let closeIterator = false;
  let terminalEventKind: TerminalTurnEventKind | null = null;

  try {
    while (true) {
      const next = await iterator.next();
      if (next.done) {
        break;
      }
      const event = next.value;

      if (event.type === "thread.started") {
        sdkThreadId = event.thread_id;
        await args.emit("status", {
          text: `SDK thread started: ${event.thread_id}`,
          status: "running",
          payload: { sdk_thread_id: event.thread_id, event_type: event.type },
        });
        continue;
      }
      if (event.type === "turn.started") {
        await args.emit("turn.started", {
          text: "Turn started",
          status: "running",
          payload: { event_type: event.type, sdk_thread_id: sdkThreadId },
        });
        continue;
      }
      if (event.type === "turn.completed") {
        turnOutcome = noteTurnCompleted(turnOutcome);
        usage = event.usage;
        await args.emit("turn.completed", {
          text: "Turn completed",
          status: "completed",
          payload: { usage: event.usage, event_type: event.type, sdk_thread_id: sdkThreadId },
        });
        terminalEventKind = event.type;
        if (args.onTerminalEvent) {
          try {
            await args.onTerminalEvent(event.type);
          } catch {
            // Cleanup hooks are best-effort; iterator.return() remains the fallback.
          }
        }
        closeIterator = true;
        break;
      }
      if (event.type === "turn.failed") {
        turnOutcome = noteTurnFailure(turnOutcome, event.error.message);
        await args.emit("turn.failed", {
          text: event.error.message,
          status: "failed",
          payload: { message: event.error.message, event_type: event.type, sdk_thread_id: sdkThreadId },
        });
        terminalEventKind = event.type;
        if (args.onTerminalEvent) {
          try {
            await args.onTerminalEvent(event.type);
          } catch {
            // Cleanup hooks are best-effort; iterator.return() remains the fallback.
          }
        }
        closeIterator = true;
        break;
      }
      if (event.type === "error") {
        turnOutcome = noteTurnFailure(turnOutcome, event.message);
        await args.emit("turn.failed", {
          text: event.message,
          status: "failed",
          payload: { message: event.message, event_type: event.type, sdk_thread_id: sdkThreadId },
        });
        continue;
      }

      const item = event.item;
      if (item.type === "agent_message") {
        const previousText = previousAgentTextById.get(item.id) ?? "";
        const delta = diffText(previousText, item.text);
        const hasMeaningfulText = Boolean(item.text.trim());
        previousAgentTextById.set(item.id, item.text);
        if (hasMeaningfulText) {
          lastMeaningfulAssistantText = item.text;
        }
        if (delta) {
          await args.emit("assistant.delta", {
            text: item.text,
            delta,
            item_type: item.type,
            status: event.type === "item.completed" ? "completed" : "streaming",
            payload: { item_id: item.id, event_type: event.type, sdk_thread_id: sdkThreadId },
          });
        }
        if (event.type === "item.completed") {
          completedItems.push(item);
          if (hasMeaningfulText) {
            finalResponse = item.text;
          }
          await args.emit("assistant.completed", {
            text: item.text,
            item_type: item.type,
            status: "completed",
            payload: { item_id: item.id, event_type: event.type, sdk_thread_id: sdkThreadId },
          });
        }
        continue;
      }

      if (event.type === "item.completed") {
        completedItems.push(item);
      }
      const { text, payload: itemPayload } = summarizeItem(item);
      if (item.type === "command_execution" || item.type === "mcp_tool_call") {
        const kind = event.type === "item.completed" ? "tool.completed" : "tool.started";
        await args.emit(kind, {
          text,
          item_type: item.type,
          status: event.type === "item.completed" ? "completed" : "running",
          payload: {
            ...(itemPayload ?? {}),
            item_id: item.id,
            event_type: event.type,
            sdk_thread_id: sdkThreadId,
          },
        });
        continue;
      }

      if (event.type === "item.started" || event.type === "item.completed") {
        await args.emit("status", {
          text,
          item_type: item.type,
          status: event.type === "item.completed" ? "completed" : "running",
          payload: {
            ...(itemPayload ?? {}),
            item_id: item.id,
            event_type: event.type,
            sdk_thread_id: sdkThreadId,
          },
        });
      }
    }
  } finally {
    if (closeIterator && typeof iterator.return === "function") {
      try {
        await iterator.return();
      } catch (error) {
        if (!terminalEventKind) {
          throw error;
        }
      }
    }
  }

  return {
    threadId: sdkThreadId,
    finalResponse: finalResponse || lastMeaningfulAssistantText,
    usage,
    itemCount: completedItems.length,
    failureMessage: terminalTurnFailureMessage(turnOutcome),
  };
}

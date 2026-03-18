import { promises as fs } from "fs";
import path from "path";

import { Codex, type ApprovalMode, type SandboxMode, type ThreadItem, type ThreadOptions, type Usage, type WebSearchMode } from "@openai/codex-sdk";
import { createTurnOutcomeState, noteTurnCompleted, noteTurnFailure, terminalTurnFailureMessage } from "./turn_outcome.js";

type RequestPayload = {
  action: "start" | "reply";
  prompt?: string | null;
  thread_id?: string | null;
  mail_thread_id?: string | null;
  task_id?: string | null;
  cwd?: string | null;
  model?: string | null;
  sandbox_mode?: SandboxMode | null;
  approval_policy?: ApprovalMode | null;
  skip_git_repo_check?: boolean | null;
  web_search_mode?: WebSearchMode | null;
  codex_path_override?: string | null;
  stream_path?: string | null;
};

type ResponsePayload = {
  thread_id: string | null;
  final_response: string;
  usage: unknown;
  item_count: number;
};

type NormalizedEvent = {
  ts: string;
  seq: number;
  thread_id: string;
  task_id: string;
  backend: "codex";
  backend_transport: "sdk";
  kind: string;
  text?: string;
  delta?: string;
  item_type?: string;
  status?: string;
  payload?: Record<string, unknown>;
};

function optionalText(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const text = value.trim();
  return text ? text : undefined;
}

function payloadOrEnvText(value: unknown, envName: string): string | undefined {
  return optionalText(value) ?? optionalText(process.env[envName]);
}

async function readStdin(): Promise<string> {
  const chunks: string[] = [];
  for await (const chunk of process.stdin) {
    chunks.push(String(chunk));
  }
  return chunks.join("");
}

async function writeJsonlLine(targetPath: string | undefined, payload: Record<string, unknown>): Promise<void> {
  if (!targetPath) {
    return;
  }
  await fs.mkdir(path.dirname(targetPath), { recursive: true });
  await fs.appendFile(targetPath, `${JSON.stringify(payload)}\n`, "utf8");
}

function timestamp(): string {
  return new Date().toISOString();
}

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

async function main(): Promise<void> {
  const rawInput = await readStdin();
  if (!rawInput.trim()) {
    throw new Error("Missing JSON request on stdin.");
  }
  const payload = JSON.parse(rawInput) as RequestPayload;
  const action = payload.action;
  if (action !== "start" && action !== "reply") {
    throw new Error(`Unsupported action: ${String(action)}`);
  }

  const prompt = optionalText(payload.prompt) ?? "Continue the previous task.";
  const threadOptions: ThreadOptions = {
    model: optionalText(payload.model),
    sandboxMode: payload.sandbox_mode ?? undefined,
    workingDirectory: optionalText(payload.cwd),
    skipGitRepoCheck: payload.skip_git_repo_check ?? true,
    webSearchMode: payload.web_search_mode ?? undefined,
    approvalPolicy: payload.approval_policy ?? undefined,
  };
  const codex = new Codex({
    codexPathOverride: optionalText(payload.codex_path_override),
  });
  const thread =
    action === "reply"
      ? codex.resumeThread(optionalText(payload.thread_id) ?? fail("reply action requires thread_id"), threadOptions)
      : codex.startThread(threadOptions);
  const streamPath = optionalText(payload.stream_path);
  if (streamPath) {
    await fs.mkdir(path.dirname(streamPath), { recursive: true });
    await fs.writeFile(streamPath, "", "utf8");
  }
  const mailThreadId = payloadOrEnvText(payload.mail_thread_id, "MAIL_RUNNER_MAIL_THREAD_ID") ?? fail("mail_thread_id is required");
  const taskId = payloadOrEnvText(payload.task_id, "MAIL_RUNNER_TASK_ID") ?? fail("task_id is required");
  const streamed = await thread.runStreamed(prompt);
  const completedItems: ThreadItem[] = [];
  const previousAgentTextById = new Map<string, string>();
  let seq = 0;
  let finalResponse = "";
  let usage: Usage | null = null;
  let sdkThreadId = thread.id;
  let turnOutcome = createTurnOutcomeState();

  const emit = async (kind: string, extra: Omit<NormalizedEvent, "ts" | "seq" | "thread_id" | "task_id" | "backend" | "backend_transport" | "kind"> = {}): Promise<void> => {
    seq += 1;
    const event: NormalizedEvent = {
      ts: timestamp(),
      seq,
      thread_id: mailThreadId,
      task_id: taskId,
      backend: "codex",
      backend_transport: "sdk",
      kind,
      ...extra,
    };
    await writeJsonlLine(streamPath, event as Record<string, unknown>);
  };

  for await (const event of streamed.events) {
    if (event.type === "thread.started") {
      sdkThreadId = event.thread_id;
      await emit("status", {
        text: `SDK thread started: ${event.thread_id}`,
        status: "running",
        payload: { sdk_thread_id: event.thread_id, event_type: event.type },
      });
      continue;
    }
    if (event.type === "turn.started") {
      await emit("turn.started", {
        text: "Turn started",
        status: "running",
        payload: { event_type: event.type, sdk_thread_id: sdkThreadId },
      });
      continue;
    }
    if (event.type === "turn.completed") {
      turnOutcome = noteTurnCompleted(turnOutcome);
      usage = event.usage;
      await emit("turn.completed", {
        text: "Turn completed",
        status: "completed",
        payload: { usage: event.usage, event_type: event.type, sdk_thread_id: sdkThreadId },
      });
      continue;
    }
    if (event.type === "turn.failed") {
      turnOutcome = noteTurnFailure(turnOutcome, event.error.message);
      await emit("turn.failed", {
        text: event.error.message,
        status: "failed",
        payload: { message: event.error.message, event_type: event.type, sdk_thread_id: sdkThreadId },
      });
      continue;
    }
    if (event.type === "error") {
      turnOutcome = noteTurnFailure(turnOutcome, event.message);
      await emit("turn.failed", {
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
      previousAgentTextById.set(item.id, item.text);
      if (delta) {
        await emit("assistant.delta", {
          text: item.text,
          delta,
          item_type: item.type,
          status: event.type === "item.completed" ? "completed" : "streaming",
          payload: { item_id: item.id, event_type: event.type, sdk_thread_id: sdkThreadId },
        });
      }
      if (event.type === "item.completed") {
        completedItems.push(item);
        finalResponse = item.text;
        await emit("assistant.completed", {
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
      await emit(kind, {
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
      await emit("status", {
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

  const failureMessage = terminalTurnFailureMessage(turnOutcome);
  if (failureMessage) {
    throw new Error(failureMessage);
  }
  const response: ResponsePayload = {
    thread_id: thread.id ?? sdkThreadId,
    final_response: finalResponse,
    usage,
    item_count: completedItems.length,
  };
  process.stdout.write(`${JSON.stringify(response)}\n`);
}

function fail(message: string): never {
  throw new Error(message);
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.stack || error.message : String(error);
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});

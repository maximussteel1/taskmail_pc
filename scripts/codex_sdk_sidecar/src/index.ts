import { promises as fs } from "fs";
import path from "path";

import { Codex, type ApprovalMode, type SandboxMode, type ThreadOptions, type WebSearchMode } from "@openai/codex-sdk";
import { consumeTurnEvents } from "./turn_stream.js";

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
  const turnAbort = new AbortController();
  const streamed = await thread.runStreamed(prompt, { signal: turnAbort.signal });
  let seq = 0;

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

  const consumed = await consumeTurnEvents({
    events: streamed.events,
    initialThreadId: thread.id,
    emit,
    onTerminalEvent: async () => {
      if (!turnAbort.signal.aborted) {
        turnAbort.abort();
      }
    },
  });
  const failureMessage = consumed.failureMessage;
  if (failureMessage) {
    throw new Error(failureMessage);
  }
  const response: ResponsePayload = {
    thread_id: consumed.threadId,
    final_response: consumed.finalResponse,
    usage: consumed.usage,
    item_count: consumed.itemCount,
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

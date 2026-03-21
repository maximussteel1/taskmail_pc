import { promises as fs } from "fs";
import path from "path";
import { Codex } from "@openai/codex-sdk";
import { consumeTurnEvents } from "./turn_stream.js";
function optionalText(value) {
    if (typeof value !== "string") {
        return undefined;
    }
    const text = value.trim();
    return text ? text : undefined;
}
function payloadOrEnvText(value, envName) {
    return optionalText(value) ?? optionalText(process.env[envName]);
}
async function readStdin() {
    const chunks = [];
    for await (const chunk of process.stdin) {
        chunks.push(String(chunk));
    }
    return chunks.join("");
}
async function writeJsonlLine(targetPath, payload) {
    if (!targetPath) {
        return;
    }
    await fs.mkdir(path.dirname(targetPath), { recursive: true });
    await fs.appendFile(targetPath, `${JSON.stringify(payload)}\n`, "utf8");
}
function timestamp() {
    return new Date().toISOString();
}
async function main() {
    const rawInput = await readStdin();
    if (!rawInput.trim()) {
        throw new Error("Missing JSON request on stdin.");
    }
    const payload = JSON.parse(rawInput);
    const action = payload.action;
    if (action !== "start" && action !== "reply") {
        throw new Error(`Unsupported action: ${String(action)}`);
    }
    const prompt = optionalText(payload.prompt) ?? "Continue the previous task.";
    const threadOptions = {
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
    const thread = action === "reply"
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
    const emit = async (kind, extra = {}) => {
        seq += 1;
        const event = {
            ts: timestamp(),
            seq,
            thread_id: mailThreadId,
            task_id: taskId,
            backend: "codex",
            backend_transport: "sdk",
            kind,
            ...extra,
        };
        await writeJsonlLine(streamPath, event);
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
    const response = {
        thread_id: consumed.threadId,
        final_response: consumed.finalResponse,
        usage: consumed.usage,
        item_count: consumed.itemCount,
    };
    process.stdout.write(`${JSON.stringify(response)}\n`);
}
function fail(message) {
    throw new Error(message);
}
main().catch((error) => {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    process.stderr.write(`${message}\n`);
    process.exitCode = 1;
});

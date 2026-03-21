from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_codex_sdk_sidecar_transient_errors_do_not_override_completed_turn() -> None:
    module_path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "codex_sdk_sidecar"
        / "dist"
        / "turn_outcome.js"
    )
    script = """
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const modulePath = process.argv[1];
const mod = await import(pathToFileURL(modulePath).href);

let state = mod.createTurnOutcomeState();
state = mod.noteTurnFailure(state, "stream disconnected");
const failureBeforeCompletion = mod.terminalTurnFailureMessage(state);

state = mod.noteTurnCompleted(state);
const failureAfterCompletion = mod.terminalTurnFailureMessage(state);

state = mod.noteTurnFailure(state, "late reconnect warning");
const failureAfterLateWarning = mod.terminalTurnFailureMessage(state);

assert.equal(failureBeforeCompletion, "stream disconnected");
assert.equal(failureAfterCompletion, null);
assert.equal(failureAfterLateWarning, null);

process.stdout.write(JSON.stringify({
  failureBeforeCompletion,
  failureAfterCompletion,
  failureAfterLateWarning,
}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(module_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "failureBeforeCompletion": "stream disconnected",
        "failureAfterCompletion": None,
        "failureAfterLateWarning": None,
    }


def test_codex_sdk_sidecar_stops_consuming_once_turn_completed() -> None:
    module_path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "codex_sdk_sidecar"
        / "dist"
        / "turn_stream.js"
    )
    script = """
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const modulePath = process.argv[1];
const mod = await import(pathToFileURL(modulePath).href);

async function* fakeEvents() {
  yield { type: "thread.started", thread_id: "sdk_thread_1" };
  yield { type: "turn.started" };
  yield { type: "error", message: "stream disconnected" };
  yield { type: "item.completed", item: { id: "msg_1", type: "agent_message", text: "final reply" } };
  yield {
    type: "turn.completed",
    usage: {
      input_tokens: 1,
      cached_input_tokens: 0,
      output_tokens: 2,
    },
  };
  await new Promise(() => {});
}

const emitted = [];
const result = await Promise.race([
  mod.consumeTurnEvents({
    events: fakeEvents(),
    initialThreadId: null,
    emit: async (kind, payload = {}) => {
      emitted.push({ kind, payload });
    },
  }),
  new Promise((_, reject) => setTimeout(() => reject(new Error("timed out")), 1000)),
]);

assert.equal(result.threadId, "sdk_thread_1");
assert.equal(result.finalResponse, "final reply");
assert.equal(result.itemCount, 1);
assert.equal(result.failureMessage, null);
assert.equal(result.usage.output_tokens, 2);
assert.equal(emitted.at(-1)?.kind, "turn.completed");

process.stdout.write(JSON.stringify({
  threadId: result.threadId,
  finalResponse: result.finalResponse,
  itemCount: result.itemCount,
  failureMessage: result.failureMessage,
  outputTokens: result.usage.output_tokens,
  emittedKinds: emitted.map((item) => item.kind),
}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(module_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "threadId": "sdk_thread_1",
        "finalResponse": "final reply",
        "itemCount": 1,
        "failureMessage": None,
        "outputTokens": 2,
        "emittedKinds": [
            "status",
            "turn.started",
            "turn.failed",
            "assistant.delta",
            "assistant.completed",
            "turn.completed",
        ],
    }


def test_codex_sdk_sidecar_keeps_last_non_empty_reply_when_terminal_message_is_empty() -> None:
    module_path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "codex_sdk_sidecar"
        / "dist"
        / "turn_stream.js"
    )
    script = """
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const modulePath = process.argv[1];
const mod = await import(pathToFileURL(modulePath).href);

async function* fakeEvents() {
  yield { type: "thread.started", thread_id: "sdk_thread_3" };
  yield { type: "turn.started" };
  yield { type: "item.completed", item: { id: "msg_3", type: "agent_message", text: "question payload" } };
  yield { type: "item.completed", item: { id: "msg_4", type: "agent_message", text: "" } };
  yield {
    type: "turn.completed",
    usage: {
      input_tokens: 2,
      cached_input_tokens: 0,
      output_tokens: 3,
    },
  };
}

const result = await Promise.race([
  mod.consumeTurnEvents({
    events: fakeEvents(),
    initialThreadId: null,
    emit: async () => {},
  }),
  new Promise((_, reject) => setTimeout(() => reject(new Error("timed out")), 1000)),
]);

assert.equal(result.threadId, "sdk_thread_3");
assert.equal(result.finalResponse, "question payload");
assert.equal(result.itemCount, 2);

process.stdout.write(JSON.stringify({
  threadId: result.threadId,
  finalResponse: result.finalResponse,
  itemCount: result.itemCount,
}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(module_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "threadId": "sdk_thread_3",
        "finalResponse": "question payload",
        "itemCount": 2,
    }


def test_codex_sdk_sidecar_aborts_terminal_turn_before_iterator_cleanup() -> None:
    module_path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "codex_sdk_sidecar"
        / "dist"
        / "turn_stream.js"
    )
    script = """
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const modulePath = process.argv[1];
const mod = await import(pathToFileURL(modulePath).href);

const events = [
  { type: "thread.started", thread_id: "sdk_thread_2" },
  { type: "turn.started" },
  { type: "item.completed", item: { id: "msg_2", type: "agent_message", text: "reply after abort" } },
  {
    type: "turn.completed",
    usage: {
      input_tokens: 3,
      cached_input_tokens: 0,
      output_tokens: 4,
    },
  },
];

let nextIndex = 0;
let aborted = false;
let observedTerminalEvent = null;
let returnSawAbort = false;

const iterator = {
  async next() {
    if (nextIndex < events.length) {
      return { done: false, value: events[nextIndex++] };
    }
    return new Promise(() => {});
  },
  async return() {
    returnSawAbort = aborted;
    assert.equal(aborted, true, "iterator.return() should only run after terminal cleanup aborts the turn");
    return { done: true, value: undefined };
  },
  [Symbol.asyncIterator]() {
    return this;
  },
};

const result = await Promise.race([
  mod.consumeTurnEvents({
    events: iterator,
    initialThreadId: null,
    emit: async () => {},
    onTerminalEvent: async (kind) => {
      observedTerminalEvent = kind;
      aborted = true;
    },
  }),
  new Promise((_, reject) => setTimeout(() => reject(new Error("timed out")), 1000)),
]);

assert.equal(observedTerminalEvent, "turn.completed");
assert.equal(returnSawAbort, true);
assert.equal(result.threadId, "sdk_thread_2");
assert.equal(result.finalResponse, "reply after abort");
assert.equal(result.failureMessage, null);

process.stdout.write(JSON.stringify({
  observedTerminalEvent,
  returnSawAbort,
  threadId: result.threadId,
  finalResponse: result.finalResponse,
}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(module_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "observedTerminalEvent": "turn.completed",
        "returnSawAbort": True,
        "threadId": "sdk_thread_2",
        "finalResponse": "reply after abort",
    }

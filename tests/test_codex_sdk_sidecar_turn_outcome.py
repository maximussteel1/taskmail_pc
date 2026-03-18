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

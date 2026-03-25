"""Wrapper for the standalone sdk-first question-answer smoke."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mail_runner.sdk_question_answer_smoke import main


if __name__ == "__main__":
    raise SystemExit(main())

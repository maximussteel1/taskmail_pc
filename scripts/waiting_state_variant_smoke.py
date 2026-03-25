"""Wrapper for the standalone waiting-state variant fixture smoke."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mail_runner.waiting_state_variant_smoke import main


if __name__ == "__main__":
    raise SystemExit(main())

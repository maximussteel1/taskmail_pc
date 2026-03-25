"""Wrapper for the standalone PC control-plane fixture smoke."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mail_runner.pc_control_plane_fixture_smoke import main


if __name__ == "__main__":
    raise SystemExit(main())

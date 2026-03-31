from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mail_runner.android_session_read_live import main


if __name__ == "__main__":
    raise SystemExit(main())

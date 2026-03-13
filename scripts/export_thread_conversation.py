"""Export a stitched multi-turn conversation for one thread."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mail_runner.transcript_export import (
    build_thread_transcript,
    render_transcript_json,
    render_transcript_markdown,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract a thread's multi-turn conversation and stitch it in order."
    )
    parser.add_argument("thread_id", help="Thread ID, for example thread_013")
    parser.add_argument(
        "--task-root",
        default="tasks",
        help="Task root directory that contains per-thread archives. Defaults to ./tasks",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Keep turns whose extracted content is empty.",
    )
    parser.add_argument(
        "--output",
        help="Optional output file path. Prints to stdout when omitted.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    turns = build_thread_transcript(
        args.thread_id,
        args.task_root,
        include_empty=args.include_empty,
    )
    rendered = (
        render_transcript_markdown(args.thread_id, turns)
        if args.format == "markdown"
        else render_transcript_json(turns)
    )

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
        try:
            print(rendered, end="")
        except UnicodeEncodeError:
            sys.stdout.buffer.write(rendered.encode("utf-8", errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

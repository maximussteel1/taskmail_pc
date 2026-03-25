"""Summarize recent external-delivery evidence for a task root or live config."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mail_runner.config import PROJECT_ROOT, load_config
from mail_runner.external_delivery_window import build_external_delivery_window_report


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize recent external-delivery evidence for a task root or config.",
    )
    parser.add_argument("--config", "-c", help="Optional config path used to resolve task_root and owner preference")
    parser.add_argument("--task-root", help="Optional explicit task_root. Overrides --config resolved task_root.")
    parser.add_argument(
        "--limit-runs",
        type=int,
        default=20,
        help="How many most-recent runs with external delivery evidence to include.",
    )
    parser.add_argument(
        "--owner-preference",
        choices=("auto", "cos", "file_surface"),
        help="Optional explicit owner preference. Defaults to config value when --config is passed.",
    )
    parser.add_argument("--output", help="Optional JSON output path.")
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()

    config = load_config(args.config) if args.config else None
    if args.task_root:
        task_root = Path(args.task_root)
    elif config is not None:
        config_base_dir = Path(args.config).resolve().parent if args.config else PROJECT_ROOT
        task_root = config.resolve_task_root(config_base_dir)
    else:
        task_root = PROJECT_ROOT / "tasks"

    owner_preference = args.owner_preference
    if owner_preference is None and config is not None:
        owner_preference = config.external_delivery_backend_preference

    report = build_external_delivery_window_report(
        task_root,
        limit_runs=args.limit_runs,
        owner_preference=owner_preference,
    )
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

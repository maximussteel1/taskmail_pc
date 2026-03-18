"""Hosted entrypoint for the long-running mail runner loop."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .app import PROJECT_ROOT, configure_logging, run_forever
from .config import DEFAULT_CONFIG_PATH, ENV_PREFIX, AppConfig, load_config
from .host_lock import HostAlreadyRunningError, RuntimeHostLock
from .host_state import (
    HOST_STATUS_FAILED,
    HOST_STATUS_RUNNING,
    HOST_STATUS_STARTING,
    HOST_STATUS_STOPPED,
    HostStateStore,
    current_timestamp,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_RUNTIME_DIR = PROJECT_ROOT / "_tmp_live_mail_runner"

HOST_EXIT_OK = 0
HOST_EXIT_FAILED = 1
HOST_EXIT_ALREADY_RUNNING = 2


def resolve_config_path(config_path: str | None) -> Path:
    if config_path:
        return Path(config_path).resolve()
    env_config = os.getenv(f"{ENV_PREFIX}CONFIG")
    if env_config:
        return Path(env_config).resolve()
    return DEFAULT_CONFIG_PATH.resolve()


def resolve_runtime_dir(runtime_dir: str | None) -> Path:
    if runtime_dir:
        return Path(runtime_dir).resolve()
    return DEFAULT_RUNTIME_DIR.resolve()


def run_host(
    *,
    config_path: str | None = None,
    runtime_dir: str | None = None,
    configure_logging_fn=configure_logging,
    config_loader=load_config,
    loop_runner=run_forever,
) -> int:
    configure_logging_fn()

    resolved_config_path = resolve_config_path(config_path)
    resolved_runtime_dir = resolve_runtime_dir(runtime_dir)
    resolved_runtime_dir.mkdir(parents=True, exist_ok=True)

    state_store = HostStateStore(resolved_runtime_dir)
    runtime_lock = RuntimeHostLock(resolved_runtime_dir)
    pid = os.getpid()
    started_at = current_timestamp()

    try:
        runtime_lock.acquire()
    except HostAlreadyRunningError:
        LOGGER.error(
            "Host refused to start because runtime_dir is already owned: %s",
            resolved_runtime_dir,
        )
        return HOST_EXIT_ALREADY_RUNNING

    state_store.write(
        status=HOST_STATUS_STARTING,
        pid=pid,
        started_at=started_at,
        config_path=str(resolved_config_path),
        runtime_dir=str(resolved_runtime_dir),
    )

    try:
        config = config_loader(str(resolved_config_path))
        _run_loop(
            config,
            resolved_config_path=resolved_config_path,
            resolved_runtime_dir=resolved_runtime_dir,
            state_store=state_store,
            started_at=started_at,
            pid=pid,
            loop_runner=loop_runner,
        )
    except KeyboardInterrupt:
        state_store.write(
            status=HOST_STATUS_STOPPED,
            pid=pid,
            started_at=started_at,
            config_path=str(resolved_config_path),
            runtime_dir=str(resolved_runtime_dir),
            exit_reason="Interrupted by operator.",
        )
        LOGGER.info("Host interrupted by operator.")
        return HOST_EXIT_OK
    except Exception as exc:
        state_store.write(
            status=HOST_STATUS_FAILED,
            pid=pid,
            started_at=started_at,
            config_path=str(resolved_config_path),
            runtime_dir=str(resolved_runtime_dir),
            exit_reason=f"{type(exc).__name__}: {exc}",
        )
        LOGGER.exception("Host terminated unexpectedly.")
        return HOST_EXIT_FAILED
    else:
        state_store.write(
            status=HOST_STATUS_STOPPED,
            pid=pid,
            started_at=started_at,
            config_path=str(resolved_config_path),
            runtime_dir=str(resolved_runtime_dir),
            exit_reason="Run loop returned normally.",
        )
        LOGGER.info("Host stopped after the run loop returned.")
        return HOST_EXIT_OK
    finally:
        runtime_lock.release()


def _run_loop(
    config: AppConfig,
    *,
    resolved_config_path: Path,
    resolved_runtime_dir: Path,
    state_store: HostStateStore,
    started_at: str,
    pid: int,
    loop_runner,
) -> None:
    os.environ["MAIL_RUNNER_CONFIG"] = str(resolved_config_path)
    os.environ["MAIL_RUNNER_RUNTIME_DIR"] = str(resolved_runtime_dir)
    state_store.write(
        status=HOST_STATUS_RUNNING,
        pid=pid,
        started_at=started_at,
        config_path=str(resolved_config_path),
        runtime_dir=str(resolved_runtime_dir),
    )
    LOGGER.info(
        "Host started. pid=%s config=%s runtime_dir=%s",
        pid,
        resolved_config_path,
        resolved_runtime_dir,
    )
    loop_runner(config, base_dir=resolved_config_path.parent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Host the long-running mail runner loop.")
    parser.add_argument("--config", help="Optional path to config.yaml")
    parser.add_argument(
        "--runtime-dir",
        help="Runtime directory for host metadata and logs (default: .\\_tmp_live_mail_runner).",
    )
    args = parser.parse_args(argv)
    return run_host(config_path=args.config, runtime_dir=args.runtime_dir)


if __name__ == "__main__":
    raise SystemExit(main())

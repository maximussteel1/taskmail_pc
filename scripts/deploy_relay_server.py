from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mail_runner.relay_server.auth import token_fingerprint
from mail_runner.relay_server.deploy import (
    RelayDeploymentConfig,
    relay_bundle_members,
    render_env_file,
    render_systemd_unit,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deploy the minimal relay server to a VPS over ssh/scp.")
    parser.add_argument("--host", required=True, help="Remote VPS host or IP.")
    parser.add_argument("--user", default="ubuntu", help="Remote SSH username.")
    parser.add_argument("--key-path", required=True, help="SSH private key path.")
    parser.add_argument("--remote-base-dir", default="/opt/mail_runner_relay", help="Remote relay base directory.")
    parser.add_argument("--service-name", default="mail-runner-relay", help="systemd service name.")
    parser.add_argument("--env-file-path", default="/etc/mail-runner-relay.env", help="Remote env file path.")
    parser.add_argument("--bind-host", default="0.0.0.0", help="Relay bind host.")
    parser.add_argument("--port", type=int, default=8787, help="Relay listen port.")
    parser.add_argument("--log-level", default="INFO", help="Relay log level.")
    parser.add_argument("--server-name", default="mail-runner-relay", help="Relay server display name.")
    parser.add_argument("--run-user", default="ubuntu", help="Remote service user.")
    parser.add_argument("--python-bin", default="python3", help="Remote Python executable name.")
    parser.add_argument("--state-dir", default="", help="Persistent state directory. Defaults to <remote-base-dir>/shared/state.")
    parser.add_argument(
        "--task-root",
        default="",
        help="Optional remote task_root visible to the relay as MAIL_RUNNER_TASK_ROOT.",
    )
    parser.add_argument("--smtp-host", required=True, help="SMTP host used by the relay for user-facing delivery.")
    parser.add_argument("--smtp-port", type=int, default=465, help="SMTP port used by the relay.")
    parser.add_argument("--smtp-user", required=True, help="SMTP username used by the relay.")
    parser.add_argument("--smtp-password", required=True, help="SMTP password used by the relay.")
    parser.add_argument("--from-name", default="Mail Runner Relay", help="From display name used by the relay.")
    parser.add_argument("--from-addr", required=True, help="From email address used by the relay.")
    parser.add_argument(
        "--control-plane-mode",
        default="hybrid",
        choices=["mail_first", "hybrid", "vps_only"],
        help="MAIL_RUNNER_CONTROL_PLANE_MODE injected into the relay environment.",
    )
    parser.add_argument(
        "--taskmail-bot-mailbox-addr",
        default="",
        help="Bot mailbox address used as the delivery target for direct TaskMail new_task bridge ingress.",
    )
    parser.add_argument(
        "--taskmail-direct-from-name",
        default="TaskMail User",
        help="From display name used when the relay bridges direct TaskMail packets into bot mailbox mail ingress.",
    )
    parser.add_argument(
        "--taskmail-direct-from-addr",
        default="",
        help="From email address used when the relay bridges direct TaskMail packets into bot mailbox mail ingress.",
    )
    parser.add_argument(
        "--taskmail-direct-smtp-host",
        default="",
        help="Optional SMTP host used only for TaskMail direct bridge mail ingress.",
    )
    parser.add_argument(
        "--taskmail-direct-smtp-port",
        type=int,
        default=465,
        help="Optional SMTP port used only for TaskMail direct bridge mail ingress.",
    )
    parser.add_argument(
        "--taskmail-direct-smtp-user",
        default="",
        help="Optional SMTP username used only for TaskMail direct bridge mail ingress.",
    )
    parser.add_argument(
        "--taskmail-direct-smtp-password",
        default="",
        help="Optional SMTP password used only for TaskMail direct bridge mail ingress.",
    )
    parser.add_argument("--tls-certfile", default="", help="Remote TLS certificate path for WSS/HTTPS.")
    parser.add_argument("--tls-keyfile", default="", help="Remote TLS private key path for WSS/HTTPS.")
    parser.add_argument("--transport-token", default="", help="Explicit relay transport token.")
    parser.add_argument(
        "--android-app-token",
        default="",
        help="Explicit bearer token for the Android-facing create-session facade.",
    )
    return parser


def _project_root() -> Path:
    return PROJECT_ROOT


def _normalize_remote_path(path: str) -> str:
    return path.replace("\\", "/")


def _run(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    if input_text is None:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
        stdout = result.stdout
        stderr = result.stderr
    else:
        result = subprocess.run(
            command,
            input=input_text.encode("utf-8"),
            text=False,
            capture_output=True,
            check=False,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
    result.stdout = stdout
    result.stderr = stderr
    return result


def _current_user() -> str:
    if os.name == "nt":
        return os.environ.get("USERNAME", "").strip() or "Administrator"
    return os.environ.get("USER", "").strip() or "user"


def _prepare_private_key_copy(key_path: Path) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    if os.name != "nt":
        return key_path, None

    temp_dir = tempfile.TemporaryDirectory(prefix="relay-key-")
    temp_key = Path(temp_dir.name) / key_path.name
    shutil.copyfile(key_path, temp_key)
    user_name = _current_user()
    _run(["icacls", str(temp_key), "/inheritance:r"])
    _run(["icacls", str(temp_key), "/grant:r", f"{user_name}:R"])
    return temp_key, temp_dir


def _ssh_base_args(user: str, host: str, key_path: Path) -> list[str]:
    return [
        "ssh",
        "-F",
        _ssh_config_path_without_proxy(),
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-i",
        str(key_path),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "IdentitiesOnly=yes",
        f"{user}@{host}",
    ]


def _scp_base_args(user: str, host: str, key_path: Path) -> list[str]:
    return [
        "scp",
        "-F",
        _ssh_config_path_without_proxy(),
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-i",
        str(key_path),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "IdentitiesOnly=yes",
    ]


def _ssh_config_path_without_proxy() -> str:
    # Force direct PC->VPS deployment traffic and ignore ambient SSH proxy /
    # jump-host settings from the operator environment.
    return "NUL" if os.name == "nt" else "/dev/null"


def _create_bundle(repo_root: Path, bundle_path: Path) -> None:
    with tarfile.open(bundle_path, "w:gz") as tar:
        for member in relay_bundle_members():
            source = repo_root / member
            if source.is_dir():
                for path in sorted(source.rglob("*")):
                    if path.is_file():
                        tar.add(path, arcname=_normalize_remote_path(str(path.relative_to(repo_root))))
                continue
            tar.add(source, arcname=_normalize_remote_path(member))


def _remote_bootstrap_script(
    config: RelayDeploymentConfig,
    *,
    release_name: str,
    bundle_remote_path: str,
    env_remote_path: str,
    unit_remote_path: str,
) -> str:
    base_dir = _normalize_remote_path(config.remote_base_dir)
    release_dir = f"{base_dir}/releases/{release_name}"
    mkdir_lines = [
        "sudo mkdir -p \"$BASE_DIR/releases\" \"$BASE_DIR/shared/logs\"",
        f"sudo mkdir -p {config.state_dir}",
    ]
    if config.task_root:
        mkdir_lines.append(f"sudo mkdir -p {config.task_root}")
        mkdir_lines.append(f"sudo chown -R {config.run_user}:{config.run_user} {config.task_root}")
    return "\n".join(
        [
            "set -euo pipefail",
            f"BASE_DIR={base_dir}",
            f"RELEASE_DIR={release_dir}",
            f"RUN_USER={config.run_user}",
            *mkdir_lines,
            "sudo chown -R \"$RUN_USER\":\"$RUN_USER\" \"$BASE_DIR\"",
            "mkdir -p \"$RELEASE_DIR\"",
            f"tar -xzf {bundle_remote_path} -C \"$RELEASE_DIR\"",
            f"if [ ! -x {config.venv_python} ]; then sudo apt-get update && sudo apt-get install -y python3-venv; fi",
            f"if [ ! -x {config.venv_python} ]; then {config.python_bin} -m venv {config.remote_base_dir}/venv; fi",
            f"{config.venv_python} -m pip install --upgrade pip",
            f"{config.venv_python} -m pip install -r \"$RELEASE_DIR/requirements.txt\"",
            f"ln -sfn \"$RELEASE_DIR\" {config.current_dir}",
            f"sudo install -o root -g root -m 0644 {env_remote_path} {config.env_file_path}",
            f"sudo install -o root -g root -m 0644 {unit_remote_path} {config.unit_path}",
            f"sudo systemctl daemon-reload",
            f"sudo systemctl enable --now {config.service_name}",
            f"sudo systemctl restart {config.service_name}",
            f"sudo systemctl is-active {config.service_name}",
            f"for attempt in 1 2 3 4 5; do if curl --fail --silent {'https' if config.tls_certfile else 'http'}://127.0.0.1:{config.port}/healthz {'-k' if config.tls_certfile else ''}; then break; fi; sleep 1; done",
            f"curl --fail --silent {'https' if config.tls_certfile else 'http'}://127.0.0.1:{config.port}/healthz {'-k' if config.tls_certfile else ''} >/dev/null",
            f"rm -f {bundle_remote_path} {env_remote_path} {unit_remote_path}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo_root = _project_root()
    key_path = Path(args.key_path).resolve()
    config = RelayDeploymentConfig(
        service_name=args.service_name,
        remote_base_dir=args.remote_base_dir,
        env_file_path=args.env_file_path,
        bind_host=args.bind_host,
        port=args.port,
        log_level=args.log_level,
        server_name=args.server_name,
        run_user=args.run_user,
        python_bin=args.python_bin,
        state_dir=args.state_dir,
        task_root=args.task_root,
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        smtp_user=args.smtp_user,
        smtp_password=args.smtp_password,
        from_name=args.from_name,
        from_addr=args.from_addr,
        control_plane_mode=args.control_plane_mode,
        taskmail_bot_mailbox_addr=args.taskmail_bot_mailbox_addr,
        taskmail_direct_from_name=args.taskmail_direct_from_name,
        taskmail_direct_from_addr=args.taskmail_direct_from_addr,
        taskmail_direct_smtp_host=args.taskmail_direct_smtp_host,
        taskmail_direct_smtp_port=args.taskmail_direct_smtp_port,
        taskmail_direct_smtp_user=args.taskmail_direct_smtp_user,
        taskmail_direct_smtp_password=args.taskmail_direct_smtp_password,
        tls_certfile=args.tls_certfile,
        tls_keyfile=args.tls_keyfile,
    )
    transport_token = str(args.transport_token or "").strip() or secrets.token_urlsafe(32)
    android_app_token = str(args.android_app_token or "").strip() or secrets.token_urlsafe(32)
    release_name = datetime.now().strftime("release_%Y%m%d_%H%M%S")

    with tempfile.TemporaryDirectory(prefix="relay-deploy-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        bundle_path = temp_dir / f"{release_name}.tar.gz"
        env_path = temp_dir / f"{config.service_name}.env"
        unit_path = temp_dir / f"{config.service_name}.service"
        _create_bundle(repo_root, bundle_path)
        env_path.write_text(
            render_env_file(
                config,
                transport_token=transport_token,
                android_app_token=android_app_token,
            ),
            encoding="utf-8",
        )
        unit_path.write_text(render_systemd_unit(config), encoding="utf-8")

        prepared_key, prepared_key_dir = _prepare_private_key_copy(key_path)
        try:
            scp_base = _scp_base_args(args.user, args.host, prepared_key)
            ssh_base = _ssh_base_args(args.user, args.host, prepared_key)
            bundle_remote_path = f"/tmp/{bundle_path.name}"
            env_remote_path = f"/tmp/{env_path.name}"
            unit_remote_path = f"/tmp/{unit_path.name}"
            _run([*scp_base, str(bundle_path), f"{args.user}@{args.host}:{bundle_remote_path}"])
            _run([*scp_base, str(env_path), f"{args.user}@{args.host}:{env_remote_path}"])
            _run([*scp_base, str(unit_path), f"{args.user}@{args.host}:{unit_remote_path}"])
            remote_script = _remote_bootstrap_script(
                config,
                release_name=release_name,
                bundle_remote_path=bundle_remote_path,
                env_remote_path=env_remote_path,
                unit_remote_path=unit_remote_path,
            )
            bootstrap_result = _run([*ssh_base, "bash", "-s"], input_text=remote_script)
            public_health_url = f"{'https' if config.tls_certfile else 'http'}://{args.host}:{config.port}/healthz"
            summary_lines = [
                f"release_name={release_name}",
                f"service_name={config.service_name}",
                f"remote_base_dir={config.remote_base_dir}",
                f"env_file_path={config.env_file_path}",
                f"public_health_url={public_health_url}",
                f"transport_token_id={token_fingerprint(transport_token)}",
                f"android_app_token={android_app_token}",
                f"android_app_token_id={token_fingerprint(android_app_token)}",
                "bootstrap_output_start",
                bootstrap_result.stdout.strip(),
                "bootstrap_output_end",
            ]
            print("\n".join(summary_lines))
        finally:
            if prepared_key_dir is not None:
                prepared_key_dir.cleanup()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

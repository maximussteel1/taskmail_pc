# VPS Environment Baseline

## Status

- Date: 2026-03-20
- Scope: inspected baseline for the current VPS that is intended for the first relay workstream
- Layer: Layer 2 repository plan
- Inspection method: read-only SSH inspection from the current repository workspace

## Access Baseline

- Working SSH path:
  - user: `ubuntu`
  - auth: private key
- Known non-working path:
  - `root` password login from the current local notes did not authenticate

This document intentionally does not copy raw secrets or the full login material.

## Host Summary

- Provider family: Tencent Cloud `CVM`
- Virtualization: `kvm`
- Hostname: `VM-0-11-ubuntu`
- OS: `Ubuntu 24.04 LTS`
- Kernel: `6.8.0-71-generic`
- Architecture: `x86_64`
- Init/service manager: `systemd 255`
- SSH server: `OpenSSH_9.6p1`
- Time zone: `Asia/Shanghai`
- Clock sync: enabled

## Resource Baseline

- CPU: `2 vCPU`
- Memory: about `1.9 GiB`
- Swap: about `1.9 GiB`
- Root disk: `40 GiB`
- Free root disk during inspection: about `33 GiB`

This is enough for a lightweight relay/control-plane MVP.

## Installed Runtime/Tool Baseline

Present:

- `python3 3.12.3`
- `git 2.43.0`
- `curl 8.5.0`
- `cloud-init`

Not present during inspection:

- `node`
- `npm`
- `docker`
- `podman`
- `nginx`

## Network And Service Baseline

- System state: `running`
- Passwordless `sudo` for `ubuntu`: available
- Listening ports observed during inspection:
  - `22/tcp` for SSH
  - local resolver/system ports only
- `ufw`:
  - installed
  - status: `inactive`

## Immediate Development Implications

- Start with a Python-first relay service.
- Do not make Docker a day-one requirement.
- Keep deployment simple enough for `systemd + venv` first.
- Treat firewall enablement as a deliberate later step, not an implicit default.
- Standardize on key-based `ubuntu` access before any automation is written.

## Risks To Address Before Production Use

- remove reliance on plaintext root-password notes,
- ensure SSH key handling is documented and ignored by Git,
- define which public relay port will be opened later,
- decide whether `Node` is needed before adding another runtime to the box.

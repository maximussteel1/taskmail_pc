# PC Control-Plane Fixture Smoke 使用参考

## 目的

本文记录仓库里当前用于验证 `VPS-first` PC control-plane 已实现骨架的独立 fixture smoke 入口。

它验证的不是完整多机联调，而是当前仓库**已经存在**的最小骨架是否自洽：

1. `pc_hello -> hello_ack`
2. `workspace_snapshot`
3. `command_dispatch -> command_ack`
4. `connection_epoch` fencing
5. 基础拒绝语义，例如 `unsupported_backend`

同时，这条 smoke 会把当前还没实现的 control-plane 对象显式记成 gap，而不是假装已经存在。

## 相关入口

- 入口脚本：`.\.venv\Scripts\python.exe .\scripts\pc_control_plane_fixture_smoke.py`
- 实际实现：`mail_runner/pc_control_plane_fixture_smoke.py`
- 协议 helpers：`mail_runner/relay_server/pc_control_protocol.py`
- 运行时：`mail_runner/relay_server/pc_control_runtime.py`
- PC 侧 sidecar：`mail_runner/pc_control_plane_client.py`

## 与其他文档的关系

- 当前 `vps-only` 支持测试目标见 [vps_only_support_test_targets.md](./vps_only_support_test_targets.md)
- 当前 control-plane fixture 实测结果与证据见 [pc_control_plane_fixture_smoke_validation.md](./pc_control_plane_fixture_smoke_validation.md)

## 运行方式

```powershell
.\.venv\Scripts\python.exe .\scripts\pc_control_plane_fixture_smoke.py
```

常用可选参数：

- `--run-name <name>`：固定结果目录名，便于留证据
- `--output-dir <path>`：指定输出根目录，默认 `_tmp_pc_control_plane_fixture_smoke`

## 这条 smoke 实际做了什么

脚本使用 in-memory store 和 fixture client/runtime，模拟一条最小 loopback：

1. 发送 `pc_hello`
2. 接收 `hello_ack`
3. 发送 `workspace_snapshot`
4. 下发一条合法 `command_dispatch`
5. PC 侧做本地 admission，并返回 `command_ack=accepted`
6. 再下发一条合法 `command_dispatch`
7. 在 runner 忙碌条件下返回 `command_ack=accepted_but_queued`
8. 直接验证一条 `unsupported_backend` 拒绝路径
9. 通过第二次 hello + 旧 heartbeat 验证 `stale_connection_epoch`

脚本还会额外记录当前 gap：

- `event`
- `result`
- `output_chunk`
- capability 层宣称存在 `artifact_manifest`，但还没有 canonical control-plane packet

## 与 pytest 的关系

这条 smoke 当前**不挂在 `tests/` 主测试集里**。

当前口径是：

- 协议 parser / runtime 的轻量自动化测试继续放在 `tests/`
- 这条 fixture smoke 单独保留一份“当前 control-plane 骨架已经到哪”的综合证据

## 清理口径

这条 smoke 是纯 fixture harness，不会拉起额外进程、sidecar 或监听端口。

结果文件会显式记录：

- `cleanup.required = false`
- `cleanup.cleanup_ok = true`

## 什么时候应更新本文

出现以下任一情况时，应更新：

- `pc_control_plane_fixture_smoke.py` 的步骤或断言变化
- control-plane 已实现消息类型变化
- 仓库开始落地 canonical `event / result / output_chunk / artifact_manifest` packet

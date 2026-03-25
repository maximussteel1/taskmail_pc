# PC Control-Plane Fixture Smoke 使用参考

## 目的

本文记录仓库里当前用于验证 `VPS-first` PC control-plane 已实现骨架的独立 fixture smoke 入口。

它验证的不是完整多机联调，而是当前仓库**已经存在**的最小骨架是否自洽：

1. `pc_hello -> hello_ack`
2. `workspace_snapshot`
3. `command_dispatch -> command_ack`
4. `command_ack -> event -> output_chunk -> reconnect -> output_resume_request -> selective replay -> result -> artifact_manifest`
5. `connection_epoch` fencing
6. 基础拒绝语义，例如 `unsupported_backend`

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
6. 对已接受命令补一条 canonical `event`
7. 对已接受命令先补一条 canonical `output_chunk(seq=1)`
8. 通过第二次 `pc_hello` 模拟重连
9. 由 server 侧按已有 `stream_id + seq` cursor 生成 `output_resume_request(after_seq=1)`
10. 由 client 侧基于已落盘 `stream.events.jsonl` 只补发缺失尾段 `output_chunk(seq=2)`
11. 对已接受命令补一条 canonical `result`
12. 基于真实 `artifact_index.json + artifact_file_binding_index.json` 投影一条 canonical `artifact_manifest`
13. 再下发一条合法 `command_dispatch`
14. 在 runner 忙碌条件下返回 `command_ack=accepted_but_queued`
15. 直接验证一条 `unsupported_backend` 拒绝路径
16. 通过旧 heartbeat 验证 `stale_connection_epoch`

脚本还会额外记录当前 gap：

- 真实 websocket 往返下的 `output_resume_request` roundtrip
- 多 `PC` 路由与订阅
- live `/v1/files` / COS external-delivery roundtrip 的 artifact evidence

补充说明：

- client 侧基于已落盘 `stream.events.jsonl` 的 reconnect resend 当前已由 `tests/test_pc_control_plane_client.py` 覆盖
- `output_resume_request` / `server-driven resume` 的 protocol/runtime/client first-pass 与 websocket roundtrip 当前已由定向 pytest 覆盖
- 这条 fixture smoke 当前已经改为走真实 `artifact_index.json + artifact_file_binding_index.json` 本地 truth-projection
- 这条 fixture smoke 当前已经覆盖 in-memory loopback 下的 reconnect -> `output_resume_request` -> selective replay
- 但它本身仍然不覆盖真实 websocket roundtrip、多 `PC` 订阅面，也不覆盖 live external-delivery roundtrip

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
- replay、truth-projection 或 higher-level evidence 级 gap 被关闭或改写

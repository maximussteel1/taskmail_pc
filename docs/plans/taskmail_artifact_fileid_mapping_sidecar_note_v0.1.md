# TaskMail `artifact_id -> file_id` 映射 Sidecar 说明（v0.1）

更新时间：2026-03-23

## 状态

- 本文定义 `mail_based_task_manager` 仓对 `artifact_id -> file_id` 映射 sidecar 的 repo-side 最小方案。
- 本文不改写 `docs/current/*` current truth；当前 repo-local artifact truth 仍是 `RunArtifact` + `artifact_index.json`。
- 本文的作用是把“本地 artifact 真相”与“transport-facing file object 真相”之间的投影关系写清楚，避免后续把 `file_id` 散落到 payload、日志或临时脚本里。

## Read First

- `docs/plans/run_artifact_delivery_plan.md`
- `docs/plans/taskmail_android_pc_control_artifact_companion_note_v0.1.md`
- `docs/plans/taskmail_file_surface_repo_responsibility_note_v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-pc-control-artifact-contract-v0.1.md`
- `mail_runner/artifact_resolver.py`
- `mail_runner/external_delivery.py`

## 1. 一句话职责

这个 sidecar 的职责不是替代 `artifact_index.json`，而是记录：

- 哪个 repo-local `artifact_id`
- 在哪一次上传后
- 对应到了哪个 transport-facing `file_id`
- 以及该绑定是否成功、何时成功、回填了什么 descriptor

## 2. 为什么需要单独 sidecar

如果没有单独 sidecar，repo-side 很容易退回到以下坏味道：

- 在 `artifact_index.json` 里混入 transport-specific 字段，污染本地 artifact truth
- 把 `file_id` 只写进一次性 payload 或 stdout，后续无法稳定复用
- 上传失败后没有 machine-readable 失败记录，只剩人工日志
- 同一个 `artifact_id` 重传后，无法判断应该复用哪个 `file_id`

因此 repo-side 需要一层明确投影：

- `artifact_index.json`
  - repo-local artifact truth
- `artifact_file_binding_index.json`
  - transport file binding truth

## 3. 推荐路径与生命周期

推荐 sidecar 路径：

```text
tasks/
  thread_001/
    runs/
      <task_id>/
        artifacts/
          manifest.json
          artifact_index.json
          artifact_file_binding_index.json
```

repo-side 读法：

- `artifact_file_binding_index.json` 与 `artifact_index.json` 同级
- 它不是用户交付文件
- 它在 `artifact_index.json` 生成之后才有意义
- 它只记录 transport-facing file binding，不回写 repo-local artifact 主真相

## 4. Sidecar 数据模型

推荐首版结构：

```json
{
  "schemaVersion": "taskmail-artifact-file-binding-index-v1",
  "task_id": "task_001",
  "thread_id": "thread_001",
  "artifacts_root": "tasks/thread_001/runs/task_001/artifacts",
  "generated_at": "2026-03-23T18:10:00Z",
  "surface": "v1_files",
  "items": [
    {
      "artifact_id": "artifact_chart",
      "local_path": "E:\\repo\\tasks\\thread_001\\runs\\task_001\\artifacts\\chart.png",
      "name": "chart.png",
      "kind": "image",
      "mime_type": "image/png",
      "byte_size": 182733,
      "sha256": "2f9f...",
      "bindings": [
        {
          "status": "uploaded",
          "uploaded_at": "2026-03-23T18:09:55Z",
          "role": "attachment",
          "file_id": "file_01JQFILE",
          "metadata_url": "/v1/files/file_01JQFILE",
          "download_url": "/v1/files/file_01JQFILE/content",
          "trace_id": "trace_01JQ...",
          "probe_id": "probe_01JQ...",
          "request_id": "req_01JQ...",
          "packet_id": "pkt_01JQ..."
        }
      ]
    }
  ]
}
```

顶层最低字段：

- `schemaVersion`
- `task_id`
- `thread_id`
- `artifacts_root`
- `generated_at`
- `surface`
- `items`

每个 artifact item 的最低字段：

- `artifact_id`
- `local_path`
- `name`
- `kind`
- `mime_type`
- `byte_size`
- `sha256`
- `bindings`

每个 binding 的最低字段：

- `status`
- `uploaded_at`
- `role`
- `file_id`
- `metadata_url`
- `download_url`

可选但强烈建议字段：

- `trace_id`
- `probe_id`
- `request_id`
- `packet_id`
- `error_code`
- `error_message`

## 5. `bindings` 的读法

repo-side 首版建议把 `bindings` 读成“同一 artifact 的 transport 投影历史”，而不是只保留最后一个值。

`status` 首版建议冻结为：

- `uploaded`
- `failed`
- `superseded`

读法规则：

- 同一个 `artifact_id` 可有多次 binding
- 后续 payload / result / event 默认选择“最新的 `uploaded` binding”
- 如果更晚一次上传失败，不应覆盖更早的成功 binding
- 如果本地 bytes 已变化且重新上传成功，应追加新 binding，并把旧 binding 视为 `superseded`

repo-side 不应：

- 因为重试上传就抹掉失败历史
- 因为存在旧 `file_id` 就在 bytes 改变后静默复用旧 binding

## 6. 写入时机与提交顺序

推荐提交顺序：

1. 先生成或读取 `artifact_index.json`
2. 再执行 `/v1/files` 上传
3. 上传成功或失败后更新 `artifact_file_binding_index.json`
4. sidecar durable 落盘后，才把 `file_id` 回填进 control payload、result、event 或 closeout artifact

repo-side 关键约束：

- 不应先把 `file_id` 发给外部，再补 sidecar
- sidecar 写入失败时，不应把该次上传当作“可稳定复用”
- sidecar 应使用与其他 file-backed truth 一致的安全写策略，而不是直接覆盖正式文件

## 7. 谁读这个 sidecar

推荐消费者：

- `/control` payload builder
- `/v1/files` 上传编排器
- probe/debug report merger
- closeout / evidence bundle builder

不建议消费者：

- `artifact_resolver`
- `reporter`
- mail 纯本地投递路径

原因很简单：

- `artifact_resolver` 与 `reporter` 应继续围绕 repo-local artifact truth 工作
- sidecar 只属于 transport-facing file projection 层

## 8. 失败记录要求

即使上传失败，也建议 sidecar 保留失败 binding：

```json
{
  "status": "failed",
  "uploaded_at": "2026-03-23T18:11:02Z",
  "role": "debug_artifact",
  "error_code": "payload_too_large",
  "error_message": "HTTP 413"
}
```

这样 repo-side 才能稳定回答：

- 这个 artifact 有没有尝试上传
- 为什么失败
- 是否还存在可复用的旧成功 binding

## 9. 与 shared contract 的关系

repo-side sidecar 不是 shared contract 的一部分，但它应镜像 shared contract 的关键字段：

- `artifact_id`
- `file_id`
- `metadata_url`
- `download_url`
- `trace_id`
- `probe_id`
- `request_id`
- `packet_id`

原因是：

- 它的目标就是稳定支撑 shared contract 的 payload / result / event 回填
- 如果 sidecar 与 shared contract 字段名完全脱钩，后续实现会再次出现双语义漂移

## 10. Merge Gate

在把这个 sidecar 当成 repo-side 正式依赖前，至少应满足：

1. 有 `artifact_index.json` 的 run 能稳定生成 sidecar
2. 上传成功样本会回填 `artifact_id -> file_id`
3. 上传失败样本会留下 machine-readable failure binding
4. 同一 `artifact_id` 重传后，选择规则可复现
5. `artifact_index.json` 不因 file binding 被污染

## 11. 当前结论

repo-side 最稳的做法不是把 `file_id` 散落进各处，而是显式引入：

- 本地 artifact 真相：`artifact_index.json`
- transport 文件投影：`artifact_file_binding_index.json`

这样后续 `/control`、`/v1/files`、probe、closeout 才能共享同一份 file binding 读法。

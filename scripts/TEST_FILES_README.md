# 测试文件说明

本目录下的 `test_*.json` 文件为测试用的临时结果文件，可以安全删除。

## 调用方式

```bash
# 从本项目目录运行
py scripts/fetch_mails.py -c mail_config.local.yaml -o <输出文件> [选项]

# 从外部项目调用（使用绝对路径）
py E:\projects\mail_based_task_manager\scripts\fetch_mails.py -o <输出文件> [选项]
```

## 参数说明

| 参数 | 说明 |
|------|------|
| `-c, --config` | 配置文件路径（可选，默认使用 `config.yaml` 或环境变量） |
| `-o, --output` | 输出 JSON 文件路径（必填） |
| `-n, --count` | 最大获取邮件数量（可选） |
| `--unseen` | 仅获取未读邮件（默认） |
| `--all` | 获取所有邮件 |

## 示例

```bash
# 获取最新 100 封邮件
py scripts/fetch_mails.py -c mail_config.local.yaml -o output.json -n 100 --all

# 仅获取未读邮件（最多 10 封）
py scripts/fetch_mails.py -c mail_config.local.yaml -o output.json -n 10 --unseen

# 从外部项目调用
py E:\projects\mail_based_task_manager\scripts\fetch_mails.py -o D:\my_project\emails.json -n 50 --all
```

## 输出格式

JSON 数组，每个元素包含：
- `message_id`: 邮件唯一标识
- `subject`: 主题
- `from_addr`: 发件人
- `to_addr`: 收件人
- `date`: 日期
- `in_reply_to`: 回复的邮件 ID
- `references`: 引用链
- `body_text`: 正文内容
- `raw_headers`: 原始头部信息
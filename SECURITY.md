# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| Latest  | :white_check_mark: |
| < Latest| :x:                |

## Reporting a Vulnerability

如果你发现了安全漏洞，请 **不要** 在公开 Issue 中披露。

请通过以下方式报告：
- 发送邮件至：chasezjj@163.com

我们会在 48 小时内确认收到报告，并在 7 天内提供初步评估。

## 安全最佳实践

- 所有 API Key 和密码存储在本地 `~/.superhealth/config.toml`，权限设置为 `0o600`
- 数据库文件 `health.db` 包含敏感健康数据，请勿提交到版本控制
- 定期更新依赖以修复已知漏洞：`pip install -U -r requirements.txt`

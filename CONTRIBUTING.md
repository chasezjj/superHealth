# Contributing to SuperHealth

感谢你的兴趣！以下是参与贡献的指南。

## 开发环境

```bash
git clone https://github.com/YOUR_USERNAME/superhealth.git
cd superhealth
pip install -e ".[dev]"
```

## 代码风格

- 使用 [ruff](https://docs.astral.sh/ruff/) 进行代码格式化与检查
- 使用 [mypy](https://mypy.readthedocs.io/) 进行类型检查
- 所有公共函数需包含类型注解

```bash
ruff check src/
ruff format src/
mypy src/ --ignore-missing-imports
```

## 提交 PR

1. Fork 本仓库并创建分支：`git checkout -b feature/my-feature`
2. 确保所有测试通过：`pytest`
3. 更新相关文档
4. 提交 Pull Request，描述清楚变更内容和动机

## 报告问题

请使用 GitHub Issues，并包含：
- 问题描述
- 复现步骤
- 期望行为 vs 实际行为
- 环境信息（Python 版本、操作系统）

## 安全漏洞

请勿在公开 Issue 中报告安全漏洞。请发送邮件至安全联系人（见 [SECURITY.md](SECURITY.md)）。

# 桌面备忘录

一个轻量的本地桌面备忘录工具，使用标准 Python 项目布局（src layout）。

## 环境

- Python >= 3.10
- 开发工具：`pytest`、`ruff`

## 安装

```bash
pip install -e ".[dev]"
```

## 使用

```bash
desktop-memo --help
```

## 测试

```bash
pytest
```

## 目录结构

```
桌面备忘录/
├── .gitignore
├── pyproject.toml
├── README.md
├── src/
│   └── desktop_memo/
│       ├── __init__.py
│       └── cli.py
└── tests/
    ├── __init__.py
    └── test_cli.py
```

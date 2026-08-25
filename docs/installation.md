# 安装与运行环境

## 环境要求

- Python 3.10 或更高版本
- SQLite 3（Python 标准库已内置；建议带 FTS5 与 trigram tokenizer）

项目运行依赖记录在 requirements.txt，开发和测试依赖记录在
requirements-dev.txt；后者也包含稳定版本分发包所需的 `build`。pyproject.toml
同时保存可安装包元数据。

## Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

## macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

只运行程序而不参与开发时，可以安装较小的运行依赖集：

```bash
python -m pip install -r requirements.txt
```

安装完成后，在项目根目录验证命令行入口：

~~~bash
python -m social_database help
~~~

main.py 是兼容入口，python main.py help 仍然可用。

程序不需要 AstrBot 运行依赖。AstrBot 或其他采集器只需输出兼容 xlsx 或
标准 JSON v1，再由 `python -m social_database import` 或 `import-json` 导入。

## 安装为命令行程序

需要从其他目录调用时，可以安装当前项目：

~~~bash
python -m pip install .
social-database help
~~~

开发阶段通常直接使用 python -m social_database，避免每次修改后重新安装。

## 常见问题

- 出现 ModuleNotFoundError：确认已经激活 .venv，并重新安装依赖。
- PowerShell 禁止激活脚本：可以不激活，直接执行
  .\.venv\Scripts\python.exe -m social_database help。
- 提示数据库不存在：先导入数据，或使用 --db 指向已有数据库。
- 搜索索引显示 `fts5_unavailable`：当前 SQLite 未提供 FTS5 trigram。程序会
  自动使用 LIKE，业务数据和导入仍可用；换用支持 FTS5 的 Python 后运行
  `python -m social_database reindex` 即可启用加速。

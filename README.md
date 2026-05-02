# SocialDatabase

一个用于整理群成员表格数据的 SQLite 项目。它从格式固定的 Excel 文件中读取群成员信息，自动完成基础清洗、去重、合并，并提供按多个关键词模糊搜索的能力。

## 项目目标

这个项目的核心目标有三点：

1. 从固定格式的表格中批量导入群成员数据。
2. 按 `user_id + group_id` 维度做去重和合并，避免重复记录。
3. 支持按 `user_id`、`group_id`、`group_name`、`nickname`、`card`、`title`、`join_time`、`last_sent_time` 等字段进行搜索。

## 目录结构

```text
SocialDatabase/
├── README.md
├── Examples/
│   ├── data.xlsx
│   └── data2.xlsx
├── config.py
├── config_template.py
├── importer.py
├── main.py
├── models.py
├── search.py
└── src/
  └── data/
    └── members.db
```

## 运行逻辑

### 1. 导入

`main.py` 接收 `import` 命令后，会调用 `importer.py` 读取 Excel 文件。

导入流程如下：

1. 读取 Excel 的每个工作表。
2. 根据表头提取固定字段。
3. 过滤掉没有 `user_id` 的无效行。
4. 按 `group_id` 创建或更新群组记录。
5. 按 `user_id` 创建或更新成员记录。
6. 按 `user_id + group_id` 创建或更新成员-群组绑定信息。

### 2. 搜索

`main.py` 接收 `search` 命令后，会调用 `search.py`。

搜索流程如下：

1. 使用关键字对多个字段做 `LIKE` 模糊匹配。
2. 查询 `member_group_info` 表，并联查 `groups` 表获取群名。
3. 按 `user_id` 聚合搜索结果。
4. 默认以 JSON 输出结果，同时保留文本格式化能力。

### 3. 交互式搜索

`interactive` 模式会持续读取输入内容，直到用户输入 `quit`、`exit` 或 `q`。

## 配置说明

项目配置已经从代码中拆出来，集中放在 `config.py`。

常用配置项包括：

| 配置项 | 说明 |
| --- | --- |
| `DB_PATH` | 默认 SQLite 数据库文件路径 |
| `SEARCH_OUTPUT_FORMAT` | 搜索结果默认输出格式，当前为 `json` |
| `REQUIRED_COLUMNS` | Excel 导入时必须具备的表头 |
| `SEARCH_TEXT_SEPARATOR` | 文本输出中的分隔线样式 |
| `SEARCH_UNKNOWN_VALUE` | 文本输出中用于显示空值的占位符 |
| `SEARCH_PROMPT` | 交互模式下的输入提示 |
| `SEARCH_EXIT_COMMANDS` | 退出交互模式的关键词 |

如果你要自定义配置，请以 `config_template.py` 为基础复制一份为 `config.py` 再修改。模板中不包含敏感信息。

## 简单操作指南

1. 导入数据：`python main.py import Examples/data.xlsx`
2. 搜索数据：`python main.py search <关键词>`
3. 进入交互模式：`python main.py interactive`
4. 查看帮助：`python main.py help`

## 运行方式

建议在项目根目录运行：

```bash
python main.py import Examples/data.xlsx
python main.py search <关键词>
python main.py interactive
```

如果你想直接运行子模块，也可以使用：

```bash
python importer.py Examples/data.xlsx
python search.py <关键词>
```

## 数据格式要求

导入文件需要是 Excel 文件，并且表头中至少包含以下字段：

`group_id`, `user_id`, `nickname`, `card`, `join_time`, `last_sent_time`, `title`, `group_name`

其中：

- `user_id` 是有效行的最小判断条件。
- `group_id` 决定群组维度。
- `group_name` 用于补全群组名称。
- `nickname`、`card`、`join_time`、`last_sent_time`、`title` 会被保存到成员-群组绑定信息中。

## 数据模型

项目使用三张表组织数据：

1. `groups`：保存群组基础信息。
2. `members`：保存成员基础信息。
3. `member_group_info`：保存成员在具体群组中的绑定信息。

其中 `member_group_info` 是搜索和展示的核心表，因为它把成员和群组信息组合在一起，便于按关键词聚合查询。

## 输出格式

当前默认输出为 JSON，格式大致如下：

```json
{
  "count": 1,
  "results": [
    {
      "user_id": "<示例用户ID>",
      "groups": [
        {
          "group_id": "<示例群ID>",
          "group_name": "示例群",
          "nickname": "张三",
          "card": "群名片",
          "join_time": "YYYY-MM-DD",
          "last_sent_time": "YYYY-MM-DD",
          "title": "管理员"
        }
      ]
    }
  ]
}
```

如果后续你需要人类可读文本输出，也可以继续使用 `search.py` 中保留的文本格式化逻辑。

## 开发建议

1. 新增字段时，先更新 `src/config.py` 中的 `REQUIRED_COLUMNS`。
2. 修改数据库结构时，先同步 `src/models.py`，再调整导入与搜索逻辑。
3. 如果要增加新的搜索关键词，建议在 `src/search.py` 的查询条件里补充，并同步更新 README。

## 开源协议

本项目采用 MIT License，详见 [LICENSE](LICENSE)。

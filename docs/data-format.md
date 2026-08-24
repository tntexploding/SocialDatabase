# Excel 数据格式

## 文件要求

- 文件扩展名必须是 .xlsx。
- 程序读取工作簿中的全部工作表。
- 每个工作表的第一行是表头。
- 所有工作表都必须包含完整的必要表头。
- 可以包含额外列，额外列不会写入数据库。

## 必要列

| 列名 | 是否允许空值 | 写入位置 |
| --- | --- | --- |
| group_id | 数据行不允许 | groups、member_group_info |
| user_id | 数据行不允许 | members、member_group_info |
| nickname | 允许 | member_group_info |
| card | 允许 | member_group_info |
| join_time | 允许 | member_group_info |
| last_sent_time | 允许 | member_group_info |
| title | 允许 | member_group_info |
| group_name | 允许 | groups |

表头缺列属于文件格式错误，会终止整次导入。某一数据行缺少 group_id 或
user_id 时只跳过该行。

## 值转换

- 空单元格转换为 null。
- 字符串会去除首尾空白。
- 日期和时间转换为 ISO 格式文本。
- 其他单元格通过字符串形式保存。

时间字段目前作为文本存储和搜索，不执行时区换算或日期范围查询。

## 合并规则

- group_id 相同：视为同一群组，最近的非空 group_name 生效。
- user_id 相同：视为同一成员。
- user_id 与 group_id 同时相同：视为同一条群成员关系。
- 重复关系中的新非空字段覆盖旧字段；新空值不删除已有信息。
- 新数据源中没有出现的历史成员或群组关系保持不变。
- 内容相同的 xlsx 默认根据 SHA-256 跳过，可使用 --force 强制处理。

项目不把新文件视为完整成员快照，不执行退群成员删除。未来即使增加
在群状态，也应使用状态或观察批次表达，而不是硬删除历史关系。

## 文件存放

真实 Excel 文件放在 data/input/。此目录内容受 .gitignore 保护，不应强制
加入 Git。自动化测试不得使用真实成员数据，应在 pytest 临时目录生成最小
工作簿。

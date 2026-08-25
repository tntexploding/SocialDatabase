# 本地数据目录

此目录只保存本机运行所需的私有或生成资源：

- input/：待导入的 .xlsx 或标准 JSON 批次文件。
- database/：程序生成的 SQLite 数据库及默认 backups/ 备份目录。
- output/：本地搜索导出的 JSON、CSV 或 xlsx 文件。

三个子目录中的实际数据均不进入 Git，只保留 .gitkeep 维护目录结构。
可公开的小型测试数据应放在 tests/fixtures/；测试生成的数据必须使用
pytest 临时目录，不能写入这里。

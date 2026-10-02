# task-list 关键词筛选回归测试

`test_task_list_query.py` 是针对 `task-list --query` 关键词匹配及其与
`--status` 取交集行为的命令行回归测试。

## 运行方式

在仓库根目录（`kanban/` 包所在目录）执行：

```bash
# 运行全部用例（通过为退出码 0，失败为 1）
python3 test_task_list_query.py

# 等价写法
python3 -m unittest test_task_list_query -v

# 只运行单个用例
python3 test_task_list_query.py \
    TaskListQueryRegression.test_query_percent_is_literal
```

仅依赖 Python 3 标准库，无需网络、无需安装第三方包。

## 隔离与数据准备

- 每个用例在系统临时目录中创建全新的 SQLite 文件，通过公开入口
  `python -m kanban --db <临时库>` 调用，结束后自动删除；
  不会读写使用者自己的数据库，用例可单独执行、可重复执行且结果一致。
- 测试数据只经 `project-create` / `task-create` / `task-move` 三个现有命令
  准备，不直接操作数据库，不依赖任何预存项目。

## 覆盖内容

- 大小写敏感的连续子串匹配：`API` 命中含大写 `API` 的两条任务；
  `Fix API` 只匹配单空格标题；内部双空格保留；`登录` 只匹配中文标题。
- 关键词首尾空白（空格、制表符）被去除，内部空白保留。
- `%`、`_`、单引号作为普通字符，分别只命中 `100%`、`item_name`、
  `Bob's task`。
- `--query` 与 `--status doing` 取交集，且只返回当前项目任务
  （另一项目的同名 doing 任务不串入）。
- 无匹配、项目没有任务时返回空数组；省略 `--query` 时保留原有全部列表与
  `--status` 筛选语义；结果按任务标识升序。
- 错误路径（空字符串或纯空白关键词、非法状态、项目标识为 0 或非数字、
  项目不存在、`--query` 缺值）：退出码 2、标准输出为空、标准错误说明原因，
  且已有任务与状态保持不变。
- 成功路径：退出码 0、标准输出为单个可解析的 JSON 数组、标准错误为空，
  任务对象字段为 `id` / `project_id` / `title` / `status`。

失败时会打印具体命令行输入、退出码、标准输出、标准错误以及实际与预期
任务集合的逐项差异，不依赖 JSON 键序或错误文案的完整拼写。

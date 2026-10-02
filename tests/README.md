# 回归测试说明

`test_task_list_query.py` 是针对 `task-list` 关键词筛选（`--query`）及其与状态筛选（`--status`）取交集行为的命令行回归测试。仅使用 Python 标准库（`unittest` / `subprocess` / `tempfile`），不改动产品代码。

## 运行方式

在仓库根目录下执行：

```bash
python -m unittest tests.test_task_list_query -v      # 运行全部用例
python tests/test_task_list_query.py                  # 直接运行整个文件
python tests/test_task_list_query.py -k status        # 按名称子串挑选用例
python -m unittest tests.test_task_list_query.TaskListQueryTest.test_query_case_sensitive_substring
```

## 测试方式

- 每个用例通过 README 公开的入口 `python -m kanban --db <临时目录下的 SQLite 文件>` 以子进程运行，数据库随用例创建、随用例删除，不接触使用者的数据库，可重复执行且结果一致。
- 数据仅通过 `project-create` / `task-create` / `task-move` 准备，不依赖网络或预先保存的项目。

## 覆盖内容

- **关键词匹配**：大小写敏感的标题连续子串（`Fix API` / `fix api` / `Fix  API` / `修复登录`）；首尾空白去除、内部空白保留；`%`、`_`、`'` 按普通字符匹配。
- **交集语义**：`--query` 与 `--status` 同时使用时取交集，且只作用于当前项目；省略 `--query` 时保留原有列表与状态筛选语义；结果按任务标识升序。
- **成功契约**：退出码 0、标准错误为空、标准输出为单个 JSON 数组，任务对象沿用 `id` / `project_id` / `title` / `status` 结构与字段值；无匹配或空项目返回 `[]`。
- **错误契约**：空或纯空白关键词、缺少 `--query` 参数值、非法状态、零或非数字项目标识、不存在的项目，均退出码 2、标准输出为空、标准错误说明原因，且已有任务及状态保持不变。

断言核对实际任务集合与顺序，不依赖 JSON 键序或错误文案的完整拼写；失败输出包含触发用例的输入与期望/实际差异，便于定位。

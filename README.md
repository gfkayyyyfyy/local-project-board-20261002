# 轻量项目看板

为小团队建设可长期使用的本地项目协作产品，逐步覆盖项目与任务、看板状态、负责人和标签、评论与变更历史、筛选检索、简单工作量统计以及数据导入导出。

计划采用：Python 3 标准库 / sqlite3 / argparse。

## 当前可用：最小闭环

仅使用 Python 3 标准库与 SQLite，无需网络或第三方依赖。入口：

```
python -m kanban --db <数据库路径> <命令> [参数...]
```

首次成功操作会自动创建不存在的数据库文件；不同进程使用相同路径读写同一份数据。

### 命令

| 命令 | 说明 | 成功输出 |
| --- | --- | --- |
| `project-create <项目名称>` | 创建项目，名称去除首尾空白，允许重名 | `{"id", "name"}` |
| `task-create <项目标识> <任务标题>` | 在已有项目下新建任务，标题去除首尾空白，初始状态为 `todo` | `{"id", "project_id", "title", "status"}` |
| `task-move <任务标识> <状态>` | 在 `todo`/`doing`/`done` 间移动（大小写精确匹配）；移到当前状态原样返回且不产生改动 | 任务对象 |
| `task-list <项目标识>` | 列出项目下全部任务，按任务标识升序；无任务时为 `[]` | 任务对象数组 |

项目与任务均使用稳定正整数标识，任务标识在同一数据库内跨项目唯一。

### 退出码约定

- `0`：成功，标准输出仅含一个 JSON 值，标准错误为空。
- `2`：用法错误（缺少参数、名称/标题为空白、标识不是正整数、目标项目或任务不存在、状态非法）。标准输出为空，标准错误说明原因，已有数据不变。
- `1`：存储失败（数据库路径无法打开、数据库文件不可用）。标准输出为空，标准错误说明原因。

### 示例

```sh
python -m kanban --db board.db project-create "Team A"
python -m kanban --db board.db task-create 1 "design API"
python -m kanban --db board.db task-move 1 doing
python -m kanban --db board.db task-list 1
```

运行测试：

```sh
python -m unittest discover -s tests -v
```

当前版本仅包含项目与任务创建、任务状态移动及项目内查询，不含负责人、标签、评论、历史、删除、筛选或导入导出。

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

# 标识输入边界回归测试

`test_id_boundary.py` 针对 task-create / task-list 的项目标识与 task-move
的任务标识，覆盖 SQLite 整数上限（9223372036854775807）边界，运行方式同上：

```bash
python3 test_id_boundary.py
python3 -m unittest test_id_boundary -v
```

- 超上限值（上限 + 1、五千个连续的 9，及它们带五千个前导零的同值写法）
  在三处标识参数上均退出码 2、标准输出为空、标准错误说明超出支持范围、
  无 Python 回溯，且不新增或改动任何任务。
- 上限值本身按存在性处理：范围内不存在的标识退出码 2 并说明不存在，
  不因位数多被当作越界。
- 前导零按数值等价处理（`0001` 乃至前导五千个零均与 `1` 相同）；全零按
  非正整数拒绝。
- 零、负数、非数字、缺失参数、范围内不存在标识的失败路径，以及失败后
  原任务标题/状态/所属项目/标识不变（含 `task-move <上限+1> doing`
  被拒绝后 `task-list 1` 中任务仍为 todo 的端到端场景）。
- 数据库无法打开时仍为退出码 1 的存储失败协议。

# task-rename 改名回归测试

`test_task_rename.py` 针对 `task-rename <任务标识> <新标题>`，数据准备、
改名与结果核对全部经公开命令完成（`project-create` / `task-create` /
`task-move` / `task-rename` / `task-list`），不直接操作数据库：

```bash
python3 test_task_rename.py
python3 -m unittest test_task_rename -v
python3 test_task_rename.py \
    TaskRenameRegression.test_rename_persists_saved_title
```

- 成功路径：在两个项目中准备同标题任务（目标项目另有一条同标题任务），
  目标任务置为 doing 后用带前导零的同值标识改名为 `"  修复  API_100%'  "`；
  退出码 0、stderr 为空、stdout 为只含 `id` / `project_id` / `title` /
  `status` 的单个 JSON 对象；保存标题为 `修复  API_100%'`（首尾空白去除，
  内部双空格、大小写、中文、`%`、`_`、单引号原样保留），标识、项目与
  doing 状态不变；其他任务的完整对象、数量与标识排序不变；按新标题
  关键词筛选能命中它，按旧标题筛选只剩未改名任务。
- 仅首尾空白不同的同一标题再次提交仍成功，返回同一任务且不新增记录；
  改成另一条任务的标题也成功（允许重名）。
- 拒绝路径：空字符串或纯空白标题、缺少必要参数、任务标识为 0 / 负数 /
  非数字 / `9223372036854775808` / 范围内不存在的标识，均退出码 2、
  stdout 为空、stderr 说明原因，两个项目的任务完整列表不变；数据库路径
  指向已有目录时退出码 1 并说明存储失败。
- 不依赖 JSON 键序或错误文案逐字拼写；失败时打印输入、退出码、stdout、
  stderr 及预期与实际差异。

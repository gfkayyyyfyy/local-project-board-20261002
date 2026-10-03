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

# task-create 创建任务回归测试

`test_task_create.py` 针对 `task-create <项目标识> <任务标题>`，数据准备、
创建与结果核对全部经公开命令完成（`project-create` / `task-move` /
`task-list`），不直接操作数据库：

```bash
python3 test_task_create.py
python3 -m unittest test_task_create -v
python3 -m unittest discover -p 'test_task_create.py' -v
python3 test_task_create.py \
    TaskCreateRegression.test_create_returns_json_object_and_persists
```

- 成功路径（两个项目）：标题 `"  整理  API_100%'  "` 保存为
  `整理  API_100%'`（只去首尾空白，内部双空格、中文、大小写、`%`、`_`、
  单引号原样保留）；退出码 0、stderr 为空、stdout 为只含 `id` /
  `project_id` / `title` / `status` 的单个 JSON 对象，id 为正整数、
  所属项目正确、状态 todo；由独立命令进程 `task-list` 查询同一数据库，
  任务与创建结果一致且不串入另一项目；重复提交同标题保留两条不同标识、
  按 id 升序；另一项目建新任务 id 仍唯一；已有任务移到 doing 后再创建，
  新任务 todo、已有任务完整内容不变；项目 1 存在时 `0001` 与 `1` 等价。
- 拒绝路径：空字符串或纯空白标题、缺少必要参数、项目标识为 0 / 负数 /
  非数字 / `9223372036854775808` / 范围内不存在的值，均退出码 2、
  stdout 为空、stderr 说明原因；上限值本身按项目是否存在处理，不误判
  为越界；失败前后项目与任务列表完全相同（含 `project-list` 快照）。
- 存储失败：有效参数配合指向已有目录的 `--db` 为退出码 1、stdout 为空、
  stderr 说明 `storage failure`，且不污染既有临时库。
- 不依赖 JSON 键序或错误文案逐字拼写；失败时打印命令行输入、实际退出码、
  stdout、stderr 及预期差异。

# project-create 创建项目回归测试
`test_project_create.py` 针对 `project-create <项目名称>`，数据准备、创建与
结果核对全部经公开命令完成（`project-create` / `task-create` / `task-move` /
`project-list` / `task-list`），不直接读写数据库：

```bash
python3 test_project_create.py
python3 -m unittest test_project_create -v
python3 -m unittest discover -p 'test_project_create.py' -v
python3 test_project_create.py \
    ProjectCreateRegression.test_create_on_missing_database_persists_and_is_listed
```

- 成功路径：父目录存在而数据库文件不存在时自动建库并创建成功；退出码 0、
  stderr 为空、stdout 为只含 `id` / `name` 的单个 JSON 对象，id 为正整数；
  输入 `"  研发  API_100%'  "`（首尾另加空格与制表符）时返回与落库名称均为
  `研发  API_100%'`（首尾空白去除，内部双空格、中文、大小写、`%`、`_`、
  单引号原样保留）；由另一个独立命令进程的 `project-list` 读到完全相同的
  对象；再次提交同一名称产生不同标识，两条记录同时保留并按标识升序。
- 已有项目中准备一条 doing 任务后再创建新项目：原项目对象与该任务的完整
  内容（标识、所属项目、标题、状态）保持不变，项目列表仅追加新项目。
- 拒绝路径：空字符串、纯空格或制表符名称、缺少名称参数，在可打开的既有
  临时库上均退出码 2、stdout 为空、stderr 说明原因且无 Python 回溯；拒绝
  前后 `project-list` 与原项目 `task-list` 返回值完全一致。
- 存储失败：有效名称配合指向已有目录的 `--db` 为退出码 1、stdout 为空、
  stderr 包含 `storage failure`，且不污染既有临时库。
- 不依赖 JSON 键序、错误文案逐字拼写或固定标识值；失败时打印输入、实际
  退出码、stdout、stderr 及预期差异。每个用例使用独立临时目录与全新
  SQLite 文件，结束后自动清理，不接触使用者自己的数据库。

# project-stats 项目任务状态汇总回归测试

`test_project_stats.py` 针对 `project-stats <项目标识>`，数据准备与结果核对全部
经公开命令完成（`project-create` / `task-create` / `task-move` / `task-list` /
`project-list` / `project-stats`），不直接写数据库（仅一处只读连接核对空库），
运行方式同其他回归：

```bash
python3 test_project_stats.py
python3 -m unittest test_project_stats -v
python3 test_project_stats.py \
    ProjectStatsRegression.test_acceptance_scenario
```

- 用户指定的验收场景端到端覆盖：项目 1 四条任务（t1/t2 todo、t3 doing、
  t4 done），项目 2 一条 doing；`task-move 1 doing` 后 `project-stats 1`
  恰为 `{"project_id":1,"total":4,"todo":1,"doing":2,"done":1}`，项目 2
  保持 `total=1, doing=1`；另一个独立进程查询同一路径读到相同结果。
- 成功路径：退出码 0、stderr 为空、stdout 为只含 `project_id` / `total` /
  `todo` / `doing` / `done` 的单个 JSON 对象，五个字段均为非负整数且
  三者之和等于 `total`；空项目四个数量均为 0；同标题任务各计一次；只统计
  目标项目；统计只反映当前已保存状态（同一任务多次来回移动不累计次数）；
  重复查询结果一致，且查询前后 `task-list` / `project-list` 快照不变
  （只读，不补写业务记录）。
- 标识规则：`0001` 及前导五千个零的 `1` 与 `1` 等价；零、全零、负数、
  非数字、小数/科学计数法/带正负号或空白、全角数字、`9223372036854775808`、
  五千个 9 及其前导零同值写法均退出码 2、stdout 为空、stderr 说明原因
  （越界值说明超出支持范围）且无回溯，拒绝前后统计不变；上限本身按存在性
  处理（不存在则说明 `does not exist`）；范围内不存在（如 999）、缺少项目
  标识、缺少 `--db` 同样退出码 2。
- 存储与初始化：`--db` 指向已有目录时退出码 1、stdout 为空、stderr 含
  `storage failure` 且无回溯；父目录存在而数据库文件不存在时沿用初始化
  行为创建空库，随后因项目 1 不存在退出码 2，库中无任何项目或任务。

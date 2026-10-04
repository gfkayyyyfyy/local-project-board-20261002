# project-list 关键词筛选回归测试

`test_project_list_query.py` 针对 `project-list [--query 关键词]` 的项目名称
筛选，数据准备与结果核对全部经公开命令完成（`project-create` / `task-create`
/ `project-list`），不直接写数据库，运行方式同其他回归：

```bash
python3 test_project_list_query.py
python3 -m unittest test_project_list_query -v
python3 test_project_list_query.py \
    ProjectListQueryRegression.test_acceptance_api_matches_project_names_only
```

- 用户指定的验收场景端到端覆盖：依次创建 `研发 API`、`研发 api`、`研发 API`
  （第三个无任务）和 `资料`（其任务标题含 `API`），`project-list --query
  "  API  "` 恰好返回第一、第三个项目（标识与创建一致），第二、第四个不出现。
- 关键词语义与 `task-list --query` 一致，但只匹配项目名称：去首尾空白、保留
  内部空白、大小写敏感的连续子串，不分词、不归一化；中文、`%`、`_`、引号为
  普通字符；同名项目各自保留，尚无任务的项目同样参与匹配；任务标题命中而项目
  名称不含关键词的项目不出现；无命中或空库返回空数组；省略 `--query` 时仍
  返回全部项目；结果按项目标识数值升序，对象只含 `id` / `name`。
- 只读与重复查询：筛选不改动项目、任务及任务状态，重复查询结果一致。
- 拒绝路径：空字符串或纯空白关键词、`--query` 缺值均退出码 2、stdout 为空、
  stderr 说明原因且无回溯，已有项目不变。
- 存储失败：有效关键词配合 `--db` 指向已有目录、父目录不存在或文件不是可用
  SQLite 数据库时退出码 1、stdout 为空、stderr 含 `storage failure` 且无回溯；
  父目录存在而数据库文件不存在时沿用自动建库行为并成功返回空数组。

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

# task-move --from 预期当前状态回归测试

`test_task_move_from.py` 针对 `task-move <任务标识> <状态> [--from <状态>]`
的来源状态校验，数据准备、移动与结果核对全部经公开命令完成
（`project-create` / `task-create` / `task-move` / `task-show` /
`task-list` / `project-stats`），不直接写数据库，运行方式同其他回归：

```bash
python3 test_task_move_from.py
python3 -m unittest test_task_move_from -v
python3 test_task_move_from.py \
    TaskMoveFromRegression.test_acceptance_match_then_repeat_conflicts
```

- 用户指定的验收场景端到端覆盖：两个同标题任务，任务 1（项目 1）为
  doing、任务 2 为 todo；`task-move 1 done --from doing` 退出码 0、
  stderr 为空、stdout 为状态 done 的任务 1 对象（标识、项目、标题原样），
  再次执行同一命令退出码 2、stdout 为空、stderr 同时说明当前状态 done 与
  预期状态 doing，任务 1 仍为 done、任务 2 仍为 todo，统计为
  todo=1 / doing=0 / done=1。
- 即使目标状态与当前相同（任务 2 todo，目标 todo，`--from doing`），
  来源不匹配也拒绝；当前与预期相符且目标等于当前（`--from todo` 目标
  todo）则成功返回原任务且不新增记录；来源合法但与当前值不同时不创建
  任务、不改变目标状态；标识允许前导零（`0001` 与 `1` 等价）。
- 原样拼写校验：`--from` 与目标状态的大小写变化、首尾空白、空字符串、
  未知值均退出码 2、stdout 为空、stderr 说明状态非法且无回溯；
  `--from` 缺值、必需位置参数缺失、标识无效/为 0/负数/不存在同样退出码
  2，拒绝前后 task-show 与 project-stats 不变。
- 省略 `--from` 时保留三种合法状态之间直接转换（含同状态移动）的现有
  规则；数据库无法打开时退出码 1、stdout 为空、stderr 含 `storage
  failure`。

# task-move 后接 task-rename 连续修改回归测试

`test_task_move_then_rename.py` 针对同一任务的连续修改（先 `task-move`
再 `task-rename`），数据准备、修改与结果核对全部经公开命令完成
（`project-create` / `task-create` / `task-move` / `task-rename` /
`task-show` / `task-list` / `project-stats`），不直接操作数据库：

```bash
python3 test_task_move_then_rename.py
python3 -m unittest test_task_move_then_rename -v
python3 test_task_move_then_rename.py \
    TaskMoveThenRenameRegression.test_move_then_rename_only_changes_target
```

- 连续操作场景端到端覆盖：两个项目各一条标题为 `整理 API` 的任务；
  先把项目一的目标任务从 todo 移到 doing，再改名为首尾带空白的
  ` 修复 API `（保存为 `修复 API`）。最终只有目标任务变为标题
  `修复 API`、状态 doing，标识与所属项目不变；另一项目的同标题任务
  保持原标题与 todo 状态，两个项目均不新增记录。
- 每次修改的退出码为 0、stderr 为空，stdout 为与 `task-show` 结构一致
  的单个 JSON 任务对象（保存后的最新值）；独立命令进程的 `task-show`
  读到与改名结果完全一致的对象，`task-list` 与 `project-stats` 继续
  反映当前标题与状态（新标题关键词只在项目一命中，项目一统计
  doing=1，项目二统计 todo=1）。
- 不依赖 JSON 键序或错误文案逐字拼写；失败时打印输入、退出码、stdout、
  stderr 及预期差异。

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

# task-show 按标识读取任务回归测试

`test_task_show.py` 针对 `task-show <任务标识>`，数据准备与结果核对全部经
公开命令完成（`project-create` / `task-create` / `task-move` /
`task-rename` / `task-list` / `project-list`），不直接写数据库（仅一处
只读连接核对自动建库后的空库），运行方式同其他回归：

```bash
python3 test_task_show.py
python3 -m unittest test_task_show -v
python3 test_task_show.py \
    TaskShowRegression.test_acceptance_two_same_titled_tasks
```

- 用户指定的验收场景端到端覆盖：两个项目各一条标题为 `整理 API` 的任务，
  第二条移到 doing、第一条保持 todo；用第二条标识及其前导零写法
  （`000x`、前导五千个零）查询都只返回第二条（所属项目二、doing）；
  其对象与 `task-list` 中该任务完全一致；经 `task-rename` 改名为
  `修复 API` 后再查询，返回新标题及原有标识、项目与状态。
- 成功路径：退出码 0、stderr 为空、stdout 只有一个 JSON 对象（非数组、
  无多余内容），对象只含 `id` / `project_id` / `title` / `status`，
  标题按保存值原样返回（内部空白、中文、大小写、`%`、`_`、引号不变），
  同标题任务按标识区分；成功查询与业务失败前后项目与任务快照均不变，
  重复查询结果一致（只读）。
- 标识规则：`0001` 及前导五千个零的 `1` 与 `1` 等价；零、全零、负数、
  非数字、小数/科学计数法、带正号或首尾空白、全角数字均为标识无效；
  `9223372036854775808`、五千个 9 及其前导零同值写法为越界；上限本身与
  范围内不存在标识（如 999）为任务不存在；三类均退出码 2、stdout 为空、
  stderr 分别说明标识无效、超出支持范围或 `does not exist` 且无回溯；
  缺少任务标识、缺少 `--db` 或其路径值同样退出码 2。
- 存储与初始化：`--db` 指向已有目录、父目录不存在、文件存在但不是可用
  SQLite 数据库时退出码 1、stdout 为空、stderr 含 `storage failure`
  且无回溯；父目录存在而数据库文件不存在时沿用自动建库行为创建空库，
  随后因任务不存在退出码 2，库中无任何项目或任务。

# task-transfer 跨项目转移任务回归测试

`test_task_transfer.py` 针对 `task-transfer <任务标识> <目标项目标识>`，
数据准备、转移与结果核对全部经公开命令完成（`project-create` /
`task-create` / `task-move` / `task-transfer` / `task-list` /
`project-stats` / `project-list`），不直接读写数据库：

```bash
python3 test_task_transfer.py
python3 -m unittest test_task_transfer -v
python3 test_task_transfer.py \
    TaskTransferRegression.test_transfer_to_other_project
```

- 成功路径：doing 状态任务转移到另一项目，退出码 0、stderr 为空、
  stdout 为与 `task-show` 结构一致的单个 JSON 对象（只含 `id` /
  `project_id` / `title` / `status`），仅 `project_id` 变为目标项目；
  独立命令进程 `task-list` 查询同一数据库确认归属已落库且按标识升序，
  来源项目不再包含该任务、目标项目原有同标题任务与来源项目对照任务
  完整对象不变、项目名称不变、任务总数不变（不复制）；`0001`/`0002`
  前导零写法与数值等价；目标就是当前所属项目时成功返回原任务且不新增
  记录；目标项目为空（新建无任务项目）也允许转移；`project-stats`
  按新归属统计（来源扣除、目标计入）。
- 转移后继续移动：`test_move_after_transfer_still_locates_by_original_id`
  端到端覆盖 alpha 的 T（doing）转入 beta 后，以 T 的原标识执行
  `task-move <T> done --from doing`——任务仍按跨项目唯一的原标识定位，
  来源校验读取转移后当前保存的 doing，退出码 0、stderr 为空、stdout 与
  `task-show` 同结构；独立查询确认 T 属于 beta、原标识与标题不变，
  alpha 只剩对照任务 S、beta 中 T(done) 与 U(todo) 各一次且按标识升序。
  再次提交同一移动请求退出码 2、stdout 为空、stderr 同时含当前 `done`
  与预期 `doing` 且无回溯，拒绝前后 `task-show`、两项目 `task-list` 与
  `project-stats` 完全相同；最终 alpha 共一条 todo、beta todo/done 各一条，
  两项目 doing 均为零，任务总数始终为三；转移与移动的响应均与随后独立
  查询一致。
- 拒绝路径：缺少任务标识或目标项目标识、标识非数字、零或全零、
  `9223372036854775808` 越界、任务不存在、目标项目不存在，均退出码 2、
  stdout 为空、stderr 说明对应原因且无回溯，失败前后两个项目的任务与
  项目快照完全一致。
- 存储失败：`--db` 指向不存在父目录下的文件时退出码 1、stdout 为空、
  stderr 说明存储失败且无回溯。
- 不依赖 JSON 键序或错误文案逐字拼写；失败时打印命令行输入、实际退出码、
  stdout、stderr 及预期差异。

# task-transfer 后组合筛选回归测试

`test_task_transfer_filters.py` 针对跨项目转移前后
`--status todo --status doing --query " API "` 组合筛选（两状态并集再与
标题关键词取交集）下的 `task-list` 与 `project-stats` 结果，数据准备、
转移与核对全部经公开命令完成（`project-create` / `task-create` /
`task-move` / `task-transfer` / `task-list` / `project-stats` /
`task-show` / `project-list`），不直接写数据库：

```bash
python3 test_task_transfer_filters.py
python3 -m unittest test_task_transfer_filters -v
python3 test_task_transfer_filters.py \
    TaskTransferFilterRegression.test_acceptance_ownership_change_updates_combined_filter
```

- 用户指定的固定场景：源项目有两条 `Fix API`（doing、todo）、一条 doing 的
  `Fix api` 与一条 done 的 `Docs`，目标项目已有一条 todo 的 `Fix API`。
  转移前源项目组合筛选命中两条（total=2/todo=1/doing=1/done=0），目标项目
  只命中一条 todo；把源项目 doing 的 `Fix API` 转入目标项目后，源项目只命中
  原来的 todo，目标项目命中两条（total=2/todo=1/doing=1/done=0）。列表按
  任务标识升序，同标题任务各自保留，小写 `api` 与 `Docs` 均不进入结果。
- 转移响应与随后独立进程的 `task-show` 对象一致：标识、标题、状态不变，
  仅 `project_id` 变为目标项目；其他任务完整对象与项目名称保持原值；
  成功调用退出码 0、stderr 为空、stdout 只有一个可解析 JSON 值；
  重复查询结果逐字节一致且不改动数据。
- 重复转移到当前所属项目成功返回原任务，组合筛选列表与统计（连同完整列表、
  项目名称）均不变；转移到确定不存在的项目退出码 2、stdout 为空、stderr
  说明项目不存在且无 Python 回溯，前后完整任务列表与筛选统计完全相同。
- 断言只使用创建时返回的标识，不依赖 JSON 键序或错误文案逐字一致；失败时
  打印命令输入、退出码、stdout、stderr 与预期结果。每个用例使用独立临时
  数据库，结束后自动清理。

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

# project-rename 项目改名回归测试

`test_project_rename.py` 针对 `project-rename <项目标识> <新名称>`，数据准备、
改名与结果核对全部经公开命令完成（`project-create` / `task-create` /
`task-move` / `project-rename` / `project-list` / `task-list`），不直接写数据库
（仅一处只读连接核对自动建库后的空库），运行方式同其他回归：

```bash
python3 test_project_rename.py
python3 -m unittest test_project_rename -v
python3 test_project_rename.py \
ProjectRenameRegression.test_acceptance_two_same_named_projects
```

- 用户指定的验收场景端到端覆盖：两个同名项目 `研发`，前者有一条 doing
  任务、后者无任务；只把前者改名为首尾带空白的 `  研发 API  `（保存为
  `研发 API`）后，另一个独立命令进程的 `project-list --query API` 只返回
  前者，全量列表仍按标识升序、后者名称不变，两个项目的任务内容与数量
  完全不变；按旧名称子串 `研发` 筛选仍同时命中两者。
- 成功路径：退出码 0、stderr 为空、stdout 为只含 `id` / `name` 的单个
  JSON 对象，`id` 为原标识（带前导零的同值标识等价），`name` 为保存值；
  输入 `"  升级  API_100%'  "` 保存为 `升级  API_100%'`（首尾空白去除，
  内部双空格、中文、大小写、`%`、`_`、单引号原样保留）；改名只影响该
  项目名称，不创建项目、不合并同名项目，其任务的标识、标题、状态、所属
  项目与数量以及其他项目均不变。
- 仅首尾空白不同的同一名称再次提交仍成功，返回同一项目且不新增记录；
  改成另一个项目的名称也成功（允许重名，同名项目按标识各自保留）。
- 拒绝路径：空字符串或纯空白名称、缺少必要参数、项目标识为 0 / 负数 /
  非数字 / `9223372036854775808` / 范围内不存在的标识，均退出码 2、
  stdout 为空、stderr 说明原因且无回溯，项目与任务列表保持不变；父目录
  存在而数据库文件不存在时沿用自动建库行为创建空库，随后因项目不存在
  退出码 2，库中无任何项目或任务。
- 存储失败：`--db` 指向已有目录时退出码 1、stdout 为空、stderr 含
  `storage failure` 且无回溯，不污染既有临时库。

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

# project-stats 关键词筛选回归测试

`test_project_stats_query.py` 针对 `project-stats <项目标识> [--query 关键词]`，
数据准备与结果核对全部经公开命令完成（`project-create` / `task-create` /
`task-move` / `task-rename` / `task-list`），不直接写数据库，运行方式同其他回归：

```bash
python3 test_project_stats_query.py
python3 -m unittest test_project_stats_query -v
python3 test_project_stats_query.py \
    ProjectStatsQueryRegression.test_acceptance_scenario
```

- 用户指定的验收场景端到端覆盖：项目 1 两条 `Fix API`（todo / doing）及
  `Fix api`、`Docs`（done），项目 2 一条 `Fix API`（done）；
  `project-stats 1 --query API` 恰为
  `{"project_id":1,"total":2,"todo":1,"doing":1,"done":0}`，
  `task-list 1 --query API` 恰好返回统计对应的两条任务，项目 2 同名任务
  不串入；省略 `--query` 时统计范围仍为项目全部任务。
- 关键词语义与 `task-list --query` 一致：去首尾空白、保留内部空白、大小写
  敏感的连续子串；中文、`%`、`_`、引号为普通字符；只匹配标题不匹配项目
  名称；同标题任务按标识分别计数；空项目或无命中均成功返回四个 0。
- 统计反映当前保存值：`task-rename`、`task-move` 后按最新标题与状态计数；
  重复查询结果一致且只读，查询前后 `task-list` / `project-stats` /
  `project-list` 快照不变；独立进程查询同一路径结果相同。
- 拒绝路径：空字符串或纯空白关键词、`--query` 缺值、缺少项目标识、标识为
  0 / 负数 / 非数字 / `9223372036854775808` / 五千个 9 等均退出码 2、
  stdout 为空、stderr 说明原因（越界说明范围、范围内不存在说明
  `does not exist`）；前导零（含五千个）按数值等价；拒绝前后统计不变。
- 存储失败：`--db` 指向已有目录时退出码 1、stdout 为空、stderr 含
  `storage failure` 且无回溯。

# project-stats 状态筛选回归测试

`test_project_stats_status.py` 针对 `project-stats <项目标识> [--status 状态]
[--query 关键词]` 的状态筛选及其与关键词的交集行为，数据准备与结果核对全部
经公开命令完成（`project-create` / `task-create` / `task-move` /
`task-rename` / `task-list`），不直接写数据库，运行方式同其他回归：

```bash
python3 test_project_stats_status.py
python3 -m unittest test_project_stats_status -v
python3 test_project_stats_status.py \
    ProjectStatsStatusRegression.test_acceptance_scenario
```

- 用户指定的验收场景端到端覆盖：项目 1 两条 doing 的 `Fix API`、一条 todo
  的 `Fix API` 和一条 doing 的 `Fix api`，项目 2 另有一条 doing 的
  `Fix API`；`project-stats 1 --status doing --query API` 恰为
  `{"project_id":1,"total":2,"todo":0,"doing":2,"done":0}`，同条件的
  `task-list` 恰好只列出这两条任务，项目 2 不受影响；移动或改名其中一条
  后统计按最新状态与标题变化。
- 状态筛选语义：只接受 `todo` / `doing` / `done` 原样拼写；带状态筛选时
  `total` 等于命中任务数，所选状态的数量与 `total` 相等，另外两个状态为
  0；输出对象仍只含 `project_id` / `total` / `todo` / `doing` / `done`；
  与 `--query` 同时使用时取交集，关键词沿用现有规则（去首尾空白、保留
  内部空白、大小写敏感的连续子串，中文、`%`、`_`、引号为普通字符，不
  匹配项目名称）；省略 `--status` 时全量统计与关键词统计行为不变。
- 空项目或筛选无命中仍成功返回四个 0；同标题任务按各自标识分别计数；
  重复查询结果一致且只读，查询前后 `task-list` / `project-stats` /
  `project-list` 快照不变；独立进程查询同一路径结果相同。
- 拒绝路径：非法状态（大小写变化、带首尾空白、空字符串、未知取值）、
  `--status` 缺值、空关键词、缺少项目标识、标识为 0 / 负数 / 非数字 /
  `9223372036854775808` / 五千个 9 等均退出码 2、stdout 为空、stderr
  说明原因（越界说明范围、范围内不存在说明 `does not exist`）且无回溯；
  前导零（含五千个）按数值等价；拒绝前后项目与任务不变。
- 存储失败：`--db` 指向已有目录时退出码 1、stdout 为空、stderr 含
  `storage failure` 且无回溯。

# project-stats 多状态并集筛选回归测试

`test_project_stats_multi_status.py` 针对 `project-stats <项目标识>
[--status 状态]... [--query 关键词]` 的可重复 `--status` 多状态并集筛选，
数据准备与结果核对全部经公开命令完成（`project-create` / `task-create` /
`task-move` / `task-list` / `project-stats`），不直接写数据库，
运行方式同其他回归：

```bash
python3 test_project_stats_multi_status.py
python3 -m unittest test_project_stats_multi_status -v
python3 test_project_stats_multi_status.py \
    ProjectStatsMultiStatusRegression.test_acceptance_two_statuses_with_query
```

- 用户指定的验收场景端到端覆盖：项目 1 有 todo 的 `API准备`、doing 的
  `API实现`、done 的 `API归档` 及 todo 的 `文档整理`，项目 2 另有 doing 的
  `API实现`；`project-stats 1 --status todo --status doing --query " API "`
  恰为 `{"project_id":1,"total":2,"todo":1,"doing":1,"done":0}`，
  交换状态顺序并重复 `todo` 后结果相同，项目 2 同名任务不串入。
- 多状态语义：多个状态取并集，再与项目范围及 `--query` 标题条件取交集；
  状态顺序与重复值不影响结果，同一任务只计一次；全部三种状态等同于省略
  `--status`；只传一个状态或省略 `--status` 时保持原有统计结果；`total`
  为命中任务数，各状态数量只统计命中任务，未选择状态为 0，三者之和等于
  `total`；空项目或无命中返回四个 0；重复查询结果一致且只读。
- 拒绝路径：每次出现的状态都按原样校验，非法值即使位于合法值之前也拒绝
  整次查询（大小写变化、带首尾空白、空字符串、逗号合并状态、未知取值），
  `--status` 缺值、空关键词、缺少项目标识、标识为 0 / 负数 / 非数字 /
  `9223372036854775808`、项目不存在，均退出码 2、stdout 为空、stderr
  说明原因且无回溯，已有项目与任务不变。
- 存储失败：`--db` 指向已有目录时退出码 1、stdout 为空、stderr 含
  `storage failure` 且无回溯。

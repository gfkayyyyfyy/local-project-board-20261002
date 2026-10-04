"""kanban 命令行入口：python -m kanban --db <数据库路径> <子命令> ...

子命令：
  project-create <项目名称>           创建项目，输出 {"id", "name"}
  project-list [--query 关键词]
      按标识升序列出项目，输出 [{"id", "name"}]，可按名称关键词
      （大小写敏感的连续子串）筛选，省略 --query 时列出全部项目
  project-rename <项目标识> <新名称> [--from <预期原名称>]
      修改项目名称，保留标识与已有任务，输出 {"id", "name"}；可选 --from
      指定本次改名预期的当前名称，预期原名称与新名称都先去除首尾空白
      （内部空白、大小写、中文、%、_、引号原样保留），仅当项目已保存的
      名称与处理后的预期原名称逐字相等时才保存新名称，
      省略 --from 时不检查原名称
  task-create <项目标识> <任务标题>   在已有项目下创建任务（初始状态 todo）
  task-move <任务标识> <状态> [--from <状态>]
      在 todo/doing/done 之间移动任务；可选 --from 指定本次移动预期的
      当前状态，仅当任务已保存的状态与 --from 完全相同时才设置目标状态，
      省略 --from 时三种合法状态之间可直接转换
  task-rename <任务标识> <新标题> [--from <预期原标题>]
      修改任务标题，保留标识、所属项目与状态；可选 --from 指定本次改名
      预期的当前标题，预期原标题与新标题都先去除首尾空白（内部空白、
      大小写、中文、%、_、引号原样保留），仅当任务已保存的标题与处理后
      的预期原标题逐字相等时才保存新标题，省略 --from 时不检查原标题
  task-show <任务标识>               按跨项目唯一的任务标识读取单条任务，
                                      无需先知道所属项目，查询不改动数据
  task-transfer <任务标识> <目标项目标识> [--from <来源项目标识>]
      将单条任务转移到已有项目：只凭跨项目唯一的任务标识定位任务，
      仅修改所属项目，任务标识、标题、状态原样保留；目标就是当前所属
      项目时仍成功返回原任务，不新增记录，不复制任务，不创建项目；
      可选 --from 指定预期的当前所属项目，仅当任务已保存的所属项目
      与之按数值相等时才转移，省略 --from 时不检查来源
  task-list <项目标识> [--status 状态]... [--query 关键词]
      按任务标识升序列出项目任务，可按状态和标题关键词（大小写敏感的连续子串）
      筛选；--status 可在同一次调用中重复传入，每次接收一个状态，多个状态取并集，
      再与项目范围及标题关键词条件取交集；省略 --status 时返回全部状态的任务
  project-stats <项目标识> [--status 状态]... [--query 关键词]
      汇总项目当前各状态任务数量，可按状态和标题关键词（大小写敏感的
      连续子串）筛选；--status 可在同一次调用中重复传入，每次接收一个
      状态，多个状态取并集，再与项目范围及标题关键词条件取交集；
      省略 --status 时统计全部状态，
      输出 {"project_id", "total", "todo", "doing", "done"}

退出码：0 成功；2 参数或业务校验失败；1 存储（数据库）失败。
"""

import argparse
import json
import re
import sqlite3
import sys

VALID_STATUSES = ("todo", "doing", "done")

# SQLite INTEGER（有符号 64 位）可存储的最大正整数
SQLITE_MAX_INT = 9223372036854775807
_SQLITE_MAX_INT_DIGITS = str(SQLITE_MAX_INT)

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects (id),
    title      TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'todo'
);
"""

_POSITIVE_INT = re.compile(r"[0-9]+")


def usage_error(message):
    """参数或业务校验失败：退出码 2，标准输出保持为空。"""
    print(f"error: {message}", file=sys.stderr)
    sys.exit(2)


def storage_error(message):
    """数据库无法打开或不可用：退出码 1，标准输出保持为空。"""
    print(f"error: storage failure: {message}", file=sys.stderr)
    sys.exit(1)


def parse_positive_int(raw, what):
    """把 ASCII 数字拼写的标识解析为正整数。

    允许前导零，并按数值而非原始字符串长度判断边界：去掉前导零后与 SQLite
    有符号 64 位整数上限按位数和字典序比较，因此即使输入五千个零或五千个 9
    也不会触发 Python 大整数转换（3.11+ 对超长数字字符串有限制）。
    """
    if not isinstance(raw, str) or not _POSITIVE_INT.fullmatch(raw):
        usage_error(f"{what} must be a positive integer, got {raw!r}")
    digits = raw.lstrip("0") or "0"
    if digits == "0":
        usage_error(f"{what} must be a positive integer, got {raw!r}")
    if (len(digits) > len(_SQLITE_MAX_INT_DIGITS)
            or (len(digits) == len(_SQLITE_MAX_INT_DIGITS)
                and digits > _SQLITE_MAX_INT_DIGITS)):
        usage_error(
            f"{what} is out of the supported range "
            f"(1..{SQLITE_MAX_INT}), got {raw!r}"
        )
    return int(digits)


def connect(db_path):
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        return conn
    except sqlite3.Error as exc:
        storage_error(str(exc))


def emit(value):
    print(json.dumps(value, ensure_ascii=False))


def task_object(row):
    return {"id": row[0], "project_id": row[1], "title": row[2], "status": row[3]}


def cmd_project_create(conn, args):
    name = args.name.strip()
    if not name:
        usage_error("project name must not be empty")
    try:
        cur = conn.execute("INSERT INTO projects (name) VALUES (?)", (name,))
        conn.commit()
    except sqlite3.Error as exc:
        storage_error(str(exc))
    emit({"id": cur.lastrowid, "name": name})


def cmd_project_list(conn, args):
    keyword = normalize_query(args.query)
    if keyword is None:
        sql = "SELECT id, name FROM projects ORDER BY id ASC"
        params = ()
    else:
        # INSTR 为大小写敏感的连续子串匹配，只匹配项目名称、不匹配任务标题；
        # 中文、%、_、引号等均按普通字符处理
        sql = (
            "SELECT id, name FROM projects WHERE INSTR(name, ?) > 0 "
            "ORDER BY id ASC"
        )
        params = (keyword,)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error as exc:
        storage_error(str(exc))
    emit([{"id": row[0], "name": row[1]} for row in rows])


def require_project(conn, project_id):
    row = conn.execute(
        "SELECT id FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if row is None:
        usage_error(f"project {project_id} does not exist")


def cmd_project_rename(conn, args):
    project_id = parse_positive_int(args.project_id, "project id")
    name = args.name.strip()
    if not name:
        usage_error("project name must not be empty")
    # 预期原名称同样先去除首尾空白，内部空白、大小写、中文、%、_、引号
    # 原样保留；去空白后为空按参数错误拒绝
    if args.from_name is not None:
        expected_name = args.from_name.strip()
        if not expected_name:
            usage_error("expected original name (--from) must not be empty")
    else:
        expected_name = None
    try:
        row = conn.execute(
            "SELECT id, name FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            usage_error(f"project {project_id} does not exist")
        # 提供 --from 时，仅当项目已保存的名称与处理后的预期原名称逐字相等
        # 才允许改名，不做模糊匹配；新名称与当前名称相同但预期不匹配也拒绝
        if expected_name is not None and row[1] != expected_name:
            usage_error(
                f"project {project_id} current name is {row[1]!r}, "
                f"but --from expected {expected_name!r}"
            )
        if row[1] != name:
            conn.execute("UPDATE projects SET name = ? WHERE id = ?",
                         (name, project_id))
            conn.commit()
    except sqlite3.Error as exc:
        storage_error(str(exc))
    emit({"id": row[0], "name": name})


def cmd_task_create(conn, args):
    project_id = parse_positive_int(args.project_id, "project id")
    title = args.title.strip()
    if not title:
        usage_error("task title must not be empty")
    try:
        require_project(conn, project_id)
        cur = conn.execute(
            "INSERT INTO tasks (project_id, title, status) VALUES (?, ?, 'todo')",
            (project_id, title),
        )
        conn.commit()
    except sqlite3.Error as exc:
        storage_error(str(exc))
    emit({"id": cur.lastrowid, "project_id": project_id, "title": title,
          "status": "todo"})


def update_task(conn, args, plan_change, check_current=None):
    """task-move / task-rename / task-transfer 共用的单任务修改流程。

    依次完成：解析任务标识；由 plan_change() 校验本次修改的取值并返回
    （更新列, 新值），校验失败直接以退出码 2 拒绝；按标识定位目标任务
    （不存在则拒绝）；若提供 check_current()，再用已保存的任务对象做
    一次业务校验（如 task-move --from 的预期当前状态、task-transfer
    的目标项目存在性），不满足则以
    退出码 2 拒绝且不写库；仅当保存值确实变化时才写库提交；最后输出与
    task-show 结构一致的单个任务对象（保存后的最新值）。除本次修改的
    字段外，任务标识与其余字段保持不变，不新增记录，不影响其他任务。
    """
    task_id = parse_positive_int(args.task_id, "task id")
    column, value = plan_change()
    try:
        row = conn.execute(
            "SELECT id, project_id, title, status FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            usage_error(f"task {task_id} does not exist")
        task = task_object(row)
        if check_current is not None:
            check_current(task)
        if task[column] != value:
            # column 只取自下方三个命令的内部常量（"status" / "title" / "project_id"）
            conn.execute(
                "UPDATE tasks SET " + column + " = ? WHERE id = ?",
                (value, task_id),
            )
            conn.commit()
        task[column] = value
    except sqlite3.Error as exc:
        storage_error(str(exc))
    emit(task)


def cmd_task_move(conn, args):
    def invalid_status_error(label, status):
        usage_error(
            f"invalid {label} status {status!r}; expected one of: "
            + ", ".join(VALID_STATUSES)
        )

    def plan_change():
        # 目标状态与 --from 都按原样拼写精确校验：不去除空白、不转换大小写
        if args.status not in VALID_STATUSES:
            invalid_status_error("target", args.status)
        if args.from_status is not None and args.from_status not in VALID_STATUSES:
            invalid_status_error("--from", args.from_status)
        return "status", args.status

    expected_status = args.from_status

    def check_current(task):
        # 仅当任务已保存的状态与 --from 完全相同时才允许移动；
        # 目标状态与当前状态相同但来源不匹配时同样拒绝
        if expected_status is not None and task["status"] != expected_status:
            usage_error(
                f"task {task['id']} current status is {task['status']!r}, "
                f"but --from expected {expected_status!r}"
            )

    update_task(conn, args, plan_change, check_current)


def cmd_task_rename(conn, args):
    expected = {}

    def plan_change():
        title = args.title.strip()
        if not title:
            usage_error("task title must not be empty")
        # 预期原标题同样先去除首尾空白，内部空白、大小写、中文、%、_、引号
        # 原样保留；去空白后为空按参数错误拒绝
        if args.from_title is not None:
            expected["title"] = args.from_title.strip()
            if not expected["title"]:
                usage_error("expected original title (--from) must not be empty")
        else:
            expected["title"] = None
        return "title", title

    def check_current(task):
        # 提供 --from 时，仅当任务已保存的标题与处理后的预期原标题逐字相等
        # 才允许改名，不做模糊匹配；新标题与当前标题相同但预期不匹配也拒绝
        expected_title = expected["title"]
        if expected_title is not None and task["title"] != expected_title:
            usage_error(
                f"task {task['id']} current title is {task['title']!r}, "
                f"but --from expected {expected_title!r}"
            )

    update_task(conn, args, plan_change, check_current)


def cmd_task_show(conn, args):
    task_id = parse_positive_int(args.task_id, "task id")
    try:
        row = conn.execute(
            "SELECT id, project_id, title, status FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            usage_error(f"task {task_id} does not exist")
    except sqlite3.Error as exc:
        storage_error(str(exc))
    emit(task_object(row))


def cmd_task_transfer(conn, args):
    target = {}

    def plan_change():
        target["project_id"] = parse_positive_int(args.project_id, "project id")
        if args.from_project is not None:
            target["from_project"] = parse_positive_int(
                args.from_project, "--from project id"
            )
        else:
            target["from_project"] = None
        return "project_id", target["project_id"]

    def check_current(task):
        # 目标项目必须已存在；目标就是当前所属项目时同样成功，
        # 由 update_task 跳过实际写入，不新增记录
        require_project(conn, target["project_id"])
        # 提供 --from 时，仅当任务当前所属项目与预期来源按数值相等才允许
        # 转移；目标就是当前项目时同样检查。预期来源不存在也按归属不匹配
        # 拒绝，不创建该项目
        expected = target["from_project"]
        if expected is not None and task["project_id"] != expected:
            usage_error(
                f"task {task['id']} current project is {task['project_id']}, "
                f"but --from expected project {expected}"
            )

    update_task(conn, args, plan_change, check_current)


def normalize_query(raw):
    """规范化 --query 关键词（task-list、project-stats、project-list 共用）。

    省略 --query 时返回 None，表示不加关键词条件；否则去除首尾空白（保留内部
    空白），去空白后为空则按参数错误拒绝。匹配阶段使用 INSTR 做大小写敏感
    的连续子串匹配，因此中文、%、_、引号等均为普通字符；task-list 与
    project-stats 只匹配任务标题（不匹配项目名称），project-list 只匹配
    项目名称（不匹配任务标题）。
    """
    if raw is None:
        return None
    keyword = raw.strip()
    if not keyword:
        usage_error("query keyword must not be empty")
    return keyword


def validate_statuses(statuses):
    """校验可重复传入的 --status 列表（task-list、project-stats 共用）；省略（None）时不加状态条件。

    每次出现的值都按原样校验（不去除空白、不转换大小写），只接受
    todo/doing/done；任意一个值非法即拒绝整次查询，即使其余值合法。
    """
    if statuses is None:
        return None
    for status in statuses:
        if status not in VALID_STATUSES:
            usage_error(
                f"invalid status {status!r}; expected one of: "
                + ", ".join(VALID_STATUSES)
            )
    return statuses


def task_query_conditions(project_id, keyword, statuses=None):
    """组装项目任务查询共用的 WHERE 片段与绑定参数。

    固定按项目标识过滤；statuses 非空时追加状态精确匹配的并集条件（IN），
    重复状态自然去重，与参数先后顺序无关；keyword 非 None 时追加
    INSTR(title, ?) > 0 的大小写敏感连续子串条件。各条件之间为交集。
    """
    clauses = ["project_id = ?"]
    params = [project_id]
    if statuses:
        unique = list(dict.fromkeys(statuses))
        clauses.append("status IN (" + ", ".join("?" * len(unique)) + ")")
        params.extend(unique)
    if keyword is not None:
        # INSTR 为大小写敏感的连续子串匹配，%、_、引号等均按普通字符处理
        clauses.append("INSTR(title, ?) > 0")
        params.append(keyword)
    return clauses, params


def prepare_task_query(args):
    """task-list / project-stats 共用的查询准备：

    解析项目标识、规范化标题关键词、校验可重复传入的 --status 状态列表，
    并返回项目标识与 WHERE 片段/参数。项目是否存在由调用方在数据库访问
    阶段统一确认。
    """
    project_id = parse_positive_int(args.project_id, "project id")
    keyword = normalize_query(args.query)
    statuses = validate_statuses(args.status)
    clauses, params = task_query_conditions(project_id, keyword, statuses)
    return project_id, clauses, params


def cmd_task_list(conn, args):
    project_id, clauses, params = prepare_task_query(args)
    try:
        require_project(conn, project_id)
        rows = conn.execute(
            "SELECT id, project_id, title, status FROM tasks "
            "WHERE " + " AND ".join(clauses) + " ORDER BY id ASC",
            params,
        ).fetchall()
    except sqlite3.Error as exc:
        storage_error(str(exc))
    emit([task_object(row) for row in rows])


def cmd_project_stats(conn, args):
    project_id, clauses, params = prepare_task_query(args)
    counts = dict.fromkeys(VALID_STATUSES, 0)
    try:
        require_project(conn, project_id)
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM tasks WHERE "
            + " AND ".join(clauses) + " GROUP BY status",
            params,
        ).fetchall()
    except sqlite3.Error as exc:
        storage_error(str(exc))
    for status, count in rows:
        if status in counts:
            counts[status] = count
    total = sum(counts.values())
    emit({
        "project_id": project_id,
        "total": total,
        "todo": counts["todo"],
        "doing": counts["doing"],
        "done": counts["done"],
    })


def build_parser():
    parser = argparse.ArgumentParser(
        prog="kanban", description="本地项目任务看板（SQLite 存储）"
    )
    parser.add_argument("--db", required=True, help="SQLite 数据库文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("project-create", help="创建项目")
    p.add_argument("name", help="项目名称")
    p.set_defaults(func=cmd_project_create)

    p = sub.add_parser("project-list", help="列出全部项目")
    p.add_argument(
        "--query",
        help="可选名称关键词：去除首尾空白后按大小写敏感的连续子串匹配项目名称",
    )
    p.set_defaults(func=cmd_project_list)

    p = sub.add_parser("project-rename", help="修改项目名称")
    p.add_argument("project_id", help="项目标识（正整数）")
    p.add_argument("name", help="新项目名称")
    p.add_argument(
        "--from",
        dest="from_name",
        metavar="NAME",
        help="可选的预期原名称：与新名称一样先去除首尾空白（内部空白、"
             "大小写、中文、%%、_、引号原样保留），仅当项目已保存的名称与"
             "处理后的预期原名称逐字相等时才改名；省略时不检查原名称",
    )
    p.set_defaults(func=cmd_project_rename)

    p = sub.add_parser("task-create", help="创建任务")
    p.add_argument("project_id", help="目标项目标识（正整数）")
    p.add_argument("title", help="任务标题")
    p.set_defaults(func=cmd_task_create)

    p = sub.add_parser("task-move", help="移动任务状态")
    p.add_argument("task_id", help="任务标识（正整数）")
    p.add_argument("status", help="目标状态：todo / doing / done")
    p.add_argument(
        "--from",
        dest="from_status",
        metavar="STATUS",
        help="可选的预期当前状态：todo / doing / done，仅当任务已保存的状态"
             "与之完全相同时才移动；省略时三种合法状态之间可直接转换",
    )
    p.set_defaults(func=cmd_task_move)

    p = sub.add_parser("task-rename", help="修改任务标题")
    p.add_argument("task_id", help="任务标识（正整数）")
    p.add_argument("title", help="新任务标题")
    p.add_argument(
        "--from",
        dest="from_title",
        metavar="TITLE",
        help="可选的预期原标题：与新标题一样先去除首尾空白（内部空白、"
             "大小写、中文、%%、_、引号原样保留），仅当任务已保存的标题与"
             "处理后的预期原标题逐字相等时才改名；省略时不检查原标题",
    )
    p.set_defaults(func=cmd_task_rename)

    p = sub.add_parser("task-show", help="按标识读取单条任务")
    p.add_argument("task_id", help="任务标识（正整数，允许前导零）")
    p.set_defaults(func=cmd_task_show)

    p = sub.add_parser("task-transfer", help="将任务转移到已有项目")
    p.add_argument("task_id", help="任务标识（正整数，允许前导零）")
    p.add_argument("project_id", help="目标项目标识（正整数，允许前导零）")
    p.add_argument(
        "--from",
        dest="from_project",
        metavar="PROJECT_ID",
        help="可选的预期来源项目标识（正整数，允许前导零）：仅当任务当前"
             "所属项目与之按数值相等时才转移；省略时不检查来源",
    )
    p.set_defaults(func=cmd_task_transfer)

    p = sub.add_parser("task-list", help="列出项目任务")
    p.add_argument("project_id", help="项目标识（正整数）")
    p.add_argument(
        "--status",
        action="append",
        help="可选状态筛选：todo / doing / done；可重复传入，多个状态取并集；"
             "省略时返回全部任务",
    )
    p.add_argument(
        "--query",
        help="可选标题关键词：去除首尾空白后按大小写敏感的连续子串匹配标题",
    )
    p.set_defaults(func=cmd_task_list)

    p = sub.add_parser("project-stats", help="汇总项目任务状态数量")
    p.add_argument("project_id", help="项目标识（正整数）")
    p.add_argument(
        "--status",
        action="append",
        help="可选状态筛选：todo / doing / done；可重复传入，多个状态取并集；"
             "省略时统计全部状态",
    )
    p.add_argument(
        "--query",
        help="可选标题关键词：去除首尾空白后按大小写敏感的连续子串匹配标题",
    )
    p.set_defaults(func=cmd_project_stats)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    conn = connect(args.db)
    try:
        args.func(conn, args)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

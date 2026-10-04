"""kanban 命令行入口：python -m kanban --db <数据库路径> <子命令> ...

子命令：
  project-create <项目名称>           创建项目，输出 {"id", "name"}
  project-list [--query 关键词]
      按标识升序列出项目，输出 [{"id", "name"}]，可按名称关键词
      （大小写敏感的连续子串）筛选，省略 --query 时列出全部项目
  project-rename <项目标识> <新名称>
      修改项目名称，保留标识与已有任务，输出 {"id", "name"}
  task-create <项目标识> <任务标题>   在已有项目下创建任务（初始状态 todo）
  task-move <任务标识> <状态>         在 todo/doing/done 之间移动任务
  task-rename <任务标识> <新标题>     修改任务标题，保留标识、所属项目与状态
  task-show <任务标识>               按跨项目唯一的任务标识读取单条任务，
                                      无需先知道所属项目，查询不改动数据
  task-list <项目标识> [--status 状态] [--query 关键词]
      按任务标识升序列出项目任务，可按状态和标题关键词（大小写敏感的连续子串）筛选
  project-stats <项目标识> [--status 状态] [--query 关键词]
      汇总项目当前各状态任务数量，可按状态精确匹配和标题关键词
      （大小写敏感的连续子串）筛选，两者同时使用时取交集，
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
    try:
        row = conn.execute(
            "SELECT id, name FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            usage_error(f"project {project_id} does not exist")
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


def update_task(conn, args, plan_change):
    """task-move / task-rename 共用的单任务修改流程。

    依次完成：解析任务标识；由 plan_change() 校验本次修改的取值并返回
    （更新列, 新值），校验失败直接以退出码 2 拒绝；按标识定位目标任务
    （不存在则拒绝）；仅当保存值确实变化时才写库提交；最后输出与
    task-show 结构一致的单个任务对象（保存后的最新值）。任务标识、
    所属项目及其余字段保持不变，不新增记录，不影响其他任务。
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
        if task[column] != value:
            # column 只取自下方两个命令的内部常量（"status" / "title"）
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
    def plan_change():
        status = args.status
        if status not in VALID_STATUSES:
            usage_error(
                f"invalid status {status!r}; expected one of: "
                + ", ".join(VALID_STATUSES)
            )
        return "status", status

    update_task(conn, args, plan_change)


def cmd_task_rename(conn, args):
    def plan_change():
        title = args.title.strip()
        if not title:
            usage_error("task title must not be empty")
        return "title", title

    update_task(conn, args, plan_change)


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


def validate_status(status):
    """校验可选的 --status 精确匹配值；省略（None）时不加状态条件。"""
    if status is not None and status not in VALID_STATUSES:
        usage_error(
            f"invalid status {status!r}; expected one of: "
            + ", ".join(VALID_STATUSES)
        )
    return status


def task_query_conditions(project_id, keyword, status=None):
    """组装项目任务查询共用的 WHERE 片段与绑定参数。

    固定按项目标识过滤；status 非空时追加状态精确匹配；keyword 非 None 时
    追加 INSTR(title, ?) > 0 的大小写敏感连续子串条件。各条件之间为交集。
    """
    clauses = ["project_id = ?"]
    params = [project_id]
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if keyword is not None:
        # INSTR 为大小写敏感的连续子串匹配，%、_、引号等均按普通字符处理
        clauses.append("INSTR(title, ?) > 0")
        params.append(keyword)
    return clauses, params


def prepare_task_query(args, *, status_filter=False):
    """task-list / project-stats 共用的查询准备：

    解析项目标识、规范化标题关键词、按需校验状态，并返回项目标识与 WHERE
    片段/参数。项目是否存在由调用方在数据库访问阶段统一确认。
    """
    project_id = parse_positive_int(args.project_id, "project id")
    keyword = normalize_query(args.query)
    status = validate_status(args.status) if status_filter else None
    clauses, params = task_query_conditions(project_id, keyword, status)
    return project_id, clauses, params


def cmd_task_list(conn, args):
    project_id, clauses, params = prepare_task_query(args, status_filter=True)
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
    project_id, clauses, params = prepare_task_query(args, status_filter=True)
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
    p.set_defaults(func=cmd_project_rename)

    p = sub.add_parser("task-create", help="创建任务")
    p.add_argument("project_id", help="目标项目标识（正整数）")
    p.add_argument("title", help="任务标题")
    p.set_defaults(func=cmd_task_create)

    p = sub.add_parser("task-move", help="移动任务状态")
    p.add_argument("task_id", help="任务标识（正整数）")
    p.add_argument("status", help="目标状态：todo / doing / done")
    p.set_defaults(func=cmd_task_move)

    p = sub.add_parser("task-rename", help="修改任务标题")
    p.add_argument("task_id", help="任务标识（正整数）")
    p.add_argument("title", help="新任务标题")
    p.set_defaults(func=cmd_task_rename)

    p = sub.add_parser("task-show", help="按标识读取单条任务")
    p.add_argument("task_id", help="任务标识（正整数，允许前导零）")
    p.set_defaults(func=cmd_task_show)

    p = sub.add_parser("task-list", help="列出项目任务")
    p.add_argument("project_id", help="项目标识（正整数）")
    p.add_argument(
        "--status",
        help="可选状态筛选：todo / doing / done；省略时返回全部任务",
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
        help="可选状态筛选：todo / doing / done；省略时统计全部状态",
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

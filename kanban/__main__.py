"""kanban 命令行入口：python -m kanban --db <数据库路径> <子命令> ...

子命令：
  project-create <项目名称>           创建项目，输出 {"id", "name"}
  task-create <项目标识> <任务标题>   在已有项目下创建任务（初始状态 todo）
  task-move <任务标识> <状态>         在 todo/doing/done 之间移动任务
  task-list <项目标识> [--status 状态] [--query 关键词]
                                      按任务标识升序列出项目任务，可按状态和标题关键词筛选

退出码：0 成功；2 参数或业务校验失败；1 存储（数据库）失败。
"""

import argparse
import json
import re
import sqlite3
import sys

VALID_STATUSES = ("todo", "doing", "done")

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
    if not _POSITIVE_INT.fullmatch(raw):
        usage_error(f"{what} must be a positive integer, got {raw!r}")
    value = int(raw)
    if value < 1:
        usage_error(f"{what} must be a positive integer, got {raw!r}")
    return value


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


def require_project(conn, project_id):
    row = conn.execute(
        "SELECT id FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if row is None:
        usage_error(f"project {project_id} does not exist")


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


def cmd_task_move(conn, args):
    task_id = parse_positive_int(args.task_id, "task id")
    status = args.status
    if status not in VALID_STATUSES:
        usage_error(
            f"invalid status {status!r}; expected one of: "
            + ", ".join(VALID_STATUSES)
        )
    try:
        row = conn.execute(
            "SELECT id, project_id, title, status FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            usage_error(f"task {task_id} does not exist")
        if row[3] != status:
            conn.execute("UPDATE tasks SET status = ? WHERE id = ?",
                         (status, task_id))
            conn.commit()
    except sqlite3.Error as exc:
        storage_error(str(exc))
    emit({"id": row[0], "project_id": row[1], "title": row[2], "status": status})


def cmd_task_list(conn, args):
    project_id = parse_positive_int(args.project_id, "project id")
    status = args.status
    if status is not None and status not in VALID_STATUSES:
        usage_error(
            f"invalid status {status!r}; expected one of: "
            + ", ".join(VALID_STATUSES)
        )
    query = args.query
    if query is not None:
        query = query.strip()
        if not query:
            usage_error("query keyword must not be empty")
    try:
        require_project(conn, project_id)
        if status is None:
            rows = conn.execute(
                "SELECT id, project_id, title, status FROM tasks "
                "WHERE project_id = ? ORDER BY id ASC",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, project_id, title, status FROM tasks "
                "WHERE project_id = ? AND status = ? ORDER BY id ASC",
                (project_id, status),
            ).fetchall()
    except sqlite3.Error as exc:
        storage_error(str(exc))
    if query is not None:
        rows = [row for row in rows if query in row[2]]
    emit([task_object(row) for row in rows])


def build_parser():
    parser = argparse.ArgumentParser(
        prog="kanban", description="本地项目任务看板（SQLite 存储）"
    )
    parser.add_argument("--db", required=True, help="SQLite 数据库文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("project-create", help="创建项目")
    p.add_argument("name", help="项目名称")
    p.set_defaults(func=cmd_project_create)

    p = sub.add_parser("task-create", help="创建任务")
    p.add_argument("project_id", help="目标项目标识（正整数）")
    p.add_argument("title", help="任务标题")
    p.set_defaults(func=cmd_task_create)

    p = sub.add_parser("task-move", help="移动任务状态")
    p.add_argument("task_id", help="任务标识（正整数）")
    p.add_argument("status", help="目标状态：todo / doing / done")
    p.set_defaults(func=cmd_task_move)

    p = sub.add_parser("task-list", help="列出项目任务")
    p.add_argument("project_id", help="项目标识（正整数）")
    p.add_argument(
        "--status",
        help="可选状态筛选：todo / doing / done；省略时返回全部任务",
    )
    p.add_argument(
        "--query",
        help="可选标题关键词：大小写敏感的连续子串匹配，可与 --status 同时使用",
    )
    p.set_defaults(func=cmd_task_list)

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

"""Command-line interface for the local kanban board."""

import json
import os
import sqlite3
import sys

from .storage import StorageError, connect

STATUSES = ("todo", "doing", "done")


class UsageError(Exception):
    """A user-facing usage error (exit code 2)."""


def _positive_int(value, what):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise UsageError("%s must be a positive integer, got %r" % (what, value))
    if number <= 0:
        raise UsageError("%s must be a positive integer, got %r" % (what, value))
    return number


def _require_args(args, count, usage):
    if len(args) < count:
        raise UsageError("missing argument(s): %s" % usage)


def _task_object(row):
    return {
        "id": row[0],
        "project_id": row[1],
        "title": row[2],
        "status": row[3],
    }


def cmd_project_create(conn, args):
    _require_args(args, 1, "project-create <name>")
    name = args[0].strip()
    if not name:
        raise UsageError("project name must not be empty")
    cur = conn.execute("INSERT INTO projects (name) VALUES (?)", (name,))
    conn.commit()
    return {"id": cur.lastrowid, "name": name}


def cmd_task_create(conn, args):
    _require_args(args, 2, "task-create <project_id> <title>")
    project_id = _positive_int(args[0], "project id")
    title = args[1].strip()
    if not title:
        raise UsageError("task title must not be empty")
    row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise UsageError("project %d does not exist" % project_id)
    cur = conn.execute(
        "INSERT INTO tasks (project_id, title, status) VALUES (?, ?, 'todo')",
        (project_id, title),
    )
    conn.commit()
    return {"id": cur.lastrowid, "project_id": project_id, "title": title, "status": "todo"}


def cmd_task_move(conn, args):
    _require_args(args, 2, "task-move <task_id> <status>")
    task_id = _positive_int(args[0], "task id")
    status = args[1]
    if status not in STATUSES:
        raise UsageError(
            "invalid status %r: must be one of todo, doing, done (exact, case-sensitive)"
            % status
        )
    row = conn.execute(
        "SELECT id, project_id, title, status FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        raise UsageError("task %d does not exist" % task_id)
    if row[3] == status:
        # Moving to the current state is a success without any write.
        return _task_object(row)
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
    conn.commit()
    return {"id": row[0], "project_id": row[1], "title": row[2], "status": status}


def cmd_task_list(conn, args):
    _require_args(args, 1, "task-list <project_id>")
    project_id = _positive_int(args[0], "project id")
    row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise UsageError("project %d does not exist" % project_id)
    rows = conn.execute(
        "SELECT id, project_id, title, status FROM tasks "
        "WHERE project_id = ? ORDER BY id ASC",
        (project_id,),
    ).fetchall()
    return [_task_object(r) for r in rows]


COMMANDS = {
    "project-create": cmd_project_create,
    "task-create": cmd_task_create,
    "task-move": cmd_task_move,
    "task-list": cmd_task_list,
}


def _parse_argv(argv):
    """Split argv into (db_path, command, command_args)."""
    db_path = None
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--db":
            if i + 1 >= len(argv):
                raise UsageError("missing argument: --db <database path>")
            db_path = argv[i + 1]
            i += 2
        elif token.startswith("--db="):
            db_path = token[len("--db="):]
            i += 1
        else:
            break
    if db_path is None:
        raise UsageError("missing required option: --db <database path>")
    if i >= len(argv):
        raise UsageError(
            "missing command: one of %s" % ", ".join(sorted(COMMANDS))
        )
    command = argv[i]
    if command not in COMMANDS:
        raise UsageError(
            "unknown command %r: one of %s" % (command, ", ".join(sorted(COMMANDS)))
        )
    return db_path, command, argv[i + 1:]


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    try:
        db_path, command, command_args = _parse_argv(argv)
    except UsageError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    # A failed invocation against a path that did not exist before must not
    # leave a database file behind: validation precedes every commit, so the
    # file would contain only the empty schema.
    db_existed = os.path.exists(db_path)
    conn = None
    succeeded = False
    try:
        try:
            conn = connect(db_path)
            result = COMMANDS[command](conn, command_args)
        except StorageError as exc:
            print("storage failure: %s" % exc, file=sys.stderr)
            return 1
        except sqlite3.Error as exc:
            print("storage failure: %s" % exc, file=sys.stderr)
            return 1
        except UsageError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        succeeded = True
        return 0
    finally:
        if conn is not None:
            conn.close()
        if not db_existed and not succeeded and os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass

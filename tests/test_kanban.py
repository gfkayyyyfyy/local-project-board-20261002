"""Acceptance tests for the kanban CLI, run as subprocesses (Python stdlib only)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class KanbanCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "board.db")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, db_path=None):
        argv = ["-m", "kanban", "--db", db_path or self.db_path, *args]
        proc = subprocess.run(
            [sys.executable, *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        return proc

    def assertSuccess(self, proc):
        self.assertEqual(
            proc.returncode,
            0,
            msg="stdout=%r stderr=%r" % (proc.stdout, proc.stderr),
        )
        self.assertEqual(proc.stderr, "")
        return json.loads(proc.stdout)

    def assertUsageError(self, proc):
        self.assertEqual(proc.returncode, 2, msg="stderr=%r" % proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.strip())

    def assertStorageError(self, proc):
        self.assertEqual(proc.returncode, 1, msg="stderr=%r" % proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.strip())

    def test_acceptance_flow_cross_process(self):
        # Two projects (duplicate names allowed).
        p1 = self.assertSuccess(self.run_cli("project-create", "  Alpha  "))
        self.assertEqual(p1, {"id": 1, "name": "Alpha"})
        p2 = self.assertSuccess(self.run_cli("project-create", "Alpha"))
        self.assertEqual(p2, {"id": 2, "name": "Alpha"})

        # Database file was created by the first successful operation.
        self.assertTrue(os.path.exists(self.db_path))

        # Task in project 1, title whitespace trimmed, starts in todo.
        t = self.assertSuccess(
            self.run_cli("task-create", "1", "  first task  ")
        )
        self.assertEqual(
            t, {"id": 1, "project_id": 1, "title": "first task", "status": "todo"}
        )

        # Task ids are unique across projects: next task in project 2 is id 2.
        t2 = self.assertSuccess(self.run_cli("task-create", "2", "other"))
        self.assertEqual(t2["id"], 2)
        self.assertEqual(t2["project_id"], 2)

        moved = self.assertSuccess(self.run_cli("task-move", "1", "doing"))
        self.assertEqual(
            moved,
            {"id": 1, "project_id": 1, "title": "first task", "status": "doing"},
        )

        # Fresh processes (the CLI process exits after every command) see the
        # persisted state: correct project, title and status...
        listed = self.assertSuccess(self.run_cli("task-list", "1"))
        self.assertEqual(
            listed,
            [{"id": 1, "project_id": 1, "title": "first task", "status": "doing"}],
        )
        # ...ordered by task id ascending...
        self.assertSuccess(self.run_cli("task-create", "1", "second"))
        listed = self.assertSuccess(self.run_cli("task-list", "1"))
        self.assertEqual([t["id"] for t in listed], [1, 3])
        # ...and the other project shows only its own task(s).
        listed2 = self.assertSuccess(self.run_cli("task-list", "2"))
        self.assertEqual([t["id"] for t in listed2], [2])

        # Empty project returns an empty array.
        self.assertSuccess(self.run_cli("project-create", "empty"))
        self.assertEqual(self.assertSuccess(self.run_cli("task-list", "3")), [])

    def test_move_any_status_and_noop(self):
        self.assertSuccess(self.run_cli("project-create", "p"))
        self.assertSuccess(self.run_cli("task-create", "1", "t"))
        for status in ("doing", "done", "todo", "todo"):
            result = self.assertSuccess(self.run_cli("task-move", "1", status))
            self.assertEqual(result["status"], status)
        # No-op move to the current status returns the task unchanged.
        result = self.assertSuccess(self.run_cli("task-move", "1", "todo"))
        self.assertEqual(
            result, {"id": 1, "project_id": 1, "title": "t", "status": "todo"}
        )
        listed = self.assertSuccess(self.run_cli("task-list", "1"))
        self.assertEqual(len(listed), 1, msg="no-op move must not add records")

    def test_usage_errors_leave_data_unchanged(self):
        self.assertSuccess(self.run_cli("project-create", "p"))
        self.assertSuccess(self.run_cli("task-create", "1", "t"))
        before = self.assertSuccess(self.run_cli("task-list", "1"))

        bad_calls = [
            [],  # no command
            ["project-create"],  # missing name
            ["project-create", "   "],  # blank name
            ["task-create"],  # missing args
            ["task-create", "1"],  # missing title
            ["task-create", "1", "  "],  # blank title
            ["task-create", "abc", "x"],  # non-numeric project id
            ["task-create", "0", "x"],  # non-positive project id
            ["task-create", "-3", "x"],
            ["task-create", "1.5", "x"],
            ["task-create", "99", "x"],  # project missing
            ["task-move"],
            ["task-move", "1"],  # missing status
            ["task-move", "abc", "todo"],  # bad task id
            ["task-move", "0", "todo"],
            ["task-move", "99", "todo"],  # task missing
            ["task-move", "1", "TODO"],  # case must match exactly
            ["task-move", "1", "blocked"],
            ["task-list"],
            ["task-list", "x"],
            ["task-list", "99"],  # project missing
            ["bogus-command"],
            ["--db"],  # missing db value
        ]
        for call in bad_calls:
            proc = self.run_cli(*call)
            self.assertUsageError(proc, )

        after = self.assertSuccess(self.run_cli("task-list", "1"))
        self.assertEqual(after, before)

    def test_failed_first_operation_does_not_create_db(self):
        db = os.path.join(self.tmp.name, "nope.db")
        proc = self.run_cli("project-create", "   ", db_path=db)
        self.assertUsageError(proc)
        self.assertFalse(os.path.exists(db))

        # Storage failure also does not create a file in a missing directory.
        missing_dir = os.path.join(self.tmp.name, "no-such-dir", "x.db")
        proc = self.run_cli("project-create", "p", db_path=missing_dir)
        self.assertStorageError(proc)

    def test_unopenable_database_path_exits_1(self):
        # A path that is an existing directory cannot be a database file.
        proc = self.run_cli("project-create", "p", db_path=self.tmp.name)
        self.assertStorageError(proc)

    def test_corrupt_database_file_exits_1(self):
        with open(self.db_path, "wb") as fh:
            fh.write(b"not a sqlite database at all")
        proc = self.run_cli("project-create", "p")
        self.assertStorageError(proc)
        self.assertEqual(proc.stdout, "")

    def test_db_option_equals_form(self):
        proc = subprocess.run(
            [sys.executable, "-m", "kanban", "--db=%s" % self.db_path,
             "project-create", "eq"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["name"], "eq")


if __name__ == "__main__":
    unittest.main()

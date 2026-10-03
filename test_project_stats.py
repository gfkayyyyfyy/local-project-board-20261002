#!/usr/bin/env python3
"""project-stats 按项目汇总任务当前状态数量的命令行回归测试。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create / task-move 三个现有命令，不直接读写数据库，
不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时文件），
可单独执行、可重复执行，结果一致，结束后自动清理。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_stats.py
    python3 -m unittest test_project_stats -v

退出码：全部通过为 0，存在失败为 1。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

SQLITE_MAX_INT = 9223372036854775807
STATS_FIELDS = {"project_id", "total", "todo", "doing", "done"}


class ProjectStatsRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-stats-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")

    def tearDown(self):
        self._tmpdir.cleanup()

    def run_cli(self, *cli_args):
        return subprocess.run(
            [sys.executable, "-m", "kanban", "--db", self.db_path, *cli_args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def cli_json(self, *cli_args):
        proc = self.run_cli(*cli_args)
        self.assertEqual(proc.returncode, 0, self._detail(cli_args, proc))
        self.assertEqual(proc.stderr, "", self._detail(cli_args, proc))
        return json.loads(proc.stdout)

    @staticmethod
    def _detail(cli_args, proc):
        return (
            f"输入参数={list(cli_args)!r}\n"
            f"退出码={proc.returncode}\n"
            f"标准输出={proc.stdout!r}\n"
            f"标准错误={proc.stderr!r}"
        )

    def assert_stats(self, project_id, expected):
        proc = self.run_cli("project-stats", str(project_id))
        detail = self._detail(("project-stats", str(project_id)), proc)
        self.assertEqual(proc.returncode, 0, f"预期成功退出码 0：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        data = json.loads(proc.stdout)
        self.assertEqual(
            set(data.keys()), STATS_FIELDS, f"输出字段不符：\n{detail}"
        )
        self.assertEqual(data, expected, detail)
        for key in ("total", "todo", "doing", "done"):
            self.assertIsInstance(
                data[key], int, f"{key} 应为非负整数：\n{detail}"
            )
            self.assertGreaterEqual(data[key], 0, detail)
        self.assertEqual(
            data["todo"] + data["doing"] + data["done"], data["total"],
            f"三个状态数量之和应等于 total：\n{detail}",
        )

    def _make_two_projects_with_tasks(self):
        """验收夹具：项目1 四条任务（2 todo / 1 doing / 1 done），项目2 一条 doing。"""
        p1 = self.cli_json("project-create", "p1")["id"]
        p2 = self.cli_json("project-create", "p2")["id"]
        for title in ("t1", "t2", "t3", "t4"):
            self.cli_json("task-create", str(p1), title)
        self.cli_json("task-move", "3", "doing")
        self.cli_json("task-move", "4", "done")
        x = self.cli_json("task-create", str(p2), "x")
        self.cli_json("task-move", str(x["id"]), "doing")
        return p1, p2

    # ---------- 正常路径 ----------

    def test_acceptance_counts_current_statuses(self):
        p1, p2 = self._make_two_projects_with_tasks()
        # 验收：task-move 1 doing 后项目1 为 total 4 / todo 1 / doing 2 / done 1
        self.cli_json("task-move", "1", "doing")
        self.assert_stats(
            p1,
            {"project_id": p1, "total": 4, "todo": 1, "doing": 2, "done": 1},
        )

    def test_other_project_stats_isolated(self):
        p1, p2 = self._make_two_projects_with_tasks()
        self.cli_json("task-move", "1", "doing")
        # 项目2 的 doing 任务不受项目1 状态移动影响
        self.assert_stats(
            p2,
            {"project_id": p2, "total": 1, "todo": 0, "doing": 1, "done": 0},
        )

    def test_empty_project_counts_are_all_zero(self):
        pid = self.cli_json("project-create", "empty")["id"]
        self.assert_stats(
            pid,
            {"project_id": pid, "total": 0, "todo": 0, "doing": 0, "done": 0},
        )

    def test_initial_all_todo_project(self):
        pid = self.cli_json("project-create", "p")["id"]
        for title in ("a", "b", "c"):
            self.cli_json("task-create", str(pid), title)
        self.assert_stats(
            pid,
            {"project_id": pid, "total": 3, "todo": 3, "doing": 0, "done": 0},
        )

    def test_duplicate_titles_counted_separately(self):
        pid = self.cli_json("project-create", "p")["id"]
        self.cli_json("task-create", str(pid), "same")
        self.cli_json("task-create", str(pid), "same")
        self.assert_stats(
            pid,
            {"project_id": pid, "total": 2, "todo": 2, "doing": 0, "done": 0},
        )

    def test_counts_reflect_saved_state_not_move_history(self):
        # 同一任务多次移动后，只按最终状态计一次
        pid = self.cli_json("project-create", "p")["id"]
        tid = self.cli_json("task-create", str(pid), "t")["id"]
        self.cli_json("task-move", str(tid), "doing")
        self.cli_json("task-move", str(tid), "done")
        self.cli_json("task-move", str(tid), "todo")
        self.assert_stats(
            pid,
            {"project_id": pid, "total": 1, "todo": 1, "doing": 0, "done": 0},
        )

    def test_leading_zeros_equivalent_to_numeric_value(self):
        p1, _ = self._make_two_projects_with_tasks()
        proc = self.run_cli("project-stats", "0001")
        detail = self._detail(("project-stats", "0001"), proc)
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(
            json.loads(proc.stdout),
            {"project_id": p1, "total": 4, "todo": 2, "doing": 1, "done": 1},
        )

    def test_repeat_queries_identical_without_changes(self):
        p1, _ = self._make_two_projects_with_tasks()
        first = self.run_cli("project-stats", str(p1)).stdout
        second = self.run_cli("project-stats", str(p1)).stdout
        self.assertEqual(first, second)

    def test_stats_does_not_modify_data(self):
        p1, p2 = self._make_two_projects_with_tasks()
        before = {
            "projects": self.run_cli("project-list").stdout,
            "tasks1": self.run_cli("task-list", str(p1)).stdout,
            "tasks2": self.run_cli("task-list", str(p2)).stdout,
        }
        for _ in range(3):
            self.run_cli("project-stats", str(p1))
        after = {
            "projects": self.run_cli("project-list").stdout,
            "tasks1": self.run_cli("task-list", str(p1)).stdout,
            "tasks2": self.run_cli("task-list", str(p2)).stdout,
        }
        self.assertEqual(before, after)

    # ---------- 错误路径：退出码 2、stdout 空、stderr 说明原因 ----------

    def assert_usage_error(self, *cli_args):
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 2, f"预期退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "", f"标准错误应说明原因：\n{detail}")

    def test_error_missing_project_id(self):
        self.assert_usage_error("project-stats")

    def test_error_zero_id(self):
        self.assert_usage_error("project-stats", "0")

    def test_error_negative_id(self):
        self.assert_usage_error("project-stats", "-1")

    def test_error_non_numeric_id(self):
        self.assert_usage_error("project-stats", "abc")

    def test_error_nonexistent_project(self):
        self.assert_usage_error("project-stats", "999")

    def test_error_id_above_max(self):
        self.assert_usage_error("project-stats", str(SQLITE_MAX_INT + 1))

    def test_error_id_many_digits(self):
        self.assert_usage_error("project-stats", "9" * 5000)

    def test_max_int_treated_as_existence_check(self):
        # 上限本身合法，只是项目不存在
        self.assert_usage_error("project-stats", str(SQLITE_MAX_INT))

    def test_nonexistent_db_initializes_then_reports_missing_project(self):
        fresh = os.path.join(self._tmpdir.name, "fresh.db")
        proc = subprocess.run(
            [sys.executable, "-m", "kanban", "--db", fresh,
             "project-stats", "1"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("does not exist", proc.stderr)
        # 初始化只建库建表，不创建项目或任务
        import sqlite3
        conn = sqlite3.connect(fresh)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 0
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0
        )

    # ---------- 存储失败：退出码 1、stdout 空、无回溯 ----------

    def test_storage_failure_when_path_unavailable(self):
        bad = os.path.join(self._tmpdir.name, "no", "such", "dir", "x.db")
        proc = subprocess.run(
            [sys.executable, "-m", "kanban", "--db", bad,
             "project-stats", "1"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertIn("storage failure", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

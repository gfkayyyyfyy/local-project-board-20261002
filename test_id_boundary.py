#!/usr/bin/env python3
"""标识输入边界的命令行回归测试。

覆盖 task-create / task-list 的项目标识与 task-move 的任务标识：

- 超过 SQLite INTEGER 上界 9223372036854775807（含 9223372036854775808
  与五千个连续的 9）：退出码 2、标准输出为空、标准错误说明标识超出支持
  范围，不出现 Python 异常回溯，且失败前后数据完全不变。
- 前导零继续被允许并按数值判断边界：普通标识与带前导零（即使前附五千个
  零）的同值标识产生相同结果；带前导零的超上限数值同样被判越界。
- 上限值本身不按位数拒绝，而是按对应项目或任务是否存在返回结果。
- 零、负数、非数字、缺失参数仍为退出码 2；范围内但不存在的标识说明
  项目或任务不存在。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品；唯一例外是
上限值本身在“存在”一侧的夹具：自增主键无法通过公开命令得到
9223372036854775807，因此用 sqlite3 在隔离库中直接植入该标识，
不用于任何产品代码路径判断。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结束后自动清理，不接触使用者自己的
数据库。不依赖网络，仅使用 Python 标准库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_id_boundary.py                  # 运行全部回归用例
    python3 -m unittest test_id_boundary -v      # 等价写法
    python3 test_id_boundary.py \
        IdBoundaryRegression.test_move_overrange_rejected_leaves_data_intact

退出码：全部通过为 0，存在失败为 1。
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

SQLITE_MAX_INT = 9223372036854775807
OVERRANGE_ID = "9223372036854775808"          # 上限 + 1，仅多一位数
FIVE_THOUSAND_NINES = "9" * 5000             # 超长数字串，越过 int 文本转换限制
FIVE_THOUSAND_ZEROS = "0" * 5000
RANGE_HINT = "range"                         # 越界文案的稳定片段（不逐字校对）
EXISTENCE_HINT = "does not exist"            # 不存在文案的稳定片段
TASK_FIELDS = {"id", "project_id", "title", "status"}


class IdBoundaryRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-idbound-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")
        self._build_fixture()

    def tearDown(self):
        self._tmpdir.cleanup()

    # ---------- 命令行与结果解析辅助 ----------

    def run_cli(self, *cli_args, db_path=None):
        """执行 python -m kanban --db <临时库> ...，返回 CompletedProcess。"""
        return subprocess.run(
            [
                sys.executable, "-m", "kanban",
                "--db", self.db_path if db_path is None else db_path,
                *cli_args,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def cli_json(self, *cli_args):
        """准备数据用：要求成功并返回解析后的单个 JSON 值。"""
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"stdout 不是合法 JSON：{detail}")

    @staticmethod
    def _detail(cli_args, proc):
        shown = [
            arg if len(arg) <= 40 else f"<{len(arg)} 个字符的数字串>"
            for arg in cli_args
        ]
        stderr = proc.stderr
        if len(stderr) > 1000:
            stderr = stderr[:1000] + f"...<{len(stderr)} 字符>"
        return (
            f"输入参数={shown!r}\n"
            f"退出码={proc.returncode}\n"
            f"标准输出={proc.stdout!r}\n"
            f"标准错误={stderr!r}"
        )

    def assert_rejected(self, cli_args, *, hint=None):
        """断言请求被错误协议拒绝且数据不变：
        退出码 2、stdout 为空、stderr 有说明且无回溯；若给出 hint，
        stderr 必须包含该稳定片段。返回 stderr 以便追加断言。
        """
        before = self._snapshot()
        proc = self.run_cli(*cli_args)
        after = self._snapshot()
        detail = self._detail(cli_args, proc)

        self.assertEqual(proc.returncode, 2, f"预期退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"拒绝时标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"拒绝时标准错误应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr,
                         f"不得出现 Python 异常回溯：\n{detail}")
        if hint is not None:
            self.assertIn(hint, proc.stderr,
                          f"标准错误应包含 {hint!r}：\n{detail}")
        self.assertEqual(after, before, f"拒绝请求不得改动任何数据：\n{detail}")
        return proc.stderr

    def assert_range_error(self, *cli_args):
        """断言越界标识被拒绝，并明确提示范围而非“不存在”。"""
        stderr = self.assert_rejected(cli_args, hint=RANGE_HINT)
        self.assertIn(str(SQLITE_MAX_INT), stderr)
        self.assertNotIn(EXISTENCE_HINT, stderr)

    def assert_not_found_error(self, *cli_args):
        """断言标识按数值通过范围校验，但对应实体不存在。"""
        stderr = self.assert_rejected(cli_args, hint=EXISTENCE_HINT)
        self.assertNotIn(RANGE_HINT, stderr)

    def _snapshot(self):
        """记录全部项目与任务当前状态，用于校验错误请求无副作用。"""
        projects = {}
        for pid in range(1, 5):
            proc = self.run_cli("task-list", str(pid))
            if proc.returncode == 0:
                projects[pid] = json.loads(proc.stdout)
        return projects

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目一（alpha）含两条 todo 任务；项目二为空项目。
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        self.task_one = self.cli_json(
            "task-create", str(self.project_alpha), "first task"
        )
        self.task_two = self.cli_json(
            "task-create", str(self.project_alpha), "second task"
        )
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        self.assertEqual(self.project_alpha, 1)
        self.assertEqual(self.task_one["id"], 1)
        self.assertEqual(self.task_two["id"], 2)
        self.assertEqual(self.project_beta, 2)

    # ---------- task-move：超上限标识 ----------

    def test_move_overrange_rejected_leaves_data_intact(self):
        # 需求主场景：全新库中创建项目与任务后，把 9223372036854775808
        # 作为任务标识移到 doing，应被拒绝；task-list 1 中任务仍为 todo。
        self.assert_range_error("task-move", OVERRANGE_ID, "doing")
        data = self.cli_json("task-list", "1")
        self.assertEqual(data, [
            {"id": 1, "project_id": 1, "title": "first task", "status": "todo"},
            {"id": 2, "project_id": 1, "title": "second task", "status": "todo"},
        ])

    def test_move_five_thousand_nines_rejected_without_traceback(self):
        # 五千个连续的 9：不得触发 int() 文本转换限制异常
        self.assert_range_error("task-move", FIVE_THOUSAND_NINES, "doing")

    def test_move_padded_overrange_rejected(self):
        # 五千个零之后接超上限数值：按数值判断仍属越界，不按字符串长度误放
        self.assert_range_error(
            "task-move", FIVE_THOUSAND_ZEROS + OVERRANGE_ID, "doing"
        )

    def test_move_short_padded_overrange_rejected(self):
        self.assert_range_error("task-move", "000" + OVERRANGE_ID, "doing")

    # ---------- task-create：超上限项目标识，不得新增任务 ----------

    def test_task_create_overrange_project_rejected(self):
        self.assert_range_error(
            "task-create", OVERRANGE_ID, "should not be created"
        )

    def test_task_create_five_thousand_nines_project_rejected(self):
        self.assert_range_error(
            "task-create", FIVE_THOUSAND_NINES, "should not be created"
        )

    def test_task_create_padded_overrange_project_rejected(self):
        self.assert_range_error(
            "task-create", FIVE_THOUSAND_ZEROS + OVERRANGE_ID, "nope"
        )

    # ---------- task-list：超上限项目标识 ----------

    def test_task_list_overrange_project_rejected(self):
        self.assert_range_error("task-list", OVERRANGE_ID)

    def test_task_list_five_thousand_nines_project_rejected(self):
        self.assert_range_error("task-list", FIVE_THOUSAND_NINES)

    def test_task_list_padded_overrange_project_rejected(self):
        self.assert_range_error(
            "task-list", FIVE_THOUSAND_ZEROS + OVERRANGE_ID,
            "--status", "todo",
        )

    # ---------- 前导零：同值标识产生相同结果 ----------

    def test_task_list_padded_existing_id_matches_plain_id(self):
        plain = self.run_cli("task-list", "1")
        padded = self.run_cli("task-list", "0001")
        huge_padded = self.run_cli("task-list", FIVE_THOUSAND_ZEROS + "1")
        for proc, label in (
            (plain, "1"), (padded, "0001"),
            (huge_padded, FIVE_THOUSAND_ZEROS + "1"),
        ):
            self.assertEqual(proc.returncode, 0, f"{label}: {proc.stderr}")
            self.assertEqual(proc.stderr, "")
        self.assertEqual(padded.stdout, plain.stdout)
        self.assertEqual(huge_padded.stdout, plain.stdout)
        data = json.loads(plain.stdout)
        self.assertEqual([t["id"] for t in data], [1, 2])
        self.assertTrue(all(t["project_id"] == 1 for t in data))

    def test_task_create_padded_existing_project_succeeds(self):
        created = self.cli_json(
            "task-create", FIVE_THOUSAND_ZEROS + "1", "third task"
        )
        self.assertEqual(
            created,
            {"id": 3, "project_id": 1, "title": "third task", "status": "todo"},
        )
        # 输出中标识是规范数值而非带前导零的字符串
        self.assertIsInstance(created["project_id"], int)

    def test_task_move_padded_existing_id_succeeds(self):
        moved = self.cli_json("task-move", FIVE_THOUSAND_ZEROS + "1", "doing")
        self.assertEqual(
            moved,
            {"id": 1, "project_id": 1, "title": "first task", "status": "doing"},
        )
        self.assertIsInstance(moved["id"], int)
        # 再用普通标识同状态移动一次，同状态移动语义保持原样
        again = self.cli_json("task-move", "1", "doing")
        self.assertEqual(again, moved)
        # 另一条任务不受影响
        listing = self.cli_json("task-list", "1")
        self.assertEqual([t["status"] for t in listing], ["doing", "todo"])

    # ---------- 上限值本身：按存在与否返回，不因位数被当作越界 ----------

    def test_max_value_nonexistent_is_not_range_error_all_entries(self):
        # 三个入口收到恰好等于上限的标识：范围校验通过，落到“不存在”分支
        self.assert_not_found_error("task-list", str(SQLITE_MAX_INT))
        self.assert_not_found_error(
            "task-list", FIVE_THOUSAND_ZEROS + str(SQLITE_MAX_INT)
        )
        self.assert_not_found_error(
            "task-create", str(SQLITE_MAX_INT), "cannot attach"
        )
        self.assert_not_found_error(
            "task-move", str(SQLITE_MAX_INT), "doing"
        )

    def test_max_value_existing_project_listed_and_accepts_task(self):
        # 自增主键无法经公开命令得到上限标识：直接在隔离库植入 id=上限的项目
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO projects (id, name) VALUES (?, ?)",
                (SQLITE_MAX_INT, "ceiling"),
            )
            conn.commit()
        listing = self.cli_json("task-list", str(SQLITE_MAX_INT))
        self.assertEqual(listing, [])
        listing = self.cli_json(
            "task-list", FIVE_THOUSAND_ZEROS + str(SQLITE_MAX_INT)
        )
        self.assertEqual(listing, [])
        created = self.cli_json(
            "task-create", str(SQLITE_MAX_INT), "ceiling task"
        )
        self.assertEqual(created["project_id"], SQLITE_MAX_INT)
        self.assertEqual(created["status"], "todo")
        listing = self.cli_json("task-list", str(SQLITE_MAX_INT))
        self.assertEqual([t["title"] for t in listing], ["ceiling task"])

    def test_max_value_existing_task_can_move(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO tasks (id, project_id, title, status) "
                "VALUES (?, ?, ?, 'todo')",
                (SQLITE_MAX_INT, self.project_alpha, "ceiling task"),
            )
            conn.commit()
        moved = self.cli_json(
            "task-move", FIVE_THOUSAND_ZEROS + str(SQLITE_MAX_INT), "done"
        )
        self.assertEqual(moved["id"], SQLITE_MAX_INT)
        self.assertEqual(moved["project_id"], self.project_alpha)
        self.assertEqual(moved["title"], "ceiling task")
        self.assertEqual(moved["status"], "done")
        self.assertIsInstance(moved["id"], int)
        [ceiling] = [
            t for t in self.cli_json("task-list", "1")
            if t["id"] == SQLITE_MAX_INT
        ]
        self.assertEqual(
            ceiling,
            {"id": SQLITE_MAX_INT, "project_id": 1,
             "title": "ceiling task", "status": "done"},
        )

    # ---------- 零、负数、非数字、空串、缺失参数 ----------

    def test_zero_negative_nonnumeric_empty_rejected_at_all_entries(self):
        bad_inputs = ["0", "-1", "abc", "1.5", " 1", "1 ", ""]
        for bad in bad_inputs:
            self.assert_rejected(("task-list", bad))
            self.assert_rejected(("task-list", bad, "--status", "todo"))
            self.assert_rejected(("task-create", bad, "title"))
            self.assert_rejected(("task-move", bad, "doing"))

    def test_missing_arguments_rejected_with_code_2(self):
        # 缺失标识或后续参数：argparse 同样走退出码 2、stdout 为空
        for args in (
            ("task-list",),
            ("task-create",),
            ("task-create", "1"),
            ("task-move",),
            ("task-move", "1"),
        ):
            proc = self.run_cli(*args)
            detail = self._detail(args, proc)
            self.assertEqual(proc.returncode, 2, detail)
            self.assertEqual(proc.stdout, "", detail)
            self.assertNotEqual(proc.stderr.strip(), "", detail)
            self.assertNotIn("Traceback", proc.stderr, detail)
        self.assertEqual(
            self.cli_json("task-list", "1"),
            [
                {"id": 1, "project_id": 1, "title": "first task",
                 "status": "todo"},
                {"id": 2, "project_id": 1, "title": "second task",
                 "status": "todo"},
            ],
        )

    def test_in_range_nonexistent_small_ids_still_reported_missing(self):
        # 范围内但不存在：说明对应实体不存在，而非越界
        self.assert_not_found_error("task-list", "999")
        self.assert_not_found_error(
            "task-create", "999", "title"
        )
        self.assert_not_found_error("task-move", "999", "doing")

    # ---------- 存储失败协议（退出码 1）保持不变 ----------

    def test_unopenable_database_still_exit_code_1(self):
        proc = self.run_cli(
            "task-list", "1", db_path=self._tmpdir.name
        )
        detail = self._detail(("task-list", "1"), proc)
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)

    # ---------- 既有成功语义不回退 ----------

    def test_success_output_shape_and_sorting_unchanged(self):
        data = self.cli_json("task-list", "1")
        self.assertEqual(
            data,
            [
                {"id": 1, "project_id": 1, "title": "first task",
                 "status": "todo"},
                {"id": 2, "project_id": 1, "title": "second task",
                 "status": "todo"},
            ],
        )
        for obj in data:
            self.assertEqual(set(obj.keys()), TASK_FIELDS)
        self.cli_json("task-move", "2", "done")
        data = self.cli_json("task-list", "1", "--status", "done")
        self.assertEqual([t["id"] for t in data], [2])
        data = self.cli_json("task-list", "1", "--query", "second")
        self.assertEqual([t["id"] for t in data], [2])
        self.assertEqual(self.cli_json("task-list", "2"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

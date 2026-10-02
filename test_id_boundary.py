#!/usr/bin/env python3
"""标识输入边界的命令行回归测试：task-create / task-list 的项目标识与
task-move 的任务标识。

覆盖 SQLite 有符号 64 位整数上限（9223372036854775807）相关行为：

- 超上限值（9223372036854775808、五千个连续的 9，及其带前导零的同值写法）
  在三处标识参数上均被拒绝：退出码 2、标准输出为空、标准错误说明标识超出
  支持范围，且不出现 Python 异常回溯。
- 上限值本身不因为位数多被当作越界：按对应项目/任务是否存在返回结果
  （全新库中三处均返回“不存在”）。
- 前导零继续允许并按数值判断：普通标识与带前导零（含五千个零前缀）的
  同值标识产生相同结果。
- 零、负数、非数字、缺失参数、范围内但不存在的标识：退出码 2 且数据不变。
- 失败请求不新增任务，也不改变已有任务的标题、状态、所属项目或标识；
  特别验证：task-move 9223372036854775808 doing 被拒绝后，
  task-list 1 中原任务仍为 todo。
- 数据库无法打开时仍沿用退出码 1 的存储失败协议。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create / task-move 三个现有命令，不直接读写数据库，
不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时文件），
可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_id_boundary.py                  # 运行全部回归用例
    python3 -m unittest test_id_boundary -v      # 等价写法
    python3 test_id_boundary.py \\
        IdBoundaryRegression.test_leading_zeros_are_equivalent  # 只跑单个用例

退出码：全部通过为 0，存在失败为 1。失败信息会给出具体命令行输入、退出码、
标准输出、标准错误与预期结果，便于定位差异。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

SQLITE_MAX_INT = "9223372036854775807"      # 2^63 - 1：上限值本身合法
OVER_MAX = "9223372036854775808"            # 上限 + 1：越界
FIVE_THOUSAND_NINES = "9" * 5000            # 超长越界值（超过 4300 位转换限制）
OVER_MAX_WITH_ZEROS = "0" * 5000 + OVER_MAX        # 前导五千个零，数值仍越界
NINES_WITH_ZEROS = "0" * 5000 + FIVE_THOUSAND_NINES  # 一万字符，数值仍越界
MAX_WITH_ZEROS = "0" * 5000 + SQLITE_MAX_INT   # 前导五千个零，数值恰为上限
FIVE_THOUSAND_ZEROS_THEN_ONE = "0" * 5000 + "1"  # 数值为 1，前导五千个零

TASK_FIELDS = {"id", "project_id", "title", "status"}


class IdBoundaryRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-id-boundary-")
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
                "--db", db_path or self.db_path,
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
        return (
            f"输入参数={list(cli_args)!r}\n"
            f"退出码={proc.returncode}\n"
            f"标准输出={proc.stdout!r}\n"
            f"标准错误={proc.stderr!r}"
        )

    def _snapshot_tasks(self):
        """记录各项目全部任务（完整对象），用于校验失败请求不产生任何副作用。"""
        snapshot = {}
        for pid in (self.project_alpha, self.project_beta):
            snapshot[pid] = self.cli_json("task-list", str(pid))
        return snapshot

    def assert_rejected(self, cli_args, before=None):
        """断言请求被拒绝：退出码 2、stdout 为空、stderr 有说明且无回溯，
        并且失败前后全部任务（标题/状态/所属项目/标识）完全不变。"""
        if before is None:
            before = self._snapshot_tasks()
        proc = self.run_cli(*cli_args)
        after = self._snapshot_tasks()
        detail = self._detail(cli_args, proc)

        self.assertEqual(proc.returncode, 2, f"预期退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"被拒绝时标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"被拒绝时标准错误应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr,
                         f"不得出现 Python 异常回溯：\n{detail}")
        self.assertEqual(after, before, f"被拒绝的请求不得改动任何任务：\n{detail}")
        return proc

    def assert_out_of_range(self, cli_args, before=None):
        """越界标识：在通用拒绝断言之上，要求 stderr 明确说明超出支持范围。"""
        proc = self.assert_rejected(cli_args, before=before)
        self.assertIn(
            "range", proc.stderr.lower(),
            f"标准错误应说明标识超出支持范围：\n{self._detail(cli_args, proc)}",
        )

    def assert_not_exists(self, cli_args, before=None):
        """范围内但不存在：在通用拒绝断言之上，要求 stderr 说明不存在。"""
        proc = self.assert_rejected(cli_args, before=before)
        self.assertIn(
            "does not exist", proc.stderr,
            f"标准错误应说明项目或任务不存在：\n{self._detail(cli_args, proc)}",
        )

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目一内含一条 todo 任务；项目二存在但为空，便于校验跨项目无副作用
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        task = self.cli_json(
            "task-create", str(self.project_alpha), "original task"
        )
        self.task_id = task["id"]
        self.assertEqual(task["status"], "todo")
        self.project_beta = self.cli_json("project-create", "beta")["id"]

    # ---------- 越界值：三处标识参数统一退出码 2、无回溯、数据不变 ----------

    def test_out_of_range_ids_rejected_for_all_three_identifiers(self):
        before = self._snapshot_tasks()
        cases = [
            # task-create / task-list 的项目标识
            ("task-create", OVER_MAX, "should not be created"),
            ("task-list", OVER_MAX),
            # task-move 的任务标识（含合法目标状态 doing，仍应先因标识被拒绝）
            ("task-move", OVER_MAX, "doing"),
            # 五千个连续的 9
            ("task-create", FIVE_THOUSAND_NINES, "should not be created"),
            ("task-list", FIVE_THOUSAND_NINES),
            ("task-move", FIVE_THOUSAND_NINES, "doing"),
            # 上限 + 1 前加五千个零：按数值仍越界，不按字符串长度判断
            ("task-create", OVER_MAX_WITH_ZEROS, "should not be created"),
            ("task-list", OVER_MAX_WITH_ZEROS),
            ("task-move", OVER_MAX_WITH_ZEROS, "doing"),
            # 五千个 9 前再加五千个零
            ("task-create", NINES_WITH_ZEROS, "should not be created"),
            ("task-list", NINES_WITH_ZEROS),
            ("task-move", NINES_WITH_ZEROS, "doing"),
        ]
        for cli_args in cases:
            with self.subTest(cli_args=_redact(cli_args)):
                self.assert_out_of_range(list(cli_args), before=before)

        # 全部被拒绝的创建请求都不得新增任务
        after = self.cli_json("task-list", str(self.project_alpha))
        self.assertEqual(len(after), len(before[self.project_alpha]))

    def test_move_overflow_leaves_original_task_todo(self):
        # 用户指定的端到端场景：全新库中建好项目与任务后，以
        # 9223372036854775808 为任务标识移动到 doing 必须被拒绝，
        # 随后 task-list 1 仍返回原任务且状态为 todo。
        proc = self.run_cli("task-move", OVER_MAX, "doing")
        detail = self._detail(("task-move", OVER_MAX, "doing"), proc)
        self.assertEqual(proc.returncode, 2, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertNotIn("Traceback", proc.stderr, detail)

        tasks = self.cli_json("task-list", "1")
        self.assertEqual(len(tasks), 1, detail)
        self.assertEqual(
            tasks[0],
            {
                "id": self.task_id,
                "project_id": self.project_alpha,
                "title": "original task",
                "status": "todo",
            },
            detail,
        )

    # ---------- 上限值本身：按存在性返回，不因位数多被当作越界 ----------

    def test_max_int_is_checked_by_existence_not_rejected_as_overflow(self):
        before = self._snapshot_tasks()
        for raw in (SQLITE_MAX_INT, MAX_WITH_ZEROS):
            with self.subTest(raw=_redact((raw,))):
                # 全新库中该标识不可能存在：三处均为“不存在”，而非“越界”
                self.assert_not_exists(
                    ["task-create", raw, "should not be created"], before=before
                )
                self.assert_not_exists(["task-list", raw], before=before)
                self.assert_not_exists(
                    ["task-move", raw, "doing"], before=before
                )

    # ---------- 前导零：与同值普通标识等价（含五千个零前缀） ----------

    def test_leading_zeros_are_equivalent_on_all_identifiers(self):
        # task-list：0001 与前导五千个零的 1 都等价于 1
        plain = self.cli_json("task-list", "1")
        self.assertEqual(self.cli_json("task-list", "0001"), plain)
        self.assertEqual(
            self.cli_json("task-list", FIVE_THOUSAND_ZEROS_THEN_ONE), plain
        )

        # task-create：以 0001 为项目标识在项目一下创建任务成功
        created = self.cli_json("task-create", "0001", "created via zeros")
        self.assertEqual(created["project_id"], self.project_alpha)
        self.assertEqual(created["status"], "todo")
        new_task_id = created["id"]

        # task-move：带前导零的任务标识移动成功；五千个零前缀同样成功
        moved = self.cli_json(
            "task-move", f"000{new_task_id}", "doing"
        )
        self.assertEqual(moved["id"], new_task_id)
        self.assertEqual(moved["status"], "doing")
        moved_back = self.cli_json(
            "task-move", "0" * 5000 + str(new_task_id), "todo"
        )
        self.assertEqual(moved_back["status"], "todo")

        # 最终列表按标识升序，两条任务字段完整、归属项目一
        tasks = self.cli_json("task-list", "1")
        self.assertEqual(
            [(t["id"], t["title"], t["status"]) for t in tasks],
            [(self.task_id, "original task", "todo"),
             (new_task_id, "created via zeros", "todo")],
        )
        for obj in tasks:
            self.assertEqual(set(obj.keys()), TASK_FIELDS)

    def test_all_zero_digits_is_rejected_as_non_positive(self):
        # 五千个零数值为 0：属于非正整数，而不是越界
        before = self._snapshot_tasks()
        self.assert_rejected(
            ["task-create", "0" * 5000, "x"], before=before
        )
        self.assert_rejected(["task-list", "0" * 5000], before=before)
        self.assert_rejected(
            ["task-move", "0" * 5000, "doing"], before=before
        )

    # ---------- 零、负数、非数字：继续退出码 2 ----------

    def test_zero_negative_and_non_numeric_identifiers_rejected(self):
        before = self._snapshot_tasks()
        invalid = ["0", "0000", "-1", "-0", "abc", "1.5", "1e3", "+1",
                   " 1", "1 ", "", "１"]
        for raw in invalid:
            with self.subTest(raw=raw):
                self.assert_rejected(
                    ["task-create", raw, "should not be created"], before=before
                )
                self.assert_rejected(["task-list", raw], before=before)
                self.assert_rejected(
                    ["task-move", raw, "doing"], before=before
                )

    # ---------- 缺失参数：argparse 继续退出码 2、stdout 为空 ----------

    def test_missing_arguments_keep_exit_code_2(self):
        before = self._snapshot_tasks()
        for cli_args in (
            ["task-create"],
            ["task-create", "1"],
            ["task-list"],
            ["task-move"],
            ["task-move", str(self.task_id)],
        ):
            with self.subTest(cli_args=cli_args):
                proc = self.run_cli(*cli_args)
                after = self._snapshot_tasks()
                detail = self._detail(cli_args, proc)
                self.assertEqual(proc.returncode, 2, detail)
                self.assertEqual(proc.stdout, "", detail)
                self.assertNotEqual(proc.stderr.strip(), "", detail)
                self.assertNotIn("Traceback", proc.stderr, detail)
                self.assertEqual(after, before, detail)

    # ---------- 范围内但不存在：退出码 2 并说明不存在 ----------

    def test_in_range_but_nonexistent_identifiers_rejected(self):
        before = self._snapshot_tasks()
        self.assert_not_exists(
            ["task-create", "999", "should not be created"], before=before
        )
        self.assert_not_exists(["task-list", "999"], before=before)
        self.assert_not_exists(
            ["task-move", "999", "doing"], before=before
        )

    # ---------- 正常路径与默认行为保持原样 ----------

    def test_valid_move_and_same_status_move_unchanged(self):
        # 同状态移动仍成功且数据不变
        same = self.cli_json("task-move", str(self.task_id), "todo")
        self.assertEqual(
            same,
            {"id": self.task_id, "project_id": self.project_alpha,
             "title": "original task", "status": "todo"},
        )
        # 普通移动到 doing 成功，列表中状态随之改变
        moved = self.cli_json("task-move", str(self.task_id), "doing")
        self.assertEqual(moved["status"], "doing")
        tasks = self.cli_json("task-list", str(self.project_alpha))
        self.assertEqual(tasks[0]["status"], "doing")

    # ---------- 存储失败协议保持退出码 1 ----------

    def test_unopenable_database_keeps_storage_failure_protocol(self):
        # 以一个已存在的目录作为数据库文件：SQLite 无法打开，应退出码 1，
        # 标准输出为空且无 Python 异常回溯
        dir_path = os.path.join(self._tmpdir.name, "cannot_be_a_db")
        os.mkdir(dir_path)
        proc = self.run_cli("project-create", "x", db_path=dir_path)
        detail = self._detail(("project-create", "x"), proc)
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)


def _redact(cli_args):
    """失败信息中只展示超长参数的长度，避免五千字符串刷屏。"""
    return tuple(
        f"<{len(a)} chars>" if isinstance(a, str) and len(a) > 40 else a
        for a in cli_args
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)

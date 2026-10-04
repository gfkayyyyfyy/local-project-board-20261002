#!/usr/bin/env python3
"""task-move --from 预期当前状态的命令行回归（验收）测试。

覆盖：

- 用户验收场景：两个同标题任务，任务 1（项目 1）为 doing、任务 2 为 todo；
  ``task-move 1 done --from doing`` 成功（退出码 0、stderr 为空、stdout 为
  与 task-show 同结构的单个任务 JSON，状态 done、其余字段原样），再次执行
  同一命令退出码 2、stdout 为空、stderr 说明当前状态 done 与预期 doing；
  任务 1 仍为 done、任务 2 不变。
- 即使目标状态与当前状态相同，来源状态不匹配也拒绝；当前状态与预期相符且
  目标与当前相同则成功返回原任务、不新增记录。
- 来源状态合法但与当前值不同时，不创建任务、不改变目标状态，后续
  task-show / task-list / project-stats 仍反映实际保存的状态。
- ``--from`` 与目标状态都只接受三种状态的原样拼写：大小写变化、首尾空白、
  空字符串、未知值均退出码 2、stdout 为空、stderr 说明非法状态。
- ``--from`` 缺值、必需位置参数缺失、任务标识无效/越界/不存在均退出码 2；
  标识允许前导零（``0001`` 与 ``1`` 等价）。
- 省略 ``--from`` 时保留三种合法状态之间可直接转换的现有规则。
- 数据库无法打开时退出码 1、stdout 为空、stderr 说明存储失败。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，不直接读写
数据库，不调用产品内部函数，不依赖网络或任何预先保存的项目。每个用例在
独立临时目录中使用全新的隔离 SQLite 数据库，结束后自动清理。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_move_from.py
    python3 -m unittest test_task_move_from -v
    python3 test_task_move_from.py \
        TaskMoveFromRegression.test_acceptance_match_then_repeat_conflicts

退出码：全部通过为 0，存在失败为 1。不依赖 JSON 键顺序或错误文案逐字一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

STATUSES = ("todo", "doing", "done")
TASK_FIELDS = {"id", "project_id", "title", "status"}
SHARED_TITLE = "Sync design doc"


class TaskMoveFromRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-from-regtest-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")
        self.project = self.cli_json("project-create", "alpha")["id"]
        # 任务 1 与任务 2 同标题；任务 1 随后置为 doing，任务 2 保持 todo
        self.task1 = self.cli_json(
            "task-create", str(self.project), SHARED_TITLE
        )["id"]
        self.task2 = self.cli_json(
            "task-create", str(self.project), SHARED_TITLE
        )["id"]
        self.assertEqual(self.task1, 1)
        self.assertEqual(self.task2, 2)
        proc = self.run_cli("task-move", str(self.task1), "doing")
        self.assertEqual(proc.returncode, 0, self.detail(
            ("task-move", "1", "doing"), proc, expected="准备任务 1 为 doing"))

    def tearDown(self):
        self._tmpdir.cleanup()

    # ---------- 命令行与解析辅助 ----------

    def run_cli(self, *cli_args, db_path=None):
        return subprocess.run(
            [sys.executable, "-m", "kanban",
             "--db", db_path or self.db_path, *cli_args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def cli_json(self, *cli_args):
        proc = self.run_cli(*cli_args)
        detail = self.detail(cli_args, proc, expected="准备数据成功")
        self.assertEqual(proc.returncode, 0, f"准备数据应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"准备数据时 stderr 应为空：\n{detail}")
        return json.loads(proc.stdout)

    def detail(self, cli_args, proc, expected=None):
        return "\n".join([
            f"输入参数={list(cli_args)!r}",
            f"实际退出码={proc.returncode}",
            f"实际标准输出={proc.stdout!r}",
            f"实际标准错误={proc.stderr!r}",
            f"预期结果={expected!r}",
        ])

    def show(self, task_id):
        proc = self.run_cli("task-show", str(task_id))
        self.assertEqual(proc.returncode, 0, self.detail(
            ("task-show", str(task_id)), proc, expected="task-show 成功"))
        return json.loads(proc.stdout)

    def stats(self):
        proc = self.run_cli("project-stats", str(self.project))
        self.assertEqual(proc.returncode, 0, self.detail(
            ("project-stats", str(self.project)), proc, expected="stats 成功"))
        return json.loads(proc.stdout)

    def task_obj(self, task_id, status):
        return {"id": task_id, "project_id": self.project,
                "title": SHARED_TITLE, "status": status}

    def assert_rejected(self, proc, cli_args):
        """来源不匹配/非法参数的统一拒绝协议：rc2、stdout 空、stderr 非空无回溯。"""
        detail = self.detail(cli_args, proc, expected="退出码 2、stdout 为空")
        self.assertEqual(proc.returncode, 2, f"应退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"失败时 stdout 应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "", f"stderr 应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr, f"不应出现回溯：\n{detail}")
        return detail

    # ---------- 用户验收场景 ----------

    def test_acceptance_match_then_repeat_conflicts(self):
        # 第一次：任务 1 当前为 doing，与 --from doing 完全相同，移到 done
        cli_args = ("task-move", "1", "done", "--from", "doing")
        proc = self.run_cli(*cli_args)
        detail = self.detail(
            cli_args, proc,
            expected="退出码 0、stderr 空、stdout 为状态 done 的任务 1 对象",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        moved = json.loads(proc.stdout)
        self.assertEqual(moved, self.task_obj(1, "done"), detail)
        self.assertEqual(set(moved), TASK_FIELDS, detail)

        # 再次执行同一命令：当前已是 done，与预期 doing 不符，拒绝
        proc2 = self.run_cli(*cli_args)
        detail2 = self.assert_rejected(proc2, cli_args)
        low = proc2.stderr.lower()
        self.assertIn("done", low, f"stderr 应说明当前状态为 done：\n{detail2}")
        self.assertIn("doing", low, f"stderr 应说明预期状态为 doing：\n{detail2}")

        # 任务 1 仍为 done，任务 2 不变（仍 todo），且没有新增记录
        self.assertEqual(self.show(1), self.task_obj(1, "done"))
        self.assertEqual(self.show(2), self.task_obj(2, "todo"))
        self.assertEqual(
            self.stats(),
            {"project_id": self.project, "total": 2,
             "todo": 1, "doing": 0, "done": 1},
        )

    def test_leading_zero_id_equivalent_with_from(self):
        # 0001 与 1 等价：同样先成功、重复时被拒绝
        proc = self.run_cli("task-move", "0001", "done", "--from", "doing")
        detail = self.detail(
            ("task-move", "0001", "done", "--from", "doing"), proc,
            expected="0001 按数值 1 处理，移动成功",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(json.loads(proc.stdout), self.task_obj(1, "done"))
        proc2 = self.run_cli("task-move", "0001", "done", "--from", "doing")
        self.assert_rejected(proc2, ("task-move", "0001", "done", "--from", "doing"))
        self.assertEqual(self.show(1), self.task_obj(1, "done"))

    def test_same_target_still_rejected_when_from_mismatches(self):
        # 任务 2 当前 todo；目标也是 todo，但 --from doing 不匹配：仍拒绝
        cli_args = ("task-move", "2", "todo", "--from", "doing")
        proc = self.run_cli(*cli_args)
        detail = self.assert_rejected(proc, cli_args)
        low = proc.stderr.lower()
        self.assertIn("todo", low, f"stderr 应说明当前状态为 todo：\n{detail}")
        self.assertIn("doing", low, f"stderr 应说明预期状态为 doing：\n{detail}")
        self.assertEqual(self.show(2), self.task_obj(2, "todo"))
        self.assertEqual(
            self.stats(),
            {"project_id": self.project, "total": 2,
             "todo": 1, "doing": 1, "done": 0},
        )

    def test_from_matches_and_target_equals_current_is_noop_success(self):
        # 当前与预期均为 todo，目标也为 todo：成功返回原任务，不新增记录
        cli_args = ("task-move", "2", "todo", "--from", "todo")
        proc = self.run_cli(*cli_args)
        detail = self.detail(
            cli_args, proc,
            expected="来源相符且目标等于当前：退出码 0、返回原任务、不新增记录",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(json.loads(proc.stdout), self.task_obj(2, "todo"))
        self.assertEqual(
            self.stats(),
            {"project_id": self.project, "total": 2,
             "todo": 1, "doing": 1, "done": 0},
        )

    def test_legal_from_but_different_value_changes_nothing(self):
        # 来源合法（todo）但任务 1 当前为 doing：拒绝，不创建任务、不改状态
        before1, before2 = self.show(1), self.show(2)
        cli_args = ("task-move", "1", "done", "--from", "todo")
        proc = self.run_cli(*cli_args)
        self.assert_rejected(proc, cli_args)
        self.assertEqual(self.show(1), before1)
        self.assertEqual(self.show(2), before2)
        self.assertEqual(
            self.stats(),
            {"project_id": self.project, "total": 2,
             "todo": 1, "doing": 1, "done": 0},
        )

    # ---------- --from 与目标状态的原样拼写校验 ----------

    def assert_usage_failure_leaves_data(self, cli_args, db_path=None):
        proc = self.run_cli(*cli_args, db_path=db_path)
        detail = self.assert_rejected(proc, cli_args)
        self.assertIn("status", proc.stderr.lower(),
                      f"stderr 应说明状态非法：\n{detail}")
        if db_path is None:
            # 拒绝前后两个任务与统计完全一致
            self.assertEqual(self.show(1), self.task_obj(1, "doing"))
            self.assertEqual(self.show(2), self.task_obj(2, "todo"))
            self.assertEqual(
                self.stats(),
                {"project_id": self.project, "total": 2,
                 "todo": 1, "doing": 1, "done": 0},
            )

    def test_invalid_from_values(self):
        for bad in ("DONE", " doing ", "", "blocked", "Todo", "done\n"):
            with self.subTest(bad=bad):
                self.assert_usage_failure_leaves_data(
                    ("task-move", "1", "done", "--from", bad))

    def test_invalid_target_values_with_from(self):
        for bad in ("DONE", " done ", "", "blocked"):
            with self.subTest(bad=bad):
                self.assert_usage_failure_leaves_data(
                    ("task-move", "1", bad, "--from", "doing"))

    def test_from_missing_value(self):
        proc = self.run_cli("task-move", "1", "done", "--from")
        self.assert_rejected(proc, ("task-move", "1", "done", "--from"))

    def test_missing_required_arguments(self):
        for cli_args in (("task-move",), ("task-move", "1"),
                         ("task-move", "1", "--from", "doing")):
            with self.subTest(cli_args=cli_args):
                proc = self.run_cli(*cli_args)
                self.assert_rejected(proc, cli_args)

    def test_invalid_or_missing_task_id_with_from(self):
        for cli_args in (
            ("task-move", "abc", "done", "--from", "doing"),
            ("task-move", "0", "done", "--from", "doing"),
            ("task-move", "-3", "done", "--from", "doing"),
            ("task-move", "999", "done", "--from", "done"),
        ):
            with self.subTest(cli_args=cli_args):
                proc = self.run_cli(*cli_args)
                self.assert_rejected(proc, cli_args)
        self.assertEqual(self.show(1), self.task_obj(1, "doing"))
        self.assertEqual(self.show(2), self.task_obj(2, "todo"))

    # ---------- 省略 --from 时保留现有规则 ----------

    def test_without_from_free_transitions_unchanged(self):
        # doing -> done、done -> todo 直接转换，同状态移动也算成功
        proc = self.run_cli("task-move", "1", "done")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), self.task_obj(1, "done"))
        proc = self.run_cli("task-move", "1", "todo")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), self.task_obj(1, "todo"))
        proc = self.run_cli("task-move", "2", "todo")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(json.loads(proc.stdout), self.task_obj(2, "todo"))

    # ---------- 存储失败 ----------

    def test_storage_failure_exit_code_one(self):
        proc = self.run_cli(
            "task-move", "1", "done", "--from", "doing",
            db_path=os.path.join(self._tmpdir.name, "missing_dir", "x.db"),
        )
        cli_args = ("task-move", "1", "done", "--from", "doing")
        detail = self.detail(cli_args, proc,
                             expected="退出码 1、stdout 空、stderr 说明存储失败")
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)

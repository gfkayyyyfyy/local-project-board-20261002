#!/usr/bin/env python3
"""task-move 状态转换的命令行回归（验收）测试。

覆盖 todo / doing / done 三种初始状态到三种目标状态的全部九种组合
（含 done -> todo、done -> doing 以及三种同状态移动），以及非法目标状态
（blocked、大写 DONE、首尾带空格的 " done "、空字符串）的失败路径。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品：用
project-create / task-create 取得真实标识，用 task-move 把目标任务
准备到初始状态，再用 task-list 查询验证结果已落库。不直接读写数据库，
不调用产品内部函数，不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用
者自己的数据库。

夹具（每个用例独立构建）：

- 项目一 alpha：目标任务 T 与一条同标题的同项目任务 S；
- 项目二 beta：一条与目标任务同标题的任务 O。

移动 T 时，只有标识为 T 的任务允许改变状态；S、O 的完整对象以及两个项目
任务列表的顺序都必须保持不变。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_move.py                  # 运行全部回归用例
    python3 -m unittest test_task_move -v      # 等价写法
    python3 test_task_move.py \
        TaskMoveRegression.test_move_todo_to_done   # 只跑单个用例

退出码：全部通过为 0，存在失败为 1。失败信息会给出输入参数、初始与目标
状态、实际退出码、标准输出、标准错误以及预期结果，不依赖 JSON 键顺序或
错误文案逐字一致。
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

# 目标任务与两条对照任务共用的标题，用于确认同标题任务不会被连带改动
SHARED_TITLE = "Sync design doc"


class TaskMoveRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-move-regtest-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")
        self._build_fixture()

    def tearDown(self):
        self._tmpdir.cleanup()

    # ---------- 命令行与结果解析辅助 ----------

    def run_cli(self, *cli_args):
        """执行 python -m kanban --db <临时库> ...，返回 CompletedProcess。"""
        return subprocess.run(
            [sys.executable, "-m", "kanban", "--db", self.db_path, *cli_args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def cli_json(self, *cli_args):
        """准备数据用：要求成功并返回解析后的单个 JSON 值。"""
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, f"准备数据应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"准备数据时标准错误应为空：\n{detail}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"准备数据的 stdout 不是合法 JSON：\n{detail}")

    def _detail(self, cli_args, proc, initial=None, target=None, expected=None):
        """组装失败定位信息：输入参数、初始/目标状态、退出码与输出、预期。"""
        lines = [
            f"输入参数={list(cli_args)!r}",
            f"初始状态={initial!r}，目标状态={target!r}",
            f"实际退出码={proc.returncode}",
            f"实际标准输出={proc.stdout!r}",
            f"实际标准错误={proc.stderr!r}",
            f"预期结果={expected!r}",
        ]
        return "\n".join(lines)

    def list_project(self, project_id):
        """通过公开命令查询某项目的全部任务，返回任务对象列表。"""
        proc = self.run_cli("task-list", str(project_id))
        detail = self._detail(
            ("task-list", str(project_id)), proc,
            expected="退出码 0、stderr 为空、stdout 为 JSON 数组",
        )
        self.assertEqual(proc.returncode, 0, f"查询应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"查询时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"查询的 stdout 不是合法 JSON：\n{detail}")
        self.assertIsInstance(data, list, f"查询结果应为 JSON 数组：\n{detail}")
        return data

    def snapshot(self):
        """记录两个项目的当前任务列表（含顺序与完整对象），用于前后比对。"""
        return {
            self.project_alpha: self.list_project(self.project_alpha),
            self.project_beta: self.list_project(self.project_beta),
        }

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目一：先建目标任务 T，再建同标题任务 S（列表顺序即 T、S）
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        target = self.cli_json(
            "task-create", str(self.project_alpha), SHARED_TITLE
        )
        sibling = self.cli_json(
            "task-create", str(self.project_alpha), SHARED_TITLE
        )
        self.target_id = target["id"]
        self.sibling_id = sibling["id"]

        # 项目二：另一条同标题任务 O，验证跨项目隔离
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        other = self.cli_json(
            "task-create", str(self.project_beta), SHARED_TITLE
        )
        self.other_id = other["id"]

    def prepare_target_status(self, initial):
        """通过公开 task-move 命令把目标任务准备到指定初始状态。"""
        if initial == "todo":
            return  # task-create 产生的新任务即为 todo
        proc = self.run_cli("task-move", str(self.target_id), initial)
        detail = self._detail(
            ("task-move", str(self.target_id), initial), proc,
            initial="todo", target=initial,
            expected="准备初始状态成功：退出码 0、stderr 为空",
        )
        self.assertEqual(proc.returncode, 0, f"准备初始状态应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"准备初始状态时 stderr 应为空：\n{detail}")

    def expected_target_object(self, status):
        return {
            "id": self.target_id,
            "project_id": self.project_alpha,
            "title": SHARED_TITLE,
            "status": status,
        }

    # ---------- 九种初始状态 × 目标状态组合 ----------

    def _check_move(self, initial, target):
        self.prepare_target_status(initial)
        before = self.snapshot()

        cli_args = ("task-move", str(self.target_id), target)
        proc = self.run_cli(*cli_args)
        expected_obj = self.expected_target_object(target)
        detail = self._detail(
            cli_args, proc, initial=initial, target=target,
            expected=(
                "退出码 0、stderr 为空、stdout 只有一个 JSON 任务对象，"
                f"对象为 {expected_obj!r}（字段集合 {sorted(TASK_FIELDS)}），"
                "且再次查询得到相同对象、其他任务与列表顺序不变"
            ),
        )

        # 成功移动：退出码 0、标准错误为空
        self.assertEqual(proc.returncode, 0, f"移动应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功移动时标准错误应为空：\n{detail}")

        # 标准输出只有一个 JSON 任务对象，字段仍为 id/project_id/title/status
        try:
            moved = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"移动结果的 stdout 不是合法 JSON：\n{detail}")
        self.assertIsInstance(
            moved, dict, f"移动结果应为单个 JSON 对象而非数组：\n{detail}"
        )
        self.assertEqual(
            set(moved.keys()), TASK_FIELDS,
            f"移动结果对象字段结构不符：\n{detail}",
        )
        # 保留原标识、项目和标题，状态等于指定目标
        self.assertEqual(moved, expected_obj, f"移动结果对象内容不符：\n{detail}")

        # 以另一次命令行调用查询同一数据库：目标对象应与移动结果一致
        after = self.snapshot()
        alpha_after = after[self.project_alpha]
        persisted = [obj for obj in alpha_after if obj["id"] == self.target_id]
        self.assertEqual(
            len(persisted), 1,
            f"查询结果中目标任务应恰好出现一次：\n{detail}\n"
            f"移动后项目一列表={alpha_after!r}",
        )
        self.assertEqual(
            persisted[0], moved,
            f"再次查询到的目标任务对象应与移动结果完全一致（结果已保存）：\n"
            f"{detail}\n查询所得={persisted[0]!r}",
        )

        # 只有指定标识的任务可能改变：构造项目一的预期列表（仅替换目标对象），
        # 同项目同标题的对照任务完整对象不变
        expected_alpha = [
            expected_obj if obj["id"] == self.target_id else obj
            for obj in before[self.project_alpha]
        ]
        self.assertEqual(
            alpha_after, expected_alpha,
            f"项目一中除目标任务外的其他任务不得改变：\n{detail}\n"
            f"移动前={before[self.project_alpha]!r}",
        )
        # 列表顺序（按标识）保持不变
        self.assertEqual(
            [obj["id"] for obj in alpha_after],
            [obj["id"] for obj in before[self.project_alpha]],
            f"移动后项目一任务列表顺序不得改变：\n{detail}",
        )
        # 另一项目的同标题任务完整对象与列表保持不变
        self.assertEqual(
            after[self.project_beta], before[self.project_beta],
            f"另一项目的任务不得受移动影响：\n{detail}\n"
            f"移动前={before[self.project_beta]!r}",
        )

        if initial == target:
            # 同状态移动：任务数量和所有字段都不变
            self.assertEqual(
                after, before,
                f"同状态移动后两个项目的任务数量与所有字段都应不变：\n{detail}",
            )

    # ---------- 非法目标状态 ----------

    def _check_invalid_status(self, bad_status, label):
        # 把目标任务置于 doing，制造“若错误地接受值就可能落库”的场景
        self.prepare_target_status("doing")
        before = self.snapshot()

        cli_args = ("task-move", str(self.target_id), bad_status)
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc, initial="doing", target=bad_status,
            expected=(
                f"非法状态 {label}：退出码 2、stdout 为空、stderr 说明状态非法"
                "且无 Python 异常回溯，两个项目的任务集合与失败前完全一致"
            ),
        )

        self.assertEqual(proc.returncode, 2, f"非法状态应返回退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"失败时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"失败时标准错误应说明状态非法：\n{detail}",
        )
        # 不依赖错误文案逐字一致，但错误说明应指向状态问题
        self.assertIn(
            "status", proc.stderr.lower(),
            f"标准错误应说明状态非法：\n{detail}",
        )
        self.assertNotIn(
            "Traceback", proc.stderr,
            f"标准错误不应包含 Python 异常回溯：\n{detail}",
        )

        # 失败后两个项目的任务集合（对象与顺序）与失败前完全一致
        after = self.snapshot()
        self.assertEqual(
            after, before,
            f"非法状态移动失败后数据不得发生任何变化：\n{detail}\n"
            f"失败前={before!r}",
        )

    def test_invalid_status_blocked(self):
        # 目标状态 blocked：不在 todo/doing/done 之内
        self._check_invalid_status("blocked", "blocked")

    def test_invalid_status_uppercase_done(self):
        # 目标状态 DONE：按原样拼写精确匹配，大写形式非法
        self._check_invalid_status("DONE", "DONE")

    def test_invalid_status_with_surrounding_spaces(self):
        # 目标状态 " done "：首尾带空格，不会被归一化为 done
        self._check_invalid_status(" done ", "' done '（首尾带空格）")

    def test_invalid_status_empty_string(self):
        # 目标状态为空字符串：非法
        self._check_invalid_status("", "''（空字符串）")


# 动态生成九种 initial × target 组合的独立用例，
# 使每个组合都能被 unittest 单独选中、单独重复运行。
for _initial in STATUSES:
    for _target in STATUSES:
        def _make_test(init_status, target_status):
            def test_move(self):
                self._check_move(init_status, target_status)
            test_move.__doc__ = (
                f"task-move：{init_status} -> {target_status}，"
                "移动结果与再次查询一致，其他任务与列表顺序不变"
                + ("（同状态移动，数量与所有字段不变）"
                   if init_status == target_status else "")
            )
            return test_move
        setattr(
            TaskMoveRegression,
            f"test_move_{_initial}_to_{_target}",
            _make_test(_initial, _target),
        )
del _initial, _target


if __name__ == "__main__":
    unittest.main(verbosity=2)

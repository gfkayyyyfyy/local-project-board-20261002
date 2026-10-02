#!/usr/bin/env python3
"""task-move 状态转换的命令行回归测试。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create / task-move 三个现有命令，不直接读写数据库，
不调用产品内部函数，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时文件），
可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用者自己的数据库。

覆盖范围：
  * todo / doing / done 三种初始状态到三种目标状态的全部九种组合
    （含 done 回到 todo / doing，以及三种同状态移动）；
  * 每次成功移动：退出码 0、标准错误为空、标准输出只有一个 JSON 任务对象，
    字段为 id / project_id / title / status，保留原标识、项目与标题，
    状态等于目标值；随后另起一次命令行查询证明结果已保存；
  * 同状态移动后任务数量与所有字段不变；
  * 同一项目及另一项目各有一条与目标任务同标题的任务，移动前后查询两项目，
    确认只有指定标识的任务状态可能改变，其他任务完整对象与列表顺序不变；
  * 非法目标状态（blocked、DONE、首尾带空格的 done、空字符串）：
    退出码 2、标准输出为空、标准错误说明状态非法且无 Python 异常回溯，
    失败前后两个项目的任务集合完全一致。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_move.py                  # 运行全部回归用例
    python3 -m unittest test_task_move -v      # 等价写法
    python3 test_task_move.py \\
        TaskMoveRegression.test_move_done_to_todo   # 只跑单个用例

退出码：全部通过为 0，存在失败为 1。失败信息会给出具体命令行输入、初始与目标
状态、实际退出码、标准输出、标准错误与预期结果，便于定位差异；断言不依赖
JSON 键顺序，也不依赖错误文案逐字一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

VALID_STATUSES = ("todo", "doing", "done")
TASK_FIELDS = {"id", "project_id", "title", "status"}

# 目标任务与同项目兄弟任务、另一项目任务共用的标题
SHARED_TITLE = "Move Me"


class TaskMoveRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-movetest-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")

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
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"stdout 不是合法 JSON：{detail}")

    @staticmethod
    def _detail(cli_args, proc, extra=""):
        return (
            f"输入参数={list(cli_args)!r}\n"
            f"{extra}"
            f"退出码={proc.returncode}\n"
            f"标准输出={proc.stdout!r}\n"
            f"标准错误={proc.stderr!r}"
        )

    # ---------- 通过公开命令准备夹具 ----------

    def _prepare(self, initial_status):
        """用公开命令搭建夹具并把目标任务准备到 initial_status。

        项目 alpha：目标任务与同标题兄弟任务各一条（另加一条不同标题任务，
        用于校验列表顺序）；项目 beta：一条同标题任务。返回目标任务的创建结果。
        """
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        self.project_beta = self.cli_json("project-create", "beta")["id"]

        target = self.cli_json(
            "task-create", str(self.project_alpha), SHARED_TITLE
        )
        self.target_id = target["id"]
        self.sibling = self.cli_json(
            "task-create", str(self.project_alpha), SHARED_TITLE
        )
        self.other_task = self.cli_json(
            "task-create", str(self.project_alpha), "Unrelated Task"
        )
        self.beta_task = self.cli_json(
            "task-create", str(self.project_beta), SHARED_TITLE
        )

        # 目标任务准备到指定初始状态（todo 为创建默认值，无需移动）
        if initial_status != "todo":
            self.cli_json("task-move", str(self.target_id), initial_status)
        # 兄弟任务与另一项目任务放到非默认状态，证明移动只影响指定标识
        self.cli_json("task-move", str(self.sibling["id"]), "doing")
        self.cli_json("task-move", str(self.beta_task["id"]), "done")
        return target

    def _list_project(self, project_id):
        """另起一次命令行调用查询项目任务列表（按标识升序）。"""
        data = self.cli_json("task-list", str(project_id))
        self.assertIsInstance(data, list)
        return data

    def _snapshot_both_projects(self):
        """记录两个项目当前完整任务列表（含顺序），用于前后比对。"""
        return {
            self.project_alpha: self._list_project(self.project_alpha),
            self.project_beta: self._list_project(self.project_beta),
        }

    # ---------- 成功移动的核心断言 ----------

    def _assert_move(self, initial_status, target_status):
        """把目标任务从 initial_status 移到 target_status 并全面验收。"""
        self._prepare(initial_status)
        before = self._snapshot_both_projects()

        cli_args = ("task-move", str(self.target_id), target_status)
        proc = self.run_cli(*cli_args)
        extra = f"初始状态={initial_status!r} 目标状态={target_status!r}\n"
        detail = self._detail(cli_args, proc, extra)

        # 退出码 0、标准错误为空
        self.assertEqual(proc.returncode, 0, f"预期成功退出码 0：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")

        # 标准输出只有一个 JSON 任务对象
        try:
            obj = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"标准输出不是合法 JSON（应只有一个任务对象）：\n{detail}")
        self.assertIsInstance(obj, dict, f"输出应为单个任务对象：\n{detail}")
        self.assertEqual(
            set(obj.keys()), TASK_FIELDS,
            f"任务对象字段应为 {sorted(TASK_FIELDS)}：\n{detail}",
        )

        # 对象保留原标识、项目和标题，状态等于指定目标
        expected = {
            "id": self.target_id,
            "project_id": self.project_alpha,
            "title": SHARED_TITLE,
            "status": target_status,
        }
        self.assertEqual(
            obj, expected,
            f"移动结果对象不符，预期 {expected!r}：\n{detail}",
        )

        # 另一次命令行调用查询同一数据库，结果应与移动返回值一致（已保存）
        after = self._snapshot_both_projects()
        alpha_after = after[self.project_alpha]
        persisted = [t for t in alpha_after if t["id"] == self.target_id]
        self.assertEqual(
            len(persisted), 1,
            f"查询结果中目标任务应恰好出现一次：\n{detail}",
        )
        self.assertEqual(
            persisted[0], obj,
            f"查询到的任务对象应与移动结果一致：\n{detail}",
        )

        # 只有指定标识的任务状态可能改变：其他任务完整对象与列表顺序不变
        before_others = [
            t for t in before[self.project_alpha] if t["id"] != self.target_id
        ]
        after_others = [
            t for t in alpha_after if t["id"] != self.target_id
        ]
        self.assertEqual(
            after_others, before_others,
            f"同项目其他任务的完整对象与顺序不得改变：\n{detail}",
        )
        self.assertEqual(
            [t["id"] for t in alpha_after],
            [t["id"] for t in before[self.project_alpha]],
            f"任务数量与列表顺序不得改变：\n{detail}",
        )
        self.assertEqual(
            after[self.project_beta], before[self.project_beta],
            f"另一项目的任务集合不得改变：\n{detail}",
        )

        if initial_status == target_status:
            # 同状态移动：任务数量和所有字段都不变
            self.assertEqual(
                after, before,
                f"同状态移动后两个项目的全部字段与数量都应不变：\n{detail}",
            )

    # ---------- 九种状态组合 ----------

    def test_move_todo_to_todo(self):
        self._assert_move("todo", "todo")

    def test_move_todo_to_doing(self):
        self._assert_move("todo", "doing")

    def test_move_todo_to_done(self):
        self._assert_move("todo", "done")

    def test_move_doing_to_todo(self):
        self._assert_move("doing", "todo")

    def test_move_doing_to_doing(self):
        self._assert_move("doing", "doing")

    def test_move_doing_to_done(self):
        self._assert_move("doing", "done")

    def test_move_done_to_todo(self):
        self._assert_move("done", "todo")

    def test_move_done_to_doing(self):
        self._assert_move("done", "doing")

    def test_move_done_to_done(self):
        self._assert_move("done", "done")

    # ---------- 非法目标状态 ----------

    def _assert_invalid_status(self, bad_status):
        """对存在的任务传入非法状态：退出码 2、stdout 空、stderr 说明状态非法、
        无 Python 异常回溯，且两个项目的任务集合与失败前完全一致。"""
        self._prepare("doing")
        before = self._snapshot_both_projects()

        cli_args = ("task-move", str(self.target_id), bad_status)
        proc = self.run_cli(*cli_args)
        extra = f"初始状态='doing' 目标状态={bad_status!r}（非法）\n"
        detail = self._detail(cli_args, proc, extra)

        self.assertEqual(proc.returncode, 2, f"预期参数错误退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"参数错误时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"参数错误时标准错误应说明状态非法：\n{detail}",
        )
        self.assertNotIn(
            "Traceback", proc.stderr,
            f"标准错误不应包含 Python 异常回溯：\n{detail}",
        )

        after = self._snapshot_both_projects()
        self.assertEqual(
            after, before,
            f"非法状态不得改动任何项目的任务：\n{detail}",
        )

    def test_error_status_blocked(self):
        # blocked 不在 todo/doing/done 之列
        self._assert_invalid_status("blocked")

    def test_error_status_uppercase_done(self):
        # DONE 大写不匹配，状态大小写敏感
        self._assert_invalid_status("DONE")

    def test_error_status_padded_done(self):
        # 首尾带空格的 done 不被接受（状态不去空白）
        self._assert_invalid_status(" done ")

    def test_error_status_empty_string(self):
        # 空字符串不是合法状态
        self._assert_invalid_status("")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""task-move / task-rename 连续修改并带 --from 预期值校验的命令行回归测试。

在现有直接连续修改回归（test_task_move_then_rename.py，保持不变）之上，
补充两次修改都携带 ``--from`` 预期值的链式场景，确认：

- 修改状态后仍能按原标题改名：先 ``task-move <id> doing --from todo``，
  再 ``task-rename <id> " 修复 API " --from " 整理 API "``，两次均成功，
  最终标题为 ``修复 API``、状态为 doing。
- 修改标题后仍能按原状态移动：在独立数据中交换两次操作的顺序（先按
  原标题改名、再按原状态移动），最终结果完全相同。
- 每个场景完成后重提原来的带 ``--from`` 移动与改名请求：此时目标值已
  等于当前值、预期来源仍为旧值，两次均退出码 2、标准输出为空、标准错误
  分别说明当前状态或标题及不匹配的预期值，不含异常回溯，且拒绝前后目标
  任务与两个项目的完整任务列表完全一致。

每次成功均退出码 0、标准错误为空、标准输出为与 task-show 结构一致的单个
任务对象（只含 id / project_id / title / status），仅本次修改的字段改变，
其余字段保留；后续独立命令进程的 task-show / task-list 读到相同的保存值，
同标题对照任务始终保持 todo 和原标题。场景二使用任务标识的前导零写法。

测试只通过 README 公开的 ``python -m kanban --db`` 入口驱动产品：用
project-create / task-create 准备数据，用 task-move / task-rename 携带
``--from`` 连续修改，用 task-show / task-list 核对落库结果。不直接读写
数据库，不调用产品内部函数，不依赖网络或任何预先保存的项目。每个用例在
独立临时目录中使用全新的隔离 SQLite 数据库，用例之间数据互相隔离，结束
后自动清理，不会接触使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_move_rename_from_chain.py            # 运行全部用例
    python3 -m unittest test_task_move_rename_from_chain -v
    python3 test_task_move_rename_from_chain.py \
        TaskMoveRenameFromChainRegression.test_move_then_rename_with_from

退出码：全部通过为 0，存在失败为 1。失败信息给出输入参数、实际退出码、
标准输出、标准错误与预期差异，不依赖 JSON 键顺序或错误文案逐字一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TASK_FIELDS = {"id", "project_id", "title", "status"}

# 两条任务共用的初始标题与改名输入（首尾带空白，保存时去除）
SHARED_TITLE = "整理 API"
NEW_TITLE_RAW = " 修复 API "
NEW_TITLE = "修复 API"


class TaskMoveRenameFromChainRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-from-chain-")
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
        """准备数据或查询用：要求成功并返回解析后的单个 JSON 值。"""
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc, expected="退出码 0、stderr 为空、stdout 为合法 JSON"
        )
        self.assertEqual(proc.returncode, 0, f"命令应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"stdout 不是合法 JSON：\n{detail}")

    @staticmethod
    def _detail(cli_args, proc, expected):
        """组装失败定位信息：输入参数、退出码与输出、预期结果。"""
        return "\n".join(
            [
                f"输入参数={list(cli_args)!r}",
                f"实际退出码={proc.returncode}",
                f"实际标准输出={proc.stdout!r}",
                f"实际标准错误={proc.stderr!r}",
                f"预期结果={expected}",
            ]
        )

    def parse_single_task(self, proc, cli_args, expected_obj):
        """要求 stdout 恰好是一个字段齐全、内容符合预期的任务对象。"""
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 0、stderr 为空、stdout 为单个 JSON 任务对象，"
                f"对象为 {expected_obj!r}（字段集合 {sorted(TASK_FIELDS)}）"
            ),
        )
        self.assertEqual(proc.returncode, 0, f"修改应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        try:
            obj, end = json.JSONDecoder().raw_decode(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"stdout 不是合法 JSON：\n{detail}")
        self.assertEqual(
            proc.stdout[end:].strip(), "",
            f"stdout 应只含单个 JSON 值，多余内容为：\n{detail}",
        )
        self.assertIsInstance(
            obj, dict, f"stdout 应为单个 JSON 对象而非数组：\n{detail}"
        )
        self.assertEqual(
            set(obj.keys()), TASK_FIELDS,
            f"结果对象字段结构不符：\n{detail}",
        )
        self.assertEqual(obj, expected_obj, f"结果对象内容不符：\n{detail}")
        return obj

    def assert_rejected(self, proc, cli_args):
        """预期不匹配的统一拒绝协议：rc2、stdout 空、stderr 非空无回溯。"""
        detail = self._detail(cli_args, proc, expected="退出码 2、stdout 为空")
        self.assertEqual(proc.returncode, 2, f"应退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"失败时 stdout 应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"stderr 应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr,
                         f"不应出现回溯：\n{detail}")
        return detail

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目一：目标任务 T（初始标题“整理 API”、状态 todo）
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        target = self.cli_json(
            "task-create", str(self.project_alpha), SHARED_TITLE
        )
        self.target_id = target["id"]

        # 项目二：同标题对照任务 O，验证不被连带改动
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        other = self.cli_json(
            "task-create", str(self.project_beta), SHARED_TITLE
        )
        self.other_id = other["id"]

    def target_obj(self, title, status):
        return {
            "id": self.target_id,
            "project_id": self.project_alpha,
            "title": title,
            "status": status,
        }

    def other_obj(self):
        return {
            "id": self.other_id,
            "project_id": self.project_beta,
            "title": SHARED_TITLE,
            "status": "todo",
        }

    def snapshot(self):
        """目标任务与两个项目的完整任务列表（经独立命令进程读取）。"""
        return {
            "target": self.cli_json("task-show", str(self.target_id)),
            "alpha": self.cli_json("task-list", str(self.project_alpha)),
            "beta": self.cli_json("task-list", str(self.project_beta)),
        }

    # ---------- 链式场景（两种顺序共用） ----------

    def run_chain(self, id_arg):
        """按“先移动后改名”执行带 --from 的连续修改并核对中间与最终结果。"""
        # 第一步：task-move 目标 doing、--from 为 todo；仅状态字段改变
        move_args = ("task-move", id_arg, "doing", "--from", "todo")
        moved = self.parse_single_task(
            self.run_cli(*move_args), move_args,
            self.target_obj(SHARED_TITLE, "doing"),
        )
        before = self.target_obj(SHARED_TITLE, "todo")
        for field in ("id", "project_id", "title"):
            self.assertEqual(
                moved[field], before[field],
                f"task-move 只应修改 status，{field} 不应改变：\n"
                f"修改前={before!r}\n修改后={moved!r}",
            )

        # 第二步：task-rename 新标题“ 修复 API ”、--from 为“ 整理 API ”；
        # 状态修改后仍能按原标题改名，仅标题字段改变
        rename_args = ("task-rename", id_arg, NEW_TITLE_RAW,
                       "--from", SHARED_TITLE)
        renamed = self.parse_single_task(
            self.run_cli(*rename_args), rename_args,
            self.target_obj(NEW_TITLE, "doing"),
        )
        for field in ("id", "project_id", "status"):
            self.assertEqual(
                renamed[field], moved[field],
                f"task-rename 只应修改 title，{field} 不应改变：\n"
                f"修改前={moved!r}\n修改后={renamed!r}",
            )
        return moved, renamed

    def run_chain_reversed(self, id_arg):
        """交换顺序：先按原标题改名，再按原状态移动，最终结果相同。"""
        # 第一步：task-rename 新标题“ 修复 API ”、--from 为“ 整理 API ”；
        # 仅标题字段改变
        rename_args = ("task-rename", id_arg, NEW_TITLE_RAW,
                       "--from", SHARED_TITLE)
        renamed = self.parse_single_task(
            self.run_cli(*rename_args), rename_args,
            self.target_obj(NEW_TITLE, "todo"),
        )
        before = self.target_obj(SHARED_TITLE, "todo")
        for field in ("id", "project_id", "status"):
            self.assertEqual(
                renamed[field], before[field],
                f"task-rename 只应修改 title，{field} 不应改变：\n"
                f"修改前={before!r}\n修改后={renamed!r}",
            )

        # 第二步：task-move 目标 doing、--from 为 todo；标题修改后仍能按
        # 原状态移动，仅状态字段改变
        move_args = ("task-move", id_arg, "doing", "--from", "todo")
        moved = self.parse_single_task(
            self.run_cli(*move_args), move_args,
            self.target_obj(NEW_TITLE, "doing"),
        )
        for field in ("id", "project_id", "title"):
            self.assertEqual(
                moved[field], renamed[field],
                f"task-move 只应修改 status，{field} 不应改变：\n"
                f"修改前={renamed!r}\n修改后={moved!r}",
            )
        return renamed, moved

    def assert_saved_state(self, final_obj):
        """独立查询读到相同保存值；对照任务与列表结构保持不变。"""
        # 独立命令进程 task-show 读到与最终结果完全一致的对象
        shown = self.cli_json("task-show", str(self.target_id))
        self.assertEqual(
            shown, final_obj,
            f"task-show 应读到与修改结果一致的对象：\n"
            f"修改结果={final_obj!r}\n查询所得={shown!r}",
        )
        # 项目一列表：只剩最终状态的目标任务，数量不变（不新增记录）
        self.assertEqual(
            self.cli_json("task-list", str(self.project_alpha)),
            [final_obj],
            "项目一应只剩最终状态的目标任务",
        )
        # 项目二：同标题对照任务始终保持 todo 和原标题
        self.assertEqual(
            self.cli_json("task-list", str(self.project_beta)),
            [self.other_obj()],
            "另一项目的同标题任务不得受影响",
        )

    def assert_repeat_rejected(self, id_arg):
        """重提原来的带 --from 请求：均退出码 2，拒绝前后数据完全一致。"""
        # 重提移动：目标 doing 已等于当前状态，预期来源仍为旧值 todo
        move_args = ("task-move", id_arg, "doing", "--from", "todo")
        before = self.snapshot()
        proc = self.run_cli(*move_args)
        detail = self.assert_rejected(proc, move_args)
        low = proc.stderr.lower()
        self.assertIn("doing", low,
                      f"stderr 应说明当前状态为 doing：\n{detail}")
        self.assertIn("todo", low,
                      f"stderr 应说明预期状态为 todo：\n{detail}")
        after = self.snapshot()
        self.assertEqual(after, before,
                         f"被拒绝的移动不得改动任何数据：\n{detail}\n"
                         f"拒绝前={before!r}\n拒绝后={after!r}")

        # 重提改名：目标“修复 API”已等于当前标题，预期来源仍为“整理 API”
        rename_args = ("task-rename", id_arg, NEW_TITLE_RAW,
                       "--from", SHARED_TITLE)
        before = self.snapshot()
        proc = self.run_cli(*rename_args)
        detail = self.assert_rejected(proc, rename_args)
        self.assertIn(NEW_TITLE, proc.stderr,
                      f"stderr 应说明当前标题为 {NEW_TITLE!r}：\n{detail}")
        self.assertIn(SHARED_TITLE, proc.stderr,
                      f"stderr 应说明预期标题为 {SHARED_TITLE!r}：\n{detail}")
        after = self.snapshot()
        self.assertEqual(after, before,
                         f"被拒绝的改名不得改动任何数据：\n{detail}\n"
                         f"拒绝前={before!r}\n拒绝后={after!r}")

        # 任务数量、标识与归属不变：两项目各一条，标识与创建时一致
        self.assertEqual(
            [t["id"] for t in after["alpha"]], [self.target_id],
            "项目一任务标识与数量不应改变",
        )
        self.assertEqual(
            [t["id"] for t in after["beta"]], [self.other_id],
            "项目二任务标识与数量不应改变",
        )
        self.assertEqual(after["alpha"][0]["project_id"], self.project_alpha)
        self.assertEqual(after["beta"][0]["project_id"], self.project_beta)

    # ---------- 场景一：先移动再改名 ----------

    def test_move_then_rename_with_from(self):
        moved, renamed = self.run_chain(str(self.target_id))
        # 两次修改作用于同一条记录
        self.assertEqual(moved["id"], renamed["id"])
        self.assertEqual(moved["project_id"], renamed["project_id"])
        # 最终标题为“修复 API”、状态为 doing，独立查询与对照任务核对
        self.assert_saved_state(renamed)
        # 重提原来的带 --from 移动与改名请求均被拒绝且不改数据
        self.assert_repeat_rejected(str(self.target_id))

    # ---------- 场景二：交换顺序，前导零标识 ----------

    def test_rename_then_move_with_from_leading_zero_id(self):
        # 前导零写法（如 0001）与数值标识等价
        id_arg = "0" * 3 + str(self.target_id)
        renamed, moved = self.run_chain_reversed(id_arg)
        # 两次修改作用于同一条记录，最终结果与场景一相同
        self.assertEqual(renamed["id"], moved["id"])
        self.assertEqual(moved, self.target_obj(NEW_TITLE, "doing"))
        self.assert_saved_state(moved)
        # 重提原来的带 --from 请求（同样用前导零写法）均被拒绝且不改数据
        self.assert_repeat_rejected(id_arg)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""task-move 后接 task-rename 连续修改同一任务的命令行回归（验收）测试。

场景：两个项目各有一条同标题任务；先把项目一中的目标任务从 todo 移到
doing，再把它改名为首尾带空白的 " 修复 API "（保存为 "修复 API"）。
最终只有目标任务变为标题 "修复 API"、状态 doing，标识与所属项目不变；
另一项目的同标题任务保持原标题与 todo 状态，两个项目的任务数量与按
标识升序的顺序不变。

同时核对：每次修改的标准输出都是与 task-show 结构一致的单个 JSON 任务
对象（保存后的最新值）；由独立命令进程 task-show / task-list /
project-stats 读到的标题与状态与修改结果一致。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品：用
project-create / task-create 准备数据，用 task-move / task-rename 连续
修改，用 task-show / task-list / project-stats 验证结果已落库。不直接
读写数据库，不调用产品内部函数，不依赖网络，也不依赖任何预先保存的
项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理，不会接触
使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_move_then_rename.py                  # 运行全部回归用例
    python3 -m unittest test_task_move_then_rename -v      # 等价写法
    python3 test_task_move_then_rename.py \
        TaskMoveThenRenameRegression.test_move_then_rename_only_changes_target

退出码：全部通过为 0，存在失败为 1。失败信息会给出输入参数、实际退出码、
标准输出、标准错误以及预期结果，不依赖 JSON 键顺序或错误文案逐字一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TASK_FIELDS = {"id", "project_id", "title", "status"}

# 两条任务共用的初始标题，用于确认同标题任务不会被连带改动
SHARED_TITLE = "整理 API"

# 改名输入：首尾带空白；保存值为去除首尾空白后的标题
NEW_TITLE_RAW = " 修复 API "
NEW_TITLE = "修复 API"


class TaskMoveThenRenameRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-move-rename-regtest-")
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

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目一：目标任务 T（初始 todo）
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        target = self.cli_json(
            "task-create", str(self.project_alpha), SHARED_TITLE
        )
        self.target_id = target["id"]

        # 项目二：同标题任务 O，验证跨项目隔离
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        other = self.cli_json(
            "task-create", str(self.project_beta), SHARED_TITLE
        )
        self.other_id = other["id"]

    def expected_other_object(self):
        return {
            "id": self.other_id,
            "project_id": self.project_beta,
            "title": SHARED_TITLE,
            "status": "todo",
        }

    # ---------- 连续操作：先移动再改名 ----------

    def test_move_then_rename_only_changes_target(self):
        # 第一步：把目标任务从 todo 移到 doing，标题仍为共享标题
        moved = self.parse_single_task(
            self.run_cli("task-move", str(self.target_id), "doing"),
            ("task-move", str(self.target_id), "doing"),
            {
                "id": self.target_id,
                "project_id": self.project_alpha,
                "title": SHARED_TITLE,
                "status": "doing",
            },
        )

        # 第二步：把目标任务改名为首尾带空白的 " 修复 API "，
        # 保存为 "修复 API"，doing 状态保留
        renamed = self.parse_single_task(
            self.run_cli("task-rename", str(self.target_id), NEW_TITLE_RAW),
            ("task-rename", str(self.target_id), NEW_TITLE_RAW),
            {
                "id": self.target_id,
                "project_id": self.project_alpha,
                "title": NEW_TITLE,
                "status": "doing",
            },
        )

        # 独立命令进程 task-show 读到与改名结果完全一致的对象
        shown = self.cli_json("task-show", str(self.target_id))
        self.assertEqual(
            shown, renamed,
            f"task-show 应读到与改名结果一致的对象：\n"
            f"改名结果={renamed!r}\n查询所得={shown!r}",
        )

        # 项目一列表：目标任务为最终值，数量不变（不新增记录）
        alpha_tasks = self.cli_json("task-list", str(self.project_alpha))
        self.assertEqual(
            alpha_tasks, [renamed],
            f"项目一应只剩最终状态的目标任务：\n实际={alpha_tasks!r}",
        )

        # 项目二：同标题任务保持原标题与 todo 状态，完整对象不变
        beta_tasks = self.cli_json("task-list", str(self.project_beta))
        self.assertEqual(
            beta_tasks, [self.expected_other_object()],
            f"另一项目的同标题任务不得受影响：\n实际={beta_tasks!r}",
        )

        # 统计反映当前状态：项目一 doing=1，项目二 todo=1
        self.assertEqual(
            self.cli_json("project-stats", str(self.project_alpha)),
            {
                "project_id": self.project_alpha,
                "total": 1,
                "todo": 0,
                "doing": 1,
                "done": 0,
            },
            "项目一统计应反映移动后的 doing 状态",
        )
        self.assertEqual(
            self.cli_json("project-stats", str(self.project_beta)),
            {
                "project_id": self.project_beta,
                "total": 1,
                "todo": 1,
                "doing": 0,
                "done": 0,
            },
            "项目二统计应保持 todo=1 不变",
        )

        # 列表查询反映当前标题：新标题关键词只在项目一命中目标任务，
        # 旧标题关键词在项目一不再命中、在项目二仍命中原样任务
        self.assertEqual(
            self.cli_json(
                "task-list", str(self.project_alpha), "--query", NEW_TITLE
            ),
            [renamed],
            "项目一按新标题筛选应命中改名后的目标任务",
        )
        self.assertEqual(
            self.cli_json(
                "task-list", str(self.project_alpha), "--query", SHARED_TITLE
            ),
            [],
            "项目一按旧标题筛选应不再命中任何任务",
        )
        self.assertEqual(
            self.cli_json(
                "task-list", str(self.project_beta), "--query", SHARED_TITLE
            ),
            [self.expected_other_object()],
            "项目二按旧标题筛选应仍命中未改动的任务",
        )

        # 移动结果与改名结果标识一致：两次修改作用于同一条记录
        self.assertEqual(moved["id"], renamed["id"])
        self.assertEqual(moved["project_id"], renamed["project_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

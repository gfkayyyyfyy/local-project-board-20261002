#!/usr/bin/env python3
"""task-move 与 task-rename 连续操作的命令行回归（验收）测试。

覆盖用户指定的连续操作场景：两个项目各有一条同标题任务，先将项目一的
任务从 todo 移到 doing，再把它改名为首尾带空白的 " 修复 API "（保存为
"修复 API"）。最终只有目标任务变为标题 "修复 API"、状态 doing，标识与
所属项目不变；另一项目的同标题任务保持原标题与 todo 状态，两个项目均
不新增记录，列表与统计反映最新标题与状态。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品：用
project-create / task-create 准备数据，依次执行 task-move 与
task-rename，再用 task-show / task-list / project-stats 等独立命令
进程核对结果已落库。不直接读写数据库，不调用产品内部函数，不依赖网络，
也不依赖任何预先保存的项目。

用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理，不会接触
使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_move_rename.py                 # 运行全部回归用例
    python3 -m unittest test_task_move_rename -v     # 等价写法
    python3 test_task_move_rename.py \
        TaskMoveRenameRegression.test_move_then_rename_sequential

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

# 两个项目中两条任务共用的初始标题
SHARED_TITLE = "Sync design doc"

# 改名输入与保存值：首尾空白被去除，内部内容原样保留
NEW_TITLE_RAW = " 修复 API "
NEW_TITLE = "修复 API"


class TaskMoveRenameRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-seq-regtest-")
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
        detail = self._detail(cli_args, proc, expected="准备数据成功：退出码 0、stderr 为空")
        self.assertEqual(proc.returncode, 0, f"准备数据应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"准备数据时标准错误应为空：\n{detail}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"准备数据的 stdout 不是合法 JSON：\n{detail}")

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

    def query_json(self, *cli_args):
        """独立查询用：要求成功并返回解析后的单个 JSON 值。"""
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc,
            expected="查询成功：退出码 0、stderr 为空、stdout 为合法 JSON",
        )
        self.assertEqual(proc.returncode, 0, f"查询应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"查询时标准错误应为空：\n{detail}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"查询的 stdout 不是合法 JSON：\n{detail}")

    def parse_single_task(self, proc, cli_args, expected_obj):
        """要求 stdout 恰好是一个 JSON 任务对象并等于预期对象。"""
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 0、stderr 为空、stdout 为单个 JSON 任务对象，"
                f"对象为 {expected_obj!r}（字段集合 {sorted(TASK_FIELDS)}）"
            ),
        )
        self.assertEqual(proc.returncode, 0, f"操作应成功（退出码 0）：\n{detail}")
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
        # 两个项目各一条同标题任务，初始状态均为 todo
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        target = self.cli_json(
            "task-create", str(self.project_alpha), SHARED_TITLE
        )
        self.target_id = target["id"]

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

    # ---------- 连续操作验收场景 ----------

    def test_move_then_rename_sequential(self):
        """todo -> doing 移动后再改名：只有目标任务变化，另一项目任务保持原值。"""
        # 第一步：把项目一的目标任务从 todo 移到 doing
        moved_obj = {
            "id": self.target_id,
            "project_id": self.project_alpha,
            "title": SHARED_TITLE,
            "status": "doing",
        }
        proc = self.run_cli("task-move", str(self.target_id), "doing")
        moved = self.parse_single_task(
            proc, ("task-move", str(self.target_id), "doing"), moved_obj
        )
        # 移动只改状态：标题仍为共享标题，标识与所属项目不变
        self.assertEqual(moved["title"], SHARED_TITLE)
        self.assertEqual(moved["status"], "doing")

        # 移动后、改名前：另一项目的同标题任务保持原值
        self.assertEqual(
            self.query_json("task-list", str(self.project_beta)),
            [self.expected_other_object()],
            "移动后另一项目的同标题任务应保持原标题与 todo 状态",
        )

        # 第二步：把目标任务改名为首尾带空白的 " 修复 API "
        renamed_obj = {
            "id": self.target_id,
            "project_id": self.project_alpha,
            "title": NEW_TITLE,
            "status": "doing",
        }
        proc = self.run_cli("task-rename", str(self.target_id), NEW_TITLE_RAW)
        renamed = self.parse_single_task(
            proc, ("task-rename", str(self.target_id), NEW_TITLE_RAW), renamed_obj
        )
        # 改名只改标题：首尾空白已去除，doing 状态、标识与所属项目保留
        self.assertEqual(renamed["title"], NEW_TITLE)
        self.assertEqual(renamed["status"], "doing")

        # 另一次独立查询读到与改名结果相同的内容（结果已落库）
        self.assertEqual(
            self.query_json("task-show", str(self.target_id)),
            renamed,
            "task-show 独立查询应读到与改名结果一致的最新对象",
        )

        # 最终只有目标任务变化：项目一列表恰为改名后的目标任务，不新增记录
        self.assertEqual(
            self.query_json("task-list", str(self.project_alpha)),
            [renamed],
            "项目一最终应只有改名后的目标任务一条记录",
        )
        # 另一项目的同标题任务保持原标题与 todo 状态，同样不新增记录
        self.assertEqual(
            self.query_json("task-list", str(self.project_beta)),
            [self.expected_other_object()],
            "项目二的同标题任务应保持原标题与 todo 状态",
        )

        # 统计反映当前标题与状态：项目一 doing 1，项目二 todo 1
        self.assertEqual(
            self.query_json("project-stats", str(self.project_alpha)),
            {
                "project_id": self.project_alpha,
                "total": 1,
                "todo": 0,
                "doing": 1,
                "done": 0,
            },
            "项目一统计应反映目标任务的 doing 状态",
        )
        self.assertEqual(
            self.query_json("project-stats", str(self.project_beta)),
            {
                "project_id": self.project_beta,
                "total": 1,
                "todo": 1,
                "doing": 0,
                "done": 0,
            },
            "项目二统计应保持 todo 1 不变",
        )
        # 列表按新标题可筛选到目标任务，另一项目按新标题无命中
        self.assertEqual(
            self.query_json(
                "task-list", str(self.project_alpha), "--query", NEW_TITLE
            ),
            [renamed],
            "项目一按新标题筛选应命中目标任务",
        )
        self.assertEqual(
            self.query_json(
                "task-list", str(self.project_beta), "--query", NEW_TITLE
            ),
            [],
            "项目二按新标题筛选不应命中任何任务",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

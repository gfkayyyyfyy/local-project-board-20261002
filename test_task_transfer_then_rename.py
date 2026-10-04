#!/usr/bin/env python3
"""task-transfer 后接 task-rename 连续修改同一任务的命令行回归（验收）测试。

场景：项目甲有目标任务 T（doing）与同标题对照任务 S（todo），项目乙有
同标题对照任务 O（todo），三条任务标题均为 "整理 API"。先用
task-transfer 把 T 转移到乙，再用 task-rename 和 T 原标识的前导零写法
把标题改为首尾带空白的 " 修复 API "（保存为 "修复 API"）。两次修改都
作用于同一条任务：标识始终为 T，转移后所属项目为乙且改名后仍属乙，
状态保持 doing。

同时核对转移后的重复提交与拒绝路径：仅首尾空白不同的同一保存标题再次
提交应成功返回同一对象且不改动任何列表；空字符串或纯空白标题应以
退出码 2 拒绝（stdout 为空、stderr 说明标题为空且无异常回溯），拒绝后
T 仍属于乙并保持此前的标题与状态，S、O 与任务总数均不变。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品：用
project-create / task-create / task-move 准备数据，用 task-transfer /
task-rename 连续修改，用 task-show / task-list 验证结果已落库。不直接
读写数据库，不调用产品内部函数，不依赖网络，也不依赖任何预先保存的
项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理，不会接触
使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_transfer_then_rename.py                  # 运行全部回归用例
    python3 -m unittest test_task_transfer_then_rename -v      # 等价写法
    python3 test_task_transfer_then_rename.py \
        TaskTransferThenRenameRegression.test_transfer_then_rename_same_task

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

# 三条任务共用的初始标题，用于确认同标题任务不会被连带改动
SHARED_TITLE = "整理 API"

# 改名输入：首尾带空白；保存值为去除首尾空白后的标题
NEW_TITLE_RAW = " 修复 API "
NEW_TITLE = "修复 API"


class TaskTransferThenRenameRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(
            prefix="kanban-transfer-rename-regtest-"
        )
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

    def list_project(self, project_id):
        """通过公开命令查询某项目的全部任务（按标识升序）。"""
        return self.cli_json("task-list", str(project_id))

    def query_project(self, project_id, keyword):
        """通过公开命令按标题关键词筛选某项目的任务。"""
        return self.cli_json("task-list", str(project_id), "--query", keyword)

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目甲：目标任务 T 与同标题对照任务 S；T 准备为 doing，S 保持 todo
        self.project_jia = self.cli_json("project-create", "甲")["id"]
        target = self.cli_json(
            "task-create", str(self.project_jia), SHARED_TITLE
        )
        sibling = self.cli_json(
            "task-create", str(self.project_jia), SHARED_TITLE
        )
        self.target_id = target["id"]
        self.sibling_id = sibling["id"]
        self.cli_json("task-move", str(self.target_id), "doing")

        # 项目乙：另一条同标题对照任务 O（todo），验证跨项目隔离
        self.project_yi = self.cli_json("project-create", "乙")["id"]
        other = self.cli_json(
            "task-create", str(self.project_yi), SHARED_TITLE
        )
        self.other_id = other["id"]

    def expected_sibling_object(self):
        return {
            "id": self.sibling_id,
            "project_id": self.project_jia,
            "title": SHARED_TITLE,
            "status": "todo",
        }

    def expected_other_object(self):
        return {
            "id": self.other_id,
            "project_id": self.project_yi,
            "title": SHARED_TITLE,
            "status": "todo",
        }

    def expected_target_object(self):
        """改名后目标任务 T 的最终保存对象。"""
        return {
            "id": self.target_id,
            "project_id": self.project_yi,
            "title": NEW_TITLE,
            "status": "doing",
        }

    def leading_zero_id(self, task_id):
        """同一标识的前导零写法（按数值等价定位）。"""
        return "000" + str(task_id)

    # ---------- 连续操作：先转移再改名 ----------

    def transfer_then_rename(self):
        """执行验收场景的两步修改，返回（转移响应对象, 改名响应对象）。"""
        # 第一步：把 T 从甲转移到乙，标识、标题、doing 状态原样保留
        transfer_args = ("task-transfer", str(self.target_id),
                         str(self.project_yi))
        transferred = self.parse_single_task(
            self.run_cli(*transfer_args),
            transfer_args,
            {
                "id": self.target_id,
                "project_id": self.project_yi,
                "title": SHARED_TITLE,
                "status": "doing",
            },
        )

        # 第二步：用 T 原标识的前导零写法改名为首尾带空白的 " 修复 API "，
        # 保存为 "修复 API"；标识仍为 T，所属项目仍为乙，状态仍为 doing
        rename_args = (
            "task-rename", self.leading_zero_id(self.target_id), NEW_TITLE_RAW,
        )
        renamed = self.parse_single_task(
            self.run_cli(*rename_args),
            rename_args,
            self.expected_target_object(),
        )

        # 两次修改的响应标识一致：作用于同一条任务
        self.assertEqual(
            transferred["id"], renamed["id"],
            f"转移与改名应作用于同一条任务：\n"
            f"转移结果={transferred!r}\n改名结果={renamed!r}",
        )
        return transferred, renamed

    def assert_final_state(self, renamed):
        """核对改名落库后的完整状态：task-show、两项目列表、筛选与总数。"""
        # 由新的命令进程查询同一数据库：task-show 与改名响应完全一致
        shown = self.cli_json("task-show", str(self.target_id))
        self.assertEqual(
            shown, renamed,
            f"task-show 应读到与改名结果一致的对象：\n"
            f"改名结果={renamed!r}\n查询所得={shown!r}",
        )

        # 甲的任务列表只剩原样的 S
        jia_tasks = self.list_project(self.project_jia)
        self.assertEqual(
            jia_tasks, [self.expected_sibling_object()],
            f"甲应只剩原样的对照任务 S：\n实际={jia_tasks!r}",
        )

        # 乙的列表包含改名后的 T 和原样的 O，按标识升序
        yi_tasks = self.list_project(self.project_yi)
        expected_yi = sorted(
            [renamed, self.expected_other_object()],
            key=lambda obj: obj["id"],
        )
        self.assertEqual(
            yi_tasks, expected_yi,
            f"乙应包含改名后的 T 和原样的 O（按标识升序）：\n实际={yi_tasks!r}",
        )
        self.assertEqual(
            [obj["id"] for obj in yi_tasks],
            sorted(obj["id"] for obj in yi_tasks),
            f"乙的列表应按标识升序：\n实际={yi_tasks!r}",
        )

        # 任务总数仍为三条（不新增、不复制）
        self.assertEqual(
            len(jia_tasks) + len(yi_tasks), 3,
            f"任务总数应仍为三条：\n甲={jia_tasks!r}\n乙={yi_tasks!r}",
        )

        # 按新标题筛选乙只返回 T，筛选甲返回空数组；按旧标题筛选乙只返回 O
        self.assertEqual(
            self.query_project(self.project_yi, NEW_TITLE), [renamed],
            "乙按新标题筛选应只返回改名后的 T",
        )
        self.assertEqual(
            self.query_project(self.project_jia, NEW_TITLE), [],
            "甲按新标题筛选应返回空数组",
        )
        self.assertEqual(
            self.query_project(self.project_yi, SHARED_TITLE),
            [self.expected_other_object()],
            "乙按旧标题筛选应只返回原样的 O",
        )

    # ---------- 验收场景 ----------

    def test_transfer_then_rename_same_task(self):
        _, renamed = self.transfer_then_rename()
        self.assert_final_state(renamed)

    # ---------- 转移后的重复提交与拒绝路径 ----------

    def test_repeat_same_saved_title_is_idempotent(self):
        _, renamed = self.transfer_then_rename()

        # 仅首尾空白不同的同一保存标题再次提交：成功并返回同一对象
        repeat_args = (
            "task-rename", str(self.target_id), "\t" + NEW_TITLE + "  ",
        )
        repeated = self.parse_single_task(
            self.run_cli(*repeat_args), repeat_args, renamed,
        )
        self.assertEqual(repeated, renamed)

        # 重复提交后两项目的完整任务列表保持不变
        self.assert_final_state(renamed)

    def test_empty_or_blank_title_rejected(self):
        _, renamed = self.transfer_then_rename()

        for bad_title in ("", "   ", " \t "):
            with self.subTest(bad_title=bad_title):
                before_jia = self.list_project(self.project_jia)
                before_yi = self.list_project(self.project_yi)

                cli_args = ("task-rename", str(self.target_id), bad_title)
                proc = self.run_cli(*cli_args)
                detail = self._detail(
                    cli_args, proc,
                    expected=(
                        "退出码 2、stdout 为空、stderr 说明标题为空"
                        "且无 Python 异常回溯；拒绝后 T 仍属于乙并保持"
                        "此前的标题和状态，S、O 与任务数量均不变"
                    ),
                )
                self.assertEqual(
                    proc.returncode, 2,
                    f"空标题应被拒绝（退出码 2）：\n{detail}",
                )
                self.assertEqual(
                    proc.stdout, "", f"拒绝时标准输出应为空：\n{detail}"
                )
                self.assertNotEqual(
                    proc.stderr.strip(), "",
                    f"拒绝时标准错误应说明原因：\n{detail}",
                )
                low = proc.stderr.lower()
                self.assertIn(
                    "title", low,
                    f"标准错误应说明是标题问题：\n{detail}",
                )
                self.assertIn(
                    "empty", low,
                    f"标准错误应说明标题为空：\n{detail}",
                )
                self.assertNotIn(
                    "Traceback", proc.stderr,
                    f"标准错误不应包含 Python 异常回溯：\n{detail}",
                )

                # 拒绝后：T 仍属于乙并保持此前的标题和状态，
                # S、O 与任务数量均不变
                self.assertEqual(
                    self.cli_json("task-show", str(self.target_id)), renamed,
                    f"拒绝后 T 应保持此前的完整对象：\n{detail}",
                )
                self.assertEqual(
                    self.list_project(self.project_jia), before_jia,
                    f"拒绝后甲的任务列表不得变化：\n{detail}",
                )
                self.assertEqual(
                    self.list_project(self.project_yi), before_yi,
                    f"拒绝后乙的任务列表不得变化：\n{detail}",
                )
                self.assertEqual(
                    len(before_jia) + len(before_yi), 3,
                    f"任务总数应仍为三条：\n{detail}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

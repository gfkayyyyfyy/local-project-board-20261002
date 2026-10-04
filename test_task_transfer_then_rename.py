#!/usr/bin/env python3
"""task-transfer 后接 task-rename 连续修改同一任务的命令行回归（验收）测试。

场景：项目甲中有目标任务 T（doing）与同标题对照任务 S（todo），项目乙中
有同标题对照任务 O（todo），三条任务初始标题均为 "整理 API"。先用
task-transfer 把 T 转移到乙，再用 task-rename 以 T 原标识的前导零写法
把标题改为首尾带空白的 " 修复 API "（保存为 "修复 API"）。两次操作必须
作用于同一条任务：转移响应保留原标题与 doing 状态；改名响应仅改变标题，
标识仍为 T、所属项目仍为乙、状态仍为 doing。

同时核对：每次修改的标准输出都是与 task-show 结构一致的单个 JSON 任务
对象（保存后的最新值）；由独立命令进程 task-show / task-list 读到的结果
与修改响应完全一致；甲只剩原样的 S，乙包含改名后的 T 与原样的 O，按标识
升序，任务总数始终为三；按新标题筛选乙只返回 T、筛选甲返回空数组、按旧
标题筛选乙只返回 O。

另核对转移后任务上的重复提交与拒绝路径：仅首尾空白不同的同一保存标题
再次提交应成功返回同一对象且两项目列表不变；空字符串或纯空白标题应以
退出码 2 拒绝（stdout 为空、stderr 说明标题为空且无异常回溯），拒绝后
T 仍属于乙并保持此前的标题与状态，S、O 与任务数量均不变。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品：用
project-create / task-create / task-move 准备真实数据，用 task-transfer /
task-rename 连续修改，用 task-show / task-list 查询验证结果已落库。不直接
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

# 目标任务与两条对照任务共用的初始标题，用于确认同标题任务不会被连带改动
SHARED_TITLE = "整理 API"

# 改名输入：首尾带空白；保存值为去除首尾空白后的标题
NEW_TITLE_RAW = " 修复 API "
NEW_TITLE = "修复 API"

# 重复提交用的等价输入：仅首尾空白与首次不同，保存值仍为 NEW_TITLE
NEW_TITLE_RAW_AGAIN = "\t 修复 API \n "


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
            obj, dict, f"结果应为单个 JSON 对象而非数组：\n{detail}"
        )
        self.assertEqual(
            set(obj.keys()), TASK_FIELDS,
            f"结果对象字段结构应与 task-show 一致：\n{detail}",
        )
        self.assertEqual(obj, expected_obj, f"结果对象内容不符：\n{detail}")
        return obj

    def show_task(self, task_id):
        """通过公开命令按标识读取单条任务，返回任务对象。"""
        proc = self.run_cli("task-show", str(task_id))
        detail = self._detail(
            ("task-show", str(task_id)), proc,
            expected="退出码 0、stderr 为空、stdout 为单个 JSON 任务对象",
        )
        self.assertEqual(proc.returncode, 0, f"task-show 应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"task-show 时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"task-show 的 stdout 不是合法 JSON：\n{detail}")
        self.assertIsInstance(
            data, dict, f"task-show 结果应为单个 JSON 对象：\n{detail}",
        )
        return data

    def list_project(self, project_id, *extra_args):
        """通过公开 task-list 命令查询项目任务，返回任务对象列表。"""
        cli_args = ("task-list", str(project_id), *extra_args)
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc,
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

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目甲：先建目标任务 T，再建同标题对照任务 S，并把 T 置为 doing
        self.project_jia = self.cli_json("project-create", "甲")["id"]
        target = self.cli_json("task-create", str(self.project_jia), SHARED_TITLE)
        sibling = self.cli_json("task-create", str(self.project_jia), SHARED_TITLE)
        self.target_id = target["id"]
        self.sibling_id = sibling["id"]
        moved = self.cli_json("task-move", str(self.target_id), "doing")
        self.assertEqual(
            moved,
            {"id": self.target_id, "project_id": self.project_jia,
             "title": SHARED_TITLE, "status": "doing"},
            "夹具准备：T 应为甲项目 doing 状态",
        )

        # 项目乙：另一条同标题对照任务 O（todo）
        self.project_yi = self.cli_json("project-create", "乙")["id"]
        other = self.cli_json("task-create", str(self.project_yi), SHARED_TITLE)
        self.other_id = other["id"]

        # 夹具自检：甲有 T(doing)、S(todo)，乙有 O(todo)，总数为三
        self.assertEqual(
            self.list_project(self.project_jia),
            [
                {"id": self.target_id, "project_id": self.project_jia,
                 "title": SHARED_TITLE, "status": "doing"},
                {"id": self.sibling_id, "project_id": self.project_jia,
                 "title": SHARED_TITLE, "status": "todo"},
            ],
            "夹具准备：甲项目应包含 T(doing) 与 S(todo)",
        )
        self.assertEqual(
            self.list_project(self.project_yi),
            [{"id": self.other_id, "project_id": self.project_yi,
              "title": SHARED_TITLE, "status": "todo"}],
            "夹具准备：乙项目应只包含 O(todo)",
        )

    # ---------- 预期对象 ----------

    def expected_sibling(self):
        """对照任务 S 的完整对象（始终保持原样）。"""
        return {"id": self.sibling_id, "project_id": self.project_jia,
                "title": SHARED_TITLE, "status": "todo"}

    def expected_other(self):
        """对照任务 O 的完整对象（始终保持原样）。"""
        return {"id": self.other_id, "project_id": self.project_yi,
                "title": SHARED_TITLE, "status": "todo"}

    def expected_transferred(self):
        """转移后、改名前 T 的完整对象：仅所属项目变为乙。"""
        return {"id": self.target_id, "project_id": self.project_yi,
                "title": SHARED_TITLE, "status": "doing"}

    def expected_renamed(self):
        """转移并改名后 T 的完整对象：仅标题变为保存值。"""
        return {"id": self.target_id, "project_id": self.project_yi,
                "title": NEW_TITLE, "status": "doing"}

    # ---------- 共享步骤：转移并改名（含完整核对） ----------

    def _transfer_then_rename(self):
        """执行 task-transfer 与 task-rename 并核对两次响应，返回改名响应对象。"""
        # 第一次修改：task-transfer 把 T 转移到乙
        transfer_args = (
            "task-transfer", str(self.target_id), str(self.project_yi),
        )
        transfer_proc = self.run_cli(*transfer_args)
        transferred = self.parse_single_task(
            transfer_proc, transfer_args, self.expected_transferred(),
        )

        # 第二次修改：task-rename 用 T 原标识的前导零写法，标题首尾带空白
        leading_zero_id = "000" + str(self.target_id)
        rename_args = ("task-rename", leading_zero_id, NEW_TITLE_RAW)
        rename_proc = self.run_cli(*rename_args)
        renamed = self.parse_single_task(
            rename_proc, rename_args, self.expected_renamed(),
        )

        # 两次操作必须作用于同一条任务：标识一致，仅标题变化
        self.assertEqual(
            renamed["id"], transferred["id"],
            f"两次操作应作用于同一任务标识：\n"
            f"转移响应={transferred!r}\n改名响应={renamed!r}",
        )
        self.assertEqual(
            {k: v for k, v in renamed.items() if k != "title"},
            {k: v for k, v in transferred.items() if k != "title"},
            f"改名只应改变标题，标识/所属项目/状态不变：\n"
            f"转移响应={transferred!r}\n改名响应={renamed!r}",
        )
        return renamed

    def _check_final_state(self, renamed):
        """核对改名后的完整落库状态：task-show、两项目列表、关键词筛选。"""
        jia, yi = self.project_jia, self.project_yi

        # 由新的命令进程查询同一数据库：task-show 与改名响应完全一致
        shown = self.show_task(self.target_id)
        self.assertEqual(
            shown, renamed,
            f"task-show 查询应与改名响应完全一致（结果已保存）：\n"
            f"task-show 所得={shown!r}\n改名响应={renamed!r}",
        )

        # 甲的任务列表只剩原样的 S
        jia_list = self.list_project(jia)
        self.assertEqual(
            jia_list, [self.expected_sibling()],
            f"甲应只剩对照任务 S（原样）：\n实际={jia_list!r}",
        )

        # 乙的列表包含改名后的 T 和原样的 O，按标识升序
        yi_list = self.list_project(yi)
        expected_yi = sorted(
            [renamed, self.expected_other()], key=lambda obj: obj["id"]
        )
        self.assertEqual(
            yi_list, expected_yi,
            f"乙应包含改名后的 T 与原样的 O（按标识升序）：\n实际={yi_list!r}",
        )
        self.assertEqual(
            [obj["id"] for obj in yi_list],
            sorted(obj["id"] for obj in yi_list),
            f"乙列表应按任务标识升序：\n实际={yi_list!r}",
        )

        # 任务总数仍为三条（不新增、不复制、不丢失）
        self.assertEqual(
            len(jia_list) + len(yi_list), 3,
            f"任务总数应仍为三：\n甲={jia_list!r}\n乙={yi_list!r}",
        )

        # 按新标题筛选：乙只返回 T，甲返回空数组
        self.assertEqual(
            self.list_project(yi, "--query", NEW_TITLE), [renamed],
            f"按新标题筛选乙应只返回改名后的 T",
        )
        self.assertEqual(
            self.list_project(jia, "--query", NEW_TITLE), [],
            f"按新标题筛选甲应返回空数组",
        )
        # 按旧标题筛选乙：只返回原样的 O（T 已改名，不再匹配）
        self.assertEqual(
            self.list_project(yi, "--query", SHARED_TITLE),
            [self.expected_other()],
            f"按旧标题筛选乙应只返回 O",
        )

    # ---------- 成功路径 ----------

    def test_transfer_then_rename_same_task(self):
        renamed = self._transfer_then_rename()
        self._check_final_state(renamed)

    def test_repeat_rename_same_saved_title_is_noop(self):
        # 转移并改名后，仅首尾空白不同的同一保存标题再次提交：
        # 应成功返回同一对象，两项目的完整任务列表保持不变
        renamed = self._transfer_then_rename()
        jia_before = self.list_project(self.project_jia)
        yi_before = self.list_project(self.project_yi)

        repeat_args = (
            "task-rename", str(self.target_id), NEW_TITLE_RAW_AGAIN,
        )
        repeat_proc = self.run_cli(*repeat_args)
        repeated = self.parse_single_task(repeat_proc, repeat_args, renamed)
        self.assertEqual(
            repeated, renamed,
            f"重复提交同一保存标题应返回同一对象：\n"
            f"首次响应={renamed!r}\n重复响应={repeated!r}",
        )

        # 两项目的完整任务列表与重复提交前完全一致
        self.assertEqual(
            self.list_project(self.project_jia), jia_before,
            f"重复提交后甲的任务列表不得改变：\n之前={jia_before!r}",
        )
        self.assertEqual(
            self.list_project(self.project_yi), yi_before,
            f"重复提交后乙的任务列表不得改变：\n之前={yi_before!r}",
        )
        # task-show 仍与改名响应一致，任务总数仍为三
        self.assertEqual(self.show_task(self.target_id), renamed)
        self.assertEqual(
            len(jia_before) + len(yi_before), 3,
            f"任务总数应仍为三：\n甲={jia_before!r}\n乙={yi_before!r}",
        )

    # ---------- 拒绝路径 ----------

    def _check_rename_rejected(self, bad_title):
        """空/纯空白标题应被拒绝：退出码 2、stdout 为空、数据不变。"""
        renamed = self._transfer_then_rename()
        jia_before = self.list_project(self.project_jia)
        yi_before = self.list_project(self.project_yi)

        cli_args = ("task-rename", str(self.target_id), bad_title)
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 2、stdout 为空、stderr 说明标题为空且无 Python 异常回溯；"
                "拒绝后 T 仍属于乙并保持此前的标题与状态，S、O 与任务数量不变"
            ),
        )
        self.assertEqual(
            proc.returncode, 2, f"空标题应被拒绝（退出码 2）：\n{detail}"
        )
        self.assertEqual(proc.stdout, "", f"拒绝时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"拒绝时标准错误应说明原因：\n{detail}",
        )
        low = proc.stderr.lower()
        self.assertTrue(
            "title" in low or "标题" in proc.stderr,
            f"标准错误应说明与任务标题相关的原因：\n{detail}",
        )
        self.assertTrue(
            "empty" in low or "空" in proc.stderr,
            f"标准错误应说明标题为空：\n{detail}",
        )
        self.assertNotIn(
            "Traceback", proc.stderr,
            f"标准错误不应包含 Python 异常回溯：\n{detail}",
        )

        # 拒绝后 T 仍属于乙，保持此前的标题与状态
        self.assertEqual(
            self.show_task(self.target_id), renamed,
            f"拒绝后 T 应保持改名后的完整对象：\n{detail}",
        )
        # S、O 与任务数量均不变：两项目完整列表与拒绝前一致
        self.assertEqual(
            self.list_project(self.project_jia), jia_before,
            f"拒绝后甲的任务列表不得改变：\n{detail}",
        )
        self.assertEqual(
            self.list_project(self.project_yi), yi_before,
            f"拒绝后乙的任务列表不得改变：\n{detail}",
        )
        self.assertEqual(
            len(jia_before) + len(yi_before), 3,
            f"任务总数应仍为三：\n{detail}",
        )

    def test_rename_empty_title_rejected(self):
        self._check_rename_rejected("")

    def test_rename_blank_title_rejected(self):
        self._check_rename_rejected("   \t\n  ")


if __name__ == "__main__":
    unittest.main(verbosity=2)

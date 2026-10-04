#!/usr/bin/env python3
"""task-move / task-rename 连续修改并带 --from 预期值校验的命令行回归测试。

两个场景互相独立、顺序相反，用于确认两条带预期值校验的修改流程可以连续
作用于同一条任务，且每个场景使用全新的临时数据库：每个库中创建两个项目，
各放一条标题为 "整理 API"、状态为 todo 的任务，标识均取自创建结果。

场景一（test_move_with_from_then_rename_with_from）：目标任务标识使用
前导零写法。先执行 ``task-move <id> doing --from todo``（确认当前状态后
移动），再执行 ``task-rename <id> " 修复 API " --from " 整理 API "``
（确认仍能按原标题改名）；两次成功后标题为 "修复 API"、状态为 doing，
即修改状态后仍能按原标题改名。

场景二（test_rename_with_from_then_move_with_from）：在另一个独立数据库中
交换两次操作的顺序，先按原标题改名、再按原状态移动，最终结果与场景一
完全相同，即修改标题后仍能按原状态移动。

每次成功均核对：退出码 0、stderr 为空、stdout 为与 task-show 结构一致的
单个任务对象（只含 id / project_id / title / status），且相对上一次结果
仅本次修改的字段改变、其余字段保留；随后由独立命令进程 task-show 读到
相同的保存值；另一项目中的同标题对照任务始终保持原标题与 todo 状态。

每个场景完成后，分别原样重提此前成功的带 --from 移动与改名请求：此时
目标值已等于当前值，而 --from 预期仍是旧值，因此两次都必须以退出码 2
拒绝，stdout 为空，stderr 分别说明当前状态/标题与不匹配的预期值且不含
Python 异常回溯。每次拒绝前后分别核对目标任务对象与两个项目的完整任务
列表完全一致：已成功保存的标题与状态保留，任务数量、标识与归属不变。

测试只通过 README 公开的 ``python -m kanban --db`` 入口驱动产品：用
project-create / task-create 准备数据，用 task-move / task-rename 修改，
用 task-show / task-list 核对。不直接读写数据库，不调用产品内部函数，
不依赖网络。每例数据互相隔离并在结束后清理，不接触使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_move_rename_from_sequence.py                  # 运行全部用例
    python3 -m unittest test_task_move_rename_from_sequence -v      # 等价写法
    python3 test_task_move_rename_from_sequence.py \
        TaskMoveRenameFromSequenceRegression.test_move_with_from_then_rename_with_from

退出码：全部通过为 0，存在失败为 1。失败信息会给出输入参数、实际退出码、
标准输出、标准错误以及预期差异，不依赖 JSON 键顺序或错误文案逐字相同。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TASK_FIELDS = {"id", "project_id", "title", "status"}

# 两个项目中任务的共同初始标题；改名与重提拒绝都以它为预期原标题
SHARED_TITLE = "整理 API"

# 改名输入首尾带空白；保存值为去除首尾空白后的标题
NEW_TITLE_RAW = " 修复 API "
NEW_TITLE = "修复 API"


class TaskMoveRenameFromSequenceRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(
            prefix="kanban-move-rename-from-regtest-"
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
        """组装失败定位信息：输入参数、退出码与两路输出、预期结果。"""
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
        """要求 stdout 恰好是一个字段齐全、内容符合预期的单个任务对象。"""
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
            f"结果对象字段结构应与 task-show 一致：\n{detail}",
        )
        self.assertEqual(obj, expected_obj, f"结果对象内容不符：\n{detail}")
        return obj

    def show_task(self, task_id):
        """通过独立命令进程 task-show 按标识读取单条任务。"""
        cli_args = ("task-show", str(task_id))
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc, expected="退出码 0、stderr 为空、stdout 为任务对象"
        )
        self.assertEqual(proc.returncode, 0, f"task-show 应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"task-show 时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"task-show 的 stdout 不是合法 JSON：\n{detail}")
        self.assertEqual(set(data.keys()), TASK_FIELDS, f"task-show 字段不符：\n{detail}")
        return data

    def list_project(self, project_id):
        """通过公开 task-list 命令查询项目的完整任务列表（按标识升序）。"""
        cli_args = ("task-list", str(project_id))
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc, expected="退出码 0、stderr 为空、stdout 为 JSON 数组"
        )
        self.assertEqual(proc.returncode, 0, f"task-list 应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"task-list 时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"task-list 的 stdout 不是合法 JSON：\n{detail}")
        self.assertIsInstance(data, list, f"task-list 结果应为数组：\n{detail}")
        return data

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目一与目标任务：标识取自创建结果，不假设固定数值
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        target = self.cli_json(
            "task-create", str(self.project_alpha), SHARED_TITLE
        )
        self.target_id = target["id"]
        self.target_created = {
            "id": self.target_id,
            "project_id": self.project_alpha,
            "title": SHARED_TITLE,
            "status": "todo",
        }
        self.assertEqual(
            target, self.target_created,
            "夹具准备：task-create 应返回标题为整理 API、状态 todo 的目标任务",
        )

        # 项目二与同标题对照任务（始终保持原标题与 todo）
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        other = self.cli_json(
            "task-create", str(self.project_beta), SHARED_TITLE
        )
        self.other_id = other["id"]
        self.other_unchanged = {
            "id": self.other_id,
            "project_id": self.project_beta,
            "title": SHARED_TITLE,
            "status": "todo",
        }
        self.assertEqual(other, self.other_unchanged)

    # ---------- 预期对象与快照 ----------

    def expected_target(self, title, status):
        """目标任务在各阶段应有的完整对象。"""
        return {
            "id": self.target_id,
            "project_id": self.project_alpha,
            "title": title,
            "status": status,
        }

    def expected_final(self):
        """两个场景共同的最终状态：标题 修复 API、状态 doing、归属不变。"""
        return self.expected_target(NEW_TITLE, "doing")

    def snapshot(self):
        """目标任务对象加两个项目的完整任务列表，用于拒绝前后比对。"""
        return (
            self.show_task(self.target_id),
            self.list_project(self.project_alpha),
            self.list_project(self.project_beta),
        )

    def assert_only_field_changed(self, before, after, changed_field, label):
        """两次成功响应之间只允许 changed_field 一个字段不同。"""
        self.assertEqual(
            {k: v for k, v in before.items() if k != changed_field},
            {k: v for k, v in after.items() if k != changed_field},
            f"{label}：除 {changed_field} 外其余字段必须保留：\n"
            f"之前={before!r}\n之后={after!r}",
        )
        self.assertNotEqual(
            before[changed_field], after[changed_field],
            f"{label}：{changed_field} 应已改变：\n之前={before!r}\n之后={after!r}",
        )

    def assert_final_state(self, final_obj):
        """核对两个场景共同的落库结果与对照任务。"""
        # 独立进程读到与最后一次修改完全一致的保存值
        self.assertEqual(
            self.show_task(self.target_id), final_obj,
            "task-show 应读到与最后一次修改一致的保存值",
        )
        alpha_tasks = self.list_project(self.project_alpha)
        beta_tasks = self.list_project(self.project_beta)
        self.assertEqual(
            alpha_tasks, [final_obj],
            f"项目一应只含最终状态的目标任务：\n实际={alpha_tasks!r}",
        )
        self.assertEqual(
            beta_tasks, [self.other_unchanged],
            f"项目二的同标题对照任务应始终保持原标题与 todo：\n实际={beta_tasks!r}",
        )
        # 任务数量、标识与归属
        all_tasks = alpha_tasks + beta_tasks
        self.assertEqual(len(all_tasks), 2, f"任务总数应始终为两条：\n{all_tasks!r}")
        self.assertEqual(
            sorted(t["id"] for t in all_tasks),
            sorted([self.target_id, self.other_id]),
            "任务标识不得新增或丢失",
        )
        self.assertEqual(
            [(t["id"], t["project_id"]) for t in all_tasks],
            [
                (self.target_id, self.project_alpha),
                (self.other_id, self.project_beta),
            ],
            "任务归属应保持不变",
        )

    def assert_rejected_and_unchanged(self, cli_args, current_value, expected_value):
        """重提原 --from 请求：退出码 2、stdout 空、stderr 说明当前值与预期值，
        拒绝前后目标任务与两个项目的完整任务列表完全一致。返回 stderr。"""
        before = self.snapshot()
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 2、stdout 为空、stderr 说明当前值 "
                f"{current_value!r} 与不匹配的预期值 {expected_value!r}，"
                "且不含 Python 回溯；拒绝前后数据完全一致"
            ),
        )
        self.assertEqual(proc.returncode, 2, f"预期来源已过期，应拒绝（退出码 2）：\n{detail}")
        self.assertEqual(proc.stdout, "", f"拒绝时标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "", f"stderr 应说明不匹配原因：\n{detail}")
        self.assertIn(
            current_value, proc.stderr,
            f"stderr 应说明当前保存值为 {current_value!r}：\n{detail}",
        )
        self.assertIn(
            expected_value, proc.stderr,
            f"stderr 应说明不匹配的 --from 预期值为 {expected_value!r}：\n{detail}",
        )
        self.assertNotIn("Traceback", proc.stderr, f"stderr 不应含异常回溯：\n{detail}")

        after = self.snapshot()
        self.assertEqual(
            after, before,
            f"拒绝前后目标任务与两个项目的完整任务列表必须一致：\n"
            f"拒绝前={before!r}\n拒绝后={after!r}",
        )
        # 已成功保存的标题与状态仍在，对照任务原样
        self.assertEqual(after[0], self.expected_final(), "目标任务保存值应保留")
        self.assertEqual(
            after[2], [self.other_unchanged], "对照任务应保持原标题与 todo"
        )
        return proc.stderr

    # ---------- 场景一：先移动（--from todo）再改名（--from 整理 API） ----------

    def test_move_with_from_then_rename_with_from(self):
        # 至少一个场景使用任务标识的前导零写法（与数值标识等价）
        target_ref = "000" + str(self.target_id)

        # 第一步：确认当前状态为 todo 后移到 doing；标题保持原样
        move_args = ("task-move", target_ref, "doing", "--from", "todo")
        moved = self.parse_single_task(
            self.run_cli(*move_args), move_args,
            self.expected_target(SHARED_TITLE, "doing"),
        )
        self.assert_only_field_changed(
            self.target_created, moved, "status", "移动后"
        )
        # 独立进程确认 doing 已落库、标题未变
        self.assertEqual(self.show_task(self.target_id), moved)

        # 第二步：状态已是 doing，仍按原标题 整理 API 改名；doing 保留
        rename_args = ("task-rename", target_ref, NEW_TITLE_RAW, "--from", SHARED_TITLE)
        renamed = self.parse_single_task(
            self.run_cli(*rename_args), rename_args,
            self.expected_target(NEW_TITLE, "doing"),
        )
        self.assert_only_field_changed(moved, renamed, "title", "改名后")
        self.assert_final_state(renamed)

        # 场景完成后原样重提两次成功请求：目标值已等于当前值、预期仍是旧值，
        # 均应拒绝且不改变任何数据
        self.assert_rejected_and_unchanged(move_args, "doing", "todo")
        self.assert_rejected_and_unchanged(rename_args, NEW_TITLE, SHARED_TITLE)

        # 两次拒绝后最终保存值仍可读回
        self.assertEqual(self.show_task(self.target_id), self.expected_final())

    # ---------- 场景二：先改名（--from 整理 API）再移动（--from todo） ----------

    def test_rename_with_from_then_move_with_from(self):
        target_ref = str(self.target_id)

        # 第一步：确认原标题为 整理 API 后改名；状态保持 todo
        rename_args = ("task-rename", target_ref, NEW_TITLE_RAW, "--from", SHARED_TITLE)
        renamed = self.parse_single_task(
            self.run_cli(*rename_args), rename_args,
            self.expected_target(NEW_TITLE, "todo"),
        )
        self.assert_only_field_changed(
            self.target_created, renamed, "title", "改名后"
        )
        # 独立进程确认新标题已落库、状态未变
        self.assertEqual(self.show_task(self.target_id), renamed)

        # 第二步：标题已是 修复 API，仍按原状态 todo 移动到 doing；新标题保留
        move_args = ("task-move", target_ref, "doing", "--from", "todo")
        moved = self.parse_single_task(
            self.run_cli(*move_args), move_args,
            self.expected_target(NEW_TITLE, "doing"),
        )
        self.assert_only_field_changed(renamed, moved, "status", "移动后")
        self.assert_final_state(moved)

        # 与场景一相同的最终结果；重提原请求均被拒绝、数据不变
        self.assert_rejected_and_unchanged(rename_args, NEW_TITLE, SHARED_TITLE)
        self.assert_rejected_and_unchanged(move_args, "doing", "todo")

        self.assertEqual(self.show_task(self.target_id), self.expected_final())


if __name__ == "__main__":
    unittest.main(verbosity=2)

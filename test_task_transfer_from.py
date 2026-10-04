#!/usr/bin/env python3
"""task-transfer --from 预期来源项目校验的命令行回归（验收）测试。

覆盖：

- 用户验收场景：项目甲有 doing 的目标任务 T 和 todo 的对照任务 S，项目乙有
  一条与 T 同标题的 todo 任务 O；``task-transfer T 乙 --from 甲`` 成功
  （退出码 0、stderr 为空、stdout 只有与 task-show 同结构的单个任务 JSON，
  仅 project_id 变为乙，标识/标题/doing 状态保留），紧接着重复同一请求被
  拒绝（退出码 2、stdout 为空、stderr 同时指出当前乙与预期甲的标识且无
  Python 回溯）；拒绝前后 task-show、两项目 task-list 与 project-stats
  完全一致，T 仍在乙且为 doing，S、O 未变，任务总数始终为三。
- 边界：任务、目标项目与来源项目标识带前导零时按数值判断；目标就是当前
  所属项目且来源匹配时成功返回原任务、不新增记录，来源不匹配时仍退出码 2。
- 来源标识合法但不存在时按归属不匹配拒绝，不创建项目。
- ``--from`` 缺值、零（含全零）、非数字（含负数、空字符串）或
  9223372036854775808（超出 SQLite 有符号 64 位整数上限）均退出码 2、
  stdout 为空、stderr 说明对应原因且无回溯，已有项目与任务不变。
- 省略 ``--from`` 时仍允许原有的直接转移。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，不直接读写
数据库，不调用产品内部函数，不依赖网络或任何预先保存的项目。每个用例在
独立临时目录中使用全新的隔离 SQLite 数据库，结束后自动清理，不接触使用者
自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_transfer_from.py
    python3 -m unittest test_task_transfer_from -v
    python3 test_task_transfer_from.py \\
        TaskTransferFromRegression.test_acceptance_transfer_with_from_then_repeat_conflicts

退出码：全部通过为 0，存在失败为 1。不依赖 JSON 键顺序或错误文案逐字一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TASK_FIELDS = {"id", "project_id", "title", "status"}

# 目标任务 T 与项目乙中的 O 共用标题；对照任务 S 使用另一标题
SHARED_TITLE = "Sync design doc"
CONTROL_TITLE = "control task"

# SQLite 有符号 64 位整数上限，上限 + 1 用于越界用例
SQLITE_OVERFLOW = "9223372036854775808"


class TaskTransferFromRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-transfer-from-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")
        self._build_fixture()

    def tearDown(self):
        self._tmpdir.cleanup()

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目甲：目标任务 T（随后置为 doing）与 todo 的对照任务 S
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        target = self.cli_json(
            "task-create", str(self.project_alpha), SHARED_TITLE
        )
        control = self.cli_json(
            "task-create", str(self.project_alpha), CONTROL_TITLE
        )
        self.target_id = target["id"]
        self.control_id = control["id"]
        # 项目乙：一条与 T 同标题的 todo 任务 O
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        other = self.cli_json(
            "task-create", str(self.project_beta), SHARED_TITLE
        )
        self.other_id = other["id"]
        # T 置为 doing；S、O 保持 todo
        doing_obj = self.cli_json(
            "task-move", str(self.target_id), "doing"
        )
        self.assertEqual(
            doing_obj,
            {"id": self.target_id, "project_id": self.project_alpha,
             "title": SHARED_TITLE, "status": "doing"},
        )

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
        """准备数据用：要求成功并返回解析后的单个 JSON 值。"""
        proc = self.run_cli(*cli_args)
        detail = self.detail(cli_args, proc, expected="准备数据成功")
        self.assertEqual(proc.returncode, 0, f"准备数据应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"准备数据时 stderr 应为空：\n{detail}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"准备数据的 stdout 不是合法 JSON：\n{detail}")

    def detail(self, cli_args, proc, expected=None):
        return "\n".join([
            f"输入参数={list(cli_args)!r}",
            f"实际退出码={proc.returncode}",
            f"实际标准输出={proc.stdout!r}",
            f"实际标准错误={proc.stderr!r}",
            f"预期结果={expected!r}",
        ])

    def show_task(self, task_id):
        proc = self.run_cli("task-show", str(task_id))
        detail = self.detail(
            ("task-show", str(task_id)), proc,
            expected="退出码 0、stderr 为空、stdout 为单个 JSON 任务对象",
        )
        self.assertEqual(proc.returncode, 0, f"task-show 应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"task-show 时 stderr 应为空：\n{detail}")
        data = json.loads(proc.stdout)
        self.assertIsInstance(data, dict, f"task-show 应返回 JSON 对象：\n{detail}")
        return data

    def list_project(self, project_id):
        proc = self.run_cli("task-list", str(project_id))
        detail = self.detail(
            ("task-list", str(project_id)), proc,
            expected="退出码 0、stderr 为空、stdout 为 JSON 数组",
        )
        self.assertEqual(proc.returncode, 0, f"task-list 应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"task-list 时 stderr 应为空：\n{detail}")
        data = json.loads(proc.stdout)
        self.assertIsInstance(data, list, f"task-list 应返回 JSON 数组：\n{detail}")
        return data

    def stats(self, project_id):
        proc = self.run_cli("project-stats", str(project_id))
        detail = self.detail(
            ("project-stats", str(project_id)), proc,
            expected="退出码 0、stderr 为空、stdout 为统计 JSON 对象",
        )
        self.assertEqual(proc.returncode, 0, f"project-stats 应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"project-stats 时 stderr 应为空：\n{detail}")
        return json.loads(proc.stdout)

    def project_names(self):
        proc = self.run_cli("project-list")
        detail = self.detail(("project-list",), proc, expected="退出码 0")
        self.assertEqual(proc.returncode, 0, f"project-list 应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"project-list 时 stderr 应为空：\n{detail}")
        return json.loads(proc.stdout)

    def snapshot(self):
        """记录 task-show、两项目列表、统计与项目名单的完整快照。"""
        return {
            "target_show": self.show_task(self.target_id),
            "control_show": self.show_task(self.control_id),
            "other_show": self.show_task(self.other_id),
            "alpha": self.list_project(self.project_alpha),
            "beta": self.list_project(self.project_beta),
            "alpha_stats": self.stats(self.project_alpha),
            "beta_stats": self.stats(self.project_beta),
            "projects": self.project_names(),
        }

    def task_obj(self, task_id, project_id, title, status):
        return {"id": task_id, "project_id": project_id,
                "title": title, "status": status}

    def expected_target(self, project_id, status="doing"):
        return self.task_obj(
            self.target_id, project_id, SHARED_TITLE, status
        )

    # ---------- 统一断言 ----------

    def assert_transfer_success(self, cli_args, expected_obj):
        """转移成功协议：rc0、stderr 空、stdout 为与 task-show 同结构对象。

        成功结果必须与随后的独立 task-show 查询完全一致，返回该对象。
        """
        proc = self.run_cli(*cli_args)
        detail = self.detail(
            cli_args, proc,
            expected=(
                "退出码 0、stderr 为空、stdout 只有一个 JSON 任务对象，"
                f"对象为 {expected_obj!r}，且与随后 task-show 查询一致"
            ),
        )
        self.assertEqual(proc.returncode, 0, f"转移应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时 stderr 应为空：\n{detail}")
        try:
            obj = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"成功输出应为合法 JSON：\n{detail}")
        self.assertIsInstance(
            obj, dict, f"成功输出应为单个 JSON 对象而非数组：\n{detail}"
        )
        self.assertEqual(
            set(obj), TASK_FIELDS, f"对象字段结构应与 task-show 一致：\n{detail}"
        )
        self.assertEqual(obj, expected_obj, f"任务对象内容不符：\n{detail}")
        shown = self.show_task(self.target_id)
        self.assertEqual(
            shown, obj,
            f"成功结果应与独立 task-show 查询一致（结果已落库）：\n{detail}\n"
            f"task-show 所得={shown!r}",
        )
        return obj

    def assert_rejected(self, cli_args, current_project=None,
                        expected_project=None):
        """拒绝协议：rc2、stdout 空、stderr 非空无回溯；返回失败详情。"""
        proc = self.run_cli(*cli_args)
        detail = self.detail(
            cli_args, proc,
            expected=(
                "退出码 2、stdout 为空、stderr 说明原因且无 Python 回溯"
                + (
                    f"，当前项目 {current_project}、预期来源 {expected_project}"
                    if current_project is not None else ""
                )
            ),
        )
        self.assertEqual(proc.returncode, 2, f"应退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"拒绝时 stdout 应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "", f"stderr 应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr, f"不应出现 Python 回溯：\n{detail}")
        if current_project is not None:
            self.assertIn(
                str(current_project), proc.stderr,
                f"stderr 应指出当前所属项目标识 {current_project}：\n{detail}",
            )
        if expected_project is not None:
            self.assertIn(
                str(expected_project), proc.stderr,
                f"stderr 应指出 --from 预期来源标识 {expected_project}：\n{detail}",
            )
        return proc, detail

    # ---------- 用户验收场景 ----------

    def test_acceptance_transfer_with_from_then_repeat_conflicts(self):
        t, s, o = self.target_id, self.control_id, self.other_id
        alpha, beta = self.project_alpha, self.project_beta

        # 第一次：T 当前属于甲，与 --from 甲按数值相等，转到乙
        cli_args = ("task-transfer", str(t), str(beta), "--from", str(alpha))
        moved = self.assert_transfer_success(
            cli_args, self.expected_target(beta, "doing")
        )

        # 落库核对：甲只剩对照 S（todo 未变），乙包含 T(doing) 与 O(todo)
        alpha_list = self.list_project(alpha)
        beta_list = self.list_project(beta)
        self.assertEqual(
            alpha_list,
            [self.task_obj(s, alpha, CONTROL_TITLE, "todo")],
            f"来源项目应只剩对照任务 S：\n转移结果={moved!r}\n实际={alpha_list!r}",
        )
        self.assertEqual(
            beta_list,
            [
                self.task_obj(t, beta, SHARED_TITLE, "doing"),
                self.task_obj(o, beta, SHARED_TITLE, "todo"),
            ],
            f"目标项目应包含 T 与 O：\n实际={beta_list!r}",
        )
        self.assertEqual(
            [obj["id"] for obj in beta_list],
            sorted(obj["id"] for obj in beta_list),
            "目标项目列表应按标识升序",
        )

        # 第二次请求前记录完整快照；重复同一请求应因归属不匹配被拒绝
        before_reject = self.snapshot()
        proc, detail = self.assert_rejected(
            cli_args, current_project=beta, expected_project=alpha
        )

        # 拒绝前后：task-show、两项目 task-list、project-stats 完全一致
        after_reject = self.snapshot()
        self.assertEqual(
            after_reject, before_reject,
            f"拒绝前后查询结果必须完全一致：\n{detail}\n"
            f"拒绝前={before_reject!r}\n拒绝后={after_reject!r}",
        )

        # T 仍在乙且为 doing，对照 S 与 O 未变，任务总数仍为三条
        self.assertEqual(
            self.show_task(t), self.expected_target(beta, "doing"),
            f"被拒绝后 T 应仍在乙且为 doing：\n{detail}",
        )
        self.assertEqual(
            self.show_task(s),
            self.task_obj(s, alpha, CONTROL_TITLE, "todo"),
        )
        self.assertEqual(
            self.show_task(o),
            self.task_obj(o, beta, SHARED_TITLE, "todo"),
        )
        self.assertEqual(
            self.stats(alpha),
            {"project_id": alpha, "total": 1,
             "todo": 1, "doing": 0, "done": 0},
        )
        self.assertEqual(
            self.stats(beta),
            {"project_id": beta, "total": 2,
             "todo": 1, "doing": 1, "done": 0},
        )
        self.assertEqual(
            len(self.list_project(alpha)) + len(self.list_project(beta)), 3
        )

    # ---------- 前导零：任务/目标项目/来源项目均按数值判断 ----------

    def test_leading_zero_ids_evaluated_numerically(self):
        t, alpha, beta = self.target_id, self.project_alpha, self.project_beta
        # 夹具标识恰为 1/2/1；全部使用前导零写法，应与数值写法等价
        self.assertEqual((t, alpha, beta), (1, 1, 2))

        cli_args = ("task-transfer", "0001", "0002", "--from", "0001")
        self.assert_transfer_success(
            cli_args, self.expected_target(beta, "doing")
        )
        # 重复同样的前导零请求：当前为乙(2)、预期甲(1)，拒绝且无回溯
        proc, detail = self.assert_rejected(
            cli_args, current_project=beta, expected_project=alpha
        )
        # 拒绝不写库：T 仍在乙 doing，甲只剩 S、乙为 T 与 O
        self.assertEqual(self.show_task(t), self.expected_target(beta, "doing"))
        self.assertEqual(
            (len(self.list_project(alpha)), len(self.list_project(beta))),
            (1, 2),
            f"拒绝前后任务分布不得变化：\n{detail}",
        )

    # ---------- 目标就是当前项目 ----------

    def test_same_project_with_matching_from_is_noop_success(self):
        # T 仍在甲；目标就是当前项目且 --from 与当前归属一致：
        # 成功返回原任务，不新增记录（前导零写法同样按数值判断）
        before = self.snapshot()
        cli_args = ("task-transfer", "0001", "0001", "--from", "0001")
        self.assert_transfer_success(
            cli_args, self.expected_target(self.project_alpha, "doing")
        )
        after = self.snapshot()
        self.assertEqual(
            after, before,
            "原地转移且来源匹配时不应新增或改动任何记录：\n"
            f"转移前={before!r}\n转移后={after!r}",
        )
        self.assertEqual(
            len(self.list_project(self.project_alpha))
            + len(self.list_project(self.project_beta)),
            3,
        )

    def test_same_project_with_mismatched_from_is_rejected(self):
        # 目标就是当前项目甲，但 --from 指向乙：仍按归属不匹配拒绝
        t, alpha, beta = self.target_id, self.project_alpha, self.project_beta
        before = self.snapshot()
        cli_args = ("task-transfer", str(t), str(alpha), "--from", str(beta))
        _, detail = self.assert_rejected(
            cli_args, current_project=alpha, expected_project=beta
        )
        after = self.snapshot()
        self.assertEqual(
            after, before,
            f"拒绝前后数据必须完全一致：\n{detail}\n"
            f"拒绝前={before!r}\n拒绝后={after!r}",
        )
        self.assertEqual(self.show_task(t), self.expected_target(alpha, "doing"))

    # ---------- 来源标识合法但不存在 ----------

    def test_nonexistent_from_rejected_as_ownership_mismatch(self):
        # 999 是合法正整数但并无此项目：按归属不匹配拒绝，不创建项目
        t, alpha, beta = self.target_id, self.project_alpha, self.project_beta
        missing = 999
        before = self.snapshot()
        cli_args = ("task-transfer", str(t), str(beta), "--from", str(missing))
        _, detail = self.assert_rejected(
            cli_args, current_project=alpha, expected_project=missing
        )
        after = self.snapshot()
        self.assertEqual(
            after, before,
            f"拒绝前后数据必须完全一致：\n{detail}\n"
            f"拒绝前={before!r}\n拒绝后={after!r}",
        )
        # 不创建来源项目，也不创建任何新项目：仍是甲、乙两个
        self.assertEqual(
            self.project_names(),
            [{"id": alpha, "name": "alpha"}, {"id": beta, "name": "beta"}],
            f"来源项目不存在不得导致创建项目：\n{detail}",
        )
        self.assertEqual(self.show_task(t), self.expected_target(alpha, "doing"))

    # ---------- --from 参数校验 ----------

    def test_invalid_from_values_rejected_and_leave_data(self):
        # (--from 之后的参数片段, stderr 应含的原因提示)
        cases = [
            ((), "--from"),              # --from 缺值
            (("0",), "positive integer"),
            (("000",), "positive integer"),
            (("abc",), "positive integer"),
            (("2x",), "positive integer"),
            (("-3",), "positive integer"),
            (("",), "positive integer"),
            ((SQLITE_OVERFLOW,), "range"),
        ]
        t, alpha, beta = self.target_id, self.project_alpha, self.project_beta
        for from_fragment, hint in cases:
            with self.subTest(from_value=from_fragment):
                before = self.snapshot()
                cli_args = ("task-transfer", str(t), str(beta), "--from",
                            *from_fragment)
                proc, detail = self.assert_rejected(cli_args)
                self.assertIn(
                    hint, proc.stderr.lower(),
                    f"stderr 应说明对应原因（含 {hint!r}）：\n{detail}",
                )
                after = self.snapshot()
                self.assertEqual(
                    after, before,
                    f"非法 --from 被拒绝后数据不得变化：\n{detail}\n"
                    f"拒绝前={before!r}\n拒绝后={after!r}",
                )
        # 全部拒绝后：T 仍在甲 doing，项目仍只有甲、乙，任务总数三条
        self.assertEqual(self.show_task(t), self.expected_target(alpha, "doing"))
        self.assertEqual(
            [p["id"] for p in self.project_names()], [alpha, beta]
        )
        self.assertEqual(
            len(self.list_project(alpha)) + len(self.list_project(beta)), 3
        )

    # ---------- 省略 --from 时保留原有直接转移语义 ----------

    def test_without_from_still_transfers_directly(self):
        t, s, o = self.target_id, self.control_id, self.other_id
        alpha, beta = self.project_alpha, self.project_beta
        cli_args = ("task-transfer", str(t), str(beta))
        self.assert_transfer_success(
            cli_args, self.expected_target(beta, "doing")
        )
        # 省略 --from 同样只改所属项目：对照任务不变，不复制任务
        self.assertEqual(
            self.list_project(alpha),
            [self.task_obj(s, alpha, CONTROL_TITLE, "todo")],
        )
        self.assertEqual(
            self.list_project(beta),
            [
                self.task_obj(t, beta, SHARED_TITLE, "doing"),
                self.task_obj(o, beta, SHARED_TITLE, "todo"),
            ],
        )
        self.assertEqual(
            self.stats(alpha),
            {"project_id": alpha, "total": 1,
             "todo": 1, "doing": 0, "done": 0},
        )
        self.assertEqual(
            self.stats(beta),
            {"project_id": beta, "total": 2,
             "todo": 1, "doing": 1, "done": 0},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

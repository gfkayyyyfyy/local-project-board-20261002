#!/usr/bin/env python3
"""task-transfer --from 预期来源项目的命令行回归（验收）测试。

覆盖：

- 用户验收场景：项目甲 alpha 有同标题的目标任务 T（doing）与对照任务 S
  （todo），项目乙 beta 有一条与 T 同标题的 todo 任务 U；
  ``task-transfer T beta --from alpha`` 成功（退出码 0、stderr 为空、
  stdout 为与 task-show 同结构的单个任务 JSON，仅 project_id 改为乙，
  标识、标题、doing 状态原样保留），紧接着重复同一请求退出码 2、stdout
  为空、stderr 同时指出当前所属乙与预期来源甲的标识且无 Python 回溯；
  独立 task-show、两项目 task-list、project-stats 查询确认拒绝前后结果
  完全一致：T 仍在乙且为 doing，S、U 未变，任务总数始终为三。
- 任务、目标项目与来源项目标识带前导零时按数值判断（``0001`` 与 ``1``
  等价）。
- 目标就是当前所属项目且来源匹配时成功返回原任务、不新增记录；来源
  不匹配时仍按退出码 2 拒绝。
- 来源标识合法（正整数、在范围内）但项目不存在时按归属不匹配拒绝，
  不创建项目、不移动任务。
- ``--from`` 缺值、零（含全零）、非数字、``9223372036854775808``
  越界均退出码 2、stdout 为空、stderr 说明对应原因，已有项目与任务不变。
- 省略 ``--from`` 时仍允许原有的直接转移（不检查来源）。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，不直接读写
数据库，不调用产品内部函数，不依赖网络或任何预先保存的项目。每个用例
在独立临时目录中使用全新的隔离 SQLite 数据库，结束后自动清理，可单独
执行、可重复执行且结果一致，不会接触使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_transfer_from.py
    python3 -m unittest test_task_transfer_from -v
    python3 test_task_transfer_from.py \
        TaskTransferFromRegression.test_acceptance_transfer_then_repeat_conflicts

退出码：全部通过为 0，存在失败为 1。不依赖 JSON 键顺序或错误文案逐字
一致（stderr 中的标识以是否出现对应十进制数字判断）。
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TASK_FIELDS = {"id", "project_id", "title", "status"}

# 三条任务共用的标题，用于确认同标题任务不会被连带改动
SHARED_TITLE = "整理 API"

# SQLite 有符号 64 位整数上限
SQLITE_MAX_INT_PLUS_ONE = "9223372036854775808"

# 表示 --from 缺值（命令行中 --from 后不再跟值）
MISSING_FROM_VALUE = object()


class TaskTransferFromRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-transfer-from-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")

        # 项目甲 alpha（标识 1）：先建目标任务 T（标识 1），再建同标题的
        # 对照任务 S（标识 2，保持 todo）
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        target = self.cli_json(
            "task-create", str(self.project_alpha), SHARED_TITLE
        )
        sibling = self.cli_json(
            "task-create", str(self.project_alpha), SHARED_TITLE
        )
        self.target_id = target["id"]
        self.sibling_id = sibling["id"]

        # 项目乙 beta（标识 2）：一条与目标同标题的 todo 任务 U（标识 3）
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        other = self.cli_json(
            "task-create", str(self.project_beta), SHARED_TITLE
        )
        self.other_id = other["id"]

        self.assertEqual([self.project_alpha, self.project_beta], [1, 2])
        self.assertEqual([self.target_id, self.sibling_id, self.other_id],
                         [1, 2, 3])

        # 目标任务置为 doing；S、U 保持 todo
        proc = self.run_cli("task-move", str(self.target_id), "doing")
        self.assertEqual(proc.returncode, 0, self.detail(
            ("task-move", str(self.target_id), "doing"), proc,
            expected="准备目标任务为 doing",
        ))

    def tearDown(self):
        self._tmpdir.cleanup()

    # ---------- 命令行与解析辅助 ----------

    def run_cli(self, *cli_args, db_path=None):
        """执行 python -m kanban --db <临时库> ...，返回 CompletedProcess。"""
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
        """组装失败定位信息：输入参数、退出码、两路输出与预期差异。"""
        return "\n".join([
            f"输入参数={list(cli_args)!r}",
            f"实际退出码={proc.returncode}",
            f"实际标准输出={proc.stdout!r}",
            f"实际标准错误={proc.stderr!r}",
            f"预期结果={expected!r}",
        ])

    def show_task(self, task_id):
        """通过公开命令按标识读取单条任务，返回任务对象。"""
        proc = self.run_cli("task-show", str(task_id))
        detail = self.detail(
            ("task-show", str(task_id)), proc,
            expected="退出码 0、stderr 为空、stdout 为单个 JSON 任务对象",
        )
        self.assertEqual(proc.returncode, 0, f"task-show 应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"task-show 时 stderr 应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"task-show 的 stdout 不是合法 JSON：\n{detail}")
        self.assertIsInstance(
            data, dict, f"task-show 结果应为单个 JSON 对象：\n{detail}",
        )
        return data

    def list_project(self, project_id):
        """通过公开命令查询某项目的全部任务，返回任务对象列表。"""
        proc = self.run_cli("task-list", str(project_id))
        detail = self.detail(
            ("task-list", str(project_id)), proc,
            expected="退出码 0、stderr 为空、stdout 为 JSON 数组",
        )
        self.assertEqual(proc.returncode, 0, f"查询应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"查询时 stderr 应为空：\n{detail}")
        data = json.loads(proc.stdout)
        self.assertIsInstance(data, list, f"查询结果应为 JSON 数组：\n{detail}")
        return data

    def stats(self, project_id):
        """通过公开命令查询某项目的状态统计。"""
        proc = self.run_cli("project-stats", str(project_id))
        detail = self.detail(
            ("project-stats", str(project_id)), proc,
            expected="退出码 0、stderr 为空、stdout 为统计 JSON 对象",
        )
        self.assertEqual(proc.returncode, 0, f"统计查询应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"统计查询时 stderr 应为空：\n{detail}")
        return json.loads(proc.stdout)

    def project_names(self):
        """通过公开命令查询全部项目（按标识升序的 {id, name} 列表）。"""
        proc = self.run_cli("project-list")
        detail = self.detail(("project-list",), proc, expected="退出码 0")
        self.assertEqual(proc.returncode, 0, f"项目查询应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"项目查询时 stderr 应为空：\n{detail}")
        return json.loads(proc.stdout)

    def snapshot(self):
        """记录三条任务的 task-show、两项目列表、统计与项目清单的完整快照。"""
        return {
            "target_show": self.show_task(self.target_id),
            "sibling_show": self.show_task(self.sibling_id),
            "other_show": self.show_task(self.other_id),
            "alpha_list": self.list_project(self.project_alpha),
            "beta_list": self.list_project(self.project_beta),
            "alpha_stats": self.stats(self.project_alpha),
            "beta_stats": self.stats(self.project_beta),
            "projects": self.project_names(),
        }

    def task_obj(self, task_id, project_id, status):
        return {"id": task_id, "project_id": project_id,
                "title": SHARED_TITLE, "status": status}

    def assert_transfer_success(self, proc, cli_args, expected_obj):
        """成功协议：退出码 0、stderr 为空、stdout 为与 task-show 同结构对象。"""
        detail = self.detail(
            cli_args, proc,
            expected=(
                "退出码 0、stderr 为空、stdout 只有一个 JSON 任务对象，"
                f"对象为 {expected_obj!r}（字段集合 {sorted(TASK_FIELDS)}），"
                "且独立 task-show 查询得到完全相同的对象"
            ),
        )
        self.assertEqual(proc.returncode, 0, f"转移应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功转移时 stderr 应为空：\n{detail}")
        try:
            moved = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"转移结果的 stdout 不是合法 JSON：\n{detail}")
        self.assertIsInstance(
            moved, dict, f"转移结果应为单个 JSON 对象而非数组：\n{detail}",
        )
        self.assertEqual(
            set(moved), TASK_FIELDS,
            f"转移结果对象字段结构应与 task-show 一致：\n{detail}",
        )
        self.assertEqual(moved, expected_obj, f"转移结果对象内容不符：\n{detail}")
        # 成功结果必须与随后的独立查询一致（结果已落库）
        self.assertEqual(
            self.show_task(expected_obj["id"]), moved,
            f"转移后的 task-show 应与转移响应完全一致：\n{detail}",
        )
        return moved

    def assert_rejected(self, proc, cli_args, current_project=None,
                        expected_project=None):
        """归属不匹配/非法参数的统一拒绝协议：rc2、stdout 空、stderr 说明原因。

        current_project / expected_project 给出时，stderr 中必须能找到这两个
        标识的十进制写法（不依赖错误文案的逐字拼写）。
        """
        detail = self.detail(
            cli_args, proc,
            expected=(
                "退出码 2、stdout 为空、stderr 说明原因且无 Python 回溯"
                + (f"，并指出来源标识当前 {current_project}、预期 "
                   f"{expected_project}" if current_project is not None else "")
            ),
        )
        self.assertEqual(proc.returncode, 2, f"应退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"失败时 stdout 应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "", f"stderr 应说明原因：\n{detail}",
        )
        self.assertNotIn(
            "Traceback", proc.stderr, f"不应出现 Python 回溯：\n{detail}",
        )
        if current_project is not None:
            numbers = set(re.findall(r"[0-9]+", proc.stderr))
            self.assertIn(
                str(current_project), numbers,
                f"stderr 应指出任务当前所属项目标识 {current_project}：\n{detail}",
            )
            self.assertIn(
                str(expected_project), numbers,
                f"stderr 应指出 --from 预期的来源项目标识 "
                f"{expected_project}：\n{detail}",
            )
        return detail

    def transfer_args(self, task_arg, project_arg, from_value):
        """组装 task-transfer 参数；from_value 为 MISSING_FROM_VALUE 时缺值。"""
        args = ["task-transfer", str(task_arg), str(project_arg)]
        if from_value is not MISSING_FROM_VALUE:
            args.extend(["--from", str(from_value)])
        else:
            args.append("--from")
        return tuple(args)

    # ---------- 用户验收场景 ----------

    def test_acceptance_transfer_then_repeat_conflicts(self):
        t, s, u = self.target_id, self.sibling_id, self.other_id
        alpha, beta = self.project_alpha, self.project_beta

        # 第一次：T 当前属于甲，与 --from 甲相符，转入乙
        cli_args = ("task-transfer", str(t), str(beta), "--from", str(alpha))
        proc = self.run_cli(*cli_args)
        moved = self.assert_transfer_success(
            proc, cli_args,
            self.task_obj(t, beta, "doing"),
        )

        # 目标项目乙的列表应包含转移结果，且与响应一致、按标识升序
        beta_list = self.list_project(beta)
        self.assertEqual(
            [obj for obj in beta_list if obj["id"] == t], [moved],
            f"乙的列表应恰好包含转移后的目标任务：\n{self.detail(cli_args, proc)}",
        )
        self.assertEqual(
            [obj["id"] for obj in beta_list],
            sorted(obj["id"] for obj in beta_list),
        )
        # 来源项目甲的列表不再包含目标
        self.assertEqual(
            [obj for obj in self.list_project(alpha) if obj["id"] == t], [],
        )

        # 紧接着重复同一请求：T 当前属于乙，与预期甲不符，拒绝
        repeat_proc = self.run_cli(*cli_args)
        detail = self.assert_rejected(
            repeat_proc, cli_args,
            current_project=beta, expected_project=alpha,
        )

        # 拒绝前后的 task-show、两项目 task-list、project-stats 完全一致。
        # 先以拒绝后的查询重建“成功后、拒绝前”的期望快照。
        expected_snapshot = {
            "target_show": self.task_obj(t, beta, "doing"),
            "sibling_show": self.task_obj(s, alpha, "todo"),
            "other_show": self.task_obj(u, beta, "todo"),
            "alpha_list": [self.task_obj(s, alpha, "todo")],
            "beta_list": [
                self.task_obj(t, beta, "doing"),
                self.task_obj(u, beta, "todo"),
            ],
            "alpha_stats": {"project_id": alpha, "total": 1,
                            "todo": 1, "doing": 0, "done": 0},
            "beta_stats": {"project_id": beta, "total": 2,
                           "todo": 1, "doing": 1, "done": 0},
            "projects": [{"id": alpha, "name": "alpha"},
                         {"id": beta, "name": "beta"}],
        }
        after_reject = self.snapshot()
        self.assertEqual(
            after_reject, expected_snapshot,
            f"拒绝前后查询结果应完全一致：\n{detail}\n"
            f"实际快照={after_reject!r}",
        )

        # 目标仍在乙且为 doing；对照任务 S、U 未变
        self.assertEqual(self.show_task(t), self.task_obj(t, beta, "doing"))
        self.assertEqual(self.show_task(s), self.task_obj(s, alpha, "todo"))
        self.assertEqual(self.show_task(u), self.task_obj(u, beta, "todo"))
        # 两项目统计
        self.assertEqual(self.stats(alpha), expected_snapshot["alpha_stats"])
        self.assertEqual(self.stats(beta), expected_snapshot["beta_stats"])
        # 任务总数仍为三条（不复制、不新增）
        self.assertEqual(
            len(self.list_project(alpha)) + len(self.list_project(beta)), 3,
        )
        self.assertEqual(self.project_names(), expected_snapshot["projects"])

    # ---------- 前导零按数值判断 ----------

    def test_leading_zeros_compared_numerically(self):
        t, alpha, beta = self.target_id, self.project_alpha, self.project_beta

        # 任务、目标项目、来源项目均带前导零：按数值解析与比较
        cli_args = ("task-transfer", "0001", "0002", "--from", "0001")
        proc = self.run_cli(*cli_args)
        self.assert_transfer_success(
            proc, cli_args, self.task_obj(t, beta, "doing"),
        )

        # 重复同一请求：当前乙(2) 与预期甲(1) 不符，拒绝；
        # 错误信息中的标识按数值（1、2）出现
        repeat = self.run_cli(*cli_args)
        self.assert_rejected(
            repeat, cli_args, current_project=beta, expected_project=alpha,
        )

        # 拒绝后状态与成功时一致
        self.assertEqual(self.show_task(t), self.task_obj(t, beta, "doing"))
        self.assertEqual(
            len(self.list_project(alpha)) + len(self.list_project(beta)), 3,
        )

    # ---------- 目标就是当前项目 ----------

    def test_same_project_match_returns_same_task_without_new_record(self):
        t, alpha = self.target_id, self.project_alpha
        before = self.snapshot()

        # 目标就是当前项目且来源匹配：成功返回原任务，不新增记录。
        # 来源用前导零写法，按数值与当前所属项目比较。
        for from_value in (str(alpha), "0001"):
            with self.subTest(from_value=from_value):
                cli_args = ("task-transfer", str(t), str(alpha),
                            "--from", from_value)
                proc = self.run_cli(*cli_args)
                self.assert_transfer_success(
                    proc, cli_args, self.task_obj(t, alpha, "doing"),
                )

        # 两项目的任务、统计、项目清单与操作前完全一致（没有写入或新增）
        self.assertEqual(
            self.snapshot(), before,
            "原地转移成功后不应产生任何可观察变化",
        )
        self.assertEqual(
            self.stats(alpha),
            {"project_id": alpha, "total": 2,
             "todo": 1, "doing": 1, "done": 0},
        )

    def test_same_project_mismatch_still_rejected(self):
        t, alpha, beta = self.target_id, self.project_alpha, self.project_beta
        before = self.snapshot()

        # 目标就是当前项目（甲），但 --from 写成乙：来源不匹配，拒绝
        cli_args = ("task-transfer", str(t), str(alpha),
                    "--from", str(beta))
        proc = self.run_cli(*cli_args)
        detail = self.assert_rejected(
            proc, cli_args, current_project=alpha, expected_project=beta,
        )
        self.assertEqual(
            self.snapshot(), before,
            f"拒绝后数据不得变化：\n{detail}",
        )
        # 目标仍在甲且为 doing，统计不变
        self.assertEqual(self.show_task(t), self.task_obj(t, alpha, "doing"))
        self.assertEqual(
            self.stats(alpha),
            {"project_id": alpha, "total": 2,
             "todo": 1, "doing": 1, "done": 0},
        )

    # ---------- 来源合法但不存在 / 一般归属不匹配 ----------

    def test_nonexistent_source_rejected_as_ownership_mismatch(self):
        t, alpha, beta = self.target_id, self.project_alpha, self.project_beta
        before = self.snapshot()

        # 来源标识 999 合法（正整数、在范围内）但项目不存在：按归属不匹配
        # 拒绝，不创建项目；前导零写法 00999 与 999 等价
        for from_value in ("999", "00999"):
            with self.subTest(from_value=from_value):
                cli_args = ("task-transfer", str(t), str(beta),
                            "--from", from_value)
                proc = self.run_cli(*cli_args)
                detail = self.assert_rejected(
                    proc, cli_args,
                    current_project=alpha, expected_project=999,
                )
                self.assertEqual(
                    self.snapshot(), before,
                    f"拒绝后数据不得变化：\n{detail}",
                )

        # 没有创建标识 999 的项目，项目清单仍为甲、乙两个
        self.assertEqual(
            self.project_names(),
            [{"id": alpha, "name": "alpha"},
             {"id": beta, "name": "beta"}],
        )
        # 目标仍在甲，乙仍只有 U 一条任务
        self.assertEqual(self.show_task(t), self.task_obj(t, alpha, "doing"))
        self.assertEqual(
            len(self.list_project(beta)), 1,
        )

    def test_source_mismatch_other_project_leaves_everything(self):
        t, s, u = self.target_id, self.sibling_id, self.other_id
        alpha, beta = self.project_alpha, self.project_beta
        before = self.snapshot()

        # 目标 T 当前属于甲，却以乙作为预期来源：拒绝
        cli_args = ("task-transfer", str(t), str(beta), "--from", str(beta))
        proc = self.run_cli(*cli_args)
        detail = self.assert_rejected(
            proc, cli_args, current_project=alpha, expected_project=beta,
        )
        self.assertEqual(
            self.snapshot(), before,
            f"拒绝后数据不得变化：\n{detail}\n拒绝前={before!r}",
        )
        self.assertEqual(self.show_task(t), self.task_obj(t, alpha, "doing"))
        self.assertEqual(self.show_task(s), self.task_obj(s, alpha, "todo"))
        self.assertEqual(self.show_task(u), self.task_obj(u, beta, "todo"))

    # ---------- --from 取值非法 ----------

    def test_invalid_from_values(self):
        t, beta = self.target_id, self.project_beta
        # (--from 的值或缺值哨兵, stderr 应说明的原因关键词)
        cases = [
            (MISSING_FROM_VALUE, "--from"),
            ("0", "positive integer"),
            ("000", "positive integer"),
            ("abc", "positive integer"),
            ("2x", "positive integer"),
            ("1.5", "positive integer"),
            (" 1 ", "positive integer"),
            (SQLITE_MAX_INT_PLUS_ONE, "range"),
        ]
        before = self.snapshot()
        for from_value, hint in cases:
            with self.subTest(from_value=from_value):
                cli_args = self.transfer_args(t, beta, from_value)
                proc = self.run_cli(*cli_args)
                detail = self.assert_rejected(proc, cli_args)
                self.assertIn(
                    hint, proc.stderr.lower(),
                    f"stderr 应说明对应原因（{hint}）：\n{detail}",
                )
                # 已有项目与任务不变
                self.assertEqual(
                    self.snapshot(), before,
                    f"非法 --from 被拒绝后数据不得变化：\n{detail}\n"
                    f"拒绝前={before!r}",
                )

        # 项目仍为两个，任务仍为三条
        self.assertEqual(len(self.project_names()), 2)
        self.assertEqual(
            len(self.list_project(self.project_alpha))
            + len(self.list_project(self.project_beta)),
            3,
        )

    # ---------- 省略 --from 保留原有直接转移 ----------

    def test_without_from_still_allows_direct_transfer(self):
        t, s, u = self.target_id, self.sibling_id, self.other_id
        alpha, beta = self.project_alpha, self.project_beta

        # 省略 --from：不检查来源，直接把 T 从甲转到乙
        cli_args = ("task-transfer", str(t), str(beta))
        proc = self.run_cli(*cli_args)
        moved = self.assert_transfer_success(
            proc, cli_args, self.task_obj(t, beta, "doing"),
        )
        self.assertEqual(
            self.list_project(beta),
            [self.task_obj(t, beta, "doing"),
             self.task_obj(u, beta, "todo")],
        )
        self.assertEqual(
            self.list_project(alpha), [self.task_obj(s, alpha, "todo")],
        )

        # 再次省略 --from 仍可直接转回甲（原语义不变）
        back_args = ("task-transfer", str(t), str(alpha))
        back_proc = self.run_cli(*back_args)
        self.assert_transfer_success(
            back_proc, back_args, self.task_obj(t, alpha, "doing"),
        )
        self.assertEqual(
            self.stats(alpha),
            {"project_id": alpha, "total": 2,
             "todo": 1, "doing": 1, "done": 0},
        )
        self.assertEqual(
            self.stats(beta),
            {"project_id": beta, "total": 1,
             "todo": 1, "doing": 0, "done": 0},
        )
        self.assertEqual(
            len(self.list_project(alpha)) + len(self.list_project(beta)), 3,
        )
        # 对照任务始终未变
        self.assertEqual(self.show_task(s), self.task_obj(s, alpha, "todo"))
        self.assertEqual(self.show_task(u), self.task_obj(u, beta, "todo"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

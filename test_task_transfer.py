#!/usr/bin/env python3
"""task-transfer 跨项目转移任务的命令行回归（验收）测试。

覆盖：把任务转移到另一已有项目（标识、标题、状态保留，仅 project_id 改变）、
目标就是当前所属项目的原地转移、目标项目已有同标题任务、目标项目为空、
前导零标识、任务不存在、目标项目不存在、标识格式非法、零值与越界、
参数缺失，以及数据库不可用时的存储失败路径。

另含转移后继续移动状态的验收场景：任务转入另一项目后仍以原标识定位，
``task-move --from`` 的来源状态校验读取当前保存值；移动成功后重复提交
同一请求会按当前状态与预期状态不符被拒绝（退出码 2），且拒绝前后
task-show、两项目任务列表与 project-stats 的结果完全一致。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品：用
project-create / task-create / task-move 准备真实数据，用 task-transfer
执行转移，再用 task-list / project-stats / project-list 查询验证结果
已落库。不直接读写数据库，不调用产品内部函数，不依赖网络，也不依赖任何
预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用
者自己的数据库。

夹具（每个用例独立构建）：

- 项目一 alpha：任务 T（标题与 S、O 相同，状态可按需准备）与同标题任务 S；
- 项目二 beta：一条与 T 同标题的任务 O。

转移 T 时，只有标识为 T 的任务允许改变所属项目；S、O 的完整对象以及两个
项目的名称都必须保持不变。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_transfer.py                  # 运行全部回归用例
    python3 -m unittest test_task_transfer -v      # 等价写法
    python3 test_task_transfer.py \
        TaskTransferRegression.test_transfer_to_other_project  # 只跑单个用例

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

# 目标任务与两条对照任务共用的标题，用于确认同标题任务不会被连带改动
SHARED_TITLE = "整理 API"

# SQLite 有符号 64 位整数上限
SQLITE_MAX_INT = 9223372036854775807


class TaskTransferRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-transfer-regtest-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")
        self._build_fixture()

    def tearDown(self):
        self._tmpdir.cleanup()

    # ---------- 命令行与结果解析辅助 ----------

    def run_cli(self, *cli_args, db_path=None):
        """执行 python -m kanban --db <临时库> ...，返回 CompletedProcess。"""
        return subprocess.run(
            [sys.executable, "-m", "kanban", "--db", db_path or self.db_path,
             *cli_args],
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

    def _detail(self, cli_args, proc, expected=None):
        """组装失败定位信息：输入参数、退出码与输出、预期。"""
        lines = [
            f"输入参数={list(cli_args)!r}",
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

    def stats(self, project_id):
        """通过公开命令查询某项目的状态统计。"""
        proc = self.run_cli("project-stats", str(project_id))
        detail = self._detail(
            ("project-stats", str(project_id)), proc,
            expected="退出码 0、stderr 为空、stdout 为统计 JSON 对象",
        )
        self.assertEqual(proc.returncode, 0, f"统计查询应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"统计查询时标准错误应为空：\n{detail}")
        return json.loads(proc.stdout)

    def project_names(self):
        """通过公开命令查询全部项目（按标识升序的 {id, name} 列表）。"""
        proc = self.run_cli("project-list")
        detail = self._detail(("project-list",), proc, expected="退出码 0")
        self.assertEqual(proc.returncode, 0, f"项目查询应成功：\n{detail}")
        return json.loads(proc.stdout)

    def show_task(self, task_id):
        """通过公开命令读取单条任务（字段集合与 task-show 契约一致）。"""
        cli_args = ("task-show", str(task_id))
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc,
            expected="退出码 0、stderr 为空、stdout 为单个 JSON 任务对象",
        )
        self.assertEqual(proc.returncode, 0, f"task-show 应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"查询时标准错误应为空：\n{detail}")
        try:
            obj = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"task-show 的 stdout 不是合法 JSON：\n{detail}")
        self.assertIsInstance(
            obj, dict, f"task-show 应返回单个 JSON 对象：\n{detail}"
        )
        self.assertEqual(
            set(obj.keys()), TASK_FIELDS,
            f"task-show 对象字段结构不符：\n{detail}",
        )
        return obj

    def full_snapshot(self):
        """记录拒绝前后需完全一致的全部视图：task-show、两项目列表、统计、名称。"""
        return {
            "target": self.show_task(self.target_id),
            "sibling": self.show_task(self.sibling_id),
            "other": self.show_task(self.other_id),
            "alpha": self.list_project(self.project_alpha),
            "beta": self.list_project(self.project_beta),
            "alpha_stats": self.stats(self.project_alpha),
            "beta_stats": self.stats(self.project_beta),
            "projects": self.project_names(),
        }

    def snapshot(self):
        """记录两个项目的当前任务列表与项目名称，用于前后比对。"""
        return {
            "alpha": self.list_project(self.project_alpha),
            "beta": self.list_project(self.project_beta),
            "projects": self.project_names(),
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

        # 项目二：另一条同标题任务 O，验证同标题不冲突、跨项目隔离
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        other = self.cli_json(
            "task-create", str(self.project_beta), SHARED_TITLE
        )
        self.other_id = other["id"]

    def prepare_target_status(self, status):
        """通过公开 task-move 命令把目标任务准备到指定状态。"""
        if status == "todo":
            return  # task-create 产生的新任务即为 todo
        self.cli_json("task-move", str(self.target_id), status)

    # ---------- 成功路径 ----------

    def _check_transfer_success(self, task_arg, project_arg, expected_obj,
                                expect_change=True):
        """执行一次转移并验证：输出对象、落库结果、其他数据不变。"""
        before = self.snapshot()

        cli_args = ("task-transfer", task_arg, project_arg)
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 0、stderr 为空、stdout 只有一个 JSON 任务对象，"
                f"对象为 {expected_obj!r}（字段集合 {sorted(TASK_FIELDS)}），"
                "且再次查询得到相同对象、其他任务与项目名称不变"
            ),
        )

        self.assertEqual(proc.returncode, 0, f"转移应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功转移时标准错误应为空：\n{detail}")

        try:
            moved = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"转移结果的 stdout 不是合法 JSON：\n{detail}")
        self.assertIsInstance(
            moved, dict, f"转移结果应为单个 JSON 对象而非数组：\n{detail}"
        )
        self.assertEqual(
            set(moved.keys()), TASK_FIELDS,
            f"转移结果对象字段结构不符：\n{detail}",
        )
        self.assertEqual(moved, expected_obj, f"转移结果对象内容不符：\n{detail}")

        # 以另一次命令行调用查询同一数据库：目标项目列表应包含该对象，
        # 且按标识升序排列
        after = self.snapshot()
        alpha_after = after["alpha"]
        beta_after = after["beta"]
        target_list = (
            alpha_after
            if expected_obj["project_id"] == self.project_alpha
            else beta_after
        )
        persisted = [obj for obj in target_list if obj["id"] == self.target_id]
        self.assertEqual(
            len(persisted), 1,
            f"目标项目列表中目标任务应恰好出现一次：\n{detail}\n"
            f"转移后目标项目列表={target_list!r}",
        )
        self.assertEqual(
            persisted[0], moved,
            f"再次查询到的目标任务对象应与转移结果完全一致（结果已保存）：\n"
            f"{detail}\n查询所得={persisted[0]!r}",
        )
        self.assertEqual(
            [obj["id"] for obj in target_list],
            sorted(obj["id"] for obj in target_list),
            f"目标项目列表应按标识升序：\n{detail}",
        )

        # 同项目同标题的对照任务 S 完整对象不变
        sibling_after = [obj for obj in alpha_after if obj["id"] == self.sibling_id]
        sibling_before = [
            obj for obj in before["alpha"] if obj["id"] == self.sibling_id
        ]
        self.assertEqual(
            sibling_after, sibling_before,
            f"来源项目的其他任务不得改变：\n{detail}",
        )
        # 项目二原有任务 O 完整对象不变
        other_after = [obj for obj in beta_after if obj["id"] == self.other_id]
        other_before = [obj for obj in before["beta"] if obj["id"] == self.other_id]
        self.assertEqual(
            other_after, other_before,
            f"目标项目原有任务不得改变：\n{detail}",
        )
        # 项目名称不变
        self.assertEqual(
            after["projects"], before["projects"],
            f"项目名称与标识不得改变：\n{detail}",
        )

        if expect_change:
            # 来源项目仍然存在，但其列表不再包含目标任务
            self.assertEqual(
                [obj for obj in alpha_after if obj["id"] == self.target_id], [],
                f"来源项目列表不应再包含目标任务：\n{detail}\n"
                f"转移后来源列表={alpha_after!r}",
            )
            # 不复制任务：两个项目任务总数不变
            self.assertEqual(
                len(alpha_after) + len(beta_after),
                len(before["alpha"]) + len(before["beta"]),
                f"转移不得新增或复制任务：\n{detail}",
            )
        else:
            # 原地转移：两个项目的任务集合与转移前完全一致
            self.assertEqual(
                alpha_after, before["alpha"],
                f"原地转移后来源项目列表不得改变：\n{detail}",
            )
            self.assertEqual(
                beta_after, before["beta"],
                f"原地转移后目标项目列表不得改变：\n{detail}",
            )

    def test_transfer_to_other_project(self):
        # doing 状态的任务转移到另一项目：仅 project_id 改变
        self.prepare_target_status("doing")
        self._check_transfer_success(
            str(self.target_id), str(self.project_beta),
            {
                "id": self.target_id,
                "project_id": self.project_beta,
                "title": SHARED_TITLE,
                "status": "doing",
            },
        )

    def test_transfer_with_leading_zeros(self):
        # 两个标识都允许前导零，按数值定位
        self.prepare_target_status("doing")
        self._check_transfer_success(
            "0001", "0002",
            {
                "id": self.target_id,
                "project_id": self.project_beta,
                "title": SHARED_TITLE,
                "status": "doing",
            },
        )

    def test_transfer_same_project_is_noop(self):
        # 目标就是当前所属项目：成功返回原任务，不新增记录
        self.prepare_target_status("doing")
        self._check_transfer_success(
            str(self.target_id), str(self.project_alpha),
            {
                "id": self.target_id,
                "project_id": self.project_alpha,
                "title": SHARED_TITLE,
                "status": "doing",
            },
            expect_change=False,
        )

    def test_transfer_to_empty_project(self):
        # 目标项目为空（新建无任务项目）也允许转移
        empty_project = self.cli_json("project-create", "empty")["id"]
        before_stats = self.stats(empty_project)
        self.assertEqual(before_stats["total"], 0)

        cli_args = ("task-transfer", str(self.target_id), str(empty_project))
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc, expected="退出码 0，任务归属空项目")
        self.assertEqual(proc.returncode, 0, f"转移到空项目应成功：\n{detail}")
        moved = json.loads(proc.stdout)
        self.assertEqual(moved["project_id"], empty_project)
        self.assertEqual(
            self.list_project(empty_project), [moved],
            f"空项目应只包含转入的任务：\n{detail}",
        )

    def test_transfer_stats_follow_new_ownership(self):
        # project-stats 按新归属计算：转出的 doing 任务计入目标项目
        self.prepare_target_status("doing")
        self.cli_json("task-transfer", str(self.target_id), str(self.project_beta))
        alpha_stats = self.stats(self.project_alpha)
        self.assertEqual(
            alpha_stats,
            {
                "project_id": self.project_alpha,
                "total": 1, "todo": 1, "doing": 0, "done": 0,
            },
            "来源项目应只剩对照任务 S（todo）",
        )
        beta_stats = self.stats(self.project_beta)
        self.assertEqual(
            beta_stats,
            {
                "project_id": self.project_beta,
                "total": 2, "todo": 1, "doing": 1, "done": 0,
            },
            "目标项目应计入转入的 doing 任务",
        )

    # ---------- 转移后继续移动状态（--from 读取当前保存值） ----------

    def test_move_after_transfer_locates_by_original_id(self):
        # 夹具中 T、S 在 alpha（T 先建、S 后建），U 在 beta，三条同标题。
        # 先把 T 置为 doing，S、U 保持 todo。
        doing_obj = self.cli_json(
            "task-move", str(self.target_id), "doing"
        )
        self.assertEqual(
            doing_obj,
            {
                "id": self.target_id,
                "project_id": self.project_alpha,
                "title": SHARED_TITLE,
                "status": "doing",
            },
        )

        # 第一步：把 T 从 alpha 转入 beta。转移结果只改变所属项目，
        # 原标识、标题、doing 状态原样保留，响应与随后独立查询一致。
        transfer_args = ("task-transfer", str(self.target_id),
                         str(self.project_beta))
        transfer_proc = self.run_cli(*transfer_args)
        detail = self._detail(
            transfer_args, transfer_proc,
            expected=(
                "退出码 0、stderr 为空、stdout 为与 task-show 同结构的单个"
                f"任务对象：T 属于 beta、标识/标题不变、状态 doing"
            ),
        )
        self.assertEqual(transfer_proc.returncode, 0, f"转移应成功：\n{detail}")
        self.assertEqual(
            transfer_proc.stderr, "", f"成功转移时标准错误应为空：\n{detail}"
        )
        try:
            transferred = json.loads(transfer_proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"转移结果的 stdout 不是合法 JSON：\n{detail}")
        expected_transferred = {
            "id": self.target_id,
            "project_id": self.project_beta,
            "title": SHARED_TITLE,
            "status": "doing",
        }
        self.assertIsInstance(
            transferred, dict,
            f"转移结果应为单个 JSON 对象而非数组：\n{detail}",
        )
        self.assertEqual(
            set(transferred.keys()), TASK_FIELDS,
            f"转移结果对象字段结构不符：\n{detail}",
        )
        self.assertEqual(transferred, expected_transferred,
                         f"转移结果应只改变所属项目：\n{detail}")
        # 独立进程 task-show 复核：响应已落库且与响应一致
        self.assertEqual(
            self.show_task(self.target_id), expected_transferred,
            f"转移后 task-show 应与转移响应一致：\n{detail}",
        )

        # 第二步：转移完成后，仍以 T 的原标识把状态从 doing 改为 done，
        # 并显式 --from doing。来源状态校验必须读取当前保存值（doing），
        # 任务跨项目后仍按同一标识定位。
        move_args = ("task-move", str(self.target_id), "done",
                     "--from", "doing")
        move_proc = self.run_cli(*move_args)
        detail = self._detail(
            move_args, move_proc,
            expected=(
                "退出码 0、stderr 为空、stdout 为与 task-show 同结构的单个"
                f"任务对象：{expected_transferred!r} 的状态改为 done"
            ),
        )
        self.assertEqual(
            move_proc.returncode, 0,
            f"转移后按原标识移动应成功（退出码 0）：\n{detail}",
        )
        self.assertEqual(
            move_proc.stderr, "",
            f"成功移动时标准错误应为空：\n{detail}",
        )
        try:
            moved = json.loads(move_proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"移动结果的 stdout 不是合法 JSON：\n{detail}")
        expected_done = dict(expected_transferred, status="done")
        self.assertIsInstance(
            moved, dict,
            f"移动结果应为单个 JSON 对象而非数组：\n{detail}",
        )
        self.assertEqual(
            set(moved.keys()), TASK_FIELDS,
            f"移动结果对象字段结构不符：\n{detail}",
        )
        self.assertEqual(
            moved, expected_done,
            f"移动结果应只把状态改为 done：\n{detail}",
        )

        # 成功移动的响应与随后独立查询一致：T 属于 beta、原标识与标题未变、
        # 状态 done。
        self.assertEqual(
            self.show_task(self.target_id), expected_done,
            f"移动后 task-show 应与移动响应一致：\n"
            f"移动响应={moved!r}",
        )

        # 独立查询确认归属、对照任务与列表顺序：
        # alpha 只剩 S；beta 中 T、U 各出现一次且按标识数值升序。
        alpha_tasks = self.list_project(self.project_alpha)
        beta_tasks = self.list_project(self.project_beta)
        expected_s = {
            "id": self.sibling_id,
            "project_id": self.project_alpha,
            "title": SHARED_TITLE,
            "status": "todo",
        }
        expected_u = {
            "id": self.other_id,
            "project_id": self.project_beta,
            "title": SHARED_TITLE,
            "status": "todo",
        }
        self.assertEqual(
            alpha_tasks, [expected_s],
            f"转移后 alpha 应只剩对照任务 S：\n实际={alpha_tasks!r}\n{detail}",
        )
        self.assertEqual(
            beta_tasks, [expected_done, expected_u],
            f"beta 应含转入的 T(done) 与对照任务 U(todo)，且按标识升序：\n"
            f"实际={beta_tasks!r}\n{detail}",
        )
        self.assertEqual(
            [obj["id"] for obj in beta_tasks],
            sorted(obj["id"] for obj in beta_tasks),
            f"beta 任务列表应按任务标识数值升序：\n{detail}",
        )
        # 不复制任务：任务总数始终为三
        self.assertEqual(len(alpha_tasks) + len(beta_tasks), 3)
        # 两个项目的名称不变
        self.assertEqual(
            self.project_names(),
            [
                {"id": self.project_alpha, "name": "alpha"},
                {"id": self.project_beta, "name": "beta"},
            ],
        )

        # 统计：alpha 一条 todo；beta 两条，todo、done 各一条；两项目 doing=0
        self.assertEqual(
            self.stats(self.project_alpha),
            {"project_id": self.project_alpha,
             "total": 1, "todo": 1, "doing": 0, "done": 0},
        )
        self.assertEqual(
            self.stats(self.project_beta),
            {"project_id": self.project_beta,
             "total": 2, "todo": 1, "doing": 0, "done": 1},
        )

        # 第三步：成功移动后再次提交同一移动请求。当前已为 done，与
        # --from doing 不符：退出码 2、stdout 为空，stderr 同时说明当前
        # done 与预期 doing，且没有 Python 回溯。
        before_reject = self.full_snapshot()
        repeat_proc = self.run_cli(*move_args)
        repeat_detail = self._detail(
            move_args, repeat_proc,
            expected=(
                "退出码 2、stdout 为空、stderr 同时说明当前状态 done 与"
                "预期状态 doing，且无 Python 回溯；拒绝不改变任何数据"
            ),
        )
        self.assertEqual(
            repeat_proc.returncode, 2,
            f"重复移动应被拒绝（退出码 2）：\n{repeat_detail}",
        )
        self.assertEqual(
            repeat_proc.stdout, "",
            f"拒绝时标准输出应为空：\n{repeat_detail}",
        )
        self.assertNotEqual(
            repeat_proc.stderr.strip(), "",
            f"拒绝时标准错误应说明原因：\n{repeat_detail}",
        )
        stderr_low = repeat_proc.stderr.lower()
        self.assertIn(
            "done", stderr_low,
            f"标准错误应说明当前状态为 done：\n{repeat_detail}",
        )
        self.assertIn(
            "doing", stderr_low,
            f"标准错误应说明 --from 预期状态为 doing：\n{repeat_detail}",
        )
        self.assertNotIn(
            "Traceback", repeat_proc.stderr,
            f"标准错误不应包含 Python 异常回溯：\n{repeat_detail}",
        )

        # 拒绝前后的 task-show、两项目任务列表和 project-stats 完全相同
        self.assertEqual(
            self.full_snapshot(), before_reject,
            f"拒绝移动后任何查询结果都不得变化：\n{repeat_detail}\n"
            f"拒绝前={before_reject!r}",
        )

    # ---------- 失败路径 ----------

    def _check_failure(self, cli_args, expect_stderr_hint=None, expect_code=2):
        """执行一次预期失败的转移：退出码、空 stdout、stderr 说明、数据不变。"""
        before = self.snapshot()
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc,
            expected=(
                f"退出码 {expect_code}、stdout 为空、stderr 说明原因"
                "且无 Python 异常回溯，两个项目的任务与名称与失败前完全一致"
            ),
        )
        self.assertEqual(
            proc.returncode, expect_code,
            f"应返回退出码 {expect_code}：\n{detail}",
        )
        self.assertEqual(proc.stdout, "", f"失败时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"失败时标准错误应说明原因：\n{detail}",
        )
        if expect_stderr_hint is not None:
            self.assertIn(
                expect_stderr_hint, proc.stderr.lower(),
                f"标准错误应说明对应原因：\n{detail}",
            )
        self.assertNotIn(
            "Traceback", proc.stderr,
            f"标准错误不应包含 Python 异常回溯：\n{detail}",
        )
        after = self.snapshot()
        self.assertEqual(
            after, before,
            f"失败后数据不得发生任何变化：\n{detail}\n失败前={before!r}",
        )

    def test_missing_arguments(self):
        # 参数缺失：退出码 2、stdout 为空
        proc = self.run_cli("task-transfer")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        proc = self.run_cli("task-transfer", str(self.target_id))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")

    def test_invalid_task_id_format(self):
        self._check_failure(
            ("task-transfer", "abc", str(self.project_beta)),
            expect_stderr_hint="task id",
        )

    def test_invalid_project_id_format(self):
        self._check_failure(
            ("task-transfer", str(self.target_id), "2x"),
            expect_stderr_hint="project id",
        )

    def test_zero_task_id(self):
        self._check_failure(
            ("task-transfer", "0", str(self.project_beta)),
            expect_stderr_hint="task id",
        )

    def test_zero_project_id_with_leading_zeros(self):
        self._check_failure(
            ("task-transfer", str(self.target_id), "000"),
            expect_stderr_hint="project id",
        )

    def test_task_id_overflow(self):
        self._check_failure(
            ("task-transfer", str(SQLITE_MAX_INT + 1), str(self.project_beta)),
            expect_stderr_hint="task id",
        )

    def test_project_id_overflow(self):
        self._check_failure(
            ("task-transfer", str(self.target_id), str(SQLITE_MAX_INT + 1)),
            expect_stderr_hint="project id",
        )

    def test_task_not_exist(self):
        self._check_failure(
            ("task-transfer", "999", str(self.project_beta)),
            expect_stderr_hint="task 999",
        )

    def test_project_not_exist(self):
        self._check_failure(
            ("task-transfer", str(self.target_id), "999"),
            expect_stderr_hint="project 999",
        )

    def test_database_unavailable(self):
        # 数据库无法打开：退出码 1、stdout 为空、stderr 说明存储失败
        bad_path = os.path.join(self._tmpdir.name, "no-such-dir", "x.db")
        proc = self.run_cli(
            "task-transfer", str(self.target_id), str(self.project_beta),
            db_path=bad_path,
        )
        detail = self._detail(
            ("task-transfer", str(self.target_id), str(self.project_beta)),
            proc, expected="退出码 1、stdout 为空、stderr 说明存储失败",
        )
        self.assertEqual(proc.returncode, 1, f"存储失败应返回退出码 1：\n{detail}")
        self.assertEqual(proc.stdout, "", f"存储失败时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"存储失败时标准错误应说明原因：\n{detail}",
        )
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

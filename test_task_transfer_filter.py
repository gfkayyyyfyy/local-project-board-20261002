#!/usr/bin/env python3
"""task-transfer 转移前后组合筛选（--status 并集 + --query 交集）结果的命令行回归测试。

固定场景：源项目 alpha 有两条同标题 "Fix API" 任务（一条 doing、一条 todo），
另有 doing 的 "Fix api" 与 done 的 "Docs"；目标项目 beta 已有一条 todo 的
"Fix API"。对两个项目分别使用 ``--status todo --status doing --query " API "``
（关键词去首尾空白后为 "API"，大小写敏感子串匹配，与状态并集取交集）调用
task-list 与 project-stats：

- 转移前：源项目命中两条 "Fix API"（统计 total=2、todo=1、doing=1、done=0），
  目标项目只命中一条 todo；小写 "Fix api" 与 "Docs" 均不进入结果。
- 把源项目中 doing 的 "Fix API" 转移到目标项目后：源项目只命中原来的 todo，
  目标项目命中两条（统计 total=2、todo=1、doing=1、done=0），列表按任务标识
  升序，同标题任务各自保留；转移响应与随后 task-show 的对象一致，目标任务
  仅 project_id 改变，其他任务与项目名称保持原值。
- 稳定性：把同一任务再次转移到当前所属项目应成功返回原任务，筛选列表与统计
  不变；转移到不存在的项目应退出码 2、标准输出为空、标准错误说明项目不存在
  且不含异常回溯，前后完整任务列表与筛选统计相同。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品：用
project-create / task-create / task-move 准备真实数据，用 task-transfer
执行转移，用 task-list / project-stats / task-show / project-list 读取
验证。不直接读写数据库，不调用产品内部函数，不依赖网络、第三方库或任何
预先保存的数据。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用
者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_transfer_filter.py                  # 运行全部回归用例
    python3 -m unittest test_task_transfer_filter -v      # 等价写法
    python3 test_task_transfer_filter.py \\
        TaskTransferFilterRegression.test_transfer_updates_filtered_results

退出码：全部通过为 0，存在失败为 1。失败信息会给出输入参数、实际退出码、
标准输出、标准错误以及预期结果；断言使用创建时返回的标识，不依赖 JSON 键
顺序或错误文案逐字一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TASK_FIELDS = {"id", "project_id", "title", "status"}
STATS_FIELDS = {"project_id", "total", "todo", "doing", "done"}

# 组合筛选条件：状态并集 (todo, doing) 与关键词交集；
# 关键词 " API " 去首尾空白后为 "API"，大小写敏感连续子串匹配
FILTER_ARGS = ("--status", "todo", "--status", "doing", "--query", " API ")

# 确定不存在的项目标识（夹具只创建两个项目）
MISSING_PROJECT_ID = "999"


class TaskTransferFilterRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-transfer-filter-")
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
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, f"准备数据应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"准备数据时标准错误应为空：\n{detail}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"准备数据的 stdout 不是合法 JSON：\n{detail}")

    @staticmethod
    def _detail(cli_args, proc, expected=None):
        """组装失败定位信息：输入参数、退出码与输出、预期。"""
        lines = [
            f"输入参数={list(cli_args)!r}",
            f"实际退出码={proc.returncode}",
            f"实际标准输出={proc.stdout!r}",
            f"实际标准错误={proc.stderr!r}",
            f"预期结果={expected!r}",
        ]
        return "\n".join(lines)

    def query_json(self, cli_args, expect_type, expected=None):
        """查询用：要求退出码 0、stderr 为空、stdout 只有一个可解析 JSON 值。"""
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc, expected=expected)
        self.assertEqual(proc.returncode, 0, f"查询应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"查询时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"查询的 stdout 不是合法 JSON：\n{detail}")
        self.assertIsInstance(data, expect_type, f"查询结果类型不符：\n{detail}")
        return data

    def filtered_list(self, project_id):
        """按组合筛选条件查询某项目的任务列表。"""
        cli_args = ("task-list", str(project_id), *FILTER_ARGS)
        data = self.query_json(cli_args, list, expected="筛选后的任务 JSON 数组")
        for obj in data:
            self.assertEqual(
                set(obj.keys()), TASK_FIELDS,
                f"任务对象字段结构不符：{obj!r}",
            )
        ids = [obj["id"] for obj in data]
        self.assertEqual(
            ids, sorted(ids),
            f"筛选列表应按任务标识升序：{data!r}",
        )
        return data

    def filtered_stats(self, project_id):
        """按组合筛选条件查询某项目的状态统计。"""
        cli_args = ("project-stats", str(project_id), *FILTER_ARGS)
        data = self.query_json(cli_args, dict, expected="筛选后的统计 JSON 对象")
        self.assertEqual(
            set(data.keys()), STATS_FIELDS,
            f"统计对象字段结构不符：{data!r}",
        )
        self.assertEqual(data["project_id"], project_id)
        return data

    def full_list(self, project_id):
        """不带筛选条件查询某项目的全部任务。"""
        return self.query_json(
            ("task-list", str(project_id)), list, expected="完整任务 JSON 数组"
        )

    def show_task(self, task_id):
        """按标识读取单条任务。"""
        return self.query_json(
            ("task-show", str(task_id)), dict, expected="单个任务 JSON 对象"
        )

    def project_names(self):
        """查询全部项目（按标识升序的 {id, name} 列表）。"""
        return self.query_json(
            ("project-list",), list, expected="项目 JSON 数组"
        )

    def snapshot(self):
        """记录两项目完整任务列表、筛选列表、筛选统计与项目名称。"""
        return {
            "alpha_full": self.full_list(self.project_alpha),
            "beta_full": self.full_list(self.project_beta),
            "alpha_filtered": self.filtered_list(self.project_alpha),
            "beta_filtered": self.filtered_list(self.project_beta),
            "alpha_stats": self.filtered_stats(self.project_alpha),
            "beta_stats": self.filtered_stats(self.project_beta),
            "projects": self.project_names(),
        }

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 源项目 alpha：两条同标题 "Fix API"（T1 doing、T2 todo），
        # 另有 doing 的 "Fix api"（小写，不命中关键词）与 done 的 "Docs"
        # （状态不在并集内），两者均不进入筛选结果
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        self.t1 = self.cli_json(
            "task-create", str(self.project_alpha), "Fix API"
        )["id"]
        self.t2 = self.cli_json(
            "task-create", str(self.project_alpha), "Fix API"
        )["id"]
        self.t3 = self.cli_json(
            "task-create", str(self.project_alpha), "Fix api"
        )["id"]
        self.t4 = self.cli_json(
            "task-create", str(self.project_alpha), "Docs"
        )["id"]
        self.cli_json("task-move", str(self.t1), "doing")
        self.cli_json("task-move", str(self.t3), "doing")
        self.cli_json("task-move", str(self.t4), "done")

        # 目标项目 beta：已有一条 todo 的 "Fix API"
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        self.t5 = self.cli_json(
            "task-create", str(self.project_beta), "Fix API"
        )["id"]

    def task_obj(self, task_id, project_id, title, status):
        return {
            "id": task_id,
            "project_id": project_id,
            "title": title,
            "status": status,
        }

    # ---------- 转移前的组合筛选基线 ----------

    def test_filtered_results_before_transfer(self):
        alpha, beta = self.project_alpha, self.project_beta

        # 源项目命中两条同标题 "Fix API"（doing 的 T1、todo 的 T2），
        # 按任务标识升序；小写 "Fix api" 与 "Docs" 均不进入结果
        self.assertEqual(
            self.filtered_list(alpha),
            [
                self.task_obj(self.t1, alpha, "Fix API", "doing"),
                self.task_obj(self.t2, alpha, "Fix API", "todo"),
            ],
        )
        self.assertEqual(
            self.filtered_stats(alpha),
            {"project_id": alpha, "total": 2, "todo": 1, "doing": 1, "done": 0},
        )

        # 目标项目只命中已有的一条 todo
        self.assertEqual(
            self.filtered_list(beta),
            [self.task_obj(self.t5, beta, "Fix API", "todo")],
        )
        self.assertEqual(
            self.filtered_stats(beta),
            {"project_id": beta, "total": 1, "todo": 1, "doing": 0, "done": 0},
        )

        # 完整列表确认被排除的任务确实存在：alpha 共四条、beta 一条
        self.assertEqual(
            self.full_list(alpha),
            [
                self.task_obj(self.t1, alpha, "Fix API", "doing"),
                self.task_obj(self.t2, alpha, "Fix API", "todo"),
                self.task_obj(self.t3, alpha, "Fix api", "doing"),
                self.task_obj(self.t4, alpha, "Docs", "done"),
            ],
        )
        self.assertEqual(
            self.full_list(beta),
            [self.task_obj(self.t5, beta, "Fix API", "todo")],
        )

    # ---------- 转移改变筛选结果 ----------

    def test_transfer_updates_filtered_results(self):
        alpha, beta = self.project_alpha, self.project_beta
        before = self.snapshot()

        # 把源项目中 doing 的 "Fix API"（T1）转移到目标项目
        cli_args = ("task-transfer", str(self.t1), str(beta))
        proc = self.run_cli(*cli_args)
        expected_obj = self.task_obj(self.t1, beta, "Fix API", "doing")
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 0、stderr 为空、stdout 只有一个 JSON 任务对象，"
                f"对象为 {expected_obj!r}（仅 project_id 变为目标项目），"
                "且随后 task-show 得到同一对象"
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
        # 标识、标题、状态不变，仅 project_id 改为目标项目
        self.assertEqual(moved, expected_obj, f"转移结果对象内容不符：\n{detail}")
        # 转移响应与随后 task-show 的对象一致（结果已落库）
        self.assertEqual(
            self.show_task(self.t1), moved,
            f"task-show 应与转移响应完全一致：\n{detail}",
        )

        # 转移后源项目只命中原来的 todo（T2）
        self.assertEqual(
            self.filtered_list(alpha),
            [self.task_obj(self.t2, alpha, "Fix API", "todo")],
            f"转移后源项目筛选列表不符：\n{detail}",
        )
        self.assertEqual(
            self.filtered_stats(alpha),
            {"project_id": alpha, "total": 1, "todo": 1, "doing": 0, "done": 0},
            f"转移后源项目筛选统计不符：\n{detail}",
        )

        # 目标项目命中两条：转入的 doing（T1）与原有的 todo（T5），
        # 同标题任务各自保留，按任务标识升序
        expected_beta = [
            self.task_obj(self.t1, beta, "Fix API", "doing"),
            self.task_obj(self.t5, beta, "Fix API", "todo"),
        ]
        expected_beta.sort(key=lambda obj: obj["id"])
        self.assertEqual(
            self.filtered_list(beta), expected_beta,
            f"转移后目标项目筛选列表不符：\n{detail}",
        )
        self.assertEqual(
            self.filtered_stats(beta),
            {"project_id": beta, "total": 2, "todo": 1, "doing": 1, "done": 0},
            f"转移后目标项目筛选统计不符：\n{detail}",
        )

        # 其他任务完整对象不变：T2、T3、T4 仍在源项目，T5 仍在目标项目
        self.assertEqual(
            self.full_list(alpha),
            [
                self.task_obj(self.t2, alpha, "Fix API", "todo"),
                self.task_obj(self.t3, alpha, "Fix api", "doing"),
                self.task_obj(self.t4, alpha, "Docs", "done"),
            ],
            f"转移后源项目完整列表不符：\n{detail}",
        )
        self.assertEqual(
            self.show_task(self.t5),
            self.task_obj(self.t5, beta, "Fix API", "todo"),
            f"目标项目原有任务不得改变：\n{detail}",
        )
        # 不复制任务、不新增记录：两项目任务总数不变
        self.assertEqual(
            len(self.full_list(alpha)) + len(self.full_list(beta)),
            len(before["alpha_full"]) + len(before["beta_full"]),
            f"转移不得新增或复制任务：\n{detail}",
        )
        # 项目名称保持原值
        self.assertEqual(
            self.project_names(), before["projects"],
            f"项目名称与标识不得改变：\n{detail}",
        )

    # ---------- 重复转移到当前所属项目：结果稳定 ----------

    def test_repeat_transfer_to_current_project_keeps_results(self):
        alpha, beta = self.project_alpha, self.project_beta

        # 先把 T1 转移到目标项目，再把它转移到当前所属项目（仍是 beta）
        self.cli_json("task-transfer", str(self.t1), str(beta))
        before = self.snapshot()

        cli_args = ("task-transfer", str(self.t1), str(beta))
        proc = self.run_cli(*cli_args)
        expected_obj = self.task_obj(self.t1, beta, "Fix API", "doing")
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 0、stderr 为空、stdout 为原任务对象 "
                f"{expected_obj!r}，筛选列表与统计与重复转移前完全一致"
            ),
        )
        self.assertEqual(
            proc.returncode, 0, f"原地重复转移应成功（退出码 0）：\n{detail}"
        )
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        try:
            moved = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"重复转移结果的 stdout 不是合法 JSON：\n{detail}")
        self.assertIsInstance(moved, dict, f"结果应为单个 JSON 对象：\n{detail}")
        self.assertEqual(
            moved, expected_obj,
            f"重复转移应成功返回原任务（不新增记录）：\n{detail}",
        )
        self.assertEqual(
            self.show_task(self.t1), moved,
            f"task-show 应与重复转移响应一致：\n{detail}",
        )

        # 筛选列表与统计不变
        after = self.snapshot()
        self.assertEqual(
            after, before,
            f"重复转移后筛选列表与统计不得变化：\n{detail}\n"
            f"重复转移前={before!r}\n重复转移后={after!r}",
        )

    # ---------- 转移到不存在的项目：拒绝且数据不变 ----------

    def test_transfer_to_missing_project_rejected(self):
        alpha, beta = self.project_alpha, self.project_beta

        # 先把 T1 转移到目标项目，使拒绝路径也覆盖转移后的状态
        self.cli_json("task-transfer", str(self.t1), str(beta))
        before = self.snapshot()

        cli_args = ("task-transfer", str(self.t1), MISSING_PROJECT_ID)
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 2、stdout 为空、stderr 说明目标项目不存在且无 Python "
                "异常回溯；前后完整任务列表与筛选统计完全相同"
            ),
        )
        self.assertEqual(
            proc.returncode, 2, f"应拒绝转移（退出码 2）：\n{detail}"
        )
        self.assertEqual(proc.stdout, "", f"拒绝时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"拒绝时标准错误应说明原因：\n{detail}",
        )
        self.assertIn(
            "project", proc.stderr.lower(),
            f"标准错误应说明与项目相关的原因：\n{detail}",
        )
        self.assertIn(
            MISSING_PROJECT_ID, proc.stderr,
            f"标准错误应指出不存在的项目标识：\n{detail}",
        )
        self.assertNotIn(
            "Traceback", proc.stderr,
            f"标准错误不应包含 Python 异常回溯：\n{detail}",
        )

        # 拒绝前后：完整任务列表、筛选列表、筛选统计、项目名称完全相同
        after = self.snapshot()
        self.assertEqual(
            after, before,
            f"拒绝后完整任务列表与筛选统计不得变化：\n{detail}\n"
            f"拒绝前={before!r}\n拒绝后={after!r}",
        )
        # 目标任务归属不变，仍可用原标识读取
        self.assertEqual(
            self.show_task(self.t1),
            self.task_obj(self.t1, beta, "Fix API", "doing"),
            f"拒绝后目标任务不得改变：\n{detail}",
        )

    # ---------- 重复查询不改动数据 ----------

    def test_repeated_queries_do_not_change_data(self):
        alpha, beta = self.project_alpha, self.project_beta
        self.cli_json("task-transfer", str(self.t1), str(beta))

        first = self.snapshot()
        # 连续重复同一组筛选查询与完整查询
        second = self.snapshot()
        self.assertEqual(
            second, first,
            f"重复查询结果应一致：\n第一次={first!r}\n第二次={second!r}",
        )
        # 再查一次，确认查询本身不产生任何写副作用
        third = self.snapshot()
        self.assertEqual(third, first, "查询不得改动任何数据")

        # 筛选统计在转移后保持稳定：源 1 条 todo，目标 todo/doing 各一条
        self.assertEqual(
            first["alpha_stats"],
            {"project_id": alpha, "total": 1, "todo": 1, "doing": 0, "done": 0},
        )
        self.assertEqual(
            first["beta_stats"],
            {"project_id": beta, "total": 2, "todo": 1, "doing": 1, "done": 0},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

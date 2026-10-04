#!/usr/bin/env python3
"""project-stats 可重复 --status 多状态并集筛选的命令行回归测试。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create / task-move 三个现有命令，不直接读写数据库，
不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时文件），
可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_stats_multi_status.py                  # 运行全部回归用例
    python3 -m unittest test_project_stats_multi_status -v      # 等价写法
    python3 test_project_stats_multi_status.py \\
        ProjectStatsMultiStatusRegression.test_acceptance_two_statuses_with_query

退出码：全部通过为 0，存在失败为 1。失败信息会给出具体命令行输入、退出码、
标准输出、标准错误与预期结果，便于定位差异。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# 项目一（alpha）内的四个标题，创建顺序即任务标识升序
ALPHA_TITLES = ["API准备", "API实现", "API归档", "文档整理"]
# 仅首尾保持 todo：API实现 -> doing，API归档 -> done
ALPHA_STATUSES = {
    "API实现": "doing",
    "API归档": "done",
}
STATS_FIELDS = {"project_id", "total", "todo", "doing", "done"}


class ProjectStatsMultiStatusRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-statsms-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")
        self._build_fixture()

    def tearDown(self):
        self._tmpdir.cleanup()

    # ---------- 命令行与结果解析辅助 ----------

    def run_cli(self, *cli_args, db_path=None):
        """执行 python -m kanban --db <临时库> ...，返回 CompletedProcess。"""
        return subprocess.run(
            [
                sys.executable, "-m", "kanban",
                "--db", db_path or self.db_path,
                *cli_args,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def cli_json(self, *cli_args):
        """准备数据用：要求成功并返回解析后的单个 JSON 值。"""
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"stdout 不是合法 JSON：{detail}")

    @staticmethod
    def _detail(cli_args, proc):
        return (
            f"输入参数={list(cli_args)!r}\n"
            f"退出码={proc.returncode}\n"
            f"标准输出={proc.stdout!r}\n"
            f"标准错误={proc.stderr!r}"
        )

    def assert_stats(self, cli_args, expected):
        """断言 project-stats 成功并返回与 expected 完全一致的统计对象。"""
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, f"预期成功退出码 0：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"标准输出不是合法 JSON（应只有一个 JSON 对象）：\n{detail}")
        self.assertIsInstance(data, dict, f"输出应为 JSON 对象：\n{detail}")
        self.assertEqual(set(data.keys()), STATS_FIELDS, detail)
        self.assertEqual(data, expected, f"统计结果不符：\n{detail}")
        for key in STATS_FIELDS:
            self.assertIsInstance(data[key], int, f"{key} 应为整数：{detail}")
            self.assertGreaterEqual(data[key], 0, f"{key} 应非负：{detail}")
        self.assertEqual(
            data["todo"] + data["doing"] + data["done"], data["total"], detail
        )
        return data

    def assert_usage_error(self, cli_args):
        """断言参数/业务校验失败：退出码 2、stdout 为空、stderr 有原因说明
        且无异常回溯，失败前后各项目任务集合与统计完全不变。"""
        before = self._snapshot_all_projects()
        proc = self.run_cli(*cli_args)
        after = self._snapshot_all_projects()
        detail = self._detail(cli_args, proc)

        self.assertEqual(proc.returncode, 2, f"预期参数错误退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"参数错误时标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"参数错误时标准错误应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr,
                         f"标准错误不应包含异常回溯：\n{detail}")
        self.assertEqual(after, before, f"参数错误不得改动已有数据：\n{detail}")

    def _snapshot_all_projects(self):
        """记录全部项目当前任务列表与全量统计，用于校验请求不产生副作用。"""
        snapshot = {}
        for pid in (self.project_alpha, self.project_beta, self.project_empty):
            snapshot[pid] = (
                self.cli_json("task-list", str(pid)),
                self.cli_json("project-stats", str(pid)),
            )
        return snapshot

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目一：API准备(todo)、API实现(doing)、API归档(done)、文档整理(todo)
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        self.alpha_task_ids = {}
        for title in ALPHA_TITLES:
            created = self.cli_json("task-create", str(self.project_alpha), title)
            self.alpha_task_ids[title] = created["id"]
        self.cli_json("task-move", str(self.alpha_task_ids["API实现"]), "doing")
        self.cli_json("task-move", str(self.alpha_task_ids["API归档"]), "done")

        # 项目二：同名 "API实现" 且为 doing，用于验证项目隔离
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        beta_task = self.cli_json(
            "task-create", str(self.project_beta), "API实现"
        )
        self.cli_json("task-move", str(beta_task["id"]), "doing")

        # 项目三：存在但没有任何任务
        self.project_empty = self.cli_json("project-create", "empty")["id"]

    # ---------- 用户指定的验收场景 ----------

    def test_acceptance_two_statuses_with_query(self):
        # project-stats 1 --status todo --status doing --query " API "：
        # 关键词去首尾空白后为 API；done 的 API归档 被状态并集排除，
        # todo 的 文档整理 被关键词排除，项目二同名 doing 任务被项目范围排除
        self.assert_stats(
            ["project-stats", str(self.project_alpha),
             "--status", "todo", "--status", "doing", "--query", " API "],
            {"project_id": self.project_alpha,
             "total": 2, "todo": 1, "doing": 1, "done": 0},
        )

    def test_acceptance_order_and_duplicates_do_not_change_result(self):
        # 交换状态顺序并重复 todo，结果与验收场景完全相同
        self.assert_stats(
            ["project-stats", str(self.project_alpha),
             "--status", "doing", "--status", "todo", "--status", "todo",
             "--query", " API "],
            {"project_id": self.project_alpha,
             "total": 2, "todo": 1, "doing": 1, "done": 0},
        )

    # ---------- 多状态并集，再与项目范围及关键词取交集 ----------

    def test_two_statuses_union_without_query(self):
        # --status doing --status done：并集命中两条，未选择的 todo 为 0
        self.assert_stats(
            ["project-stats", str(self.project_alpha),
             "--status", "doing", "--status", "done"],
            {"project_id": self.project_alpha,
             "total": 2, "todo": 0, "doing": 1, "done": 1},
        )

    def test_all_three_statuses_equal_no_status(self):
        # 三个状态全部传入与省略 --status 的结果一致
        expected = {"project_id": self.project_alpha,
                    "total": 4, "todo": 2, "doing": 1, "done": 1}
        self.assert_stats(
            ["project-stats", str(self.project_alpha),
             "--status", "done", "--status", "todo", "--status", "doing"],
            expected,
        )
        self.assert_stats(["project-stats", str(self.project_alpha)], expected)

    def test_duplicate_same_status_matches_single_status(self):
        # 重复传入同一状态与只传一次结果一致，同一任务只计一次
        expected = {"project_id": self.project_alpha,
                    "total": 1, "todo": 0, "doing": 1, "done": 0}
        self.assert_stats(
            ["project-stats", str(self.project_alpha),
             "--status", "doing", "--status", "doing"],
            expected,
        )
        self.assert_stats(
            ["project-stats", str(self.project_alpha), "--status", "doing"],
            expected,
        )

    def test_multi_status_scoped_to_project(self):
        # 项目二只有一条 doing 任务：多状态并集仍只统计本项目任务
        self.assert_stats(
            ["project-stats", str(self.project_beta),
             "--status", "todo", "--status", "doing"],
            {"project_id": self.project_beta,
             "total": 1, "todo": 0, "doing": 1, "done": 0},
        )

    def test_multi_status_empty_project_returns_all_zero(self):
        # 空项目按多状态筛选：成功返回四个 0
        self.assert_stats(
            ["project-stats", str(self.project_empty),
             "--status", "todo", "--status", "doing"],
            {"project_id": self.project_empty,
             "total": 0, "todo": 0, "doing": 0, "done": 0},
        )

    def test_multi_status_no_match_returns_all_zero(self):
        # 状态并集与关键词交集无命中：成功返回四个 0
        self.assert_stats(
            ["project-stats", str(self.project_alpha),
             "--status", "doing", "--status", "done", "--query", "文档"],
            {"project_id": self.project_alpha,
             "total": 0, "todo": 0, "doing": 0, "done": 0},
        )

    def test_single_status_result_unchanged(self):
        # 只传一次 --status 时保持原有单状态筛选结果
        self.assert_stats(
            ["project-stats", str(self.project_alpha), "--status", "done"],
            {"project_id": self.project_alpha,
             "total": 1, "todo": 0, "doing": 0, "done": 1},
        )

    def test_omit_status_result_unchanged(self):
        # 省略 --status 时仍统计项目全部状态的任务
        self.assert_stats(
            ["project-stats", str(self.project_alpha)],
            {"project_id": self.project_alpha,
             "total": 4, "todo": 2, "doing": 1, "done": 1},
        )

    def test_multi_status_read_only_and_repeatable(self):
        # 重复查询结果一致，且查询前后各项目任务与统计快照不变
        before = self._snapshot_all_projects()
        args = ["project-stats", str(self.project_alpha),
                "--status", "todo", "--status", "doing", "--query", " API "]
        first = self.run_cli(*args)
        for _ in range(3):
            again = self.run_cli(*args)
            self.assertEqual(
                (again.returncode, again.stdout, again.stderr),
                (first.returncode, first.stdout, first.stderr),
            )
        self.assertEqual(self._snapshot_all_projects(), before)

    # ---------- 每次出现的状态都按原样校验，任一非法即拒绝整次查询 ----------

    def test_error_invalid_status_before_valid_one(self):
        # 非法状态在前、合法状态在后：仍拒绝整次查询
        self.assert_usage_error(
            ["project-stats", str(self.project_alpha),
             "--status", "bogus", "--status", "todo"]
        )

    def test_error_invalid_status_after_valid_one(self):
        # 合法状态在前、非法状态在后：同样拒绝整次查询
        self.assert_usage_error(
            ["project-stats", str(self.project_alpha),
             "--status", "todo", "--status", "Done"]
        )

    def test_error_status_case_sensitive(self):
        # 大小写变化（TODO）不是合法状态
        self.assert_usage_error(
            ["project-stats", str(self.project_alpha), "--status", "TODO"]
        )

    def test_error_status_with_surrounding_whitespace(self):
        # 首尾空白不剔除，" todo" 不是合法状态
        self.assert_usage_error(
            ["project-stats", str(self.project_alpha), "--status", " todo "]
        )

    def test_error_empty_status(self):
        # 空字符串不是合法状态
        self.assert_usage_error(
            ["project-stats", str(self.project_alpha), "--status", ""]
        )

    def test_error_comma_joined_status(self):
        # "todo,doing" 不是合法状态：每次出现只接收一个状态
        self.assert_usage_error(
            ["project-stats", str(self.project_alpha),
             "--status", "todo,doing"]
        )

    def test_error_status_missing_value(self):
        # --status 出现在末尾但未给值：参数错误
        self.assert_usage_error(
            ["project-stats", str(self.project_alpha), "--status"]
        )

    def test_error_empty_query(self):
        # 空关键词（含纯空白）配合多状态：参数错误
        for raw in ("", "   "):
            with self.subTest(raw=raw):
                self.assert_usage_error(
                    ["project-stats", str(self.project_alpha),
                     "--status", "todo", "--status", "doing",
                     "--query", raw]
                )

    def test_error_invalid_project_id(self):
        # 标识非正整数或越界：参数错误
        for raw in ("0", "-1", "abc", "9223372036854775808"):
            with self.subTest(raw=raw):
                self.assert_usage_error(
                    ["project-stats", raw,
                     "--status", "todo", "--status", "doing"]
                )

    def test_error_nonexistent_project(self):
        # 范围内但不存在的项目标识：业务校验失败
        self.assert_usage_error(
            ["project-stats", "999", "--status", "todo", "--status", "doing"]
        )

    def test_error_missing_project_id(self):
        # 缺少项目标识：参数错误
        self.assert_usage_error(
            ["project-stats", "--status", "todo", "--status", "doing"]
        )

    # ---------- 存储失败 ----------

    def test_unopenable_database_is_storage_failure(self):
        dir_path = os.path.join(self._tmpdir.name, "cannot_be_a_db")
        os.mkdir(dir_path)
        proc = self.run_cli(
            "project-stats", "1", "--status", "todo", "--status", "doing",
            db_path=dir_path)
        detail = self._detail(
            ("project-stats", "1", "--status", "todo", "--status", "doing"),
            proc)
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)

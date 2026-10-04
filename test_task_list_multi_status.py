#!/usr/bin/env python3
"""task-list 可重复 --status 多状态并集筛选的命令行回归测试。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create / task-move 三个现有命令，不直接读写数据库，
不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时文件），
可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_list_multi_status.py                  # 运行全部回归用例
    python3 -m unittest test_task_list_multi_status -v      # 等价写法
    python3 test_task_list_multi_status.py \\
        TaskListMultiStatusRegression.test_union_with_query   # 只跑单个用例

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

# 项目一（alpha）依次创建的四条任务，创建顺序即任务标识升序
ALPHA_TASKS = [
    ("API准备", "todo"),
    ("API实现", "doing"),
    ("API归档", "done"),
    ("文档整理", "todo"),
]
TASK_FIELDS = {"id", "project_id", "title", "status"}


class TaskListMultiStatusRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-regtest-")
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

    def expected_tasks(self, titles, project_id=None):
        """按标题从夹具构造预期任务对象列表（按任务标识升序）。"""
        if project_id is None:
            project_id = self.project_alpha
            task_map = self.alpha_tasks
        else:
            task_map = self.beta_tasks
        return [
            {
                "id": task_map[title]["id"],
                "project_id": project_id,
                "title": title,
                "status": task_map[title]["status"],
            }
            for title in titles
        ]

    def assert_task_list(self, cli_args, expected_titles, project_id=None):
        """断言 task-list 成功并恰好返回 expected_titles 对应的任务（含顺序）。"""
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, f"预期成功退出码 0：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"标准输出不是合法 JSON（应只有一个 JSON 数组）：\n{detail}")
        self.assertIsInstance(data, list, f"输出应为 JSON 数组：\n{detail}")
        self.assertEqual(
            data,
            self.expected_tasks(expected_titles, project_id),
            f"返回任务集合或顺序不符：\n{detail}",
        )
        for obj in data:
            self.assertIsInstance(obj, dict, f"数组元素应为任务对象：\n{detail}")
            self.assertEqual(
                set(obj.keys()), TASK_FIELDS,
                f"任务对象字段结构不符：\n{detail}",
            )
        ids = [obj["id"] for obj in data]
        self.assertEqual(ids, sorted(ids), f"结果应按任务标识升序：\n{detail}")
        return proc.stdout

    def assert_usage_error(self, cli_args):
        """断言参数/业务校验失败：退出码 2、stdout 为空、stderr 有原因说明
        且不含异常回溯，失败前后各项目任务集合与状态完全不变。"""
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
        self.assertEqual(after, before, f"参数错误不得改动已有任务：\n{detail}")

    def _snapshot_all_projects(self):
        """记录全部项目当前任务（对象集合），用于校验请求不产生副作用。"""
        snapshot = {}
        for pid in (self.project_alpha, self.project_beta, self.project_empty):
            snapshot[pid] = self.cli_json("task-list", str(pid))
        return snapshot

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目一：API准备(todo)、API实现(doing)、API归档(done)、文档整理(todo)
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        self.alpha_tasks = {}
        for title, status in ALPHA_TASKS:
            created = self.cli_json("task-create", str(self.project_alpha), title)
            if status != "todo":
                self.cli_json("task-move", str(created["id"]), status)
            self.alpha_tasks[title] = {"id": created["id"], "status": status}

        # 项目二：同名 "API实现" 且为 doing，用于验证项目隔离
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        beta_task = self.cli_json(
            "task-create", str(self.project_beta), "API实现"
        )
        self.cli_json("task-move", str(beta_task["id"]), "doing")
        self.beta_tasks = {"API实现": {"id": beta_task["id"], "status": "doing"}}

        # 项目三：存在但没有任何任务
        self.project_empty = self.cli_json("project-create", "empty")["id"]

    # ---------- 多状态并集 ----------

    def test_union_with_query(self):
        # 输入 --status todo --status doing --query API：
        # 只返回项目一前两条（API准备、API实现），不含 done 的 API归档，
        # 不含标题不含 API 的文档整理，也不含项目二的同名 doing 任务
        self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--status", "todo", "--status", "doing", "--query", "API"],
            ["API准备", "API实现"],
        )

    def test_union_order_and_duplicates_do_not_change_result(self):
        # 交换状态顺序并追加重复的 --status todo：结果与上一条完全相同
        baseline = self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--status", "todo", "--status", "doing", "--query", "API"],
            ["API准备", "API实现"],
        )
        reordered = self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--status", "doing", "--status", "todo",
             "--status", "todo", "--query", "API"],
            ["API准备", "API实现"],
        )
        self.assertEqual(reordered, baseline,
                         "状态顺序与重复取值不应影响输出字节")

    def test_union_of_all_three_statuses_lists_all_tasks(self):
        # 三个状态全部选上：等价于省略 --status，返回项目全部任务且按 id 升序
        titles = [title for title, _ in ALPHA_TASKS]
        self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--status", "done", "--status", "todo", "--status", "doing"],
            titles,
        )
        self.assert_task_list(
            ["task-list", str(self.project_alpha)],
            titles,
        )

    def test_union_result_sorted_by_id_not_grouped_by_status(self):
        # --status doing --status todo：结果按任务标识升序，不按状态分组
        self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--status", "doing", "--status", "todo"],
            ["API准备", "API实现", "文档整理"],
        )

    def test_single_status_result_unchanged(self):
        # 只传一次 --status：保持原有单状态筛选结果
        self.assert_task_list(
            ["task-list", str(self.project_alpha), "--status", "doing"],
            ["API实现"],
        )

    def test_duplicate_same_status_not_returned_twice(self):
        # 重复选择同一状态：任务不会重复返回
        self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--status", "todo", "--status", "todo"],
            ["API准备", "文档整理"],
        )

    def test_union_without_query(self):
        # 多状态不带 --query：返回项目内这些状态的全部任务
        self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--status", "doing", "--status", "done"],
            ["API实现", "API归档"],
        )

    def test_union_scoped_to_project(self):
        # 项目二做同样的多状态查询：只返回项目二自己的任务
        self.assert_task_list(
            ["task-list", str(self.project_beta),
             "--status", "todo", "--status", "doing", "--query", "API"],
            ["API实现"],
            project_id=self.project_beta,
        )

    def test_union_no_match_returns_empty_array(self):
        # 多状态与关键词交集无命中：成功返回空数组
        self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--status", "todo", "--status", "doing", "--query", "归档"],
            [],
        )

    def test_empty_project_multi_status_returns_empty_array(self):
        # 空项目使用多状态筛选：成功返回空数组
        self.assert_task_list(
            ["task-list", str(self.project_empty),
             "--status", "todo", "--status", "doing"],
            [],
            project_id=self.project_empty,
        )

    def test_repeated_queries_do_not_change_data(self):
        # 查询及重复查询均不改动已保存的项目和任务
        before = self._snapshot_all_projects()
        args = ["task-list", str(self.project_alpha),
                "--status", "todo", "--status", "doing", "--query", "API"]
        self.assert_task_list(args, ["API准备", "API实现"])
        self.assert_task_list(args, ["API准备", "API实现"])
        after = self._snapshot_all_projects()
        self.assertEqual(after, before, "查询不得改动已有数据")

    # ---------- 错误路径：退出码 2、stdout 空、stderr 说明原因、数据不变 ----------

    def test_error_invalid_status_among_valid_ones(self):
        # 第一个状态非法，即使后面还有合法状态也拒绝整次查询
        self.assert_usage_error(
            ["task-list", str(self.project_alpha),
             "--status", "bog", "--status", "todo"]
        )

    def test_error_invalid_status_after_valid_ones(self):
        # 合法状态之后出现非法状态，同样拒绝整次查询
        self.assert_usage_error(
            ["task-list", str(self.project_alpha),
             "--status", "todo", "--status", "doing", "--status", "WAIT"]
        )

    def test_error_status_case_variant(self):
        # 大小写变化（TODO）不是合法状态
        self.assert_usage_error(
            ["task-list", str(self.project_alpha),
             "--status", "TODO", "--status", "todo"]
        )

    def test_error_status_with_surrounding_whitespace(self):
        # 带首尾空白的状态取值非法（状态不做去空白处理）
        self.assert_usage_error(
            ["task-list", str(self.project_alpha),
             "--status", " todo ", "--status", "doing"]
        )

    def test_error_empty_status_value(self):
        # 空字符串状态非法
        self.assert_usage_error(
            ["task-list", str(self.project_alpha),
             "--status", "", "--status", "todo"]
        )

    def test_error_comma_joined_status_value(self):
        # "todo,doing" 作为单次取值非法，不会被拆成两个状态
        self.assert_usage_error(
            ["task-list", str(self.project_alpha),
             "--status", "todo,doing", "--status", "todo"]
        )

    def test_error_missing_status_value(self):
        # --status 出现在末尾但未给值：参数错误
        self.assert_usage_error(
            ["task-list", str(self.project_alpha),
             "--status", "todo", "--status"]
        )

    def test_error_nonexistent_project_with_multi_status(self):
        # 项目不存在时多状态查询同样按现有约定拒绝
        self.assert_usage_error(
            ["task-list", "999", "--status", "todo", "--status", "doing"]
        )

    # ---------- project-stats 单状态筛选保持原行为 ----------

    def test_project_stats_single_status_unchanged(self):
        # project-stats 仍按单状态精确匹配统计
        proc = self.run_cli(
            "project-stats", str(self.project_alpha), "--status", "todo"
        )
        detail = self._detail(
            ("project-stats", str(self.project_alpha), "--status", "todo"),
            proc,
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(
            json.loads(proc.stdout),
            {"project_id": self.project_alpha,
             "total": 2, "todo": 2, "doing": 0, "done": 0},
            detail,
        )

    def test_project_stats_invalid_status_still_rejected(self):
        # project-stats 的非法状态仍被拒绝
        self.assert_usage_error(
            ["project-stats", str(self.project_alpha), "--status", "TODO"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

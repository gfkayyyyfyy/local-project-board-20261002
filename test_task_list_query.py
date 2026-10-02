#!/usr/bin/env python3
"""task-list 关键词筛选及其与 --status 取交集行为的命令行回归测试。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create / task-move 三个现有命令，不直接读写数据库，
不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时文件），
可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_list_query.py                  # 运行全部回归用例
    python3 -m unittest test_task_list_query -v      # 等价写法
    python3 test_task_list_query.py \\
        TaskListQueryRegression.test_query_percent_is_literal   # 只跑单个用例

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

# 项目一（alpha）内的七种标题，创建顺序即任务标识升序
ALPHA_TITLES = [
    "Fix API",      # 单空格、大写 API
    "fix api",      # 全小写
    "Fix  API",     # 两个空格
    "修复登录",       # 中文
    "100%",         # 含百分号
    "item_name",    # 含下划线
    "Bob's task",   # 含单引号
]
# 仅前两项之外保持 todo：Fix API -> doing，Fix  API -> done
ALPHA_STATUSES = {
    "Fix API": "doing",
    "Fix  API": "done",
}
TASK_FIELDS = {"id", "project_id", "title", "status"}


class TaskListQueryRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-regtest-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")
        self.alpha_task_ids = {}
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

    def assert_task_list(self, cli_args, expected_titles, project_id=None):
        """断言 task-list 成功并恰好返回 expected_titles 对应的任务（含顺序）。

        expected_titles 中的标题必须属于 project_id（默认项目一）；
        预期对象由建数阶段的返回值构造，字段值与产品输出逐一比对。
        """
        if project_id is None:
            project_id = self.project_alpha
            id_map = self.alpha_task_ids
            status_map = ALPHA_STATUSES
        else:
            id_map = self.other_task_ids
            status_map = self.other_statuses

        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, f"预期成功退出码 0：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"标准输出不是合法 JSON（应只有一个 JSON 数组）：\n{detail}")
        self.assertIsInstance(data, list, f"输出应为 JSON 数组：\n{detail}")

        expected = [
            {
                "id": id_map[title],
                "project_id": project_id,
                "title": title,
                "status": status_map.get(title, "todo"),
            }
            for title in expected_titles
        ]
        self.assertEqual(data, expected, f"返回任务集合或顺序不符：\n{detail}")

        for obj in data:
            self.assertIsInstance(obj, dict, f"数组元素应为任务对象：\n{detail}")
            self.assertEqual(
                set(obj.keys()), TASK_FIELDS,
                f"任务对象字段结构不符：\n{detail}",
            )
        ids = [obj["id"] for obj in data]
        self.assertEqual(ids, sorted(ids), f"结果应按任务标识升序：\n{detail}")

    def assert_usage_error(self, cli_args):
        """断言参数/业务校验失败：退出码 2、stdout 为空、stderr 有原因说明，
        且失败前后各项目任务集合与状态完全不变。"""
        before = self._snapshot_all_projects()
        proc = self.run_cli(*cli_args)
        after = self._snapshot_all_projects()
        detail = self._detail(cli_args, proc)

        self.assertEqual(proc.returncode, 2, f"预期参数错误退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"参数错误时标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"参数错误时标准错误应说明原因：\n{detail}")
        self.assertEqual(after, before, f"参数错误不得改动已有任务：\n{detail}")

    def _snapshot_all_projects(self):
        """记录全部项目当前任务（对象集合），用于校验错误请求不产生副作用。"""
        snapshot = {}
        for pid in (self.project_alpha, self.project_beta, self.project_empty):
            snapshot[pid] = self.cli_json("task-list", str(pid))
        return snapshot

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目一：七种标题；Fix API -> doing，Fix  API -> done，其余 todo
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        for title in ALPHA_TITLES:
            created = self.cli_json("task-create", str(self.project_alpha), title)
            self.alpha_task_ids[title] = created["id"]
        self.cli_json("task-move", str(self.alpha_task_ids["Fix API"]), "doing")
        self.cli_json("task-move", str(self.alpha_task_ids["Fix  API"]), "done")

        # 项目二：同名 "Fix API" 且为 doing，用于验证项目隔离与交集
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        beta_task = self.cli_json(
            "task-create", str(self.project_beta), "Fix API"
        )
        self.cli_json("task-move", str(beta_task["id"]), "doing")
        self.other_task_ids = {"Fix API": beta_task["id"]}
        self.other_statuses = {"Fix API": "doing"}

        # 项目三：存在但没有任何任务
        self.project_empty = self.cli_json("project-create", "empty")["id"]

    # ---------- 关键词：大小写敏感的连续子串 ----------

    def test_query_uppercase_api_matches_two_titles(self):
        # 输入 --query API：仅标题中含大写连续子串 "API" 的两个任务
        self.assert_task_list(
            ["task-list", str(self.project_alpha), "--query", "API"],
            ["Fix API", "Fix  API"],
        )

    def test_query_lowercase_api_is_case_sensitive(self):
        # 输入 --query api：小写只命中全小写标题，不命中大写 API
        self.assert_task_list(
            ["task-list", str(self.project_alpha), "--query", "api"],
            ["fix api"],
        )

    def test_query_single_space_phrase_matches_single_space_title(self):
        # 输入 --query "Fix API"：单空格连续子串，不匹配双空格标题
        self.assert_task_list(
            ["task-list", str(self.project_alpha), "--query", "Fix API"],
            ["Fix API"],
        )

    def test_query_internal_double_space_is_preserved(self):
        # 输入 --query "Fix  API"（内部两个空格）：内部空白保留，只命中双空格标题
        self.assert_task_list(
            ["task-list", str(self.project_alpha), "--query", "Fix  API"],
            ["Fix  API"],
        )

    def test_query_leading_space_matches_lowercase_title(self):
        # 输入 --query " api"（前导一个空格、小写 api）：只命中 "fix api"
        self.assert_task_list(
            ["task-list", str(self.project_alpha), "--query", " api"],
            ["fix api"],
        )

    def test_query_trims_leading_and_trailing_whitespace(self):
        # 输入 --query "  API  "：首尾空白被去除，等价于 API，命中两条且按 id 升序
        self.assert_task_list(
            ["task-list", str(self.project_alpha), "--query", "  API  "],
            ["Fix API", "Fix  API"],
        )

    def test_query_trims_tabs_around_keyword(self):
        # 输入 --query "\tFix API\t"：制表符同样作为首尾空白被去除
        self.assert_task_list(
            ["task-list", str(self.project_alpha), "--query", "\tFix API\t"],
            ["Fix API"],
        )

    def test_query_chinese_keyword_matches_chinese_title(self):
        # 输入 --query 登录：只命中中文标题 "修复登录"
        self.assert_task_list(
            ["task-list", str(self.project_alpha), "--query", "登录"],
            ["修复登录"],
        )

    # ---------- 关键词：%、_、单引号均为普通字符 ----------

    def test_query_percent_is_literal(self):
        # 输入 --query %：不是 SQL 通配符，只命中 "100%"
        self.assert_task_list(
            ["task-list", str(self.project_alpha), "--query", "%"],
            ["100%"],
        )

    def test_query_underscore_is_literal(self):
        # 输入 --query _：不是 SQL 通配符，只命中 "item_name"
        self.assert_task_list(
            ["task-list", str(self.project_alpha), "--query", "_"],
            ["item_name"],
        )

    def test_query_apostrophe_is_literal(self):
        # 输入 --query '：普通字符，只命中 "Bob's task"
        self.assert_task_list(
            ["task-list", str(self.project_alpha), "--query", "'"],
            ["Bob's task"],
        )

    # ---------- 关键词与状态取交集、项目隔离 ----------

    def test_query_and_status_intersection_current_project_only(self):
        # 输入项目一 --query API --status doing：
        # 只返回项目一自己的 Fix API；项目二同名 doing 任务不得串入
        self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--query", "API", "--status", "doing"],
            ["Fix API"],
        )

    def test_query_and_status_in_other_project_returns_its_own_task(self):
        # 在项目二做同样查询，只返回项目二的 Fix API（doing）
        self.assert_task_list(
            ["task-list", str(self.project_beta),
             "--query", "API", "--status", "doing"],
            ["Fix API"],
            project_id=self.project_beta,
        )

    def test_query_and_status_intersection_can_be_empty(self):
        # 项目一 --query API --status todo：两条大写 API 分别是 doing/done，交集为空
        self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--query", "API", "--status", "todo"],
            [],
        )

    def test_query_without_match_returns_empty_array(self):
        # 输入任何标题都不含的关键词：返回空数组
        self.assert_task_list(
            ["task-list", str(self.project_alpha), "--query", "NO_SUCH_KEYWORD"],
            [],
        )

    def test_project_without_tasks_returns_empty_array(self):
        # 对存在但没有任务的项目按关键词查询：返回空数组
        self.assert_task_list(
            ["task-list", str(self.project_empty), "--query", "API"],
            [],
            project_id=self.project_empty,
        )

    # ---------- 省略 --query 时保留原有列表与状态筛选语义 ----------

    def test_omit_query_lists_all_tasks_sorted_by_id(self):
        # 不带 --query：返回项目全部 7 条任务，按任务标识升序
        self.assert_task_list(
            ["task-list", str(self.project_alpha)],
            ALPHA_TITLES,
        )

    def test_status_filter_without_query_is_preserved(self):
        # 只带 --status todo：返回项目内全部 todo 任务，保持升序
        proc = self.run_cli(
            "task-list", str(self.project_alpha), "--status", "todo"
        )
        detail = self._detail(
            ("task-list", str(self.project_alpha), "--status", "todo"), proc
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        data = json.loads(proc.stdout)
        self.assertEqual(
            [obj["title"] for obj in data],
            ["fix api", "修复登录", "100%", "item_name", "Bob's task"],
            detail,
        )
        self.assertTrue(all(obj["status"] == "todo" for obj in data), detail)
        self.assertEqual(
            [obj["id"] for obj in data],
            sorted(obj["id"] for obj in data),
            detail,
        )

    def test_other_project_omit_query_only_lists_own_task(self):
        # 项目二不带 --query：只列项目二自己的任务，不含项目一任务
        self.assert_task_list(
            ["task-list", str(self.project_beta)],
            ["Fix API"],
            project_id=self.project_beta,
        )

    # ---------- 错误路径：退出码 2、stdout 空、stderr 说明原因、数据不变 ----------

    def test_error_empty_string_query(self):
        # --query ""（空字符串）
        self.assert_usage_error(
            ["task-list", str(self.project_alpha), "--query", ""]
        )

    def test_error_whitespace_only_query(self):
        # --query "   "（纯空白，去空白后为空）
        self.assert_usage_error(
            ["task-list", str(self.project_alpha), "--query", "   "]
        )

    def test_error_invalid_status(self):
        # --status bog 与 --query 同时出现时，非法状态仍被拒绝
        self.assert_usage_error(
            ["task-list", str(self.project_alpha),
             "--status", "bog", "--query", "API"]
        )

    def test_error_invalid_status_without_query(self):
        # 不带 --query 时非法状态同样被拒绝
        self.assert_usage_error(
            ["task-list", str(self.project_alpha), "--status", "WAIT"]
        )

    def test_error_zero_project_id(self):
        # 项目标识为 0（非正整数）
        self.assert_usage_error(
            ["task-list", "0", "--query", "API"]
        )

    def test_error_non_numeric_project_id(self):
        # 项目标识为非数字
        self.assert_usage_error(
            ["task-list", "abc", "--query", "API"]
        )

    def test_error_nonexistent_project(self):
        # 项目标识为正整数但项目不存在
        self.assert_usage_error(
            ["task-list", "999", "--query", "API"]
        )

    def test_error_missing_query_value(self):
        # --query 出现在末尾但未给值：参数错误，行为与其他校验错误一致
        self.assert_usage_error(
            ["task-list", str(self.project_alpha), "--query"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

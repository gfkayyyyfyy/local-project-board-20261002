#!/usr/bin/env python3
"""project-list --query 项目名称关键词筛选的命令行回归测试。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create 两个现有命令，不直接读写数据库，不依赖网络，
也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时文件），
可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_list_query.py                  # 运行全部回归用例
    python3 -m unittest test_project_list_query -v      # 等价写法
    python3 test_project_list_query.py \\
        ProjectListQueryRegression.test_acceptance_api_matches_project_names_only

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

PROJECT_FIELDS = {"id", "name"}


class ProjectListQueryRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时目录，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-regtest-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")

    def tearDown(self):
        self._tmpdir.cleanup()

    # ---------- 命令行与结果解析辅助 ----------

    def run_cli(self, *cli_args, db_path=None):
        """执行 python -m kanban --db <临时库> ...，返回 CompletedProcess。"""
        return subprocess.run(
            [sys.executable, "-m", "kanban", "--db",
             db_path if db_path is not None else self.db_path, *cli_args],
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

    def assert_project_list_query(self, keyword, expected):
        """断言 project-list --query 成功并恰好返回 expected（含顺序与结构）。"""
        cli_args = ("project-list", "--query", keyword)
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, f"预期成功退出码 0：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"标准输出不是合法 JSON（应只有一个 JSON 数组）：\n{detail}")
        self.assertIsInstance(data, list, f"输出应为 JSON 数组：\n{detail}")
        self.assertEqual(data, expected, f"返回项目集合或顺序不符：\n{detail}")
        for obj in data:
            self.assertIsInstance(obj, dict, f"数组元素应为项目对象：\n{detail}")
            self.assertEqual(
                set(obj.keys()), PROJECT_FIELDS,
                f"项目对象字段结构不符：\n{detail}",
            )
            self.assertIsInstance(obj["id"], int, f"id 应为整数：\n{detail}")
            self.assertIsInstance(obj["name"], str, f"name 应为字符串：\n{detail}")
        ids = [obj["id"] for obj in data]
        self.assertEqual(ids, sorted(ids), f"结果应按项目标识升序：\n{detail}")
        return data

    def assert_usage_error(self, cli_args):
        """断言参数失败：退出码 2、stdout 为空、stderr 有原因说明、数据不变。"""
        before = self.cli_json("project-list")
        proc = self.run_cli(*cli_args)
        after = self.cli_json("project-list")
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 2, f"预期参数错误退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"参数错误时标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"参数错误时标准错误应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr,
                         f"标准错误不应出现 Python 异常回溯：\n{detail}")
        self.assertEqual(after, before, f"参数错误不得改动已有项目：\n{detail}")

    def assert_storage_error(self, cli_args, db_path):
        """断言存储失败：退出码 1、stdout 为空、stderr 说明存储失败、无回溯。"""
        proc = self.run_cli(*cli_args, db_path=db_path)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 1, f"预期存储失败退出码 1：\n{detail}")
        self.assertEqual(proc.stdout, "", f"存储失败时标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"存储失败时标准错误应说明原因：\n{detail}")
        self.assertIn("storage failure", proc.stderr,
                      f"标准错误应说明存储失败：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr,
                         f"标准错误不应出现 Python 异常回溯：\n{detail}")

    def create_project(self, name):
        obj = self.cli_json("project-create", name)
        return {"id": obj["id"], "name": obj["name"]}

    # ---------- 用户验收场景 ----------

    def test_acceptance_api_matches_project_names_only(self):
        # 依次创建：研发 API、研发 api、研发 API（无任务）、资料（任务标题含 API）
        first = self.create_project("研发 API")
        second = self.create_project("研发 api")
        third = self.create_project("研发 API")
        docs = self.create_project("资料")
        self.cli_json("task-create", str(docs["id"]), "整理 API 文档")

        # --query "  API  "：去首尾空白后等价 API，大小写敏感只命中项目名称，
        # 恰好包含第一、第三个项目；第二个（小写 api）与第四个（名称不含 API，
        # 即使任务标题命中）均不出现
        self.assert_project_list_query("  API  ", [first, third])

    # ---------- 大小写敏感与连续子串 ----------

    def test_query_is_case_sensitive(self):
        upper = self.create_project("研发 API")
        lower = self.create_project("研发 api")
        self.assert_project_list_query("API", [upper])
        self.assert_project_list_query("api", [lower])

    def test_query_internal_whitespace_is_preserved(self):
        one_space = self.create_project("Fix API")
        two_space = self.create_project("Fix  API")
        self.assert_project_list_query("Fix API", [one_space])
        self.assert_project_list_query("Fix  API", [two_space])

    def test_query_trims_surrounding_whitespace(self):
        proj = self.create_project("研发")
        self.assert_project_list_query("  研发  ", [proj])
        self.assert_project_list_query("\t研发\t", [proj])

    def test_query_matches_contiguous_substring_anywhere(self):
        proj = self.create_project("前期研发工作")
        self.assert_project_list_query("研发", [proj])

    # ---------- 中文、%、_、引号为普通字符 ----------

    def test_query_percent_is_literal(self):
        proj = self.create_project("完成度 100%")
        self.create_project("完成度 100")
        self.assert_project_list_query("%", [proj])

    def test_query_underscore_is_literal(self):
        proj = self.create_project("project_alpha")
        self.create_project("projectXalpha")
        self.assert_project_list_query("_", [proj])

    def test_query_quote_is_literal(self):
        proj = self.create_project('他说 "你好"')
        self.create_project("他说你好")
        self.assert_project_list_query('"', [proj])

    # ---------- 只匹配项目名称：任务标题命中不算 ----------

    def test_task_title_match_does_not_include_project(self):
        no_name_match = self.create_project("资料")
        self.cli_json("task-create", str(no_name_match["id"]), "修复 API")
        self.assert_project_list_query("API", [])

    def test_projects_without_tasks_participate(self):
        with_task = self.create_project("研发组")
        without_task = self.create_project("研发二组")
        self.cli_json("task-create", str(with_task["id"]), "无关标题")
        self.assert_project_list_query("研发", [with_task, without_task])

    def test_same_named_projects_are_both_kept(self):
        first = self.create_project("研发")
        second = self.create_project("研发")
        self.assert_project_list_query("研发", [first, second])

    # ---------- 无命中、空库、省略 --query、只读 ----------

    def test_no_match_returns_empty_array(self):
        self.create_project("研发")
        self.assert_project_list_query("NO_SUCH_KEYWORD", [])

    def test_valid_query_on_missing_database_auto_creates_empty(self):
        self.assertFalse(os.path.exists(self.db_path))
        self.assert_project_list_query("API", [])
        self.assertTrue(os.path.exists(self.db_path))

    def test_omit_query_still_lists_all_projects(self):
        first = self.create_project("研发 API")
        second = self.create_project("研发 api")
        third = self.create_project("资料")
        proc = self.run_cli("project-list")
        detail = self._detail(("project-list",), proc)
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(json.loads(proc.stdout), [first, second, third], detail)

    def test_query_is_read_only_and_repeatable(self):
        first = self.create_project("研发 API")
        docs = self.create_project("资料")
        task = self.cli_json("task-create", str(docs["id"]), "整理 API 文档")
        expected = [first]
        self.assert_project_list_query("API", expected)
        self.assert_project_list_query("API", expected)
        # 项目与任务均未被筛选改动
        all_projects = [first, docs]
        self.assertEqual(self.cli_json("project-list"), all_projects)
        self.assertEqual(self.cli_json("task-list", str(docs["id"])), [task])

    # ---------- 错误路径：退出码 2 ----------

    def test_error_empty_string_query(self):
        self.create_project("研发")
        self.assert_usage_error(["project-list", "--query", ""])

    def test_error_whitespace_only_query(self):
        self.create_project("研发")
        self.assert_usage_error(["project-list", "--query", "   "])
        self.assert_usage_error(["project-list", "--query", "\t\t"])

    def test_error_missing_query_value(self):
        self.create_project("研发")
        self.assert_usage_error(["project-list", "--query"])

    # ---------- 错误路径：退出码 1（有效关键词 + 不可用存储） ----------

    def test_error_db_path_is_directory(self):
        self.assert_storage_error(
            ["project-list", "--query", "API"], self._tmpdir.name
        )

    def test_error_parent_directory_missing(self):
        missing = os.path.join(self._tmpdir.name, "no-such-dir", "x.db")
        self.assert_storage_error(["project-list", "--query", "API"], missing)

    def test_error_file_is_not_sqlite(self):
        bad = os.path.join(self._tmpdir.name, "not-a-db.db")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("这不是 SQLite 数据库文件")
        self.assert_storage_error(["project-list", "--query", "API"], bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""project-list 项目名称关键词筛选（--query）的命令行回归测试。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create 现有命令，不直接读写数据库，不依赖网络，也不依赖
任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时文件），
可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_list_query.py                  # 运行全部回归用例
    python3 -m unittest test_project_list_query -v      # 等价写法
    python3 test_project_list_query.py \\
        ProjectListQueryRegression.test_query_matches_only_project_names  # 单个用例

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

    def assert_project_list(self, cli_args, expected):
        """断言 project-list 成功并恰好返回 expected（含顺序与字段结构）。"""
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
        self.assertEqual(after, before, f"参数错误不得改动已有项目：\n{detail}")

    def assert_storage_error(self, cli_args, db_path):
        """断言存储失败：退出码 1、stdout 为空、stderr 说明存储失败、无回溯。"""
        proc = self.run_cli(*cli_args, db_path=db_path)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 1, f"预期存储失败退出码 1：\n{detail}")
        self.assertEqual(proc.stdout, "", f"存储失败时标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"存储失败时标准错误应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr,
                         f"标准错误不应出现 Python 异常回溯：\n{detail}")

    # ---------- 验收夹具：研发 API / 研发 api / 研发 API(无任务) / 资料 ----------

    def build_acceptance_fixture(self):
        first = self.cli_json("project-create", "研发 API")
        second = self.cli_json("project-create", "研发 api")
        third = self.cli_json("project-create", "研发 API")  # 同名，无任务
        material = self.cli_json("project-create", "资料")
        # “资料”项目有一条标题含 API 的任务，但名称不含 API，不应因此命中
        self.cli_json("task-create", str(material["id"]), "整理 API 文档")
        return first, second, third, material

    # ---------- 名称关键词：大小写敏感的连续子串，且只匹配项目名称 ----------

    def test_query_matches_only_project_names(self):
        # 验收场景：--query "  API  " 去首尾空白后等价于 API
        first, second, third, material = self.build_acceptance_fixture()
        ids = [first["id"], second["id"], third["id"], material["id"]]
        self.assertEqual(len(set(ids)), 4, "四个项目应取得四个不同标识")

        # 恰好包含第一个（研发 API）与第三个（研发 API、无任务），
        # 第二个（研发 api，小写）与第四个（资料，仅任务标题命中）均不出现
        self.assert_project_list(
            ["project-list", "--query", "  API  "],
            [
                {"id": first["id"], "name": "研发 API"},
                {"id": third["id"], "name": "研发 API"},
            ],
        )

    def test_query_is_case_sensitive(self):
        first, second, third, material = self.build_acceptance_fixture()
        # 小写 api 只命中第二个项目“研发 api”
        self.assert_project_list(
            ["project-list", "--query", "api"],
            [{"id": second["id"], "name": "研发 api"}],
        )

    def test_query_chinese_keyword_matches_project_name(self):
        first, second, third, material = self.build_acceptance_fixture()
        # 中文连续子串：前三个名称含“研发”，第四个“资料”不含
        self.assert_project_list(
            ["project-list", "--query", "研发"],
            [
                {"id": first["id"], "name": "研发 API"},
                {"id": second["id"], "name": "研发 api"},
                {"id": third["id"], "name": "研发 API"},
            ],
        )

    def test_query_internal_whitespace_is_preserved(self):
        spaced = self.cli_json("project-create", "A  B")
        tight = self.cli_json("project-create", "A B")
        # 内部两个空格保留：双空格关键词只命中双空格名称
        self.assert_project_list(
            ["project-list", "--query", "A  B"],
            [{"id": spaced["id"], "name": "A  B"}],
        )
        # 单空格关键词只命名单空格名称
        self.assert_project_list(
            ["project-list", "--query", "A B"],
            [{"id": tight["id"], "name": "A B"}],
        )

    def test_query_percent_underscore_and_quote_are_literal(self):
        percent = self.cli_json("project-create", "完成度 100%")
        underscore = self.cli_json("project-create", "proj_x")
        quote = self.cli_json("project-create", "他说\"你好\"")
        self.cli_json("project-create", "普通名称")
        self.assert_project_list(
            ["project-list", "--query", "%"],
            [{"id": percent["id"], "name": "完成度 100%"}],
        )
        self.assert_project_list(
            ["project-list", "--query", "_"],
            [{"id": underscore["id"], "name": "proj_x"}],
        )
        self.assert_project_list(
            ["project-list", "--query", '"'],
            [{"id": quote["id"], "name": "他说\"你好\""}],
        )

    def test_query_without_match_returns_empty_array(self):
        self.build_acceptance_fixture()
        self.assert_project_list(
            ["project-list", "--query", "NO_SUCH_KEYWORD"], []
        )

    def test_query_is_read_only_and_repeatable(self):
        first, second, third, material = self.build_acceptance_fixture()
        expected = [
            {"id": first["id"], "name": "研发 API"},
            {"id": third["id"], "name": "研发 API"},
        ]
        all_expected = [
            {"id": first["id"], "name": "研发 API"},
            {"id": second["id"], "name": "研发 api"},
            {"id": third["id"], "name": "研发 API"},
            {"id": material["id"], "name": "资料"},
        ]
        first_result = self.assert_project_list(
            ["project-list", "--query", "API"], expected
        )
        second_result = self.assert_project_list(
            ["project-list", "--query", "API"], expected
        )
        self.assertEqual(first_result, second_result, "重复查询结果应完全一致")
        # 筛选不改变全量列表、项目任务与状态
        self.assert_project_list(["project-list"], all_expected)
        material_tasks = self.cli_json("task-list", str(material["id"]))
        self.assertEqual(
            [obj["title"] for obj in material_tasks], ["整理 API 文档"]
        )
        self.assertEqual(
            [obj["status"] for obj in material_tasks], ["todo"]
        )

    # ---------- 省略 --query 时保留原有全部项目列表 ----------

    def test_omit_query_lists_all_projects(self):
        first, second, third, material = self.build_acceptance_fixture()
        self.assert_project_list(
            ["project-list"],
            [
                {"id": first["id"], "name": "研发 API"},
                {"id": second["id"], "name": "研发 api"},
                {"id": third["id"], "name": "研发 API"},
                {"id": material["id"], "name": "资料"},
            ],
        )

    # ---------- 自动建库与空结果 ----------

    def test_query_on_missing_database_auto_creates_and_returns_empty(self):
        # 父目录存在、数据库文件尚不存在：有效查询沿用自动建库，返回 []
        self.assertFalse(os.path.exists(self.db_path))
        self.assert_project_list(["project-list", "--query", "API"], [])
        self.assertTrue(os.path.exists(self.db_path))

    def test_query_on_empty_database_returns_empty_array(self):
        self.assert_project_list(["project-list", "--query", "API"], [])

    # ---------- 错误路径：退出码 2、stdout 空、stderr 说明原因、数据不变 ----------

    def test_error_empty_string_query(self):
        self.build_acceptance_fixture()
        self.assert_usage_error(["project-list", "--query", ""])

    def test_error_whitespace_only_query(self):
        self.build_acceptance_fixture()
        self.assert_usage_error(["project-list", "--query", "   "])

    def test_error_missing_query_value(self):
        self.build_acceptance_fixture()
        self.assert_usage_error(["project-list", "--query"])

    # ---------- 错误路径：有效关键词下的存储失败，退出码 1、无回溯 ----------

    def test_error_db_path_is_directory_with_query(self):
        self.assert_storage_error(
            ["project-list", "--query", "API"], self._tmpdir.name
        )

    def test_error_parent_directory_missing_with_query(self):
        missing = os.path.join(self._tmpdir.name, "no-such-dir", "x.db")
        self.assert_storage_error(["project-list", "--query", "API"], missing)

    def test_error_file_is_not_sqlite_with_query(self):
        bad = os.path.join(self._tmpdir.name, "not-a-db.db")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("这不是 SQLite 数据库文件")
        self.assert_storage_error(["project-list", "--query", "API"], bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)

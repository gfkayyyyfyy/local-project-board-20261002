#!/usr/bin/env python3
"""project-list 项目列表查询的命令行回归测试。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create / task-move 三个现有命令，不直接读写数据库，
不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时文件），
可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_list.py                  # 运行全部回归用例
    python3 -m unittest test_project_list -v      # 等价写法
    python3 test_project_list.py \\
        ProjectListRegression.test_lists_all_projects_sorted_by_id   # 只跑单个用例

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


class ProjectListRegression(unittest.TestCase):
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

    def run_cli_no_db(self, *cli_args):
        """不带 --db 执行 python -m kanban ...，返回 CompletedProcess。"""
        return subprocess.run(
            [sys.executable, "-m", "kanban", *cli_args],
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

    def assert_project_list(self, expected):
        """断言 project-list 成功并恰好返回 expected（含顺序与字段结构）。"""
        cli_args = ("project-list",)
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

    def assert_usage_error(self, cli_args, via_no_db=False):
        """断言参数失败：退出码 2、stdout 为空、stderr 有原因说明。"""
        if via_no_db:
            proc = self.run_cli_no_db(*cli_args)
        else:
            proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 2, f"预期参数错误退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"参数错误时标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"参数错误时标准错误应说明原因：\n{detail}")

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

    # ---------- 空数据库与自动创建 ----------

    def test_empty_database_returns_empty_array(self):
        # 数据库文件尚不存在（父目录存在）：自动创建并返回 []
        self.assertFalse(os.path.exists(self.db_path))
        self.assert_project_list([])
        self.assertTrue(os.path.exists(self.db_path))

    def test_auto_created_database_remains_usable(self):
        # project-list 自动创建数据库后，仍可正常创建项目并被列出
        self.assert_project_list([])
        created = self.cli_json("project-create", "研发")
        self.assert_project_list([{"id": created["id"], "name": "研发"}])

    def test_existing_empty_database_returns_empty_array(self):
        # 已存在但没有任何项目的数据库（由首次 project-list 自动创建）：返回 []
        self.assert_project_list([])
        self.assertTrue(os.path.exists(self.db_path))
        self.assert_project_list([])

    # ---------- 全部项目、升序、同名保留、名称原样 ----------

    def test_lists_all_projects_sorted_by_id(self):
        # 验收场景：两个名为“研发”的项目和一个空项目，取得三个不同标识
        first = self.cli_json("project-create", "研发")
        second = self.cli_json("project-create", "研发")
        empty = self.cli_json("project-create", "空项目")
        ids = [first["id"], second["id"], empty["id"]]
        self.assertEqual(len(set(ids)), 3, "三个项目应取得三个不同标识")

        expected = [
            {"id": first["id"], "name": "研发"},
            {"id": second["id"], "name": "研发"},
            {"id": empty["id"], "name": "空项目"},
        ]
        self.assert_project_list(expected)

        # 用其中一个标识调用 task-list，确认仍只返回该项目自己的任务
        task = self.cli_json("task-create", str(second["id"]), "二期规划")
        listed = self.cli_json("task-list", str(second["id"]))
        self.assertEqual(listed, [{
            "id": task["id"],
            "project_id": second["id"],
            "title": "二期规划",
            "status": "todo",
        }])
        # 项目列表不受任务创建影响
        self.assert_project_list(expected)

    def test_projects_without_tasks_are_included(self):
        with_tasks = self.cli_json("project-create", "有任务")
        without_tasks = self.cli_json("project-create", "无任务")
        self.cli_json("task-create", str(with_tasks["id"]), "唯一任务")
        self.assert_project_list([
            {"id": with_tasks["id"], "name": "有任务"},
            {"id": without_tasks["id"], "name": "无任务"},
        ])

    def test_names_with_quotes_and_internal_whitespace_preserved(self):
        # 中文、引号与名称内部空白按已保存的值原样返回，查询不再清理或改写
        raw_names = ['他说 "你好" 吗', "内部  两个  空格", "it's a test"]
        created = [self.cli_json("project-create", name) for name in raw_names]
        expected = [
            {"id": obj["id"], "name": obj["name"]} for obj in created
        ]
        self.assertEqual(created, expected)  # 创建返回值本身即已保存的名称
        self.assert_project_list(expected)

    def test_repeated_queries_are_identical_and_moves_do_not_change_list(self):
        proj = self.cli_json("project-create", "研发")
        task = self.cli_json("task-create", str(proj["id"]), "待办")
        expected = [{"id": proj["id"], "name": "研发"}]
        first = self.assert_project_list(expected)
        second = self.assert_project_list(expected)
        self.assertEqual(first, second, "未发生项目创建时重复查询应完全一致")
        self.cli_json("task-move", str(task["id"]), "doing")
        self.cli_json("task-move", str(task["id"]), "done")
        self.assert_project_list(expected)

    def test_query_is_read_only(self):
        # 查询前后项目与任务内容均不变
        proj = self.cli_json("project-create", "研发")
        task = self.cli_json("task-create", str(proj["id"]), "任务甲")
        before_tasks = self.cli_json("task-list", str(proj["id"]))
        before_projects = self.assert_project_list(
            [{"id": proj["id"], "name": "研发"}]
        )
        self.assert_project_list(before_projects)
        after_tasks = self.cli_json("task-list", str(proj["id"]))
        self.assertEqual(after_tasks, before_tasks)
        self.assertEqual(after_tasks[0]["id"], task["id"])

    # ---------- 错误路径：退出码 2 ----------

    def test_error_missing_db_option(self):
        self.assert_usage_error(["project-list"], via_no_db=True)

    def test_error_missing_db_value(self):
        proc = subprocess.run(
            [sys.executable, "-m", "kanban", "--db"],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertNotEqual(proc.stderr.strip(), "")

    def test_error_extra_positional_argument(self):
        self.assert_project_list([])
        self.assert_usage_error(["project-list", "多余参数"])
        self.assert_project_list([])

    def test_error_unknown_option(self):
        self.assert_project_list([])
        self.assert_usage_error(["project-list", "--unknown"])
        self.assert_project_list([])

    # ---------- 错误路径：退出码 1 ----------

    def test_error_db_path_is_directory(self):
        self.assert_storage_error(["project-list"], self._tmpdir.name)

    def test_error_parent_directory_missing(self):
        missing = os.path.join(self._tmpdir.name, "no-such-dir", "x.db")
        self.assert_storage_error(["project-list"], missing)

    def test_error_file_is_not_sqlite(self):
        bad = os.path.join(self._tmpdir.name, "not-a-db.db")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("这不是 SQLite 数据库文件")
        self.assert_storage_error(["project-list"], bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)

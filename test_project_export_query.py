#!/usr/bin/env python3
"""project-export 可选 --query 标题关键词筛选的命令行回归测试。

覆盖用户指定的验收场景与边界行为：

- 两个同名项目：项目一有两条标题为 Fix API 的任务（todo / doing 各一），
  另有一条 done 的 Fix api；项目二也有一条 Fix API。
  ``project-export <项目一> --query " API "`` 退出码 0、stderr 为空，stdout
  为单个 JSON 对象且顶层恰好包含 project 与 tasks：project 原样保留
  （id/name 与 project-list 一致），tasks 只含项目一的两条 Fix API，
  按任务标识数值升序，字段和值与同参数的 task-list --query 查询逐字一致；
  项目二的同名任务不串入，done 的 Fix api 不命中。省略 --query 时导出
  项目一全部三条任务。
- 关键词语义与 task-list --query 完全一致：只去除首尾空白（空格、制表符），
  内部空白原样保留；大小写敏感的连续子串匹配，不分词、不归一化；中文、
  %、_、单双引号均为普通字符；只匹配任务标题，仅项目名称含关键词不命中。
- 存在但无任务的项目、或筛选后无命中，均成功返回项目对象与空 tasks 数组；
  同标题任务按各自标识分别保留。
- 成功筛选与重复筛选为只读：项目列表、任务内容与 project-stats 统计在
  成功筛选、重复筛选及参数拒绝前后完全一致。
- 错误路径：空字符串、纯空白关键词、--query 缺值均退出码 2、stdout 为空、
  stderr 说明原因且无 Python 回溯；合法关键词配合不存在的项目同样退出码 2，
  原因指向项目不存在。
- 有效参数配合 --db 指向已有目录时退出码 1、stdout 为空、stderr 含
  storage failure 且无回溯。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create / task-move 三个现有命令，不直接读写数据库，
不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时文件），
可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用者自己的数据库。
预期标识全部取自创建命令的返回值，不依赖固定编号、JSON 键序或错误文案的完整
措辞；失败报告会给出输入参数、实际退出码、两路输出以及实际与预期的差异。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_export_query.py                  # 运行全部回归用例
    python3 -m unittest test_project_export_query -v      # 等价写法
    python3 test_project_export_query.py \\
        ProjectExportQueryRegression.test_acceptance_space_padded_api_query

退出码：全部通过为 0，存在失败为 1。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

PROJECT_FIELDS = {"id", "name"}
TASK_FIELDS = {"id", "project_id", "title", "status"}
EXPORT_FIELDS = {"project", "tasks"}


class ProjectExportQueryRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-exportq-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")

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

    @staticmethod
    def task(tid, project_id, title, status="todo"):
        """按建数返回值构造与 task-list 一致的任务对象。"""
        return {"id": tid, "project_id": project_id,
                "title": title, "status": status}

    def assert_export(self, cli_args, expected_project, expected_tasks):
        """断言导出成功并返回结构、内容、顺序完全一致的 {project, tasks}。"""
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, f"预期成功退出码 0：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        try:
            # 整段 stdout 必须恰好是一个 JSON 值：多余文本会解析失败
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"标准输出不是合法 JSON（应只有一个 JSON 对象）：\n{detail}")

        self.assertIsInstance(data, dict, f"输出应为 JSON 对象：\n{detail}")
        self.assertEqual(
            set(data.keys()), EXPORT_FIELDS,
            f"顶层应恰好包含 project 与 tasks：\n{detail}",
        )

        self.assertIsInstance(data["project"], dict, f"project 应为对象：\n{detail}")
        self.assertEqual(set(data["project"].keys()), PROJECT_FIELDS, detail)
        self.assertEqual(
            data["project"], expected_project, f"项目对象应原样保留：\n{detail}"
        )

        self.assertIsInstance(data["tasks"], list, f"tasks 应为数组：\n{detail}")
        self.assertEqual(
            data["tasks"], expected_tasks,
            f"任务集合、字段值或顺序不符（实际与预期差异见上）：\n{detail}",
        )
        for obj in data["tasks"]:
            self.assertIsInstance(obj, dict, f"数组元素应为任务对象：\n{detail}")
            self.assertEqual(
                set(obj.keys()), TASK_FIELDS,
                f"任务对象字段应与 task-list 一致：\n{detail}",
            )
        ids = [obj["id"] for obj in data["tasks"]]
        self.assertEqual(ids, sorted(ids), f"任务应按标识数值升序：\n{detail}")
        return data

    def assert_rejected(self, cli_args):
        """断言退出码 2、stdout 为空、stderr 有原因且无 Python 回溯。"""
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 2, f"预期参数错误退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"被拒绝时标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"被拒绝时标准错误应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr,
                         f"不得出现 Python 异常回溯：\n{detail}")
        return proc

    def snapshot(self):
        """记录全部项目、各项目任务与统计，用于校验请求无副作用。"""
        projects = self.cli_json("project-list")
        per_project = {}
        for project in projects:
            pid = str(project["id"])
            per_project[pid] = (
                self.cli_json("task-list", pid),
                self.cli_json("project-stats", pid),
            )
        return {"projects": projects, "per_project": per_project}

    # ---------- 通过公开命令准备用户指定的验收夹具 ----------

    def build_acceptance_fixture(self):
        """两个同名“研发”项目：

        项目一：Fix API(todo)、Fix API(doing)、Fix api(done)；
        项目二：Fix API(todo)。

        标识全部取自创建返回值，返回 (项目一, 项目二, 三条项目一任务,
        项目二任务) 的标识。
        """
        p1 = self.cli_json("project-create", "研发")["id"]
        p2 = self.cli_json("project-create", "研发")["id"]
        fix_todo = self.cli_json("task-create", str(p1), "Fix API")["id"]
        fix_doing = self.cli_json("task-create", str(p1), "Fix API")["id"]
        fix_lower = self.cli_json("task-create", str(p1), "Fix api")["id"]
        self.cli_json("task-move", str(fix_doing), "doing")
        self.cli_json("task-move", str(fix_lower), "done")
        other = self.cli_json("task-create", str(p2), "Fix API")["id"]
        return p1, p2, fix_todo, fix_doing, fix_lower, other

    # ---------- 用户指定的主验收场景 ----------

    def test_acceptance_space_padded_api_query(self):
        p1, p2, fix_todo, fix_doing, fix_lower, other = (
            self.build_acceptance_fixture()
        )

        # --query " API "：首尾空白被去除，实际按连续子串 "API" 匹配；
        # 只命中项目一两条大写 Fix API（todo/doing），小写 Fix api(done)
        # 与项目二的同名任务均不出现
        cli_args = ["project-export", str(p1), "--query", " API "]
        data = self.assert_export(
            cli_args,
            {"id": p1, "name": "研发"},
            [
                self.task(fix_todo, p1, "Fix API", "todo"),
                self.task(fix_doing, p1, "Fix API", "doing"),
            ],
        )

        # 任务字段和值与现有任务查询（task-list 同参数）逐字一致
        listed = self.cli_json("task-list", str(p1), "--query", " API ")
        self.assertEqual(data["tasks"], listed)
        self.assertEqual(
            sorted(obj["id"] for obj in data["tasks"]),
            [fix_todo, fix_doing],
        )
        self.assertNotIn(fix_lower, [obj["id"] for obj in data["tasks"]])
        self.assertNotIn(other, [obj["id"] for obj in data["tasks"]])

        # project 对象与 project-list 中的同名第一项逐字一致
        projects = self.cli_json("project-list")
        self.assertIn(data["project"], projects)

    def test_omit_query_exports_all_three_tasks(self):
        p1, _p2, fix_todo, fix_doing, fix_lower, _other = (
            self.build_acceptance_fixture()
        )
        # 省略关键词：项目一全部三条任务，按标识数值升序
        self.assert_export(
            ["project-export", str(p1)],
            {"id": p1, "name": "研发"},
            [
                self.task(fix_todo, p1, "Fix API", "todo"),
                self.task(fix_doing, p1, "Fix API", "doing"),
                self.task(fix_lower, p1, "Fix api", "done"),
            ],
        )

    def test_other_same_named_project_query_returns_its_own_task(self):
        _p1, p2, _fix_todo, _fix_doing, _fix_lower, other = (
            self.build_acceptance_fixture()
        )
        # 同名项目二按同样关键词筛选只返回它自己的任务
        self.assert_export(
            ["project-export", str(p2), "--query", "API"],
            {"id": p2, "name": "研发"},
            [self.task(other, p2, "Fix API", "todo")],
        )

    # ---------- 关键词：首尾空白去除、内部空白保留 ----------

    def test_outer_whitespace_trimmed_internal_whitespace_preserved(self):
        pid = self.cli_json("project-create", "空白")["id"]
        single = self.cli_json("task-create", str(pid), "Fix API")["id"]
        double = self.cli_json("task-create", str(pid), "Fix  API")["id"]
        lower = self.cli_json("task-create", str(pid), "fix api")["id"]
        project = {"id": pid, "name": "空白"}
        id_by_title = {"Fix API": single, "Fix  API": double, "fix api": lower}

        cases = [
            # 内部单空格连续子串：只命中单空格标题
            ("Fix API", "Fix API"),
            # 首尾空格被去除，等价于 "Fix API"
            (" Fix API ", "Fix API"),
            # 制表符同样作为首尾空白被去除
            ("\tFix API\t", "Fix API"),
            # 内部两个空格原样保留：只命中双空格标题
            ("Fix  API", "Fix  API"),
            # 首尾去除后内部双空格仍保留
            ("  Fix  API  ", "Fix  API"),
        ]
        for keyword, expected_title in cases:
            with self.subTest(keyword=keyword):
                self.assert_export(
                    ["project-export", str(pid), "--query", keyword],
                    project,
                    [self.task(id_by_title[expected_title], pid, expected_title)],
                )

        # 内部双空格 + 小写 api 的连续子串不存在于任何标题
        # （双空格标题为大写 API，小写标题只有单空格）：空数组但项目对象保留
        self.assert_export(
            ["project-export", str(pid), "--query", "Fix  api"], project, []
        )

    # ---------- 关键词：大小写敏感的连续子串 ----------

    def test_case_sensitive_contiguous_substring(self):
        pid = self.cli_json("project-create", "大小写")["id"]
        upper_single = self.cli_json("task-create", str(pid), "Fix API")["id"]
        upper_double = self.cli_json("task-create", str(pid), "Fix  API")["id"]
        lower = self.cli_json("task-create", str(pid), "fix api")["id"]
        project = {"id": pid, "name": "大小写"}

        # 大写 API 命中两条大写标题，按标识升序
        self.assert_export(
            ["project-export", str(pid), "--query", "API"],
            project,
            [
                self.task(upper_single, pid, "Fix API"),
                self.task(upper_double, pid, "Fix  API"),
            ],
        )
        # 小写 api 只命中小写标题
        self.assert_export(
            ["project-export", str(pid), "--query", "api"],
            project, [self.task(lower, pid, "fix api")],
        )
        # 混合大小写不做归一化：无命中
        self.assert_export(
            ["project-export", str(pid), "--query", "Api"], project, []
        )

    # ---------- 关键词：中文、%、_、引号均为普通字符 ----------

    def test_chinese_percent_underscore_and_quotes_are_literal(self):
        pid = self.cli_json("project-create", "literal")["id"]
        titles = ["修复登录", "100%", "item_name", 'Bob\'s "quote"', "plain"]
        ids = {}
        for title in titles:
            ids[title] = self.cli_json("task-create", str(pid), title)["id"]
        project = {"id": pid, "name": "literal"}

        for keyword, expected_title in (
            ("登录", "修复登录"),
            ("%", "100%"),
            ("_", "item_name"),
            ("'", 'Bob\'s "quote"'),
            ('"', 'Bob\'s "quote"'),
        ):
            with self.subTest(keyword=keyword):
                self.assert_export(
                    ["project-export", str(pid), "--query", keyword],
                    project,
                    [self.task(ids[expected_title], pid, expected_title)],
                )

    # ---------- 仅项目名称含关键词不命中任务 ----------

    def test_keyword_in_project_name_only_does_not_match_tasks(self):
        pid = self.cli_json("project-create", "API 项目组")["id"]
        self.cli_json("task-create", str(pid), "Docs")
        # 项目名称含 API 而任务标题不含：成功返回项目对象与空任务数组
        self.assert_export(
            ["project-export", str(pid), "--query", "API"],
            {"id": pid, "name": "API 项目组"}, [],
        )

    # ---------- 空项目、无命中、同标题任务 ----------

    def test_empty_project_returns_project_object_and_empty_tasks(self):
        pid = self.cli_json("project-create", "空项目")["id"]
        self.assert_export(
            ["project-export", str(pid), "--query", "API"],
            {"id": pid, "name": "空项目"}, [],
        )

    def test_no_match_returns_project_object_and_empty_tasks(self):
        p1, _p2, _fix_todo, _fix_doing, _fix_lower, _other = (
            self.build_acceptance_fixture()
        )
        self.assert_export(
            ["project-export", str(p1), "--query", "NO_SUCH_KEYWORD"],
            {"id": p1, "name": "研发"}, [],
        )

    def test_same_titled_tasks_both_retained_sorted_by_id(self):
        p1, _p2, fix_todo, fix_doing, _fix_lower, _other = (
            self.build_acceptance_fixture()
        )
        # 两条同标题 Fix API 分别保留，标识不同、按数值升序
        data = self.assert_export(
            ["project-export", str(p1), "--query", "Fix API"],
            {"id": p1, "name": "研发"},
            [
                self.task(fix_todo, p1, "Fix API", "todo"),
                self.task(fix_doing, p1, "Fix API", "doing"),
            ],
        )
        returned_ids = [obj["id"] for obj in data["tasks"]]
        self.assertEqual(len(returned_ids), 2)
        self.assertEqual(len(set(returned_ids)), 2)
        self.assertEqual(returned_ids, sorted(returned_ids))

    # ---------- 成功筛选、重复筛选只读一致 ----------

    def test_repeated_filters_identical_and_read_only(self):
        p1, p2, _fix_todo, _fix_doing, _fix_lower, _other = (
            self.build_acceptance_fixture()
        )
        before = self.snapshot()
        first = self.run_cli("project-export", str(p1), "--query", " API ")
        for _ in range(3):
            again = self.run_cli(
                "project-export", str(p1), "--query", "\t API \t"
            )
            self.assertEqual(
                (again.returncode, again.stdout, again.stderr),
                (first.returncode, first.stdout, first.stderr),
            )
        # 不带关键词的全量导出也不改变数据
        self.run_cli("project-export", str(p2))
        self.assertEqual(self.snapshot(), before)

    # ---------- 错误路径：退出码 2、stdout 空、stderr 说明原因、无回溯 ----------

    def test_data_unchanged_across_success_repeats_and_rejections(self):
        p1, p2, _fix_todo, _fix_doing, _fix_lower, _other = (
            self.build_acceptance_fixture()
        )
        before = self.snapshot()

        # 成功筛选（含重复）不改动数据
        for cli_args in (
            ["project-export", str(p1), "--query", "API"],
            ["project-export", str(p1), "--query", "API"],
            ["project-export", str(p2), "--query", "api"],
        ):
            proc = self.run_cli(*cli_args)
            self.assertEqual(proc.returncode, 0, self._detail(cli_args, proc))

        # 各类参数拒绝同样不改动项目列表、任务内容与统计
        for cli_args in (
            ["project-export", str(p1), "--query", ""],
            ["project-export", str(p1), "--query", "   "],
            ["project-export", str(p1), "--query", "\t \t"],
            ["project-export", str(p1), "--query"],
            ["project-export", "999", "--query", "API"],
        ):
            with self.subTest(cli_args=cli_args):
                self.assert_rejected(cli_args)

        self.assertEqual(self.snapshot(), before)

    def test_error_empty_or_whitespace_query_and_missing_value(self):
        p1, _p2, _fix_todo, _fix_doing, _fix_lower, _other = (
            self.build_acceptance_fixture()
        )
        before = self.snapshot()
        # 空字符串、纯空白（空格/制表符混合）关键词
        for raw in ("", "   ", "\t\t", " \t "):
            with self.subTest(raw=raw):
                self.assert_rejected(
                    ["project-export", str(p1), "--query", raw]
                )
        # --query 出现在末尾但缺值：argparse 同样按退出码 2 拒绝
        self.assert_rejected(["project-export", str(p1), "--query"])
        self.assertEqual(self.snapshot(), before)

    def test_error_valid_keyword_nonexistent_project(self):
        self.build_acceptance_fixture()
        before = self.snapshot()
        # 合法关键词 + 不存在的项目：失败形式与参数错误一致，原因指向项目不存在
        proc = self.assert_rejected(
            ["project-export", "999", "--query", "API"]
        )
        self.assertIn("does not exist", proc.stderr)
        self.assertEqual(self.snapshot(), before)

    # ---------- 存储失败 ----------

    def test_storage_failure_when_db_is_directory(self):
        self.build_acceptance_fixture()
        dir_path = os.path.join(self._tmpdir.name, "cannot_be_a_db")
        os.mkdir(dir_path)
        # 有效参数（合法项目标识与关键词）配合数据库路径指向已有目录
        proc = self.run_cli(
            "project-export", "1", "--query", "API", db_path=dir_path
        )
        detail = self._detail(
            ("project-export", "1", "--query", "API"), proc
        )
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)

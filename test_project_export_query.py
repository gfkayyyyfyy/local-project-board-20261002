#!/usr/bin/env python3
"""project-export --query 标题关键词筛选的命令行回归测试。

覆盖需求：

- 用户指定的主验收场景：两个同名项目，项目一有两条标题为 ``Fix API`` 的
  任务（todo / doing 各一条），另有一条 done 状态的 ``Fix api``；项目二也有
  一条 ``Fix API``。对项目一执行 ``project-export <id> --query " API "``：
  退出码 0、标准错误为空，标准输出为单个 JSON 对象，顶层恰好包含 project 与
  tasks；project 对象原样保留（与 project-list 中单项目结构逐字一致），
  tasks 只含项目一自己的两条 ``Fix API``（不含 ``Fix api``，也不含项目二的
  同名任务），按任务标识数值升序，字段和值与 task-list 同条件查询完全一致。
  省略 --query 时返回项目一全部三条任务。
- 关键词语义与 task-list / project-stats 的 --query 一致：只去除首尾空白，
  内部空白保留；大小写敏感的连续子串匹配，不分词、不归一化；中文、%、_、
  单双引号均为普通字符；只匹配任务标题，仅项目名称含关键词不能命中任务。
- 存在但无任务的项目、以及筛选后无命中，均成功返回 project 对象与空 tasks
  数组；同标题任务按各自标识分别保留。
- 拒绝路径：空字符串、纯空白关键词以及 --query 缺值均退出码 2、标准输出为空、
  标准错误说明原因且无 Python 回溯；合法关键词配合不存在的项目同样退出码 2，
  原因指向项目不存在。
- 存储失败：有效参数配合 --db 指向已有目录时退出码 1、标准输出为空、标准错误
  包含 storage failure。
- 只读一致性：成功筛选、重复筛选及参数拒绝前后，project-list、各项目
  task-list 与 project-stats 完全一致。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create / task-move 三个现有命令，不直接读写数据库，
不依赖网络，也不依赖任何预先保存的项目。每个用例在独立临时目录使用全新隔离
SQLite 数据库，可单独执行、可重复执行，结束后自动清理。预期标识全部取自
创建命令的返回值，不依赖固定编号、JSON 键序或完整错误措辞；失败报告包含
命令行输入、实际退出码、两路输出以及实际与预期的差异。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_export_query.py                  # 运行全部回归用例
    python3 -m unittest test_project_export_query -v      # 等价写法
    python3 -m unittest discover -p 'test_project_export_query.py' -v
    python3 test_project_export_query.py \\
        ProjectExportQueryRegression.test_acceptance_query_filters_within_project

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
SAME_PROJECT_NAME = "研发"


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
    def _task_obj(created, status=None):
        """由 task-create 返回值构造期望任务对象（移动后传入最新状态）。"""
        task = dict(created)
        if status is not None:
            task["status"] = status
        return task

    def assert_export_ok(self, cli_args, expected_project, expected_tasks):
        """断言 project-export 成功，且结构与内容逐字符合预期。

        - 退出码 0、标准错误为空、标准输出只有一个 JSON 对象；
        - 顶层恰好包含 project 与 tasks；
        - project 为只含 id/name 的项目对象且与 expected_project 逐字相等；
        - tasks 为任务对象数组，与 expected_tasks 完全相等（含字段、值、顺序），
          并按任务标识数值升序。
        返回解析后的输出，供调用方做额外核对。
        """
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, f"预期成功退出码 0：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"标准输出不是合法 JSON（应只有一个 JSON 对象）：\n{detail}")

        self.assertIsInstance(data, dict, f"输出应为 JSON 对象：\n{detail}")
        self.assertEqual(
            set(data.keys()), EXPORT_FIELDS,
            f"顶层应恰好包含 project 与 tasks：\n{detail}",
        )

        self.assertIsInstance(data["project"], dict, f"project 应为对象：\n{detail}")
        self.assertEqual(
            set(data["project"].keys()), PROJECT_FIELDS,
            f"项目对象字段结构不符：\n{detail}",
        )
        self.assertEqual(
            data["project"], expected_project,
            f"项目对象应原样保留：\n实际={data['project']!r}\n{detail}",
        )

        self.assertIsInstance(data["tasks"], list, f"tasks 应为数组：\n{detail}")
        self.assertEqual(
            data["tasks"], expected_tasks,
            f"任务集合、字段值或顺序不符：\n{detail}",
        )
        for task in data["tasks"]:
            self.assertIsInstance(task, dict, f"数组元素应为任务对象：\n{detail}")
            self.assertEqual(
                set(task.keys()), TASK_FIELDS,
                f"任务对象字段结构不符：\n{detail}",
            )
        ids = [task["id"] for task in data["tasks"]]
        self.assertEqual(ids, sorted(ids), f"任务应按标识数值升序：\n{detail}")
        return data

    def assert_usage_error(self, cli_args):
        """断言退出码 2、stdout 为空、stderr 有原因且无回溯，且数据不变。"""
        before = self._snapshot_all()
        proc = self.run_cli(*cli_args)
        after = self._snapshot_all()
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 2, f"预期参数错误退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"参数错误时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"参数错误时标准错误应说明原因：\n{detail}",
        )
        self.assertNotIn(
            "Traceback", proc.stderr,
            f"标准错误不应包含 Python 异常回溯：\n{detail}",
        )
        self.assertEqual(after, before, f"失败请求不得改动已有数据：\n{detail}")
        return proc

    def _snapshot_all(self):
        """记录全部项目、各项目任务与统计，用于校验请求无副作用。"""
        projects = self.cli_json("project-list")
        snapshot = {"projects": projects, "tasks": {}, "stats": {}}
        for project in projects:
            pid = str(project["id"])
            snapshot["tasks"][pid] = self.cli_json("task-list", pid)
            snapshot["stats"][pid] = self.cli_json("project-stats", pid)
        return snapshot

    # ---------- 通过公开命令准备用户指定的主验收夹具 ----------

    def _build_two_same_named_projects(self):
        """两个同名“研发”项目：

        项目一：t1 Fix API(todo)、t2 Fix API(doing)、t3 Fix api(done)；
        项目二：t4 Fix API(todo)。
        所有标识取自创建返回值，状态以 task-move 之后的独立查询为准。
        返回一个含全部创建返回对象（含移动后状态）的字典。
        """
        p1 = self.cli_json("project-create", SAME_PROJECT_NAME)
        p2 = self.cli_json("project-create", SAME_PROJECT_NAME)

        t1 = self.cli_json("task-create", str(p1["id"]), "Fix API")
        t2 = self.cli_json("task-create", str(p1["id"]), "Fix API")
        t3 = self.cli_json("task-create", str(p1["id"]), "Fix api")
        self.cli_json("task-move", str(t2["id"]), "doing")
        self.cli_json("task-move", str(t3["id"]), "done")
        t2 = self._task_obj(t2, "doing")
        t3 = self._task_obj(t3, "done")

        t4 = self.cli_json("task-create", str(p2["id"]), "Fix API")

        return {"p1": p1, "p2": p2, "t1": t1, "t2": t2, "t3": t3, "t4": t4}

    # ---------- 用户指定的主验收场景 ----------

    def test_acceptance_query_filters_within_project(self):
        fx = self._build_two_same_named_projects()
        p1, t1, t2, t3, t4 = fx["p1"], fx["t1"], fx["t2"], fx["t3"], fx["t4"]

        args = ("project-export", str(p1["id"]), "--query", " API ")
        data = self.assert_export_ok(
            args,
            {"id": p1["id"], "name": SAME_PROJECT_NAME},
            [t1, t2],  # 两条 Fix API：todo、doing，按标识升序
        )

        # "Fix api"(done) 与项目二的同名任务均不出现
        returned_ids = [task["id"] for task in data["tasks"]]
        self.assertNotIn(t3["id"], returned_ids)
        self.assertNotIn(t4["id"], returned_ids)

        # project 对象与 project-list 中该项目逐字一致（同名项目按标识区分）
        projects = self.cli_json("project-list")
        self.assertIn({"id": p1["id"], "name": SAME_PROJECT_NAME}, projects)
        self.assertEqual(
            data["project"],
            next(p for p in projects if p["id"] == p1["id"]),
        )

        # tasks 字段和值与现有 task-list 同条件查询完全一致
        self.assertEqual(
            data["tasks"],
            self.cli_json("task-list", str(p1["id"]), "--query", " API "),
        )

    def test_omit_query_returns_all_three_project_one_tasks(self):
        fx = self._build_two_same_named_projects()
        p1, t1, t2, t3, t4 = fx["p1"], fx["t1"], fx["t2"], fx["t3"], fx["t4"]

        # 省略关键词：项目一全部三条任务，按标识升序；项目二任务不串入
        data = self.assert_export_ok(
            ("project-export", str(p1["id"])),
            {"id": p1["id"], "name": SAME_PROJECT_NAME},
            [t1, t2, t3],
        )
        self.assertNotIn(t4["id"], [task["id"] for task in data["tasks"]])

        # 与无关键词 task-list 完全一致
        self.assertEqual(
            data["tasks"], self.cli_json("task-list", str(p1["id"]))
        )
        # project-stats 也覆盖同样三条任务
        self.assertEqual(
            self.cli_json("project-stats", str(p1["id"])),
            {"project_id": p1["id"], "total": 3,
             "todo": 1, "doing": 1, "done": 1},
        )

    def test_other_same_named_project_query_returns_its_own_task(self):
        # 项目二按同样关键词导出，只返回它自己的 Fix API
        fx = self._build_two_same_named_projects()
        p2, t4 = fx["p2"], fx["t4"]
        self.assert_export_ok(
            ("project-export", str(p2["id"]), "--query", "API"),
            {"id": p2["id"], "name": SAME_PROJECT_NAME},
            [t4],
        )

    # ---------- 关键词：只去首尾空白、内部空白保留 ----------

    def test_keyword_trims_surrounding_but_preserves_internal_whitespace(self):
        project = self.cli_json("project-create", "空格")
        single = self.cli_json("task-create", str(project["id"]), "Fix API")
        double = self.cli_json("task-create", str(project["id"]), "Fix  API")

        # 首尾空格/制表符被去除：等价于 Fix API，只命中单空格标题
        self.assert_export_ok(
            ("project-export", str(project["id"]), "--query", "  Fix API  "),
            {"id": project["id"], "name": "空格"},
            [single],
        )
        self.assert_export_ok(
            ("project-export", str(project["id"]), "--query", "\tFix API\t"),
            {"id": project["id"], "name": "空格"},
            [single],
        )
        # 内部双空格保留：只命中双空格标题
        self.assert_export_ok(
            ("project-export", str(project["id"]), "--query", "Fix  API"),
            {"id": project["id"], "name": "空格"},
            [double],
        )
        # 三个空格的连续子串谁都不命中：项目对象仍返回、任务为空
        self.assert_export_ok(
            ("project-export", str(project["id"]), "--query", "Fix   API"),
            {"id": project["id"], "name": "空格"},
            [],
        )

    # ---------- 关键词：大小写敏感的连续子串 ----------

    def test_keyword_is_case_sensitive_contiguous_substring(self):
        fx = self._build_two_same_named_projects()
        p1, t1, t2, t3 = fx["p1"], fx["t1"], fx["t2"], fx["t3"]

        # 大写 API：命中两条 Fix API（todo、doing）
        self.assert_export_ok(
            ("project-export", str(p1["id"]), "--query", "API"),
            {"id": p1["id"], "name": SAME_PROJECT_NAME},
            [t1, t2],
        )
        # 小写 api 只命中 done 的 Fix api
        self.assert_export_ok(
            ("project-export", str(p1["id"]), "--query", "api"),
            {"id": p1["id"], "name": SAME_PROJECT_NAME},
            [t3],
        )
        # 大小写不匹配或非连续子串：无命中
        for keyword in ("FIX API", "fix api", "Fix  API", "A P I", "FixAPI"):
            with self.subTest(keyword=keyword):
                self.assert_export_ok(
                    ("project-export", str(p1["id"]), "--query", keyword),
                    {"id": p1["id"], "name": SAME_PROJECT_NAME},
                    [],
                )

    # ---------- 关键词：中文、%、_、引号均为普通字符 ----------

    def test_keyword_chinese_percent_underscore_and_quotes_are_literal(self):
        project = self.cli_json("project-create", "字面量")
        titles = [
            "修复登录",       # 中文
            "100%",         # 百分号
            "item_name",    # 下划线
            "Bob's task",   # 单引号
            'say "hi"',     # 双引号
            "plain",        # 不含任何特殊字符的对照任务
        ]
        created = {}
        for title in titles:
            created[title] = self.cli_json(
                "task-create", str(project["id"]), title
            )

        for keyword, expected_title in (
            ("登录", "修复登录"),
            ("%", "100%"),
            ("_", "item_name"),
            ("'", "Bob's task"),
            ('"', 'say "hi"'),
            ("修复登录", "修复登录"),
        ):
            with self.subTest(keyword=keyword):
                self.assert_export_ok(
                    ("project-export", str(project["id"]),
                     "--query", keyword),
                    {"id": project["id"], "name": "字面量"},
                    [created[expected_title]],
                )

        # 组合子串同样按普通字符连续匹配
        self.assert_export_ok(
            ("project-export", str(project["id"]), "--query", "0%"),
            {"id": project["id"], "name": "字面量"},
            [created["100%"]],
        )

    # ---------- 只匹配任务标题，不匹配项目名称 ----------

    def test_keyword_in_project_name_alone_does_not_match_tasks(self):
        # 项目名称含 Fix API，但任务标题均不含：导出仍成功，tasks 为空
        project = self.cli_json("project-create", "Fix API 团队")
        docs = self.cli_json("task-create", str(project["id"]), "Docs")
        self.cli_json("task-create", str(project["id"]), "文档整理")

        for keyword in ("API", "Fix API", "团队"):
            with self.subTest(keyword=keyword):
                self.assert_export_ok(
                    ("project-export", str(project["id"]),
                     "--query", keyword),
                    {"id": project["id"], "name": "Fix API 团队"},
                    [],
                )

        # 标题实际可匹配的关键词仍正常命中
        self.assert_export_ok(
            ("project-export", str(project["id"]), "--query", "Docs"),
            {"id": project["id"], "name": "Fix API 团队"},
            [docs],
        )

    # ---------- 空项目、无命中、同标题任务 ----------

    def test_empty_project_returns_project_object_with_empty_tasks(self):
        project = self.cli_json("project-create", "空项目")
        self.assert_export_ok(
            ("project-export", str(project["id"]), "--query", "API"),
            {"id": project["id"], "name": "空项目"},
            [],
        )
        # 省略关键词的空项目同样返回空数组
        self.assert_export_ok(
            ("project-export", str(project["id"])),
            {"id": project["id"], "name": "空项目"},
            [],
        )

    def test_no_match_returns_project_object_with_empty_tasks(self):
        fx = self._build_two_same_named_projects()
        p1 = fx["p1"]
        self.assert_export_ok(
            ("project-export", str(p1["id"]),
             "--query", "NO_SUCH_KEYWORD_不存在"),
            {"id": p1["id"], "name": SAME_PROJECT_NAME},
            [],
        )

    def test_same_titled_tasks_are_each_retained_sorted_by_id(self):
        project = self.cli_json("project-create", "同标题")
        a = self.cli_json("task-create", str(project["id"]), "Fix API")
        b = self.cli_json("task-create", str(project["id"]), "Fix API")
        c = self.cli_json("task-create", str(project["id"]), "Fix API")
        self.cli_json("task-move", str(b["id"]), "doing")
        self.cli_json("task-move", str(c["id"]), "done")
        b = self._task_obj(b, "doing")
        c = self._task_obj(c, "done")

        # 三条同标题任务分别保留，按标识数值升序，状态各自不变
        data = self.assert_export_ok(
            ("project-export", str(project["id"]), "--query", "Fix API"),
            {"id": project["id"], "name": "同标题"},
            [a, b, c],
        )
        self.assertEqual(
            [task["status"] for task in data["tasks"]],
            ["todo", "doing", "done"],
        )

    # ---------- 成功筛选、重复筛选只读且数据一致 ----------

    def test_successful_and_repeated_filters_keep_data_consistent(self):
        fx = self._build_two_same_named_projects()
        p1, p2 = fx["p1"], fx["p2"]
        before = self._snapshot_all()

        queries = [
            ("project-export", str(p1["id"]), "--query", " API "),
            ("project-export", str(p1["id"]), "--query", "api"),
            ("project-export", str(p1["id"]), "--query", " API "),
            ("project-export", str(p1["id"])),
            ("project-export", str(p2["id"]), "--query", "API"),
        ]
        seen = {}
        for cli_args in queries:
            proc = self.run_cli(*cli_args)
            detail = self._detail(cli_args, proc)
            self.assertEqual(proc.returncode, 0, detail)
            self.assertEqual(proc.stderr, "", detail)
            # 相同请求重复执行结果逐字一致
            key = tuple(cli_args)
            if key in seen:
                self.assertEqual(
                    (proc.returncode, proc.stdout, proc.stderr), seen[key],
                    f"重复筛选结果不一致：\n{detail}",
                )
            else:
                seen[key] = (proc.returncode, proc.stdout, proc.stderr)

        # 筛选前后项目列表、任务内容与统计完全一致
        self.assertEqual(self._snapshot_all(), before)

    def test_filter_result_matches_independent_process_and_task_list(self):
        fx = self._build_two_same_named_projects()
        p1, t1, t2 = fx["p1"], fx["t1"], fx["t2"]

        # 独立命令进程执行导出，结果与同条件 task-list、project-list 一致
        proc = subprocess.run(
            [sys.executable, "-m", "kanban", "--db", self.db_path,
             "project-export", str(p1["id"]), "--query", " API "],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        detail = self._detail(
            ("project-export", str(p1["id"]), "--query", " API "), proc
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(
            json.loads(proc.stdout),
            {
                "project": {"id": p1["id"], "name": SAME_PROJECT_NAME},
                "tasks": [t1, t2],
            },
        )

    # ---------- 拒绝路径：退出码 2、stdout 空、stderr 说明原因、无回溯 ----------

    def test_error_empty_or_whitespace_only_keyword_rejected(self):
        self._build_two_same_named_projects()
        for raw in ("", "   ", "\t", " \t \t", "　"):
            with self.subTest(raw=raw):
                proc = self.assert_usage_error(
                    ["project-export", "1", "--query", raw]
                )
                # 不依赖完整措辞：原因应指出关键词不能为空
                self.assertIn("query", proc.stderr.lower())

    def test_error_missing_query_value_rejected(self):
        self._build_two_same_named_projects()
        # --query 位于末尾但缺值：argparse 以退出码 2 拒绝
        self.assert_usage_error(["project-export", "1", "--query"])

    def test_error_valid_keyword_with_nonexistent_project_rejected(self):
        fx = self._build_two_same_named_projects()
        existing_max = max(
            project["id"] for project in self.cli_json("project-list")
        )
        missing = str(existing_max + 100)
        proc = self.assert_usage_error(
            ["project-export", missing, "--query", "API"]
        )
        # 原因指向项目不存在，而非关键词或存储问题
        self.assertIn("does not exist", proc.stderr)
        self.assertNotIn("storage failure", proc.stderr)

    def test_rejected_requests_leave_projects_tasks_and_stats_unchanged(self):
        fx = self._build_two_same_named_projects()
        before = self._snapshot_all()
        bad_requests = [
            ["project-export", "1", "--query", ""],
            ["project-export", "1", "--query", "   "],
            ["project-export", "1", "--query"],
            ["project-export", "999", "--query", "API"],
        ]
        for cli_args in bad_requests:
            with self.subTest(cli_args=cli_args):
                proc = self.run_cli(*cli_args)
                detail = self._detail(cli_args, proc)
                self.assertEqual(proc.returncode, 2, detail)
                self.assertEqual(proc.stdout, "", detail)
                self.assertNotIn("Traceback", proc.stderr, detail)
        self.assertEqual(
            self._snapshot_all(), before,
            "拒绝前后项目列表、任务内容与统计应保持一致",
        )

    # ---------- 存储失败 ----------

    def test_storage_failure_when_db_path_is_directory(self):
        self._build_two_same_named_projects()
        dir_path = os.path.join(self._tmpdir.name, "cannot_be_a_db")
        os.mkdir(dir_path)
        # 合法项目标识与合法关键词，仅数据库路径指向已有目录
        proc = self.run_cli(
            "project-export", "1", "--query", "API", db_path=dir_path
        )
        detail = self._detail(
            ("project-export", "1", "--query", "API"), proc
        )
        self.assertEqual(proc.returncode, 1, f"预期存储失败退出码 1：\n{detail}")
        self.assertEqual(proc.stdout, "", f"存储失败时标准输出应为空：\n{detail}")
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""project-export 单项目 JSON 导出的命令行回归（验收）测试。

覆盖用户指定的验收场景：

- 数据库中准备两个名为 “研发” 的项目：项目 1 有标识 1（标题 “整理API”，
  doing）和标识 2（标题 “文档”，todo）两条任务；项目 2 另有一条标题
  “整理API”、状态 done 的任务；
- project-export 1 的 project 恰为 {"id":1,"name":"研发"}，tasks 恰好
  包含任务 1、2（标识、所属项目、标题、状态与现有查询一致，按标识升序），
  项目 2 的任务不出现；紧接 project-export 0001 取得相同内容；
- 导出不改动项目、任务及统计。

覆盖成功路径约定：

- 退出码 0、标准错误为空、标准输出只有一个 JSON 对象（无多余内容），
  顶层恰好含 project 与 tasks；project 采用 project-list 的单项结构
  （只含 id、name），tasks 为数组，元素采用 task-list 的任务结构
  （只含 id / project_id / title / status），按任务标识数值升序；
- 包含该项目全部 todo/doing/done 任务，同标题任务各自保留，其他项目
  （即使同名）任务不混入；存在但无任务的项目仍成功返回项目对象与空数组；
- 名称与标题按已保存值原样返回（内部空白、大小写、中文、%、_、引号不变）；
- 只读：成功导出与业务失败前后 project-list、各项目 task-list 与
  project-stats 完整快照不变，重复导出结果一致；
- 通过 project-rename / task-rename / task-move / task-transfer 修改后，
  再导出反映当前名称、标题、状态与归属。

覆盖标识规则（沿用现有正整数规则）：

- 只接受 ASCII 数字组成、数值 1..9223372036854775807 的正整数，允许
  前导零（0001 与 1 等价，前导五千个零也按数值处理）；
- 缺少标识、零/全零、负数、非数字、带空白或正负号、全角数字为标识无效，
- 超过 9223372036854775807 为越界，范围内不存在为项目不存在：三类失败均
  退出码 2、标准输出为空、标准错误说明对应原因、无回溯，且不改动数据。

覆盖存储协议：

- --db 指向已有目录、父目录不存在、文件存在但不是可用 SQLite 数据库时
  退出码 1、标准输出为空、标准错误含 storage failure、无回溯；
- 父目录存在而数据库文件不存在时沿用自动建库行为，随后因项目不存在
  返回 2；新建的库中不含任何项目或任务（只读连接核对）。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅
使用 project-create / task-create / task-move / task-rename /
project-rename / task-transfer / project-list / task-list /
project-stats 等现有命令；除一处只读连接核对自动建库后的空库外，不直接
读写数据库，不调用产品内部函数，不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用
者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_export.py                  # 运行全部回归用例
    python3 -m unittest test_project_export -v      # 等价写法
    python3 test_project_export.py \
        ProjectExportRegression.test_acceptance_two_same_named_projects

退出码：全部通过为 0，存在断言失败为非零。失败信息会给出输入参数、退出
码、标准输出、标准错误以及预期结果，不依赖 JSON 键顺序或错误文案逐字一致。
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

PROJECT_FIELDS = {"id", "name"}
TASK_FIELDS = {"id", "project_id", "title", "status"}
EXPORT_FIELDS = {"project", "tasks"}

# 验收场景：两个同名项目 “研发”
PROJECT_NAME = "研发"
P1_TITLES = ["整理API", "文档"]              # 任务 1、2，创建顺序即标识升序
P1_STATUSES = {"整理API": "doing", "文档": "todo"}
P2_TITLE = "整理API"                         # 项目 2 另有一条，状态 done
P2_STATUS = "done"

SQLITE_MAX_INT = "9223372036854775807"      # 上限值本身合法
OVER_MAX = "9223372036854775808"            # 上限 + 1：越界
FIVE_THOUSAND_NINES = "9" * 5000            # 超长越界值
OVER_MAX_WITH_ZEROS = "0" * 5000 + OVER_MAX        # 前导五千个零，数值仍越界
MAX_WITH_ZEROS = "0" * 5000 + SQLITE_MAX_INT       # 前导五千个零，数值恰为上限
FIVE_THOUSAND_ZEROS_THEN_ONE = "0" * 5000 + "1"    # 数值为 1，前导五千个零


class ProjectExportRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-export-regtest-")
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
                "--db", db_path if db_path is not None else self.db_path,
                *cli_args,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def run_cli_no_db(self, *cli_args):
        """执行完全不带 --db 的命令行，用于校验缺少全局 --db 的失败路径。"""
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
        self.assertEqual(proc.returncode, 0, f"准备数据应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"准备数据时标准错误应为空：\n{detail}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"准备数据的 stdout 不是合法 JSON：\n{detail}")

    @staticmethod
    def _detail(cli_args, proc, expected=None):
        lines = [
            f"输入参数={list(cli_args)!r}",
            f"实际退出码={proc.returncode}",
            f"实际标准输出={proc.stdout!r}",
            f"实际标准错误={proc.stderr!r}",
        ]
        if expected is not None:
            lines.append(f"预期结果={expected}")
        return "\n".join(lines)

    def list_project(self, project_id):
        """通过公开 task-list 命令查询项目全部任务，返回对象列表。"""
        cli_args = ("task-list", str(project_id))
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, f"task-list 查询应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"task-list 时标准错误应为空：\n{detail}")
        data = json.loads(proc.stdout)
        self.assertIsInstance(data, list, f"task-list 结果应为数组：\n{detail}")
        return data

    def snapshot(self):
        """记录全部项目、各项目任务与统计，用于前后比对。"""
        snap = {"projects": self.cli_json("project-list"), "tasks": {}, "stats": {}}
        for proj in snap["projects"]:
            pid = proj["id"]
            snap["tasks"][pid] = self.list_project(pid)
            snap["stats"][pid] = self.cli_json("project-stats", str(pid))
        return snap

    def export(self, raw_id):
        """对给定标识执行 project-export。"""
        return self.run_cli("project-export", raw_id)

    def assert_export(self, proc, raw_id, expected_project, expected_tasks):
        """断言导出成功并逐字段核对，返回解析后的对象。

        expected_project 为 {"id", "name"}，expected_tasks 为任务对象列表
        （须已按标识升序）。
        """
        cli_args = ("project-export", str(raw_id))
        detail = self._detail(
            cli_args, proc,
            expected=f"退出码 0、stderr 为空、project={expected_project!r}、"
                     f"tasks={expected_tasks!r}",
        )
        self.assertEqual(proc.returncode, 0, f"导出应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功导出时标准错误应为空：\n{detail}")
        try:
            obj, end = json.JSONDecoder().raw_decode(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"stdout 不是合法 JSON：\n{detail}")
        self.assertEqual(
            proc.stdout[end:].strip(), "",
            f"stdout 应只含单个 JSON 值，多余内容为：\n{detail}",
        )
        self.assertIsInstance(obj, dict, f"stdout 应为单个 JSON 对象：\n{detail}")
        self.assertEqual(
            set(obj.keys()), EXPORT_FIELDS,
            f"顶层应恰好含 project 与 tasks：\n{detail}",
        )

        project = obj["project"]
        self.assertIsInstance(project, dict, f"project 应为对象：\n{detail}")
        self.assertEqual(
            set(project.keys()), PROJECT_FIELDS,
            f"project 字段结构应与 project-list 单项一致：\n{detail}",
        )
        self.assertIsInstance(project["id"], int, f"project.id 应为整数：\n{detail}")
        self.assertIsInstance(project["name"], str,
                              f"project.name 应为字符串：\n{detail}")
        self.assertEqual(project, expected_project, f"project 内容不符：\n{detail}")

        tasks = obj["tasks"]
        self.assertIsInstance(tasks, list, f"tasks 应为数组：\n{detail}")
        for task in tasks:
            self.assertIsInstance(task, dict, f"tasks 元素应为任务对象：\n{detail}")
            self.assertEqual(
                set(task.keys()), TASK_FIELDS,
                f"任务对象字段结构应与 task-list 一致：\n{detail}",
            )
            self.assertIsInstance(task["id"], int, f"id 应为整数：\n{detail}")
            self.assertIsInstance(task["project_id"], int,
                                  f"project_id 应为整数：\n{detail}")
            self.assertIsInstance(task["title"], str, f"title 应为字符串：\n{detail}")
            self.assertIsInstance(task["status"], str,
                                  f"status 应为字符串：\n{detail}")
        self.assertEqual(tasks, expected_tasks, f"tasks 内容或顺序不符：\n{detail}")
        ids = [task["id"] for task in tasks]
        self.assertEqual(ids, sorted(ids), f"tasks 应按标识升序：\n{detail}")
        self.assertTrue(
            all(task["project_id"] == expected_project["id"] for task in tasks),
            f"tasks 只能包含所属项目 {expected_project['id']} 的任务：\n{detail}",
        )
        return obj

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目 1 “研发”：任务 1 “整理API” -> doing；任务 2 “文档” 保持 todo
        self.p1 = self.cli_json("project-create", PROJECT_NAME)["id"]
        self.assertEqual(self.p1, 1)
        self.p1_task_ids = {}
        for title in P1_TITLES:
            created = self.cli_json("task-create", str(self.p1), title)
            self.p1_task_ids[title] = created["id"]
        self.cli_json(
            "task-move", str(self.p1_task_ids["整理API"]),
            P1_STATUSES["整理API"],
        )

        # 项目 2 “研发”：另有一条 “整理API”，置为 done
        self.p2 = self.cli_json("project-create", PROJECT_NAME)["id"]
        self.assertEqual(self.p2, 2)
        p2_created = self.cli_json("task-create", str(self.p2), P2_TITLE)
        self.p2_task_id = p2_created["id"]
        self.cli_json("task-move", str(self.p2_task_id), P2_STATUS)

        self.p1_expected_tasks = [
            {
                "id": self.p1_task_ids[title],
                "project_id": self.p1,
                "title": title,
                "status": P1_STATUSES[title],
            }
            for title in P1_TITLES
        ]
        self.p2_expected_tasks = [
            {
                "id": self.p2_task_id,
                "project_id": self.p2,
                "title": P2_TITLE,
                "status": P2_STATUS,
            }
        ]

    # ---------- 用户验收场景 ----------

    def test_acceptance_two_same_named_projects(self):
        # project-export 1：project 恰为 {"id":1,"name":"研发"}，
        # tasks 恰好包含任务 1、2，项目 2 的任务不出现
        self.assert_export(
            self.export("1"), "1",
            {"id": self.p1, "name": PROJECT_NAME},
            self.p1_expected_tasks,
        )

        # 紧接 project-export 0001 取得相同内容
        first = self.export("1")
        leading = self.export("0001")
        self.assertEqual(leading.returncode, 0)
        self.assertEqual(leading.stderr, "")
        self.assertEqual(leading.stdout, first.stdout)

        # 导出内容与现有查询逐一对应
        self.assertEqual(
            json.loads(first.stdout)["tasks"], self.list_project(self.p1)
        )

        # 项目 2 的导出只含它自己的 done 任务
        self.assert_export(
            self.export("2"), "2",
            {"id": self.p2, "name": PROJECT_NAME},
            self.p2_expected_tasks,
        )

    def test_export_matches_existing_project_list_and_task_list_shapes(self):
        # project 与 project-list 中该项目对象完全一致；
        # 每个任务与 task-list 中同标识对象完全一致
        projects = {p["id"]: p for p in self.cli_json("project-list")}
        data = self.assert_export(
            self.export(str(self.p1)), str(self.p1),
            projects[self.p1], self.p1_expected_tasks,
        )
        listed = {t["id"]: t for t in self.list_project(self.p1)}
        for task in data["tasks"]:
            self.assertEqual(task, listed[task["id"]])

    # ---------- 前导零与升序 ----------

    def test_leading_zeros_equivalent_and_long_zeros(self):
        for raw_id in ("0001", "000001", FIVE_THOUSAND_ZEROS_THEN_ONE):
            with self.subTest(raw_id=(raw_id if len(raw_id) <= 40
                                      else f"<{len(raw_id)} chars>")):
                self.assert_export(
                    self.export(raw_id), raw_id,
                    {"id": self.p1, "name": PROJECT_NAME},
                    self.p1_expected_tasks,
                )

    def test_tasks_sorted_numerically_even_with_gaps(self):
        # 再建一个空项目并插入任务，确认导出始终按标识数值升序、跨项目隔离
        p3 = self.cli_json("project-create", "其他")["id"]
        created = self.cli_json("task-create", str(p3), "零杂")
        self.assertEqual(
            [t["id"] for t in self.export_tasks_only(p3)],
            [created["id"]],
        )
        # 项目 1 的任务仍只有任务 1、2 且升序，不串入项目 3
        data = json.loads(self.export(str(self.p1)).stdout)
        self.assertEqual([t["id"] for t in data["tasks"]],
                         [self.p1_task_ids[t] for t in P1_TITLES])

    def export_tasks_only(self, project_id):
        proc = self.export(str(project_id))
        self.assertEqual(proc.returncode, 0)
        return json.loads(proc.stdout)["tasks"]

    # ---------- 全部状态、同标题各自保留、空项目 ----------

    def test_includes_all_three_statuses_and_keeps_duplicate_titles(self):
        # 项目 1 现有 doing/todo，再补一条 done 的同标题任务，三种状态齐全；
        # 同标题 “整理API” 出现两条，各自保留并按标识升序
        dup = self.cli_json("task-create", str(self.p1), "整理API")
        self.cli_json("task-move", str(dup["id"]), "done")
        expected = self.p1_expected_tasks + [{
            "id": dup["id"],
            "project_id": self.p1,
            "title": "整理API",
            "status": "done",
        }]
        data = self.assert_export(
            self.export(str(self.p1)), str(self.p1),
            {"id": self.p1, "name": PROJECT_NAME}, expected,
        )
        statuses = {t["status"] for t in data["tasks"]}
        self.assertEqual(statuses, {"todo", "doing", "done"})
        self.assertEqual(
            [t["title"] for t in data["tasks"]].count("整理API"), 2
        )

    def test_existing_project_without_tasks_returns_project_and_empty_array(self):
        empty_id = self.cli_json("project-create", "空项目")["id"]
        self.assert_export(
            self.export(str(empty_id)), str(empty_id),
            {"id": empty_id, "name": "空项目"}, [],
        )

    # ---------- 名称与标题按保存值原样返回 ----------

    def test_names_and_titles_returned_exactly_as_saved(self):
        # 改名为含内部双空格、中文、大小写、%、_、引号的保存值
        raw_name = "  研发  API_100%'  "
        saved_name = "研发  API_100%'"
        self.cli_json("project-rename", str(self.p1), raw_name)
        raw_title = "  整理  API_100%'  "
        saved_title = "整理  API_100%'"
        self.cli_json(
            "task-rename", str(self.p1_task_ids["整理API"]), raw_title
        )
        expected = [
            {
                "id": self.p1_task_ids["整理API"],
                "project_id": self.p1,
                "title": saved_title,
                "status": "doing",
            },
            {
                "id": self.p1_task_ids["文档"],
                "project_id": self.p1,
                "title": "文档",
                "status": "todo",
            },
        ]
        data = self.assert_export(
            self.export(str(self.p1)), str(self.p1),
            {"id": self.p1, "name": saved_name}, expected,
        )
        self.assertEqual(data["project"]["name"], saved_name)
        self.assertEqual(data["tasks"][0]["title"], saved_title)

    # ---------- 只读：改名/移动/转移后反映当前值，导出本身无副作用 ----------

    def test_export_reflects_rename_move_and_transfer(self):
        # 先转移：项目 2 的 done 任务转入项目 1
        transferred = self.cli_json(
            "task-transfer", str(self.p2_task_id), str(self.p1)
        )
        # 再移动与改名
        self.cli_json("task-move", str(self.p2_task_id), "todo")
        self.cli_json("task-rename", str(self.p2_task_id), "转入文档")
        self.cli_json("project-rename", str(self.p1), "研发一部")

        data = json.loads(self.export(str(self.p1)).stdout)
        self.assertEqual(data["project"], {"id": self.p1, "name": "研发一部"})
        by_id = {t["id"]: t for t in data["tasks"]}
        self.assertEqual(
            by_id[self.p2_task_id],
            {"id": self.p2_task_id, "project_id": self.p1,
             "title": "转入文档", "status": "todo"},
        )
        # 项目 2 现在没有任务：仍返回项目对象与空数组
        self.assert_export(
            self.export(str(self.p2)), str(self.p2),
            {"id": self.p2, "name": PROJECT_NAME}, [],
        )
        self.assertEqual(transferred["project_id"], self.p1)

    def test_successful_export_changes_nothing_and_is_repeatable(self):
        before = self.snapshot()
        first = self.export("1")
        second = self.export("0001")
        third = self.export("2")
        after = self.snapshot()
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.stdout, first.stdout, "重复导出结果应一致")
        self.assertNotEqual(third.stdout, first.stdout,
                            "不同项目导出内容应不同")
        self.assertEqual(after, before, "导出不得改动任何项目、任务或统计")

    # ---------- 标识无效 / 越界 / 不存在 ----------

    def assert_export_rejected(self, raw_id, reason):
        before = self.snapshot()
        proc = self.export(raw_id)
        after = self.snapshot()
        label = raw_id if len(raw_id) <= 40 else f"<{len(raw_id)} chars>"
        detail = self._detail(
            ("project-export", label), proc,
            expected=f"退出码 2、stdout 为空、stderr 说明 {reason}",
        )
        self.assertEqual(proc.returncode, 2, f"应返回退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"被拒绝时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "", f"被拒绝时标准错误应说明原因：\n{detail}"
        )
        self.assertIn(
            reason, proc.stderr.lower(),
            f"标准错误应说明 {reason}：\n{detail}",
        )
        self.assertNotIn(
            "Traceback", proc.stderr, f"不得出现 Python 回溯：\n{detail}"
        )
        self.assertEqual(after, before, f"被拒绝的请求不得改动数据：\n{detail}")

    def test_invalid_identifiers_rejected(self):
        for raw_id in ("0", "0000", "0" * 5000, "-1", "-0", "abc", "1.5",
                       "1e3", "+1", " 1", "1 ", "", "１", "0x1"):
            with self.subTest(raw_id=(raw_id if len(raw_id) <= 40
                                      else f"<{len(raw_id)} chars>")):
                self.assert_export_rejected(raw_id, "positive integer")

    def test_out_of_range_identifiers_rejected(self):
        for raw_id in (OVER_MAX, FIVE_THOUSAND_NINES, OVER_MAX_WITH_ZEROS):
            with self.subTest(raw_id=(raw_id if len(raw_id) <= 40
                                      else f"<{len(raw_id)} chars>")):
                self.assert_export_rejected(raw_id, "range")

    def test_in_range_but_nonexistent_identifiers_rejected(self):
        # 范围内但不存在（含上限值本身与其前导零写法、普通空缺标识）
        for raw_id in ("999", SQLITE_MAX_INT, MAX_WITH_ZEROS):
            with self.subTest(raw_id=(raw_id if len(raw_id) <= 40
                                      else f"<{len(raw_id)} chars>")):
                self.assert_export_rejected(raw_id, "does not exist")

    # ---------- 缺少参数：退出码 2、stdout 为空、无回溯 ----------

    def test_missing_project_id_argument_rejected(self):
        before = self.snapshot()
        proc = self.run_cli("project-export")
        after = self.snapshot()
        detail = self._detail(("project-export",), proc)
        self.assertEqual(proc.returncode, 2, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertNotEqual(proc.stderr.strip(), "", detail)
        self.assertNotIn("Traceback", proc.stderr, detail)
        self.assertEqual(after, before, detail)

    def test_missing_db_option_or_value_rejected(self):
        proc = self.run_cli_no_db("project-export", "1")
        detail = self._detail(("project-export", "1"), proc)
        self.assertEqual(proc.returncode, 2, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertNotEqual(proc.stderr.strip(), "", detail)
        self.assertNotIn("Traceback", proc.stderr, detail)

        proc = self.run_cli_no_db("--db")
        detail = self._detail(("--db",), proc)
        self.assertEqual(proc.returncode, 2, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertNotEqual(proc.stderr.strip(), "", detail)
        self.assertNotIn("Traceback", proc.stderr, detail)

    # ---------- 存储失败：退出码 1 ----------

    def assert_storage_failure(self, proc, cli_args):
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 1, f"存储失败应返回退出码 1：\n{detail}")
        self.assertEqual(proc.stdout, "", f"存储失败时标准输出应为空：\n{detail}")
        self.assertIn(
            "storage failure", proc.stderr,
            f"标准错误应说明存储失败：\n{detail}",
        )
        self.assertNotIn(
            "Traceback", proc.stderr, f"不得出现 Python 回溯：\n{detail}"
        )

    def test_database_path_being_a_directory_is_storage_failure(self):
        dir_path = os.path.join(self._tmpdir.name, "cannot_be_a_db")
        os.mkdir(dir_path)
        self.assert_storage_failure(
            self.run_cli("project-export", "1", db_path=dir_path),
            ("project-export", "1"),
        )

    def test_missing_parent_directory_is_storage_failure(self):
        missing = os.path.join(self._tmpdir.name, "no_such_dir", "isolated.db")
        self.assert_storage_failure(
            self.run_cli("project-export", "1", db_path=missing),
            ("project-export", "1"),
        )

    def test_file_that_is_not_sqlite_is_storage_failure(self):
        bogus_path = os.path.join(self._tmpdir.name, "bogus.db")
        with open(bogus_path, "w", encoding="utf-8") as fh:
            fh.write("this is definitely not a sqlite database file\n")
        self.assert_storage_failure(
            self.run_cli("project-export", "1", db_path=bogus_path),
            ("project-export", "1"),
        )

    # ---------- 自动建库：空库中项目不存在，退出码 2 且不含任何记录 ----------

    def test_missing_database_file_is_created_then_project_not_found(self):
        fresh_path = os.path.join(self._tmpdir.name, "fresh.db")
        self.assertFalse(os.path.exists(fresh_path))

        proc = self.run_cli("project-export", "1", db_path=fresh_path)
        detail = self._detail(("project-export", "1"), proc)
        self.assertEqual(proc.returncode, 2, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("does not exist", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)

        # 沿用自动建库行为：文件已被创建
        self.assertTrue(os.path.isfile(fresh_path))
        # 只读连接核对：新库不含任何项目或任务（不创建业务记录）
        conn = sqlite3.connect(f"file:{fresh_path}?mode=ro", uri=True)
        try:
            project_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(project_count, 0, "自动创建的新库不应包含项目")
        self.assertEqual(task_count, 0, "自动创建的新库不应包含任务")


if __name__ == "__main__":
    unittest.main(verbosity=2)

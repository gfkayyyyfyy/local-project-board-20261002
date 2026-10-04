#!/usr/bin/env python3
"""project-export 单项目 JSON 导出的命令行回归测试。

覆盖需求：

- 成功时退出码 0、标准错误为空，标准输出为单个 JSON 对象，顶层恰好包含
  project 与 tasks；project 采用 project-list 的单项目结构（id/name），
  tasks 为数组，元素采用 task-list 的任务结构（id/project_id/title/status），
  按任务标识数值升序，包含该项目全部 todo/doing/done 任务。
- 用户指定验收场景：两个同名“研发”项目，项目一有“整理API”(doing) 与
  “文档”(todo)，项目二另有同标题“整理API”(done)；导出项目一只含项目一
  自己的两条任务，项目二任务不串入，同标题任务各自保留；0001 与 1 结果
  完全相同；导出不改变项目、任务与统计。
- 存在但无任务的项目仍成功返回项目对象与空数组；结果按任务标识数值升序，
  标识不连续时同样如此。
- 名称与标题按已保存值原样返回：内部空白、大小写、中文、%、_、引号保持原样。
- 通过现有命令改名、移动状态、转移任务后，导出反映当前名称、标题、状态与归属。
- 标识只接受 ASCII 数字且数值在 1..9223372036854775807：零、负数、非数字、
  空串、越界、缺少参数、范围内但项目不存在均退出码 2、标准输出为空、
  标准错误说明原因，且不改动已有项目或任务。
- 父目录存在而数据库文件不存在时保留自动建库行为，随后因项目不存在返回 2，
  不创建业务记录；数据库无法打开或文件不是可用 SQLite 数据库时退出码 1。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，不直接读写数据库，
不依赖网络，也不依赖任何预先保存的项目。每个用例在独立临时目录使用全新隔离
SQLite 数据库，可单独执行、可重复执行，结束后自动清理。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_export.py                  # 运行全部回归用例
    python3 -m unittest test_project_export -v      # 等价写法
    python3 test_project_export.py \\
        ProjectExportRegression.test_acceptance_duplicate_project_names  # 单用例

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
OVER_MAX = "9223372036854775808"  # SQLite 有符号 64 位整数上限 + 1


class ProjectExportRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-export-")
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

    def assert_export(self, project_id, expected_project, expected_tasks):
        """断言导出成功并返回结构、内容、顺序完全一致的 {project, tasks}。"""
        proc = self.run_cli("project-export", str(project_id))
        detail = self._detail(("project-export", str(project_id)), proc)
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
        self.assertEqual(
            data["project"], expected_project, f"项目对象不符：\n{detail}"
        )
        self.assertIsInstance(data["project"], dict, detail)
        self.assertEqual(set(data["project"].keys()), PROJECT_FIELDS, detail)

        self.assertIsInstance(data["tasks"], list, f"tasks 应为数组：\n{detail}")
        self.assertEqual(
            data["tasks"], expected_tasks,
            f"任务集合、字段值或顺序不符：\n{detail}",
        )
        for task in data["tasks"]:
            self.assertIsInstance(task, dict, f"数组元素应为任务对象：\n{detail}")
            self.assertEqual(set(task.keys()), TASK_FIELDS, f"任务字段结构不符：\n{detail}")
        ids = [task["id"] for task in data["tasks"]]
        self.assertEqual(ids, sorted(ids), f"任务应按标识数值升序：\n{detail}")
        return data

    def assert_usage_error(self, cli_args):
        """断言退出码 2、stdout 为空、stderr 有原因且无回溯，且数据不变。"""
        before = self._snapshot()
        proc = self.run_cli(*cli_args)
        after = self._snapshot()
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 2, f"预期参数错误退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"参数错误时标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"参数错误时标准错误应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr,
                         f"标准错误不应包含异常回溯：\n{detail}")
        self.assertEqual(after, before, f"失败请求不得改动已有数据：\n{detail}")

    def _snapshot(self):
        """记录全部项目与其任务、统计，用于校验请求无副作用。"""
        projects = self.cli_json("project-list")
        snapshot = {}
        for project in projects:
            pid = str(project["id"])
            snapshot[pid] = (
                project,
                self.cli_json("task-list", pid),
                self.cli_json("project-stats", pid),
            )
        return snapshot

    # ---------- 通过公开命令准备用户指定的验收夹具 ----------

    def _build_two_rd_projects(self):
        """两个同名“研发”项目：

        项目一：任务 1 整理API(doing)、任务 2 文档(todo)；
        项目二：任务 3 整理API(done)（与项目一同标题，用于验证项目隔离）。
        返回四个创建返回对象，字段值即建数阶段产品输出。
        """
        p1 = self.cli_json("project-create", "研发")
        p2 = self.cli_json("project-create", "研发")
        t1 = self.cli_json("task-create", str(p1["id"]), "整理API")
        t2 = self.cli_json("task-create", str(p1["id"]), "文档")
        self.cli_json("task-move", str(t1["id"]), "doing")
        t3 = self.cli_json("task-create", str(p2["id"]), "整理API")
        self.cli_json("task-move", str(t3["id"]), "done")
        t1 = dict(t1, status="doing")
        t3 = dict(t3, status="done")
        return p1, p2, t1, t2, t3

    # ---------- 用户指定的验收场景 ----------

    def test_acceptance_duplicate_project_names(self):
        p1, p2, t1, t2, t3 = self._build_two_rd_projects()
        self.assertEqual([p1["id"], p2["id"], t1["id"], t2["id"], t3["id"]],
                         [1, 2, 1, 2, 3])

        # project-export 1：project 恰为 {"id":1,"name":"研发"}，
        # tasks 恰含任务 1(doing) 与任务 2(todo)，按标识升序
        self.assert_export(
            1,
            {"id": 1, "name": "研发"},
            [
                {"id": 1, "project_id": 1, "title": "整理API", "status": "doing"},
                {"id": 2, "project_id": 1, "title": "文档", "status": "todo"},
            ],
        )

    def test_acceptance_leading_zeros_identical_and_read_only(self):
        p1, p2, t1, t2, t3 = self._build_two_rd_projects()
        before = self._snapshot()
        plain = self.run_cli("project-export", "1")
        zeros = self.run_cli("project-export", "0001")
        detail_plain = self._detail(("project-export", "1"), plain)
        self.assertEqual(plain.returncode, 0, detail_plain)
        self.assertEqual(plain.stderr, "", detail_plain)
        # 0001 与 1 的标准输出逐字相同，项目二的任务不出现
        self.assertEqual(
            (zeros.returncode, zeros.stdout, zeros.stderr),
            (plain.returncode, plain.stdout, plain.stderr),
        )
        data = json.loads(plain.stdout)
        self.assertEqual(
            data,
            {
                "project": {"id": p1["id"], "name": p1["name"]},
                "tasks": [
                    {"id": t1["id"], "project_id": p1["id"],
                     "title": "整理API", "status": "doing"},
                    {"id": t2["id"], "project_id": p1["id"],
                     "title": "文档", "status": "todo"},
                ],
            },
        )
        self.assertNotIn(t3["id"], [task["id"] for task in data["tasks"]])
        # 导出不改变项目、任务及统计
        self.assertEqual(self._snapshot(), before)

    def test_export_other_duplicate_project_returns_its_own_task(self):
        # 同名项目二只导出它自己那条 done 的“整理API”
        self._build_two_rd_projects()
        self.assert_export(
            2,
            {"id": 2, "name": "研发"},
            [{"id": 3, "project_id": 2, "title": "整理API", "status": "done"}],
        )

    # ---------- 三种状态全包含、按标识升序、空项目 ----------

    def test_includes_all_three_statuses_sorted_by_id(self):
        project = self.cli_json("project-create", "p")
        pid = str(project["id"])
        # 在另一项目穿插创建，使本项目任务标识不连续：本项目任务为 1、3、5
        other = self.cli_json("project-create", "other")
        a = self.cli_json("task-create", pid, "a-todo")          # id 1, todo
        self.cli_json("task-create", str(other["id"]), "x")      # id 2
        b = self.cli_json("task-create", pid, "b-doing")         # id 3
        self.cli_json("task-create", str(other["id"]), "y")      # id 4
        c = self.cli_json("task-create", pid, "c-done")          # id 5
        self.cli_json("task-move", str(b["id"]), "doing")
        self.cli_json("task-move", str(c["id"]), "done")

        self.assert_export(
            project["id"],
            {"id": project["id"], "name": "p"},
            [
                {"id": a["id"], "project_id": project["id"],
                 "title": "a-todo", "status": "todo"},
                {"id": b["id"], "project_id": project["id"],
                 "title": "b-doing", "status": "doing"},
                {"id": c["id"], "project_id": project["id"],
                 "title": "c-done", "status": "done"},
            ],
        )

    def test_existing_project_without_tasks_returns_empty_array(self):
        project = self.cli_json("project-create", "空项目")
        self.assert_export(project["id"],
                           {"id": project["id"], "name": "空项目"}, [])

    def test_export_is_repeatable_read_only(self):
        self._build_two_rd_projects()
        before = self._snapshot()
        first = self.run_cli("project-export", "1")
        for _ in range(3):
            again = self.run_cli("project-export", "0001")
            self.assertEqual(
                (again.returncode, again.stdout, again.stderr),
                (first.returncode, first.stdout, first.stderr),
            )
        self.assertEqual(self._snapshot(), before)

    # ---------- 名称与标题原样返回；改名/移动/转移后反映当前值 ----------

    def test_names_and_titles_returned_as_saved(self):
        # 创建时只去除首尾空白，内部空白、大小写、中文、%、_、引号原样保留
        name = "  研 发 %_ \"'  "
        title = "  整 理 API %_ \"'  "
        project = self.cli_json("project-create", name)
        task = self.cli_json("task-create", str(project["id"]), title)
        self.assert_export(
            project["id"],
            {"id": project["id"], "name": "研 发 %_ \"'"},
            [{"id": task["id"], "project_id": project["id"],
              "title": "整 理 API %_ \"'", "status": "todo"}],
        )

    def test_export_reflects_rename_move_and_transfer(self):
        p1, p2, t1, t2, t3 = self._build_two_rd_projects()

        # 项目一改名；任务 2 移动到 done；项目二的任务 3 转移到项目一
        self.cli_json("project-rename", "1", "研发一部")
        self.cli_json("task-move", "2", "done")
        self.cli_json("task-transfer", "3", "0001")

        # 同标题任务各自保留：项目一现有任务 1(doing)、2(done)、3(done)，
        # 两条“整理API”(id 1 与 id 3) 都出现且按标识升序
        self.assert_export(
            1,
            {"id": 1, "name": "研发一部"},
            [
                {"id": 1, "project_id": 1, "title": "整理API", "status": "doing"},
                {"id": 2, "project_id": 1, "title": "文档", "status": "done"},
                {"id": 3, "project_id": 1, "title": "整理API", "status": "done"},
            ],
        )
        # 项目二仍存在但任务已转走：空数组，名称不变
        self.assert_export(2, {"id": 2, "name": "研发"}, [])

        # 再把任务 1 改名，导出反映新标题
        self.cli_json("task-rename", "1", "整理接口")
        data = self.assert_export(
            1,
            {"id": 1, "name": "研发一部"},
            [
                {"id": 1, "project_id": 1, "title": "整理接口", "status": "doing"},
                {"id": 2, "project_id": 1, "title": "文档", "status": "done"},
                {"id": 3, "project_id": 1, "title": "整理API", "status": "done"},
            ],
        )
        self.assertEqual(
            [task["project_id"] for task in data["tasks"]], [1, 1, 1]
        )

    # ---------- 错误路径：退出码 2、stdout 空、stderr 说明原因、数据不变 ----------

    def test_error_invalid_or_out_of_range_or_zero_identifiers(self):
        self._build_two_rd_projects()
        for raw in ("0", "0000", "-1", "abc", "1.5", "1e3", "+1",
                    " 1", "1 ", "", "１", OVER_MAX,
                    "0" * 5000 + OVER_MAX):
            with self.subTest(raw=raw):
                self.assert_usage_error(["project-export", raw])

    def test_error_nonexistent_project(self):
        self._build_two_rd_projects()
        self.assert_usage_error(["project-export", "999"])
        # 上限值本身合法但全新库/本库不存在：按“不存在”拒绝而非越界
        self.assert_usage_error(["project-export", "9223372036854775807"])

    def test_error_missing_project_id(self):
        self._build_two_rd_projects()
        self.assert_usage_error(["project-export"])

    # ---------- 存储失败与全新数据库初始化 ----------

    def test_storage_failure_when_db_is_directory(self):
        dir_path = os.path.join(self._tmpdir.name, "cannot_be_a_db")
        os.mkdir(dir_path)
        proc = self.run_cli("project-export", "1", db_path=dir_path)
        detail = self._detail(("project-export", "1"), proc)
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)

    def test_storage_failure_when_file_is_not_sqlite(self):
        bogus = os.path.join(self._tmpdir.name, "bogus.db")
        with open(bogus, "w", encoding="utf-8") as handle:
            handle.write("this is definitely not a sqlite database\n")
        proc = self.run_cli("project-export", "1", db_path=bogus)
        detail = self._detail(("project-export", "1"), proc)
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)

    def test_missing_db_file_is_initialized_then_project_not_found(self):
        # 父目录存在而数据库文件不存在：保留自动建库行为；项目不存在返回 2，
        # 不创建业务记录（project-list 应为空）
        fresh = os.path.join(self._tmpdir.name, "fresh.db")
        self.assertFalse(os.path.exists(fresh))
        proc = self.run_cli("project-export", "1", db_path=fresh)
        detail = self._detail(("project-export", "1"), proc)
        self.assertEqual(proc.returncode, 2, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("does not exist", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)
        self.assertTrue(os.path.exists(fresh))

        projects = subprocess.run(
            [sys.executable, "-m", "kanban", "--db", fresh, "project-list"],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(projects.returncode, 0, projects.stderr)
        self.assertEqual(json.loads(projects.stdout), [])

    def test_storage_failure_when_parent_directory_missing(self):
        missing = os.path.join(self._tmpdir.name, "no", "such", "x.db")
        proc = self.run_cli("project-export", "1", db_path=missing)
        detail = self._detail(("project-export", "1"), proc)
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)

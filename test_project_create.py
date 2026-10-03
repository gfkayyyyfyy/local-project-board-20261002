#!/usr/bin/env python3
"""project-create 创建项目的命令行回归（验收）测试。

覆盖成功路径：

- 父目录存在而数据库文件不存在时，project-create 能成功创建数据库与项目，
  随后由独立命令进程（project-list）读到完全相同的对象；
- 成功时退出码为 0、标准错误为空、标准输出是单个可解析的 JSON 对象，
  且只含 id / name 两个字段：项目标识为正整数；
- 名称先去除首尾空白（空格、制表符），内部连续空白、中文、大小写、
  百分号、下划线和引号保持原样。例如输入 " \\t 研发  API_100%' \\t" 时，
  保存和返回的名称均为 "研发  API_100%'"；
- 再次提交同一名称会创建不同标识的项目，project-list 同时保留两条记录
  并按标识数值升序返回；
- 已有项目中准备一条 doing 任务后再创建新项目，原项目对象和该任务的
  完整内容（标识、所属项目、标题、状态）保持不变。

覆盖拒绝路径（使用可打开的既有临时库，退出码 2、标准输出为空、标准错误
说明原因、无 Python 回溯，失败前后 project-list 与原项目 task-list 的
返回值完全一致）：

- 名称为空字符串、纯空格或制表符；
- 缺少名称参数。

覆盖存储失败：有效名称配合指向已有目录的 --db 时退出码 1、标准输出为空、
标准错误说明 storage failure，既有临时库数据不变。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品：用
project-create / task-create / task-move 准备数据，用 project-list /
task-list 核对落库结果。不直接读写数据库，不调用产品内部函数，不依赖
网络，也不依赖任何预先保存的项目或使用者自己的数据。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_create.py                  # 运行全部回归用例
    python3 -m unittest test_project_create -v      # 等价写法
    python3 -m unittest discover -p 'test_project_create.py' -v
    python3 test_project_create.py \
        ProjectCreateRegression.test_create_returns_json_object_and_persists

退出码：全部通过为 0，存在断言失败为非零（通常为 1）。失败信息会给出
命令行输入、实际退出码、标准输出、标准错误以及预期差异；JSON 比较按
对象内容进行，不依赖键顺序，也不要求错误文案逐字一致，不断言固定标识值。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

PROJECT_FIELDS = {"id", "name"}

# 创建名称输入：首尾添加空格与制表符；内部为中文 + 双空格 + 大小写英文
# + 百分号 + 下划线 + 单引号
NAME_RAW = " \t 研发  API_100%' \t"
NAME = "研发  API_100%'"


class ProjectCreateRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时目录与全新 SQLite 文件，互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-pcreate-regtest-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")

    def tearDown(self):
        self._tmpdir.cleanup()

    # ---------- 命令行与结果解析辅助 ----------

    def run_cli(self, *cli_args, db_path=None):
        """执行 python -m kanban --db <临时库> ...，返回 CompletedProcess。

        每次调用都是独立的命令进程，因此查询核对与创建不在同一进程内完成。
        """
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

    def cli_json(self, *cli_args):
        """准备数据用：要求成功并返回解析后的单个 JSON 值。"""
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc, expected="准备数据成功：退出码 0、stderr 为空"
        )
        self.assertEqual(proc.returncode, 0, f"准备数据应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"准备数据时标准错误应为空：\n{detail}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"准备数据的 stdout 不是合法 JSON：\n{detail}")

    @staticmethod
    def _detail(cli_args, proc, expected):
        """组装失败定位信息：命令行输入、实际退出码/输出与预期结果。"""
        return "\n".join(
            [
                f"输入参数={list(cli_args)!r}",
                f"实际退出码={proc.returncode}",
                f"实际标准输出={proc.stdout!r}",
                f"实际标准错误={proc.stderr!r}",
                f"预期结果={expected}",
            ]
        )

    def parse_single_json_object(self, proc, cli_args, expected):
        """要求 stdout 恰好是一个 JSON 对象（拒绝数组或多余输出），返回该对象。"""
        detail = self._detail(cli_args, proc, expected)
        try:
            obj, end = json.JSONDecoder().raw_decode(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"stdout 不是合法 JSON：\n{detail}")
        self.assertEqual(
            proc.stdout[end:].strip(), "",
            f"stdout 应只含单个 JSON 值，多余内容为：\n{detail}",
        )
        self.assertIsInstance(
            obj, dict, f"stdout 应为单个 JSON 对象而非数组：\n{detail}"
        )
        return obj, detail

    def list_projects(self):
        """通过独立命令进程执行 project-list，返回项目对象列表。"""
        cli_args = ("project-list",)
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc,
            expected="退出码 0、stderr 为空、stdout 为 JSON 数组",
        )
        self.assertEqual(proc.returncode, 0, f"查询应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"查询时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"查询的 stdout 不是合法 JSON：\n{detail}")
        self.assertIsInstance(data, list, f"查询结果应为 JSON 数组：\n{detail}")
        return data

    def list_tasks(self, project_id):
        """通过独立命令进程执行 task-list，返回任务对象列表。"""
        cli_args = ("task-list", str(project_id))
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc,
            expected="退出码 0、stderr 为空、stdout 为 JSON 数组",
        )
        self.assertEqual(proc.returncode, 0, f"查询应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"查询时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"查询的 stdout 不是合法 JSON：\n{detail}")
        self.assertIsInstance(data, list, f"查询结果应为 JSON 数组：\n{detail}")
        return data

    def snapshot(self, project_id):
        """记录 project-list 与指定项目 task-list 的当前完整返回值。"""
        return {
            "projects": self.list_projects(),
            "tasks": self.list_tasks(project_id),
        }

    # ---------- 通过公开命令准备夹具 ----------

    def build_project_with_doing_task(self):
        """创建一个原有项目并在其中准备一条 doing 任务。

        返回 (项目标识, 任务完整对象)。全部经公开命令完成。
        """
        project = self.cli_json("project-create", "原有项目")
        task = self.cli_json("task-create", str(project["id"]), "进行中的任务")
        moved = self.cli_json("task-move", str(task["id"]), "doing")
        self.assertEqual(moved["status"], "doing")
        return project["id"], moved

    def assert_project_object(self, obj, expected_name, detail):
        """断言一个项目对象只含 id / name 且 id 为正整数、name 符合预期。"""
        self.assertEqual(
            set(obj.keys()), PROJECT_FIELDS,
            f"项目对象字段结构不符（应只含 {sorted(PROJECT_FIELDS)}）：\n{detail}",
        )
        self.assertIsInstance(obj["id"], int, f"id 应为整数：\n{detail}")
        self.assertGreater(obj["id"], 0, f"id 应为正整数：\n{detail}")
        self.assertIsInstance(obj["name"], str, f"name 应为字符串：\n{detail}")
        self.assertEqual(obj["name"], expected_name, f"项目名称不符：\n{detail}")

    # ---------- 成功路径：新建数据库、返回值结构、名称处理、落库核对 ----------

    def test_create_returns_json_object_and_persists(self):
        # 前置：父目录存在而数据库文件尚不存在
        self.assertTrue(os.path.isdir(self._tmpdir.name))
        self.assertFalse(os.path.exists(self.db_path))

        cli_args = ("project-create", NAME_RAW)
        proc = self.run_cli(*cli_args)
        obj, detail = self.parse_single_json_object(
            proc, cli_args,
            expected=(
                "退出码 0、stderr 为空、stdout 为只含 id / name 的单个 JSON "
                f"对象，name={NAME!r}，id 为正整数"
            ),
        )
        self.assertEqual(proc.returncode, 0, f"创建应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功创建时标准错误应为空：\n{detail}")

        # 名称：首尾空白（空格、制表符）去除，内部内容原样保留
        self.assert_project_object(obj, NAME, detail)
        self.assertNotEqual(obj["name"], NAME_RAW, detail)
        for piece in ("研发", "  ", "API", "_", "100%", "'"):
            self.assertIn(piece, obj["name"], detail)

        # 创建后数据库文件已生成
        self.assertTrue(
            os.path.isfile(self.db_path),
            f"成功创建后数据库文件应存在：\n{detail}",
        )

        # 独立命令进程查询同一数据库：读到完全相同的单个对象（不依赖键序）
        projects = self.list_projects()
        self.assertEqual(
            projects, [obj],
            f"另一命令进程的 project-list 应读到同一个项目对象：\n{detail}\n"
            f"实际列表={projects!r}",
        )

    def test_duplicate_name_keeps_distinct_ids_sorted_ascending(self):
        # 再次提交同一名称：应创建不同标识的项目
        first_args = ("project-create", NAME_RAW)
        first_proc = self.run_cli(*first_args)
        first, first_detail = self.parse_single_json_object(
            first_proc, first_args,
            expected="退出码 0、stderr 为空、单个 JSON 项目对象",
        )
        self.assertEqual(first_proc.returncode, 0, first_detail)
        self.assertEqual(first_proc.stderr, "", first_detail)
        self.assert_project_object(first, NAME, first_detail)

        second_args = ("project-create", NAME_RAW)
        second_proc = self.run_cli(*second_args)
        second, second_detail = self.parse_single_json_object(
            second_proc, second_args,
            expected="退出码 0、stderr 为空、单个 JSON 项目对象，标识与首次不同",
        )
        self.assertEqual(second_proc.returncode, 0, second_detail)
        self.assertEqual(second_proc.stderr, "", second_detail)
        self.assert_project_object(second, NAME, second_detail)

        self.assertNotEqual(
            first["id"], second["id"],
            f"重复名称应产生不同项目标识：\n首次={first!r}\n再次={second!r}",
        )

        # 查询结果同时保留两条记录并按标识数值升序
        projects = self.list_projects()
        same_name = [p for p in projects if p["name"] == NAME]
        self.assertEqual(
            same_name, [first, second],
            f"两条同名项目应按标识升序返回：\n{second_detail}\n实际={projects!r}",
        )
        self.assertEqual(
            [p["id"] for p in projects],
            sorted(p["id"] for p in projects),
            f"项目列表应按标识升序：\n{second_detail}",
        )

    def test_create_leaves_existing_project_and_doing_task_unchanged(self):
        # 已有项目中准备一条 doing 任务，记录创建前的完整快照
        project_id, doing_task = self.build_project_with_doing_task()
        before = self.snapshot(project_id)
        before_project = [
            p for p in before["projects"] if p["id"] == project_id
        ]
        self.assertEqual(
            before_project, [{"id": project_id, "name": "原有项目"}],
            f"夹具准备异常：{before!r}",
        )
        self.assertEqual(before["tasks"], [doing_task])

        # 创建新项目
        created = self.cli_json("project-create", "新项目")
        self.assertNotEqual(created["id"], project_id)

        # 原项目对象保持不变
        after = self.snapshot(project_id)
        after_project = [p for p in after["projects"] if p["id"] == project_id]
        self.assertEqual(
            after_project, before_project,
            f"创建新项目后原项目对象不得改变：\n创建前={before_project!r}\n"
            f"创建后={after_project!r}",
        )
        # 原项目的任务完整内容（标识、所属项目、标题、doing 状态）不变
        self.assertEqual(
            after["tasks"], before["tasks"],
            f"创建新项目后原项目任务不得改变：\n创建前={before['tasks']!r}\n"
            f"创建后={after['tasks']!r}",
        )
        self.assertEqual(after["tasks"], [doing_task])

        # 新项目出现在列表中，且列表仍按标识升序
        self.assertIn(created, after["projects"])
        self.assertEqual(
            [p["id"] for p in after["projects"]],
            sorted(p["id"] for p in after["projects"]),
        )

    # ---------- 拒绝路径：通用断言 ----------

    def assert_rejected(self, cli_args, existing_project_id):
        """断言请求被拒绝：退出码 2、stdout 为空、stderr 有说明且无回溯，
        并且失败前后 project-list 与原项目 task-list 完全一致。"""
        before = self.snapshot(existing_project_id)
        proc = self.run_cli(*cli_args)
        after = self.snapshot(existing_project_id)
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 2、stdout 为空、stderr 说明拒绝原因且无 Python 回溯，"
                "project-list 与原项目 task-list 保持不变"
            ),
        )
        self.assertEqual(proc.returncode, 2, f"应返回退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"被拒绝时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"被拒绝时标准错误应说明原因：\n{detail}",
        )
        self.assertNotIn(
            "Traceback", proc.stderr,
            f"标准错误不应包含 Python 异常回溯：\n{detail}",
        )
        self.assertEqual(
            after, before,
            f"被拒绝的请求不得改动任何数据：\n{detail}\n"
            f"失败前={before!r}\n失败后={after!r}",
        )
        return proc

    # ---------- 空 / 纯空白名称 ----------

    def test_empty_and_whitespace_names_rejected(self):
        # 使用可打开的既有临时库（其中已有项目与 doing 任务）
        project_id, _ = self.build_project_with_doing_task()
        for raw_name in ("", "   ", "\t", "\n \t "):
            with self.subTest(raw_name=raw_name):
                self.assert_rejected(
                    ("project-create", raw_name), project_id
                )

    # ---------- 缺少名称参数 ----------

    def test_missing_name_argument_rejected(self):
        project_id, _ = self.build_project_with_doing_task()
        self.assert_rejected(("project-create",), project_id)

    # ---------- 存储失败：退出码 1 ----------

    def test_database_path_being_a_directory_is_storage_failure(self):
        # 先在既有临时库中准备项目与 doing 任务
        project_id, _ = self.build_project_with_doing_task()
        before = self.snapshot(project_id)

        # 有效名称配合指向已有目录的 --db：SQLite 无法打开，应退出码 1
        dir_path = os.path.join(self._tmpdir.name, "cannot_be_a_db")
        os.mkdir(dir_path)
        cli_args = ("project-create", NAME_RAW)
        proc = self.run_cli(*cli_args, db_path=dir_path)
        detail = self._detail(
            cli_args, proc,
            expected="退出码 1、stdout 为空、stderr 说明 storage failure",
        )
        self.assertEqual(proc.returncode, 1, f"存储失败应返回退出码 1：\n{detail}")
        self.assertEqual(proc.stdout, "", f"存储失败时标准输出应为空：\n{detail}")
        self.assertIn(
            "storage failure", proc.stderr,
            f"标准错误应说明存储失败：\n{detail}",
        )
        self.assertNotIn(
            "Traceback", proc.stderr,
            f"标准错误不应包含 Python 异常回溯：\n{detail}",
        )
        # 既有临时库数据不发生变化，且名称没有被保存
        self.assertEqual(
            self.snapshot(project_id), before,
            f"存储失败的请求不得改动既有数据库：\n{detail}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

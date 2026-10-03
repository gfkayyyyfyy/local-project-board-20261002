#!/usr/bin/env python3
"""project-create 创建项目的命令行回归（验收）测试。

覆盖成功路径：

- 父目录存在而数据库文件不存在时，project-create 自动创建数据库并成功；
  成功时退出码为 0、标准错误为空、标准输出是只含 id / name 两个字段的
  单个 JSON 对象，id 为正整数；
- 名称先去除首尾空白（空格、制表符），内部连续空白、中文、大小写、
  百分号、下划线与引号原样保存：输入 " \\t研发  API_100%'  \\t" 时，
  返回与落库名称均为 "研发  API_100%'"；
- 由另一个独立命令进程执行 project-list，能读到与创建结果完全相同的对象；
- 再次提交同一名称会创建不同标识的项目，project-list 同时保留两条记录，
  并按标识数值升序返回；
- 已有项目中准备一条 doing 任务后再创建新项目，原项目对象与该任务的
  完整内容（标识、所属项目、标题、状态）保持不变。

覆盖拒绝路径（使用可打开的既有临时库，退出码 2、标准输出为空、标准错误
说明原因、无 Python 回溯，拒绝前后 project-list 与原项目 task-list 的
返回值完全一致）：

- 名称为空字符串或纯空白（空格、制表符等）；
- 缺少名称参数。

覆盖存储失败：有效名称配合指向已有目录的 --db 路径时退出码 1、标准输出
为空、标准错误说明 storage failure，且既有临时库数据不变。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品：用
project-create / task-create / task-move 准备数据，用 project-list /
task-list 核对落库结果。不直接读写数据库表，不调用产品内部函数，不依赖
网络，也不依赖任何预先保存的项目或使用者自己的数据。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_create.py                  # 运行全部回归用例
    python3 -m unittest test_project_create -v      # 等价写法
    python3 -m unittest discover -p 'test_project_create.py' -v
    python3 test_project_create.py \
        ProjectCreateRegression.test_create_on_missing_database_persists  # 单用例

退出码：全部通过为 0，存在断言失败为 1。失败信息会给出命令行输入、实际
退出码、标准输出、标准错误以及预期差异；JSON 比较按对象内容进行，不依赖
键顺序，不要求错误文案逐字一致，也不依赖固定的标识取值。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

PROJECT_FIELDS = {"id", "name"}

# 名称输入：首尾添加空格与制表符；内部为中文 + 双空格 + 大小写英文 + % + _ + 单引号
NAME_RAW = " \t 研发  API_100%'  \t"
NAME = "研发  API_100%'"


class ProjectCreateRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时目录，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-project-create-")
        # 既有的可打开临时库：setUp 经公开命令在其中准备种子项目与 doing 任务
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")
        self._build_fixture()

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

    def cli_json(self, *cli_args, db_path=None):
        """准备数据用：要求成功并返回解析后的单个 JSON 值。"""
        proc = self.run_cli(*cli_args, db_path=db_path)
        detail = self._detail(
            cli_args, proc,
            db_path=db_path,
            expected="准备数据成功：退出码 0、stderr 为空",
        )
        self.assertEqual(proc.returncode, 0, f"准备数据应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"准备数据时标准错误应为空：\n{detail}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"准备数据的 stdout 不是合法 JSON：\n{detail}")

    @staticmethod
    def _detail(cli_args, proc, expected, db_path=None):
        """组装失败定位信息：命令行输入、实际退出码/输出与预期结果。"""
        return "\n".join(
            [
                f"数据库路径={db_path!r}",
                f"输入参数={list(cli_args)!r}",
                f"实际退出码={proc.returncode}",
                f"实际标准输出={proc.stdout!r}",
                f"实际标准错误={proc.stderr!r}",
                f"预期结果={expected}",
            ]
        )

    def parse_single_json_object(self, proc, cli_args, expected, db_path=None):
        """要求 stdout 恰好是一个 JSON 对象（拒绝数组或多余输出），返回该对象。"""
        detail = self._detail(cli_args, proc, expected, db_path=db_path)
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

    def list_projects(self, db_path=None):
        """通过独立命令进程执行 project-list，返回项目对象列表。"""
        cli_args = ("project-list",)
        proc = self.run_cli(*cli_args, db_path=db_path)
        detail = self._detail(
            cli_args, proc,
            db_path=db_path,
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

    def snapshot(self):
        """记录当前全部项目与种子项目任务的完整返回值。"""
        return {
            "projects": self.list_projects(),
            "seed_tasks": self.list_tasks(self.seed_project_id),
        }

    def fresh_db_path(self, filename):
        """同一临时目录内一个尚不存在的全新数据库路径（父目录存在）。"""
        path = os.path.join(self._tmpdir.name, filename)
        self.assertFalse(os.path.exists(path))
        return path

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 既有库中准备一个种子项目，内含一条已置为 doing 的任务，
        # 用于核对创建/失败请求都不改动既有数据。
        seed_project = self.cli_json("project-create", "种子项目")
        self.seed_project_id = seed_project["id"]
        seed_task = self.cli_json(
            "task-create", str(self.seed_project_id), "进行中的任务"
        )
        self.seed_task_id = seed_task["id"]
        moved = self.cli_json("task-move", str(self.seed_task_id), "doing")
        self.assertEqual(moved["status"], "doing")
        self.seed_task = moved

    def assert_create_success(self, raw_name, db_path=None):
        """project-create 成功的通用断言：返回（结果对象, 失败明细）。"""
        cli_args = ("project-create", raw_name)
        proc = self.run_cli(*cli_args, db_path=db_path)
        expected_name = raw_name.strip()
        obj, detail = self.parse_single_json_object(
            proc, cli_args,
            expected=(
                "退出码 0、stderr 为空、stdout 为只含字段 "
                f"{sorted(PROJECT_FIELDS)} 的单个 JSON 对象，"
                f"id 为正整数、name={expected_name!r}"
            ),
            db_path=db_path,
        )
        self.assertEqual(proc.returncode, 0, f"创建应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功创建时标准错误应为空：\n{detail}")
        self.assertEqual(
            set(obj.keys()), PROJECT_FIELDS,
            f"创建结果对象字段结构不符：\n{detail}",
        )
        self.assertIsInstance(obj["id"], int, f"项目标识应为整数：\n{detail}")
        self.assertGreater(obj["id"], 0, f"项目标识应为正整数：\n{detail}")
        self.assertIsInstance(obj["name"], str, f"项目名称应为字符串：\n{detail}")
        self.assertEqual(
            obj["name"], expected_name,
            f"返回名称应为去除首尾空白后的保存值：\n{detail}",
        )
        return obj, detail

    # ---------- 成功路径：库文件不存在、名称处理、跨进程读取、落库核对 ----------

    def test_create_on_missing_database_persists_and_is_listed(self):
        # 父目录存在而数据库文件不存在：创建成功并自动建库
        fresh_path = self.fresh_db_path("fresh.db")

        obj, detail = self.assert_create_success(NAME_RAW, db_path=fresh_path)
        # 保存值：只去首尾空白，内部双空格、中文、大小写、%、_、单引号原样保留
        self.assertEqual(obj["name"], NAME, detail)
        self.assertNotEqual(obj["name"], NAME_RAW, detail)
        for piece in ("研发", "  ", "API", "_", "100%", "'"):
            self.assertIn(piece, obj["name"], detail)

        # 数据库文件已被创建
        self.assertTrue(
            os.path.exists(fresh_path),
            f"成功创建后数据库文件应存在：\n{detail}",
        )

        # 另一个独立命令进程的 project-list 读到完全相同的单个对象
        listed = self.list_projects(db_path=fresh_path)
        self.assertEqual(
            listed, [obj],
            f"独立进程的 project-list 应读到与创建结果相同的对象：\n{detail}\n"
            f"实际列表={listed!r}",
        )

    # ---------- 同名再次提交：不同标识、两条记录、按标识升序 ----------

    def test_same_name_creates_distinct_projects_sorted_by_id(self):
        fresh_path = self.fresh_db_path("duplicate.db")
        first, detail = self.assert_create_success(NAME_RAW, db_path=fresh_path)
        second, second_detail = self.assert_create_success(
            NAME_RAW, db_path=fresh_path
        )
        self.assertNotEqual(
            first["id"], second["id"],
            f"重复名称应产生不同项目标识：\n首次={first!r}\n再次={second!r}",
        )
        self.assertEqual(first["name"], second["name"], NAME)

        # project-list 同时保留两条记录并按标识数值升序
        listed = self.list_projects(db_path=fresh_path)
        self.assertEqual(
            listed, [first, second],
            f"两条同名项目应按标识升序返回：\n{detail}\n实际={listed!r}",
        )
        self.assertEqual(
            [p["id"] for p in listed], sorted(p["id"] for p in listed), detail
        )
        for project in listed:
            self.assertEqual(
                set(project.keys()), PROJECT_FIELDS,
                f"列表中的项目对象应只含 {sorted(PROJECT_FIELDS)}：\n{detail}",
            )

    # ---------- 创建新项目不改动已有项目及其 doing 任务 ----------

    def test_create_keeps_existing_project_and_doing_task_unchanged(self):
        before = self.snapshot()

        created, detail = self.assert_create_success(NAME_RAW)

        after = self.snapshot()
        # 种子项目对象在创建前后完全一致
        seed_before = next(
            p for p in before["projects"] if p["id"] == self.seed_project_id
        )
        seed_after = next(
            p for p in after["projects"] if p["id"] == self.seed_project_id
        )
        self.assertEqual(
            seed_after, seed_before,
            f"创建新项目不得改动原项目对象：\n{detail}\n"
            f"创建前={seed_before!r}\n创建后={seed_after!r}",
        )
        # 原项目的 doing 任务完整内容（标识、所属项目、标题、状态）不变
        self.assertEqual(
            after["seed_tasks"], before["seed_tasks"],
            f"创建新项目后原项目任务列表不得改变：\n{detail}\n"
            f"创建前={before['seed_tasks']!r}\n创建后={after['seed_tasks']!r}",
        )
        self.assertEqual(
            after["seed_tasks"], [self.seed_task],
            f"原项目应仍只有那条 doing 任务且内容不变：\n{detail}",
        )
        # 项目列表仅新增新项目，整体仍按标识升序；新对象与创建结果一致
        self.assertEqual(
            after["projects"], before["projects"] + [created],
            f"项目列表应仅追加新项目：\n{detail}\n"
            f"创建前={before['projects']!r}\n创建后={after['projects']!r}",
        )
        self.assertEqual(
            [p["id"] for p in after["projects"]],
            sorted(p["id"] for p in after["projects"]),
            detail,
        )

    # ---------- 拒绝路径：通用断言 ----------

    def assert_rejected(self, cli_args, reason=None):
        """断言请求被拒绝：退出码 2、stdout 为空、stderr 有说明且无回溯，
        并且拒绝前后 project-list 与原项目 task-list 的返回值完全相同。"""
        before = self.snapshot()
        proc = self.run_cli(*cli_args)
        after = self.snapshot()
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 2、stdout 为空、stderr 说明拒绝原因"
                + (f"（应包含 {reason!r}）" if reason else "")
                + "，project-list 与原项目 task-list 保持不变"
            ),
        )
        self.assertEqual(proc.returncode, 2, f"应返回退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"被拒绝时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"被拒绝时标准错误应说明原因：\n{detail}",
        )
        if reason is not None:
            self.assertIn(
                reason, proc.stderr.lower(),
                f"标准错误应说明相应原因：\n{detail}",
            )
        self.assertNotIn(
            "Traceback", proc.stderr,
            f"标准错误不应包含 Python 异常回溯：\n{detail}",
        )
        self.assertEqual(
            after, before,
            f"被拒绝的请求不得改动已有项目或任务：\n{detail}\n"
            f"拒绝前={before!r}\n拒绝后={after!r}",
        )
        return proc

    # ---------- 空 / 纯空白名称 ----------

    def test_empty_and_whitespace_names_rejected(self):
        for raw_name in ("", "   ", "\t", "\n \t "):
            with self.subTest(raw_name=raw_name):
                self.assert_rejected(
                    ("project-create", raw_name), reason="name"
                )

    # ---------- 缺少名称参数 ----------

    def test_missing_name_argument_rejected(self):
        # argparse 缺参同样是退出码 2、stdout 为空、stderr 有说明、数据不变
        self.assert_rejected(("project-create",))

    # ---------- 存储失败：退出码 1 ----------

    def test_database_path_being_a_directory_is_storage_failure(self):
        # 有效名称配合指向已有目录的 --db：SQLite 无法打开，应退出码 1，
        # stdout 为空、stderr 说明存储失败，既有临时库数据不发生变化
        dir_path = os.path.join(self._tmpdir.name, "cannot_be_a_db")
        os.mkdir(dir_path)
        before = self.snapshot()

        cli_args = ("project-create", NAME_RAW)
        proc = self.run_cli(*cli_args, db_path=dir_path)
        detail = self._detail(
            cli_args, proc,
            db_path=dir_path,
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
        self.assertEqual(
            self.snapshot(), before,
            f"存储失败的请求不得改动既有数据库：\n{detail}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

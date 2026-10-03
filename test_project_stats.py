#!/usr/bin/env python3
"""project-stats 项目任务状态数量汇总的命令行回归测试。

覆盖用户指定的验收场景与边界行为：

- 项目 1 准备四条任务（两条 todo、一条 doing、一条 done），项目 2 另有一条
  doing 任务；task-move 1 doing 后 project-stats 1 恰为
  {"project_id":1,"total":4,"todo":1,"doing":2,"done":1}，项目 2 数据不变。
- 统计反映当前已保存状态：同任务多次移动不累计移动次数；空项目四个数量均为 0；
  同标题任务各计一次；只统计目标项目，其他项目任务不参与；重复查询结果一致。
- 成功时退出码 0、stdout 为只含 project_id / total / todo / doing / done 的单个
  JSON 对象，其余字段为非负整数且三者之和等于 total，stderr 为空。
- 标识规则与既有命令一致：仅接受 ASCII 数字组成的正整数
  （1..9223372036854775807），前导零按数值等价（0001 与 1 一致）；零、负数、
  非数字、超上限、范围内不存在、缺少标识或 --db 均退出码 2、stdout 为空、
  stderr 说明原因且无 Python 回溯；上限本身按存在性处理。
- 查询不改动既有项目、任务及其标识，不补写任何业务记录。
- 数据库无法打开时退出码 1、stdout 为空、stderr 说明存储失败；父目录存在而
  数据库文件尚不存在时沿用初始化行为，随后因项目不存在退出码 2 且不创建
  项目或任务。
- 由另一个独立命令进程查询同一路径读到相同结果。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create / task-move 三个现有命令，不直接读写数据库，
不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时文件），
可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_stats.py                  # 运行全部回归用例
    python3 -m unittest test_project_stats -v      # 等价写法
    python3 test_project_stats.py \\
        ProjectStatsRegression.test_acceptance_scenario  # 只跑单个用例

退出码：全部通过为 0，存在失败为 1。失败信息会给出具体命令行输入、退出码、
标准输出、标准错误与预期结果，便于定位差异。
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

SQLITE_MAX_INT = "9223372036854775807"      # 2^63 - 1：上限值本身合法
OVER_MAX = "9223372036854775808"            # 上限 + 1：越界
STATS_FIELDS = {"project_id", "total", "todo", "doing", "done"}


class ProjectStatsRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-stats-")
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

    def assert_stats_ok(self, project_raw, expected):
        """断言 project-stats 成功并返回与 expected 完全一致的统计对象。"""
        proc = self.run_cli("project-stats", project_raw)
        detail = self._detail(("project-stats", project_raw), proc)
        self.assertEqual(proc.returncode, 0, f"预期成功退出码 0：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"标准输出不是合法 JSON（应只有一个 JSON 对象）：\n{detail}")
        self.assertEqual(data, expected, f"统计结果不符：\n{detail}")
        # 对象形状与字段类型约束
        self.assertEqual(set(data.keys()), STATS_FIELDS, detail)
        for key in STATS_FIELDS:
            self.assertIsInstance(data[key], int, f"{key} 应为整数：\n{detail}")
            self.assertGreaterEqual(data[key], 0, f"{key} 应非负：\n{detail}")
        self.assertNotIsInstance(data["total"], bool, detail)
        self.assertEqual(
            data["todo"] + data["doing"] + data["done"], data["total"], detail
        )
        return data

    def assert_rejected(self, cli_args, db_path=None):
        """断言请求被拒绝：退出码 2、stdout 为空、stderr 有说明且无回溯。"""
        proc = self.run_cli(*cli_args, db_path=db_path)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 2, f"预期退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"被拒绝时标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"被拒绝时标准错误应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr,
                         f"不得出现 Python 异常回溯：\n{detail}")
        return proc

    # ---------- 通过公开命令准备夹具 ----------

    def build_acceptance_fixture(self):
        """验收夹具：项目 1 四条任务（t1/t2 todo、t3 doing、t4 done），
        项目 2 一条 doing 任务。返回各项目与任务标识。"""
        p1 = self.cli_json("project-create", "p1")["id"]
        p2 = self.cli_json("project-create", "p2")["id"]
        ids = [
            self.cli_json("task-create", str(p1), f"t{i}")["id"]
            for i in range(1, 5)
        ]
        t1, _t2, t3, t4 = ids
        self.cli_json("task-move", str(t3), "doing")
        self.cli_json("task-move", str(t4), "done")
        other = self.cli_json("task-create", str(p2), "other")["id"]
        self.cli_json("task-move", str(other), "doing")
        return p1, p2, ids, other

    def snapshot(self, *project_ids):
        """记录若干项目的完整任务列表与统计，用于校验查询无副作用。"""
        result = {}
        for pid in project_ids:
            result[pid] = (
                self.cli_json("task-list", str(pid)),
                self.cli_json("project-stats", str(pid)),
            )
        return result

    # ---------- 用户指定的验收场景 ----------

    def test_acceptance_scenario(self):
        p1, p2, _ids, _other = self.build_acceptance_fixture()
        # 移动前项目 1：todo=2, doing=1, done=1
        self.assert_stats_ok(str(p1), {
            "project_id": p1, "total": 4, "todo": 2, "doing": 1, "done": 1,
        })
        # task-move 1 doing：任务 1 由 todo 移到 doing
        moved = self.cli_json("task-move", "1", "doing")
        self.assertEqual((moved["id"], moved["status"]), (1, "doing"))

        # 关键断言：与用户给定的预期逐字节结构一致
        self.assert_stats_ok(str(p1), {
            "project_id": 1, "total": 4, "todo": 1, "doing": 2, "done": 1,
        })
        # 项目 2 数据不变
        self.assert_stats_ok(str(p2), {
            "project_id": p2, "total": 1, "todo": 0, "doing": 1, "done": 0,
        })

    def test_independent_process_reads_same_result(self):
        # 每个 CLI 调用本身就是独立进程；这里显式再以原始 subprocess 方式
        # 从“另一个进程”查询同一路径，结果应与主用例一致。
        p1, _p2, _ids, _other = self.build_acceptance_fixture()
        self.cli_json("task-move", "1", "doing")
        proc = subprocess.run(
            [sys.executable, "-m", "kanban", "--db", self.db_path,
             "project-stats", str(p1)],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, self._detail(
            ("project-stats", str(p1)), proc))
        self.assertEqual(proc.stderr, "")
        self.assertEqual(json.loads(proc.stdout), {
            "project_id": p1, "total": 4, "todo": 1, "doing": 2, "done": 1,
        })

    # ---------- 空项目、同标题、当前状态、重复查询 ----------

    def test_empty_project_reports_all_zero(self):
        p1 = self.cli_json("project-create", "empty")["id"]
        self.assert_stats_ok(str(p1), {
            "project_id": p1, "total": 0, "todo": 0,
            "doing": 0, "done": 0,
        })

    def test_duplicate_titles_count_separately(self):
        p1 = self.cli_json("project-create", "p1")["id"]
        for _ in range(3):
            self.cli_json("task-create", str(p1), "same title")
        self.cli_json("task-create", str(p1), "different")
        self.assert_stats_ok(str(p1), {
            "project_id": p1, "total": 4, "todo": 4, "doing": 0, "done": 0,
        })

    def test_stats_reflect_current_state_not_move_count(self):
        p1 = self.cli_json("project-create", "p1")["id"]
        t1 = self.cli_json("task-create", str(p1), "t1")["id"]
        # 多次来回移动：只反映当前状态，不累计次数
        for status in ("doing", "done", "doing", "todo", "done"):
            self.cli_json("task-move", str(t1), status)
        self.assert_stats_ok(str(p1), {
            "project_id": p1, "total": 1, "todo": 0, "doing": 0, "done": 1,
        })

    def test_repeated_queries_are_identical_and_read_only(self):
        p1, p2, _ids, _other = self.build_acceptance_fixture()
        self.cli_json("task-move", "1", "doing")
        before = self.snapshot(p1, p2)
        first = self.run_cli("project-stats", str(p1))
        for _ in range(3):
            again = self.run_cli("project-stats", str(p1))
            self.assertEqual(
                (again.returncode, again.stdout, again.stderr),
                (first.returncode, first.stdout, first.stderr),
            )
        # 查询前后两个项目的完整任务列表与统计完全不变
        self.assertEqual(self.snapshot(p1, p2), before)
        # 项目列表也不变（不补写任何业务记录）
        projects_once = self.cli_json("project-list")
        self.assertEqual(self.cli_json("project-list"), projects_once)

    def test_other_projects_tasks_are_not_counted(self):
        p1, p2, _ids, _other = self.build_acceptance_fixture()
        # 在项目 2 再添加不同状态的任务，项目 1 的统计不受任何影响
        t = self.cli_json("task-create", str(p2), "another")["id"]
        self.cli_json("task-move", str(t), "done")
        self.assert_stats_ok(str(p1), {
            "project_id": p1, "total": 4, "todo": 2, "doing": 1, "done": 1,
        })
        self.assert_stats_ok(str(p2), {
            "project_id": p2, "total": 2, "todo": 0, "doing": 1, "done": 1,
        })

    # ---------- 标识规则与既有命令一致 ----------

    def test_leading_zeros_are_equivalent(self):
        p1, _p2, _ids, _other = self.build_acceptance_fixture()
        self.cli_json("task-move", "1", "doing")
        expected = {
            "project_id": p1, "total": 4, "todo": 1, "doing": 2, "done": 1,
        }
        self.assert_stats_ok("0001", expected)
        self.assert_stats_ok("0" * 5000 + "1", expected)

    def test_invalid_identifiers_rejected(self):
        self.build_acceptance_fixture()
        for raw in ("0", "0000", "-1", "-0", "abc", "1.5", "1e3", "+1",
                    " 1", "1 ", "", "１", OVER_MAX, "9" * 5000,
                    "0" * 5000 + OVER_MAX):
            with self.subTest(raw=raw):
                proc = self.assert_rejected(["project-stats", raw])
                # 数据保持验收前状态：项目 1 仍为 2 todo / 1 doing / 1 done
                self.assertEqual(json.loads(
                    self.run_cli("project-stats", "1").stdout), {
                        "project_id": 1, "total": 4,
                        "todo": 2, "doing": 1, "done": 1,
                })
                if raw == OVER_MAX or raw == "9" * 5000 \
                        or raw == "0" * 5000 + OVER_MAX:
                    self.assertIn("range", proc.stderr.lower())

    def test_max_int_is_checked_by_existence(self):
        self.build_acceptance_fixture()
        # 上限本身合法：全新库/现有库中无此项目，按“不存在”处理，而非越界
        for raw in (SQLITE_MAX_INT, "0" * 5000 + SQLITE_MAX_INT):
            with self.subTest(raw=f"<{len(raw)} chars>"):
                proc = self.assert_rejected(["project-stats", raw])
                self.assertIn("does not exist", proc.stderr)

    def test_nonexistent_in_range_project_rejected(self):
        self.build_acceptance_fixture()
        proc = self.assert_rejected(["project-stats", "999"])
        self.assertIn("does not exist", proc.stderr)

    def test_missing_arguments_keep_exit_code_2(self):
        self.build_acceptance_fixture()
        # 缺少项目标识
        self.assert_rejected(["project-stats"])
        # 缺少 --db：argparse 在解析阶段退出 2，stdout 为空
        proc = subprocess.run(
            [sys.executable, "-m", "kanban", "project-stats", "1"],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertNotEqual(proc.stderr.strip(), "")
        self.assertNotIn("Traceback", proc.stderr)

    # ---------- 存储与初始化行为 ----------

    def test_unopenable_database_is_storage_failure(self):
        dir_path = os.path.join(self._tmpdir.name, "cannot_be_a_db")
        os.mkdir(dir_path)
        proc = self.run_cli("project-stats", "1", db_path=dir_path)
        detail = self._detail(("project-stats", "1"), proc)
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)

    def test_missing_database_file_initializes_then_reports_missing_project(self):
        new_db = os.path.join(self._tmpdir.name, "brand-new.db")
        self.assertFalse(os.path.exists(new_db))
        proc = self.assert_rejected(["project-stats", "1"], db_path=new_db)
        self.assertIn("does not exist", proc.stderr)
        # 沿用初始化行为：数据库文件被创建，但不含任何项目或任务
        self.assertTrue(os.path.exists(new_db))
        conn = sqlite3.connect(new_db)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM projects").fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

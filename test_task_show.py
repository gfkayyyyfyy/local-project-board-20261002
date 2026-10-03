#!/usr/bin/env python3
"""task-show 按标识读取单条任务的命令行回归（验收）测试。

覆盖：

- 用户指定的验收场景：两个项目各有一条标题为“整理 API”的任务，第二条
  移到 doing、第一条保持 todo；用第二条标识及其前导零写法（0002、前导
  五千个零）查询，都只返回第二条任务，所属项目与 doing 状态准确，且与
  task-list 中该任务的对象完全一致；task-rename 改名为“修复 API”后
  再查询返回新标题与原有标识、项目、状态。
- 成功路径：退出码 0、标准错误为空、标准输出只有一个 JSON 对象，字段
  集合恰为 id / project_id / title / status；标题按保存值原样返回
  （内部空白、中文、大小写、%、_、单引号均不改变）；同标题任务按各自
  标识区分；只读，不改变项目与任务的内容和数量。
- 标识规则沿用 task-move / task-rename：允许前导零并按数值判断；零、
  负数、非数字、带正号或首尾空白、全角数字、超过 9223372036854775807
  的值退出码 2（越界值说明超出支持范围），范围内不存在的标识退出码 2
  并说明任务不存在；缺少任务标识、缺少 --db 或其路径值同样退出码 2。
- 存储失败：--db 指向目录、父目录不存在、文件不是可用 SQLite 数据库时
  退出码 1 并说明 storage failure；父目录存在而库文件不存在时自动建库，
  随后按任务不存在退出码 2，新库不含项目或任务。
- 所有失败均标准输出为空、标准错误说明原因且无 Python 回溯。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，不直接读写
数据库，不依赖网络，也不依赖任何预先保存的项目。每个用例在独立临时目录
中使用全新隔离 SQLite 数据库，可单独执行、可重复执行，结束后自动清理。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_show.py
    python3 -m unittest test_task_show -v
    python3 test_task_show.py TaskShowRegression.test_acceptance_scenario
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

SQLITE_MAX_INT = "9223372036854775807"
OVER_MAX = "9223372036854775808"

TASK_FIELDS = {"id", "project_id", "title", "status"}


class TaskShowRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-show-regtest-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")

    def tearDown(self):
        self._tmpdir.cleanup()

    # ---------- 命令行与结果解析辅助 ----------

    def run_cli(self, *cli_args, db_path=None, omit_db=False):
        cmd = [sys.executable, "-m", "kanban"]
        if not omit_db:
            cmd += ["--db", db_path or self.db_path]
        cmd += list(cli_args)
        return subprocess.run(
            cmd, cwd=REPO_ROOT, capture_output=True,
            text=True, encoding="utf-8",
        )

    def cli_json(self, *cli_args):
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, f"准备数据应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"准备数据时 stderr 应为空：\n{detail}")
        return json.loads(proc.stdout)

    @staticmethod
    def _detail(cli_args, proc):
        return (
            f"输入参数={list(cli_args)!r}\n"
            f"退出码={proc.returncode}\n"
            f"标准输出={proc.stdout!r}\n"
            f"标准错误={proc.stderr!r}"
        )

    def snapshot(self):
        """项目列表与各项目任务列表快照，用于校验只读与无副作用。"""
        snap = {"projects": self.cli_json("project-list"), "tasks": {}}
        for project in snap["projects"]:
            snap["tasks"][project["id"]] = self.cli_json(
                "task-list", str(project["id"])
            )
        return snap

    def assert_show_ok(self, raw_id, expected):
        proc = self.run_cli("task-show", raw_id)
        detail = self._detail(("task-show", raw_id), proc)
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        obj = json.loads(proc.stdout)
        self.assertIsInstance(obj, dict, detail)
        self.assertEqual(set(obj.keys()), TASK_FIELDS, detail)
        self.assertEqual(obj, expected, detail)
        return obj

    def assert_rejected(self, cli_args, omit_db=False, db_path=None,
                        expect_phrase=None, expect_range=False):
        proc = self.run_cli(
            *cli_args, omit_db=omit_db, db_path=db_path
        )
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 2, f"预期退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"失败时 stdout 应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"失败时 stderr 应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr,
                         f"不得出现 Python 回溯：\n{detail}")
        if expect_phrase:
            self.assertIn(expect_phrase, proc.stderr, detail)
        if expect_range:
            self.assertIn("range", proc.stderr.lower(), detail)
        return proc

    # ---------- 用户指定的验收场景 ----------

    def test_acceptance_scenario(self):
        p1 = self.cli_json("project-create", "p1")["id"]
        p2 = self.cli_json("project-create", "p2")["id"]
        t1 = self.cli_json("task-create", str(p1), "整理 API")
        t2 = self.cli_json("task-create", str(p2), "整理 API")
        self.cli_json("task-move", str(t2["id"]), "doing")
        before = self.snapshot()

        expected_t2 = {"id": t2["id"], "project_id": p2,
                       "title": "整理 API", "status": "doing"}
        expected_t1 = {"id": t1["id"], "project_id": p1,
                       "title": "整理 API", "status": "todo"}

        # 普通写法与前导零写法都只返回第二条
        for raw in (str(t2["id"]), "000" + str(t2["id"]),
                    "0" * 5000 + str(t2["id"])):
            with self.subTest(raw=f"<{len(raw)} chars>"):
                self.assert_show_ok(raw, expected_t2)

        # 第一条保持 todo，仍可按自己的标识读到
        self.assert_show_ok(str(t1["id"]), expected_t1)

        # 与 task-list 中同一任务的对象一致
        listed = self.cli_json("task-list", str(p2))
        self.assertEqual(
            [obj for obj in listed if obj["id"] == t2["id"]],
            [expected_t2],
        )

        # 改名为“修复 API”后返回新标题及原有标识、项目、状态
        renamed = self.cli_json("task-rename", str(t2["id"]), "修复 API")
        self.assertEqual(
            renamed,
            {"id": t2["id"], "project_id": p2,
             "title": "修复 API", "status": "doing"},
        )
        self.assert_show_ok(
            str(t2["id"]),
            {"id": t2["id"], "project_id": p2,
             "title": "修复 API", "status": "doing"},
        )

        # 全程只读查询不改变任何项目与任务
        self.assertEqual(self.snapshot()["projects"], before["projects"])
        # 改名后项目二任务标题变化是 rename 的效果；show 本身不再产生变化
        show_after_rename = self.snapshot()
        self.assert_show_ok(
            "000" + str(t2["id"]),
            {"id": t2["id"], "project_id": p2,
             "title": "修复 API", "status": "doing"},
        )
        self.assertEqual(self.snapshot(), show_after_rename)

    # ---------- 标题原样返回、同标题按标识区分 ----------

    def test_title_returned_exactly_as_saved(self):
        pid = self.cli_json("project-create", "p")["id"]
        saved_title = "整理  API_100%'"
        created = self.cli_json(
            "task-create", str(pid), "  " + saved_title + "  "
        )
        self.assertEqual(created["title"], saved_title)
        self.cli_json("task-move", str(created["id"]), "done")
        self.assert_show_ok(
            str(created["id"]),
            {"id": created["id"], "project_id": pid,
             "title": saved_title, "status": "done"},
        )

    def test_same_title_tasks_distinguished_by_id(self):
        pid = self.cli_json("project-create", "p")["id"]
        a = self.cli_json("task-create", str(pid), "same title")
        b = self.cli_json("task-create", str(pid), "same title")
        self.cli_json("task-move", str(b["id"]), "doing")
        self.assert_show_ok(
            str(a["id"]),
            {"id": a["id"], "project_id": pid,
             "title": "same title", "status": "todo"},
        )
        self.assert_show_ok(
            "000" + str(b["id"]),
            {"id": b["id"], "project_id": pid,
             "title": "same title", "status": "doing"},
        )

    # ---------- 标识失败路径 ----------

    def test_invalid_identifiers_rejected(self):
        self.cli_json("project-create", "p")
        task = self.cli_json("task-create", "1", "x")
        before = self.snapshot()
        for raw in ["0", "0000", "-1", "-0", "abc", "1.5", "1e3", "+1",
                    " 1", "1 ", "", "１"]:
            with self.subTest(raw=raw):
                self.assert_rejected(
                    ["task-show", raw], expect_phrase="positive integer"
                )
        self.assertEqual(self.snapshot(), before)

    def test_out_of_range_identifiers_rejected(self):
        for raw in (OVER_MAX, "9" * 5000, "0" * 5000 + OVER_MAX):
            with self.subTest(length=len(raw)):
                self.assert_rejected(["task-show", raw], expect_range=True)

    def test_max_int_is_checked_by_existence(self):
        # 上限本身合法：全新库中按“任务不存在”处理，而非越界
        for raw in (SQLITE_MAX_INT, "0" * 5000 + SQLITE_MAX_INT):
            with self.subTest(length=len(raw)):
                self.assert_rejected(
                    ["task-show", raw], expect_phrase="does not exist"
                )

    def test_in_range_nonexistent_identifier_rejected(self):
        self.assert_rejected(["task-show", "999"],
                             expect_phrase="does not exist")

    def test_missing_arguments_rejected(self):
        # 缺少任务标识
        self.assert_rejected(["task-show"])
        # 缺少 --db
        proc = self.run_cli("task-show", "1", omit_db=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertNotIn("Traceback", proc.stderr)
        # --db 缺少路径值
        proc = subprocess.run(
            [sys.executable, "-m", "kanban", "--db"],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertNotIn("Traceback", proc.stderr)

    # ---------- 存储失败协议 ----------

    def test_storage_failures_return_exit_code_1(self):
        # --db 指向已有目录
        dir_path = os.path.join(self._tmpdir.name, "a_dir")
        os.mkdir(dir_path)
        proc = self.run_cli("task-show", "1", db_path=dir_path)
        self.assertEqual(proc.returncode, 1, self._detail(("task-show", "1"), proc))
        self.assertEqual(proc.stdout, "")
        self.assertIn("storage failure", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

        # 父目录不存在
        proc = self.run_cli(
            "task-show", "1",
            db_path=os.path.join(self._tmpdir.name, "no-parent", "x.db"),
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertIn("storage failure", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

        # 文件不是可用的 SQLite 数据库
        bad_db = os.path.join(self._tmpdir.name, "bad.db")
        with open(bad_db, "w", encoding="utf-8") as fh:
            fh.write("not a sqlite database")
        proc = self.run_cli("task-show", "1", db_path=bad_db)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertIn("storage failure", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_missing_database_file_is_created_then_task_not_found(self):
        fresh = os.path.join(self._tmpdir.name, "fresh.db")
        self.assertFalse(os.path.exists(fresh))
        self.assert_rejected(["task-show", "1"], db_path=fresh,
                             expect_phrase="does not exist")
        self.assertTrue(os.path.exists(fresh))
        # 新库不含项目或任务
        proc = self.run_cli("project-list", db_path=fresh)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

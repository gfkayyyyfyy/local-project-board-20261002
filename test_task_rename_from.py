#!/usr/bin/env python3
"""task-rename --from 预期原标题的命令行回归（验收）测试。

覆盖：

- 用户验收场景：两个同标题任务，任务 1（项目 1）为 doing、任务 2 为 todo；
  ``task-rename 0001 "修复 API" --from " 设计 API "`` 成功（退出码 0、
  stderr 为空、stdout 为与 task-show 同结构的单个任务 JSON，标题为
  ``修复 API``、其余字段原样），再次执行同一命令退出码 2、stdout 为空、
  stderr 同时说明已保存的当前标题 ``修复 API`` 与处理后的预期标题
  ``设计 API``；任务 1 仍为 ``修复 API``、任务 2 保持原值。
- 新标题处理后等于当前标题时：预期匹配则成功返回原任务、不新增记录；
  预期不匹配仍拒绝。
- ``--from`` 与新标题都只去除首尾空白：内部空白、大小写、中文、百分号、
  下划线与引号原样保留，按逐字相等匹配，不做模糊匹配（大小写或内部空白
  不同即拒绝）。
- 预期原标题为空字符串或纯空白、``--from`` 缺值、必需位置参数缺失、
  新标题为空、任务标识无效/越界/不存在均退出码 2；标识允许前导零
  （``0001`` 与 ``1`` 等价）。
- 省略 ``--from`` 时保留直接改名的现有规则：允许重名与同标题提交。
- 数据库无法打开时退出码 1、stdout 为空、stderr 说明存储失败。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，不直接读写
数据库，不调用产品内部函数，不依赖网络或任何预先保存的项目。每个用例在
独立临时目录中使用全新的隔离 SQLite 数据库，结束后自动清理。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_rename_from.py
    python3 -m unittest test_task_rename_from -v
    python3 test_task_rename_from.py \
        TaskRenameFromRegression.test_acceptance_match_then_repeat_conflicts

退出码：全部通过为 0，存在失败为 1。不依赖 JSON 键顺序或错误文案逐字一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TASK_FIELDS = {"id", "project_id", "title", "status"}

# 两条任务共用的初始标题（用户验收场景）
OLD_TITLE = "设计 API"
NEW_TITLE = "修复 API"

# 含内部空白、大小写、中文、%、_、单引号的标题，用于核对逐字匹配
LITERAL_TITLE = "修复  API_100%'"


class TaskRenameFromRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-rename-from-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")
        self.project = self.cli_json("project-create", "alpha")["id"]
        # 任务 1 与任务 2 同标题；任务 1 随后置为 doing，任务 2 保持 todo
        self.task1 = self.cli_json(
            "task-create", str(self.project), OLD_TITLE
        )["id"]
        self.task2 = self.cli_json(
            "task-create", str(self.project), OLD_TITLE
        )["id"]
        self.assertEqual(self.task1, 1)
        self.assertEqual(self.task2, 2)
        proc = self.run_cli("task-move", str(self.task1), "doing")
        self.assertEqual(proc.returncode, 0, self.detail(
            ("task-move", "1", "doing"), proc, expected="准备任务 1 为 doing"))

    def tearDown(self):
        self._tmpdir.cleanup()

    # ---------- 命令行与解析辅助 ----------

    def run_cli(self, *cli_args, db_path=None):
        return subprocess.run(
            [sys.executable, "-m", "kanban",
             "--db", db_path or self.db_path, *cli_args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def cli_json(self, *cli_args):
        proc = self.run_cli(*cli_args)
        detail = self.detail(cli_args, proc, expected="准备数据成功")
        self.assertEqual(proc.returncode, 0, f"准备数据应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"准备数据时 stderr 应为空：\n{detail}")
        return json.loads(proc.stdout)

    def detail(self, cli_args, proc, expected=None):
        return "\n".join([
            f"输入参数={list(cli_args)!r}",
            f"实际退出码={proc.returncode}",
            f"实际标准输出={proc.stdout!r}",
            f"实际标准错误={proc.stderr!r}",
            f"预期结果={expected!r}",
        ])

    def show(self, task_id):
        proc = self.run_cli("task-show", str(task_id))
        self.assertEqual(proc.returncode, 0, self.detail(
            ("task-show", str(task_id)), proc, expected="task-show 成功"))
        return json.loads(proc.stdout)

    def list_tasks(self):
        proc = self.run_cli("task-list", str(self.project))
        self.assertEqual(proc.returncode, 0, self.detail(
            ("task-list", str(self.project)), proc, expected="task-list 成功"))
        return json.loads(proc.stdout)

    def task_obj(self, task_id, title, status):
        return {"id": task_id, "project_id": self.project,
                "title": title, "status": status}

    def assert_rejected(self, proc, cli_args):
        """预期不匹配/非法参数的统一拒绝协议：rc2、stdout 空、stderr 非空无回溯。"""
        detail = self.detail(cli_args, proc, expected="退出码 2、stdout 为空")
        self.assertEqual(proc.returncode, 2, f"应退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"失败时 stdout 应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "", f"stderr 应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr, f"不应出现回溯：\n{detail}")
        return detail

    # ---------- 用户验收场景 ----------

    def test_acceptance_match_then_repeat_conflicts(self):
        # 第一次：任务 1 已保存标题为“设计 API”，与 --from（首尾带空白）
        # 处理后的预期逐字相同，改名为“修复 API”
        cli_args = ("task-rename", "0001", NEW_TITLE, "--from", f"  {OLD_TITLE}  ")
        proc = self.run_cli(*cli_args)
        detail = self.detail(
            cli_args, proc,
            expected="退出码 0、stderr 空、stdout 为标题“修复 API”的任务 1 对象",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        renamed = json.loads(proc.stdout)
        self.assertEqual(renamed, self.task_obj(1, NEW_TITLE, "doing"), detail)
        self.assertEqual(set(renamed), TASK_FIELDS, detail)

        # 再次执行同一命令：当前已是“修复 API”，与预期“设计 API”不符，拒绝
        proc2 = self.run_cli(*cli_args)
        detail2 = self.assert_rejected(proc2, cli_args)
        # stderr 同时说明已保存的当前标题与处理后的预期标题
        self.assertIn(NEW_TITLE, proc2.stderr,
                      f"stderr 应说明当前标题为 {NEW_TITLE!r}：\n{detail2}")
        self.assertIn(OLD_TITLE, proc2.stderr,
                      f"stderr 应说明预期标题为 {OLD_TITLE!r}：\n{detail2}")

        # 任务 1 仍为“修复 API”且 doing，任务 2（同标题对照）保持原值
        self.assertEqual(self.show(1), self.task_obj(1, NEW_TITLE, "doing"))
        self.assertEqual(self.show(2), self.task_obj(2, OLD_TITLE, "todo"))
        self.assertEqual(
            [(t["id"], t["title"], t["status"]) for t in self.list_tasks()],
            [(1, NEW_TITLE, "doing"), (2, OLD_TITLE, "todo")],
        )

    def test_leading_zero_id_equivalent_with_from(self):
        # 0001 与 1 等价：同样先成功、重复时被拒绝
        proc = self.run_cli(
            "task-rename", "0001", NEW_TITLE, "--from", OLD_TITLE)
        detail = self.detail(
            ("task-rename", "0001", NEW_TITLE, "--from", OLD_TITLE), proc,
            expected="0001 按数值 1 处理，改名成功",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, NEW_TITLE, "doing"))
        proc2 = self.run_cli(
            "task-rename", "0001", NEW_TITLE, "--from", OLD_TITLE)
        self.assert_rejected(
            proc2, ("task-rename", "0001", NEW_TITLE, "--from", OLD_TITLE))
        self.assertEqual(self.show(1), self.task_obj(1, NEW_TITLE, "doing"))

    def test_same_title_match_is_noop_success(self):
        # 新标题等于当前标题且预期匹配：成功返回原任务，不新增记录
        before = self.list_tasks()
        cli_args = ("task-rename", "1", f"  {OLD_TITLE}  ",
                    "--from", f"\t{OLD_TITLE}\t")
        proc = self.run_cli(*cli_args)
        detail = self.detail(
            cli_args, proc,
            expected="预期相符且新标题等于当前：退出码 0、返回原任务、不新增记录",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, OLD_TITLE, "doing"))
        self.assertEqual(self.list_tasks(), before)

    def test_same_title_still_rejected_when_from_mismatches(self):
        # 新标题等于当前标题，但 --from 预期不匹配：仍拒绝
        cli_args = ("task-rename", "1", OLD_TITLE, "--from", NEW_TITLE)
        proc = self.run_cli(*cli_args)
        detail = self.assert_rejected(proc, cli_args)
        self.assertIn(OLD_TITLE, proc.stderr,
                      f"stderr 应说明当前标题为 {OLD_TITLE!r}：\n{detail}")
        self.assertIn(NEW_TITLE, proc.stderr,
                      f"stderr 应说明预期标题为 {NEW_TITLE!r}：\n{detail}")
        # 标题与状态均不变，没有新增记录
        self.assertEqual(self.show(1), self.task_obj(1, OLD_TITLE, "doing"))
        self.assertEqual(self.show(2), self.task_obj(2, OLD_TITLE, "todo"))

    # ---------- 逐字匹配：首尾空白去除，其余原样 ----------

    def test_from_surrounding_whitespace_is_stripped(self):
        # 预期标题首尾空白被去除后与保存标题逐字相等：成功
        proc = self.run_cli(
            "task-rename", "1", NEW_TITLE, "--from", f" \t{OLD_TITLE}\n ")
        detail = self.detail(
            ("task-rename", "1", NEW_TITLE, "--from", f" <空白>{OLD_TITLE}<空白>"),
            proc, expected="预期标题首尾空白被去除后匹配，改名成功",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, NEW_TITLE, "doing"))

    def test_from_match_is_verbatim_no_fuzzy_matching(self):
        # 大小写、内部空白不同的预期标题都不匹配：拒绝且数据不变
        # （注意：首尾空白会被去除，故这里只列去空白后仍不同的拼写）
        for wrong in ("设计 api", "设计  API", "设计API",
                      "x" + OLD_TITLE, OLD_TITLE + "x"):
            with self.subTest(wrong=wrong):
                before = self.list_tasks()
                proc = self.run_cli(
                    "task-rename", "1", NEW_TITLE, "--from", wrong)
                detail = self.assert_rejected(
                    proc, ("task-rename", "1", NEW_TITLE, "--from", wrong))
                # stderr 同时给出已保存的当前标题与处理后的预期标题
                self.assertIn(OLD_TITLE, proc.stderr,
                              f"stderr 应说明当前标题：\n{detail}")
                self.assertEqual(self.list_tasks(), before, detail)

    def test_literal_characters_in_from_matched_verbatim(self):
        # 先把任务 1 改成含内部双空格、大小写、中文、%、_、单引号的标题，
        # 再用逐字相等的 --from 改名成功；任何一个字符不同即拒绝
        proc = self.run_cli("task-rename", "1", f"  {LITERAL_TITLE}  ")
        self.assertEqual(proc.returncode, 0, self.detail(
            ("task-rename", "1", f"  {LITERAL_TITLE}  "), proc,
            expected="准备任务 1 为含特殊字符的标题"))
        self.assertEqual(
            self.show(1), self.task_obj(1, LITERAL_TITLE, "doing"))

        # 逐字相等（首尾再加空白）：成功
        proc = self.run_cli(
            "task-rename", "1", NEW_TITLE, "--from", f" {LITERAL_TITLE} ")
        detail = self.detail(
            ("task-rename", "1", NEW_TITLE, "--from", f" {LITERAL_TITLE} "),
            proc, expected="特殊字符逐字相等，改名成功",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, NEW_TITLE, "doing"))

        # 把百分号改成下划线等任何差异都不匹配
        proc = self.run_cli(
            "task-rename", "1", OLD_TITLE,
            "--from", LITERAL_TITLE.replace("%", "_"))
        self.assert_rejected(
            proc,
            ("task-rename", "1", OLD_TITLE,
             "--from", LITERAL_TITLE.replace("%", "_")))
        self.assertEqual(self.show(1), self.task_obj(1, NEW_TITLE, "doing"))

    # ---------- 非法参数 ----------

    def assert_usage_failure_leaves_data(self, cli_args, reason=None):
        before = self.list_tasks()
        proc = self.run_cli(*cli_args)
        detail = self.assert_rejected(proc, cli_args)
        if reason is not None:
            self.assertIn(reason, proc.stderr.lower(),
                          f"stderr 应说明相应原因：\n{detail}")
        self.assertEqual(self.list_tasks(), before,
                         f"被拒绝的请求不得改动数据：\n{detail}")

    def test_empty_and_whitespace_from_rejected(self):
        for bad in ("", "   ", "\t", "\n \t "):
            with self.subTest(bad=bad):
                self.assert_usage_failure_leaves_data(
                    ("task-rename", "1", NEW_TITLE, "--from", bad),
                    reason="title",
                )

    def test_empty_new_title_with_from_rejected(self):
        # 即使预期匹配，新标题去除首尾空白后为空也拒绝
        for bad in ("", "   ", "\t"):
            with self.subTest(bad=bad):
                self.assert_usage_failure_leaves_data(
                    ("task-rename", "1", bad, "--from", OLD_TITLE),
                    reason="title",
                )

    def test_from_missing_value(self):
        self.assert_usage_failure_leaves_data(
            ("task-rename", "1", NEW_TITLE, "--from"))

    def test_missing_required_arguments(self):
        for cli_args in (
            ("task-rename",),
            ("task-rename", "1"),
            ("task-rename", "1", "--from", OLD_TITLE),
        ):
            with self.subTest(cli_args=cli_args):
                self.assert_usage_failure_leaves_data(cli_args)

    def test_invalid_or_missing_task_id_with_from(self):
        for cli_args in (
            ("task-rename", "abc", NEW_TITLE, "--from", OLD_TITLE),
            ("task-rename", "0", NEW_TITLE, "--from", OLD_TITLE),
            ("task-rename", "-3", NEW_TITLE, "--from", OLD_TITLE),
            ("task-rename", "9223372036854775808", NEW_TITLE, "--from", OLD_TITLE),
            ("task-rename", "999", NEW_TITLE, "--from", OLD_TITLE),
        ):
            with self.subTest(cli_args=cli_args):
                self.assert_usage_failure_leaves_data(cli_args)
        self.assertEqual(self.show(1), self.task_obj(1, OLD_TITLE, "doing"))
        self.assertEqual(self.show(2), self.task_obj(2, OLD_TITLE, "todo"))

    # ---------- 省略 --from 时保留现有行为 ----------

    def test_without_from_renames_directly(self):
        # 直接改名：允许与同标题任务重名
        proc = self.run_cli("task-rename", "1", NEW_TITLE)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, NEW_TITLE, "doing"))

        # 改成任务 2 的标题也成功（允许重名）
        proc = self.run_cli("task-rename", "1", f"  {OLD_TITLE}  ")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, OLD_TITLE, "doing"))

        # 同标题再次提交仍成功、不新增记录
        before = self.list_tasks()
        proc = self.run_cli("task-rename", "0001", OLD_TITLE)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, OLD_TITLE, "doing"))
        self.assertEqual(self.list_tasks(), before)

    # ---------- 存储失败 ----------

    def test_storage_failure_exit_code_one(self):
        proc = self.run_cli(
            "task-rename", "1", NEW_TITLE, "--from", OLD_TITLE,
            db_path=os.path.join(self._tmpdir.name, "missing_dir", "x.db"),
        )
        cli_args = ("task-rename", "1", NEW_TITLE, "--from", OLD_TITLE)
        detail = self.detail(cli_args, proc,
                             expected="退出码 1、stdout 空、stderr 说明存储失败")
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)

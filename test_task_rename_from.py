#!/usr/bin/env python3
"""task-rename --from 预期原标题的命令行回归（验收）测试。

覆盖：

- 用户验收场景：任务 1（项目 1）标题为“设计 API”、状态 doing，另有同标题
  任务 2 作为对照；``task-rename 0001 "修复 API" --from " 设计 API "``
  成功（退出码 0、stderr 为空、stdout 为与 task-show 同结构的单个任务
  JSON，标题变为“修复 API”，标识、所属项目与 doing 状态原样保留），再次
  执行同一命令因原标题不匹配而退出码 2、stdout 为空、stderr 同时说明已
  保存的当前标题“修复 API”与处理后的预期标题“设计 API”；task-show 与
  task-list 确认任务 1 保持“修复 API”，对照任务 2 保持原值。
- --from 与新标题都先去除首尾空白（内部空白、大小写、中文、%、_、引号
  原样保留），处理后的预期标题与已保存标题逐字相等才允许改名：内部双
  空格、大小写差异等均不匹配，不作模糊匹配。
- 新标题处理后等于当前标题且预期匹配时成功返回原任务、不新增记录；即使
  新标题等于当前标题，预期不匹配也拒绝。
- 预期标题或新标题去除首尾空白后为空、--from 缺值、必要参数缺失、任务
  标识无效（0、负数、非数字）、越界（9223372036854775808）或不存在，
  均退出码 2、stdout 为空、stderr 说明原因且已有数据不变；标识允许前导
  零（0001 与 1 等价）。
- 省略 --from 时保留现有行为：直接改名、允许重名与同标题提交。
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

# 验收场景：任务 1 原标题（中文 + 单空格 + 大写英文），任务 2 同标题作对照
OLD_TITLE = "设计 API"
NEW_TITLE = "修复 API"

# SQLite 有符号 64 位整数上限 + 1：超出支持范围
OVER_MAX = "9223372036854775808"


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

    def list_project(self):
        proc = self.run_cli("task-list", str(self.project))
        self.assertEqual(proc.returncode, 0, self.detail(
            ("task-list", str(self.project)), proc, expected="task-list 成功"))
        return json.loads(proc.stdout)

    def task_obj(self, task_id, title, status):
        return {"id": task_id, "project_id": self.project,
                "title": title, "status": status}

    def assert_rejected(self, proc, cli_args):
        """统一拒绝协议：退出码 2、stdout 为空、stderr 非空无回溯。"""
        detail = self.detail(cli_args, proc, expected="退出码 2、stdout 为空")
        self.assertEqual(proc.returncode, 2, f"应退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"失败时 stdout 应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "", f"stderr 应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr, f"不应出现回溯：\n{detail}")
        return detail

    def assert_fixture_unchanged(self):
        """夹具保持初始状态：任务 1 为“设计 API”/doing，任务 2 为“设计 API”/todo。"""
        self.assertEqual(self.show(1), self.task_obj(1, OLD_TITLE, "doing"))
        self.assertEqual(self.show(2), self.task_obj(2, OLD_TITLE, "todo"))
        self.assertEqual(
            self.list_project(),
            [self.task_obj(1, OLD_TITLE, "doing"),
             self.task_obj(2, OLD_TITLE, "todo")],
        )

    # ---------- 用户验收场景 ----------

    def test_acceptance_match_then_repeat_conflicts(self):
        # 第一次：--from 带首尾空白，处理后与已保存标题逐字相等，改名成功
        cli_args = ("task-rename", "0001", NEW_TITLE, "--from", f" {OLD_TITLE} ")
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
        self.assertIn(NEW_TITLE, proc2.stderr,
                      f"stderr 应说明已保存的当前标题：\n{detail2}")
        self.assertIn(OLD_TITLE, proc2.stderr,
                      f"stderr 应说明处理后的预期标题：\n{detail2}")

        # task-show / task-list：任务 1 仍为“修复 API”，对照任务 2 保持原值
        self.assertEqual(self.show(1), self.task_obj(1, NEW_TITLE, "doing"))
        self.assertEqual(self.show(2), self.task_obj(2, OLD_TITLE, "todo"))
        self.assertEqual(
            self.list_project(),
            [self.task_obj(1, NEW_TITLE, "doing"),
             self.task_obj(2, OLD_TITLE, "todo")],
        )

    def test_from_match_renames_only_target_task(self):
        proc = self.run_cli(
            "task-rename", "1", NEW_TITLE, "--from", OLD_TITLE)
        self.assertEqual(proc.returncode, 0, self.detail(
            ("task-rename", "1", NEW_TITLE, "--from", OLD_TITLE), proc,
            expected="预期匹配，改名成功"))
        # 只改目标任务：对照任务与任务数量、顺序均不变
        self.assertEqual(
            self.list_project(),
            [self.task_obj(1, NEW_TITLE, "doing"),
             self.task_obj(2, OLD_TITLE, "todo")],
        )

    def test_same_new_title_succeeds_when_from_matches(self):
        # 新标题处理后等于当前标题且预期匹配：成功返回原任务、不新增记录
        cli_args = ("task-rename", "1", f"  {OLD_TITLE}\t", "--from", OLD_TITLE)
        proc = self.run_cli(*cli_args)
        detail = self.detail(
            cli_args, proc,
            expected="新标题等于当前标题且预期匹配：退出码 0、返回原任务",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, OLD_TITLE, "doing"), detail)
        self.assert_fixture_unchanged()

    def test_same_new_title_rejected_when_from_mismatches(self):
        # 新标题等于当前标题，但 --from 预期不匹配：仍拒绝，数据不变
        cli_args = ("task-rename", "1", OLD_TITLE, "--from", NEW_TITLE)
        proc = self.run_cli(*cli_args)
        detail = self.assert_rejected(proc, cli_args)
        self.assertIn(OLD_TITLE, proc.stderr,
                      f"stderr 应说明已保存的当前标题：\n{detail}")
        self.assertIn(NEW_TITLE, proc.stderr,
                      f"stderr 应说明处理后的预期标题：\n{detail}")
        self.assert_fixture_unchanged()

    # ---------- 逐字相等：不作模糊匹配 ----------

    def test_from_requires_verbatim_match(self):
        # 内部空白、大小写、首尾之外的内容差异都不匹配，也不作模糊匹配
        cases = [
            "设计  API",      # 内部双空格
            "设计 api",       # 大小写不同
            "设计API",        # 缺少内部空格
            f"{OLD_TITLE}2",  # 前缀相同但更长
            OLD_TITLE[:-1],   # 截短
            "设计 API ",      # 仅尾部空白差异（处理后相等，应成功）——单独验证
        ]
        for expected in cases[:-1]:
            with self.subTest(expected=expected):
                cli_args = ("task-rename", "1", NEW_TITLE, "--from", expected)
                proc = self.run_cli(*cli_args)
                detail = self.assert_rejected(proc, cli_args)
                self.assertIn(OLD_TITLE, proc.stderr,
                              f"stderr 应说明已保存的当前标题：\n{detail}")
        # 仅首尾空白差异在处理后相等：成功
        proc = self.run_cli("task-rename", "1", NEW_TITLE,
                            "--from", cases[-1])
        self.assertEqual(proc.returncode, 0, self.detail(
            ("task-rename", "1", NEW_TITLE, "--from", cases[-1]), proc,
            expected="仅首尾空白差异处理后相等，改名成功"))
        self.assertEqual(self.show(1), self.task_obj(1, NEW_TITLE, "doing"))

    def test_from_preserves_special_characters_verbatim(self):
        # 标题含中文、%、_、引号与内部空白：--from 逐字匹配后改名成功
        fancy = "  修复  API_100%'  "
        saved = "修复  API_100%'"
        proc = self.run_cli("task-rename", "1", fancy)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout)["title"], saved)
        # 逐字提供含特殊字符的预期标题（外加首尾空白），匹配成功
        proc = self.run_cli("task-rename", "0001", NEW_TITLE,
                            "--from", f"\t{saved} ")
        detail = self.detail(
            ("task-rename", "0001", NEW_TITLE, "--from", f"\t{saved} "), proc,
            expected="特殊字符逐字匹配，改名成功",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, NEW_TITLE, "doing"), detail)
        # 特殊字符有任何差异（如去掉单引号）则不匹配
        proc = self.run_cli("task-rename", "1", OLD_TITLE,
                            "--from", "修复  API_100%")
        self.assert_rejected(
            proc, ("task-rename", "1", OLD_TITLE, "--from", "修复  API_100%"))
        self.assertEqual(self.show(1), self.task_obj(1, NEW_TITLE, "doing"))

    # ---------- 拒绝路径：空标题 / 缺值 / 缺参 / 标识非法 ----------

    def test_empty_from_or_new_title_rejected(self):
        cases = [
            ("task-rename", "1", NEW_TITLE, "--from", ""),
            ("task-rename", "1", NEW_TITLE, "--from", "   "),
            ("task-rename", "1", NEW_TITLE, "--from", "\t\n "),
            ("task-rename", "1", "", "--from", OLD_TITLE),
            ("task-rename", "1", "   ", "--from", OLD_TITLE),
        ]
        for cli_args in cases:
            with self.subTest(cli_args=list(cli_args)):
                proc = self.run_cli(*cli_args)
                detail = self.assert_rejected(proc, cli_args)
                self.assertIn("title", proc.stderr.lower(),
                              f"stderr 应说明标题问题：\n{detail}")
        self.assert_fixture_unchanged()

    def test_from_missing_value_rejected(self):
        proc = self.run_cli("task-rename", "1", NEW_TITLE, "--from")
        self.assert_rejected(proc, ("task-rename", "1", NEW_TITLE, "--from"))
        self.assert_fixture_unchanged()

    def test_missing_required_arguments_rejected(self):
        for cli_args in (
            ("task-rename",),
            ("task-rename", "1"),
            ("task-rename", "--from", OLD_TITLE),
        ):
            with self.subTest(cli_args=list(cli_args)):
                proc = self.run_cli(*cli_args)
                self.assert_rejected(proc, cli_args)
        self.assert_fixture_unchanged()

    def test_invalid_or_missing_task_id_rejected(self):
        cases = [
            ("task-rename", "0", NEW_TITLE, "--from", OLD_TITLE),
            ("task-rename", "-1", NEW_TITLE, "--from", OLD_TITLE),
            ("task-rename", "abc", NEW_TITLE, "--from", OLD_TITLE),
            ("task-rename", OVER_MAX, NEW_TITLE, "--from", OLD_TITLE),
            ("task-rename", "999", NEW_TITLE, "--from", OLD_TITLE),
        ]
        for cli_args in cases:
            with self.subTest(cli_args=list(cli_args)):
                proc = self.run_cli(*cli_args)
                self.assert_rejected(proc, cli_args)
        self.assert_fixture_unchanged()

    def test_from_mismatch_does_not_create_or_modify_tasks(self):
        before = self.list_project()
        proc = self.run_cli("task-rename", "2", NEW_TITLE, "--from", NEW_TITLE)
        self.assert_rejected(
            proc, ("task-rename", "2", NEW_TITLE, "--from", NEW_TITLE))
        self.assertEqual(self.list_project(), before)
        self.assert_fixture_unchanged()

    # ---------- 省略 --from 时保留现有行为 ----------

    def test_without_from_existing_behavior_unchanged(self):
        # 直接改名成功，允许重名与同标题重复提交
        proc = self.run_cli("task-rename", "1", NEW_TITLE)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, NEW_TITLE, "doing"))
        # 改成与对照任务相同的标题：允许重名
        proc = self.run_cli("task-rename", "1", OLD_TITLE)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, OLD_TITLE, "doing"))
        # 同标题再次提交仍成功，不新增记录
        proc = self.run_cli("task-rename", "0001", f" {OLD_TITLE} ")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, OLD_TITLE, "doing"))
        self.assert_fixture_unchanged()

    # ---------- 存储失败 ----------

    def test_storage_failure_exit_code_one(self):
        cli_args = ("task-rename", "1", NEW_TITLE, "--from", OLD_TITLE)
        proc = self.run_cli(
            *cli_args,
            db_path=os.path.join(self._tmpdir.name, "missing_dir", "x.db"),
        )
        detail = self.detail(cli_args, proc,
                             expected="退出码 1、stdout 空、stderr 说明存储失败")
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)
        self.assert_fixture_unchanged()


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""project-rename --from 预期原名称的命令行回归（验收）测试。

覆盖：

- 用户验收场景：两个同名项目“研发”，前者有一条 doing 任务、后者为空；
  ``project-rename 1 "  研发 API  " --from "  研发  "`` 成功（退出码 0、
  stderr 为空、stdout 为只含 id / name 的单个项目 JSON，名称为
  ``研发 API``、标识原样），``project-list --query API`` 只返回前者，
  前者任务的标识、标题、状态、所属项目与后者空任务列表保持不变；紧接着
  重复同一请求退出码 2、stdout 为空、stderr 同时说明已保存的当前名称
  ``研发 API`` 与处理后的预期名称 ``研发``，查询结果不变。
- 新名称处理后等于当前名称时：预期匹配则成功返回原项目、不新增记录；
  预期不匹配仍拒绝。
- ``--from`` 与新名称都只去除首尾空白：内部空白、大小写、中文、百分号、
  下划线与引号原样保留，按逐字相等匹配，不做模糊匹配（大小写或内部空白
  不同即拒绝）。
- 预期原名称为空字符串或纯空白、``--from`` 缺值、必需位置参数缺失、
  新名称为空、项目标识无效/越界/不存在均退出码 2；标识允许前导零
  （``0001`` 与 ``1`` 等价）。
- 省略 ``--from`` 时保留直接改名的现有规则：允许重名与同名称提交。
- 父目录存在而数据库文件不存在时沿用自动建库行为创建空库，随后因项目
  不存在退出码 2，库中不产生任何项目或任务。
- 数据库无法打开时退出码 1、stdout 为空、stderr 说明存储失败。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，不直接读写
数据库（除一处只读连接核对自动建库后的空库），不调用产品内部函数，不依赖
网络或任何预先保存的项目。每个用例在独立临时目录中使用全新的隔离 SQLite
数据库，结束后自动清理。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_rename_from.py
    python3 -m unittest test_project_rename_from -v
    python3 test_project_rename_from.py \
        ProjectRenameFromRegression.test_acceptance_match_then_repeat_conflicts

退出码：全部通过为 0，存在失败为 1。不依赖 JSON 键顺序或错误文案逐字一致。
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

# 两个项目共用的初始名称（用户验收场景）
OLD_NAME = "研发"
NEW_NAME = "研发 API"

# 含内部空白、大小写、中文、%、_、单引号的名称，用于核对逐字匹配
LITERAL_NAME = "升级  API_100%'"

# SQLite 有符号 64 位整数上限 + 1：超出支持范围
OVER_MAX = "9223372036854775808"


class ProjectRenameFromRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-project-rename-from-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")
        # 两个同名项目“研发”：前者有一条 doing 任务，后者无任务
        self.project_alpha = self.cli_json("project-create", OLD_NAME)["id"]
        self.project_beta = self.cli_json("project-create", OLD_NAME)["id"]
        self.assertEqual(self.project_alpha, 1)
        self.assertEqual(self.project_beta, 2)
        task = self.cli_json("task-create", str(self.project_alpha), "联调设计文档")
        self.task_id = task["id"]
        moved = self.cli_json("task-move", str(self.task_id), "doing")
        self.assertEqual(moved["status"], "doing")

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

    def list_projects(self, *extra_args):
        proc = self.run_cli("project-list", *extra_args)
        self.assertEqual(proc.returncode, 0, self.detail(
            ("project-list", *extra_args), proc, expected="project-list 成功"))
        self.assertEqual(proc.stderr, "")
        return json.loads(proc.stdout)

    def list_tasks(self, project_id):
        proc = self.run_cli("task-list", str(project_id))
        self.assertEqual(proc.returncode, 0, self.detail(
            ("task-list", str(project_id)), proc, expected="task-list 成功"))
        return json.loads(proc.stdout)

    def alpha_task(self):
        return {
            "id": self.task_id,
            "project_id": self.project_alpha,
            "title": "联调设计文档",
            "status": "doing",
        }

    def snapshot(self):
        """项目列表与两个项目的当前任务列表，用于前后比对。"""
        return {
            "projects": self.list_projects(),
            self.project_alpha: self.list_tasks(self.project_alpha),
            self.project_beta: self.list_tasks(self.project_beta),
        }

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
        # 第一次：前者已保存名称为“研发”，与 --from（首尾带空白）
        # 处理后的预期逐字相同，改名为“研发 API”
        cli_args = ("project-rename", str(self.project_alpha),
                    f"  {NEW_NAME}  ", "--from", f"  {OLD_NAME}  ")
        proc = self.run_cli(*cli_args)
        detail = self.detail(
            cli_args, proc,
            expected="退出码 0、stderr 空、stdout 为名称“研发 API”的前者对象",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        renamed = json.loads(proc.stdout)
        self.assertEqual(renamed,
                         {"id": self.project_alpha, "name": NEW_NAME}, detail)
        self.assertEqual(set(renamed), PROJECT_FIELDS, detail)

        # 独立命令进程 project-list --query API 只返回前者
        self.assertEqual(
            self.list_projects("--query", "API"),
            [{"id": self.project_alpha, "name": NEW_NAME}], detail,
        )
        # 任务内容与归属保持原样：前者仍是那一条 doing 任务，后者仍无任务
        self.assertEqual(self.list_tasks(self.project_alpha), [self.alpha_task()])
        self.assertEqual(self.list_tasks(self.project_beta), [])

        # 紧接着重复同一请求：当前已是“研发 API”，与预期“研发”不符，拒绝
        proc2 = self.run_cli(*cli_args)
        detail2 = self.assert_rejected(proc2, cli_args)
        # stderr 同时说明已保存的当前名称与处理后的预期名称
        self.assertIn(NEW_NAME, proc2.stderr,
                      f"stderr 应说明当前名称为 {NEW_NAME!r}：\n{detail2}")
        self.assertIn(OLD_NAME, proc2.stderr,
                      f"stderr 应说明预期名称为 {OLD_NAME!r}：\n{detail2}")

        # 查询结果不变：按 API 筛选仍只有前者，全量列表中后者名称不变
        self.assertEqual(
            self.list_projects("--query", "API"),
            [{"id": self.project_alpha, "name": NEW_NAME}], detail2,
        )
        self.assertEqual(
            self.list_projects(),
            [
                {"id": self.project_alpha, "name": NEW_NAME},
                {"id": self.project_beta, "name": OLD_NAME},
            ],
            detail2,
        )
        # 任务数据仍保持原样
        self.assertEqual(self.list_tasks(self.project_alpha), [self.alpha_task()])
        self.assertEqual(self.list_tasks(self.project_beta), [])

    def test_leading_zero_id_equivalent_with_from(self):
        # 0001 与 1 等价：同样先成功、重复时被拒绝
        cli_args = ("project-rename", "0001",
                    f"  {NEW_NAME}  ", "--from", OLD_NAME)
        proc = self.run_cli(*cli_args)
        detail = self.detail(
            cli_args, proc, expected="0001 按数值 1 处理，改名成功")
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(json.loads(proc.stdout),
                         {"id": self.project_alpha, "name": NEW_NAME})
        proc2 = self.run_cli(*cli_args)
        self.assert_rejected(proc2, cli_args)
        self.assertEqual(
            self.list_projects("--query", "API"),
            [{"id": self.project_alpha, "name": NEW_NAME}],
        )

    def test_same_name_match_is_noop_success(self):
        # 新名称等于当前名称且预期匹配：成功返回原项目，不新增记录
        before = self.snapshot()
        cli_args = ("project-rename", str(self.project_alpha),
                    f"  {OLD_NAME}  ", "--from", f"\t{OLD_NAME}\t")
        proc = self.run_cli(*cli_args)
        detail = self.detail(
            cli_args, proc,
            expected="预期相符且新名称等于当前：退出码 0、返回原项目、不新增记录",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(json.loads(proc.stdout),
                         {"id": self.project_alpha, "name": OLD_NAME})
        self.assertEqual(self.snapshot(), before)

    def test_same_name_still_rejected_when_from_mismatches(self):
        # 新名称等于当前名称，但 --from 预期不匹配：仍拒绝
        cli_args = ("project-rename", str(self.project_alpha),
                    OLD_NAME, "--from", NEW_NAME)
        proc = self.run_cli(*cli_args)
        detail = self.assert_rejected(proc, cli_args)
        self.assertIn(OLD_NAME, proc.stderr,
                      f"stderr 应说明当前名称为 {OLD_NAME!r}：\n{detail}")
        self.assertIn(NEW_NAME, proc.stderr,
                      f"stderr 应说明预期名称为 {NEW_NAME!r}：\n{detail}")
        # 名称不变，没有新增记录；两个项目与任务列表完全一致
        self.assertEqual(self.snapshot(), {
            "projects": [
                {"id": self.project_alpha, "name": OLD_NAME},
                {"id": self.project_beta, "name": OLD_NAME},
            ],
            self.project_alpha: [self.alpha_task()],
            self.project_beta: [],
        })

    # ---------- 逐字匹配：首尾空白去除，其余原样 ----------

    def test_from_surrounding_whitespace_is_stripped(self):
        # 预期名称首尾空白被去除后与保存名称逐字相等：成功
        proc = self.run_cli(
            "project-rename", str(self.project_alpha),
            f"  {NEW_NAME}  ", "--from", f" \t{OLD_NAME}\n ")
        detail = self.detail(
            ("project-rename", "1", f"  {NEW_NAME}  ",
             "--from", f" <空白>{OLD_NAME}<空白>"),
            proc, expected="预期名称首尾空白被去除后匹配，改名成功",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(json.loads(proc.stdout),
                         {"id": self.project_alpha, "name": NEW_NAME})

    def test_from_match_is_verbatim_no_fuzzy_matching(self):
        # 内部空白不同或多出字符的预期名称都不匹配：拒绝且数据不变。
        # 注意：首尾空白会被去除，故这里只列“去空白后仍与当前名称不同、
        # 但去空白前后差异可辨”的拼写：给每个错误值在首尾再加空白，
        # 确认处理后的预期值（去空白后的错误值）也出现在 stderr 中。
        for wrong in ("研 发", "研  发", "x" + OLD_NAME, OLD_NAME + "x"):
            padded = f"  {wrong}  "
            with self.subTest(wrong=wrong):
                before = self.snapshot()
                proc = self.run_cli(
                    "project-rename", str(self.project_alpha),
                    NEW_NAME, "--from", padded)
                detail = self.assert_rejected(
                    proc,
                    ("project-rename", "1", NEW_NAME, "--from", padded))
                # stderr 同时给出已保存的当前名称与处理后的预期名称
                self.assertIn(OLD_NAME, proc.stderr,
                              f"stderr 应说明当前名称：\n{detail}")
                self.assertIn(wrong.strip(), proc.stderr,
                              f"stderr 应说明处理后的预期名称：\n{detail}")
                self.assertEqual(self.snapshot(), before, detail)

    def test_literal_characters_in_from_matched_verbatim(self):
        # 先把前者改成含内部双空格、大小写、中文、%、_、单引号的名称，
        # 再用逐字相等的 --from 改名成功；任何一个字符不同即拒绝
        proc = self.run_cli(
            "project-rename", str(self.project_alpha), f"  {LITERAL_NAME}  ")
        self.assertEqual(proc.returncode, 0, self.detail(
            ("project-rename", "1", f"  {LITERAL_NAME}  "), proc,
            expected="准备前者为含特殊字符的名称"))
        self.assertEqual(
            self.list_projects(),
            [
                {"id": self.project_alpha, "name": LITERAL_NAME},
                {"id": self.project_beta, "name": OLD_NAME},
            ],
        )

        # 逐字相等（首尾再加空白）：成功
        cli_args = ("project-rename", str(self.project_alpha),
                    NEW_NAME, "--from", f" {LITERAL_NAME} ")
        proc = self.run_cli(*cli_args)
        detail = self.detail(
            cli_args, proc, expected="特殊字符逐字相等，改名成功")
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(json.loads(proc.stdout),
                         {"id": self.project_alpha, "name": NEW_NAME})

        # 把百分号改成下划线等任何差异都不匹配
        wrong = LITERAL_NAME.replace("%", "_")
        proc = self.run_cli(
            "project-rename", str(self.project_alpha),
            LITERAL_NAME, "--from", wrong)
        self.assert_rejected(
            proc,
            ("project-rename", "1", LITERAL_NAME, "--from", wrong))
        self.assertEqual(
            self.list_projects(),
            [
                {"id": self.project_alpha, "name": NEW_NAME},
                {"id": self.project_beta, "name": OLD_NAME},
            ],
        )

    # ---------- 非法参数 ----------

    def assert_usage_failure_leaves_data(self, cli_args, reason=None):
        before = self.snapshot()
        proc = self.run_cli(*cli_args)
        detail = self.assert_rejected(proc, cli_args)
        if reason is not None:
            self.assertIn(reason, proc.stderr.lower(),
                          f"stderr 应说明相应原因：\n{detail}")
        self.assertEqual(self.snapshot(), before,
                         f"被拒绝的请求不得改动数据：\n{detail}")

    def test_empty_and_whitespace_from_rejected(self):
        for bad in ("", "   ", "\t", "\n \t "):
            with self.subTest(bad=bad):
                self.assert_usage_failure_leaves_data(
                    ("project-rename", str(self.project_alpha),
                     NEW_NAME, "--from", bad),
                    reason="name",
                )

    def test_empty_new_name_with_from_rejected(self):
        # 即使预期匹配，新名称去除首尾空白后为空也拒绝
        for bad in ("", "   ", "\t"):
            with self.subTest(bad=bad):
                self.assert_usage_failure_leaves_data(
                    ("project-rename", str(self.project_alpha),
                     bad, "--from", OLD_NAME),
                    reason="name",
                )

    def test_from_missing_value(self):
        self.assert_usage_failure_leaves_data(
            ("project-rename", str(self.project_alpha), NEW_NAME, "--from"))

    def test_missing_required_arguments(self):
        for cli_args in (
            ("project-rename",),
            ("project-rename", str(self.project_alpha)),
            ("project-rename", str(self.project_alpha), "--from", OLD_NAME),
        ):
            with self.subTest(cli_args=cli_args):
                self.assert_usage_failure_leaves_data(cli_args)

    def test_invalid_or_missing_project_id_with_from(self):
        for cli_args in (
            ("project-rename", "abc", NEW_NAME, "--from", OLD_NAME),
            ("project-rename", "0", NEW_NAME, "--from", OLD_NAME),
            ("project-rename", "-3", NEW_NAME, "--from", OLD_NAME),
            ("project-rename", OVER_MAX, NEW_NAME, "--from", OLD_NAME),
            ("project-rename", "999", NEW_NAME, "--from", OLD_NAME),
        ):
            with self.subTest(cli_args=cli_args):
                self.assert_usage_failure_leaves_data(cli_args)
        self.assertEqual(
            self.list_projects(),
            [
                {"id": self.project_alpha, "name": OLD_NAME},
                {"id": self.project_beta, "name": OLD_NAME},
            ],
        )
        self.assertEqual(self.list_tasks(self.project_alpha), [self.alpha_task()])
        self.assertEqual(self.list_tasks(self.project_beta), [])

    # ---------- 省略 --from 时保留现有行为 ----------

    def test_without_from_renames_directly(self):
        # 直接改名：允许与同名项目重名
        proc = self.run_cli(
            "project-rename", str(self.project_alpha), f"  {NEW_NAME}  ")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(json.loads(proc.stdout),
                         {"id": self.project_alpha, "name": NEW_NAME})

        # 改成后者的名称也成功（允许重名）
        proc = self.run_cli(
            "project-rename", "0001", f"  {OLD_NAME}  ")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(json.loads(proc.stdout),
                         {"id": self.project_alpha, "name": OLD_NAME})

        # 同名称再次提交仍成功、不新增记录
        before = self.snapshot()
        proc = self.run_cli("project-rename", "0001", OLD_NAME)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout),
                         {"id": self.project_alpha, "name": OLD_NAME})
        self.assertEqual(self.snapshot(), before)

    # ---------- 数据库初始化与存储失败 ----------

    def test_missing_database_file_is_created_then_project_not_found(self):
        # 父目录存在而数据库文件不存在：沿用自动建库行为，随后因项目
        # 不存在退出码 2，库中不产生任何项目或任务（带 --from 同样如此）
        fresh_path = os.path.join(self._tmpdir.name, "fresh.db")
        cli_args = ("project-rename", "1", NEW_NAME, "--from", OLD_NAME)
        proc = self.run_cli(*cli_args, db_path=fresh_path)
        detail = self.detail(
            cli_args, proc,
            expected="退出码 2、stdout 为空、stderr 说明项目不存在，自动建空库",
        )
        self.assertEqual(proc.returncode, 2, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("does not exist", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)

        conn = sqlite3.connect(fresh_path)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 0)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        finally:
            conn.close()

        empty_proc = self.run_cli("project-list", db_path=fresh_path)
        self.assertEqual(empty_proc.returncode, 0)
        self.assertEqual(json.loads(empty_proc.stdout), [])

    def test_storage_failure_exit_code_one(self):
        proc = self.run_cli(
            "project-rename", str(self.project_alpha),
            NEW_NAME, "--from", OLD_NAME,
            db_path=os.path.join(self._tmpdir.name, "missing_dir", "x.db"),
        )
        cli_args = ("project-rename", "1", NEW_NAME, "--from", OLD_NAME)
        detail = self.detail(cli_args, proc,
                             expected="退出码 1、stdout 空、stderr 说明存储失败")
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""project-rename 项目改名的命令行回归（验收）测试。

覆盖成功路径：

- 用户指定的验收场景：两个同名项目“研发”，前者有一条 doing 任务、后者无
  任务；只把前者改名为首尾带空白的 “  研发 API  ” 后，用独立命令进程
  project-list --query API 只返回前者；两个项目的任务内容与数量保持不变；
- 返回对象只含 id / name：id 为原标识（带前导零的同值标识等价），name 为
  去除首尾空白后的保存名称，内部双空格、大小写、中文、百分号、下划线与
  单引号原样保留；结果已落库，另一个命令进程的 project-list 能读到新名称；
- 改名只影响指定项目：其他项目完整对象与按标识升序的顺序不变；该项目下
  任务的标识、标题、状态、所属项目与任务数量完全不变；不创建项目、不合并
  同名项目；
- 再次提交仅首尾空白不同的同一名称仍成功，返回同一项目且不新增记录；
- 改成另一个项目的名称也成功（允许重名，同名项目各自保留）。

覆盖拒绝路径（退出码 2、标准输出为空、标准错误说明原因、无 Python
回溯，项目列表与两个项目的任务完整列表保持不变）：

- 新名称为空字符串或纯空白；
- 缺少必要参数；
- 项目标识为 0、负数、非数字、9223372036854775808（超出 SQLite 有符号
  64 位整数上限）或范围内不存在的标识；
- 父目录存在而数据库文件不存在时沿用自动建库行为创建空库，随后因项目
  不存在退出码 2，库中不产生任何项目或任务；
- 数据库路径指向已有目录：退出码 1、标准输出为空、标准错误说明存储失败。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品：用
project-create / task-create / task-move 准备数据，用 project-rename 改名，
用 project-list / task-list 查询验证结果已落库。不直接读写数据库（仅一处
只读连接核对自动建库后的空库），不调用产品内部函数，不依赖网络，也不依赖
任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用
者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_rename.py                    # 运行全部回归用例
    python3 -m unittest test_project_rename -v        # 等价写法
    python3 -m unittest discover -p 'test_project_rename.py' -v
    python3 test_project_rename.py \
        ProjectRenameRegression.test_acceptance_two_same_named_projects

退出码：全部通过为 0，存在断言失败为非零。失败信息会给出输入参数、实际
退出码、标准输出、标准错误以及预期结果，不依赖 JSON 键顺序或错误文案的
逐字拼写。
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

# 两个项目共用的初始名称，用于确认同名项目不会被连带改动或合并
SHARED_NAME = "研发"

# 验收场景的新名称：首尾各两个空格，内部为中文 + 空格 + 大写英文
API_NAME_RAW = "  研发 API  "
API_NAME = "研发 API"

# 改名输入：首尾各两个空格，内部为中文 + 双空格 + 大小写英文 + % + _ + 单引号
NEW_NAME_RAW = "  升级  API_100%'  "
NEW_NAME = "升级  API_100%'"

# SQLite 有符号 64 位整数上限 + 1：超出支持范围
OVER_MAX = "9223372036854775808"


class ProjectRenameRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-project-rename-")
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
        """组装失败定位信息：输入参数、退出码与输出、预期结果。"""
        return "\n".join(
            [
                f"输入参数={list(cli_args)!r}",
                f"实际退出码={proc.returncode}",
                f"实际标准输出={proc.stdout!r}",
                f"实际标准错误={proc.stderr!r}",
                f"预期结果={expected}",
            ]
        )

    def list_projects(self, *extra_args):
        """通过公开命令查询项目列表（可附加 --query），返回对象列表。"""
        cli_args = ("project-list", *extra_args)
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
        """通过公开命令查询项目全部任务，返回对象列表。"""
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
        for task in data:
            self.assertEqual(set(task.keys()), TASK_FIELDS, detail)
        return data

    def snapshot(self):
        """记录项目列表与两个项目的当前任务列表，用于前后比对。"""
        return {
            "projects": self.list_projects(),
            self.project_alpha: self.list_tasks(self.project_alpha),
            self.project_beta: self.list_tasks(self.project_beta),
        }

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

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 两个同名项目“研发”：前者有一条 doing 任务，后者无任务
        self.project_alpha = self.cli_json("project-create", SHARED_NAME)["id"]
        self.project_beta = self.cli_json("project-create", SHARED_NAME)["id"]

        task = self.cli_json(
            "task-create", str(self.project_alpha), "联调设计文档"
        )
        self.task_id = task["id"]
        moved = self.cli_json("task-move", str(self.task_id), "doing")
        self.assertEqual(moved["status"], "doing")

    def alpha_task(self):
        return {
            "id": self.task_id,
            "project_id": self.project_alpha,
            "title": "联调设计文档",
            "status": "doing",
        }

    def rename(self, raw_name, raw_id=None):
        """对前者执行 project-rename；默认用带前导零的同值项目标识。"""
        if raw_id is None:
            raw_id = f"000{self.project_alpha}"
        return self.run_cli("project-rename", raw_id, raw_name)

    def assert_rename_success(self, raw_name, expected_name, raw_id=None):
        """改名成功的通用断言，返回（结果对象, 失败明细）。"""
        cli_args = (
            "project-rename",
            raw_id if raw_id is not None else f"000{self.project_alpha}",
            raw_name,
        )
        proc = self.run_cli(*cli_args)
        expected_obj = {"id": self.project_alpha, "name": expected_name}
        obj, detail = self.parse_single_json_object(
            proc, cli_args,
            expected=(
                "退出码 0、stderr 为空、stdout 为单个 JSON 项目对象，"
                f"对象为 {expected_obj!r}（字段集合 {sorted(PROJECT_FIELDS)}）"
            ),
        )
        self.assertEqual(proc.returncode, 0, f"改名应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功改名时标准错误应为空：\n{detail}")
        self.assertEqual(
            set(obj.keys()), PROJECT_FIELDS,
            f"改名结果对象字段结构不符：\n{detail}",
        )
        self.assertEqual(obj, expected_obj, f"改名结果对象内容不符：\n{detail}")
        return obj, detail

    # ---------- 成功路径 ----------

    def test_acceptance_two_same_named_projects(self):
        # 用户指定的验收场景：两个“研发”，前者有 doing 任务、后者无任务；
        # 只把前者改为首尾带空白的“研发 API”
        obj, detail = self.assert_rename_success(API_NAME_RAW, API_NAME)

        # 另一个命令进程 project-list --query API 只返回前者
        found = self.list_projects("--query", "API")
        self.assertEqual(
            found, [{"id": self.project_alpha, "name": API_NAME}],
            f"按 API 筛选应只返回改名后的前者：\n实际={found!r}\n{detail}",
        )
        # 全量列表仍按标识升序，后者名称不变，前者名称为保存值
        self.assertEqual(
            self.list_projects(),
            [
                {"id": self.project_alpha, "name": API_NAME},
                {"id": self.project_beta, "name": SHARED_NAME},
            ],
            detail,
        )
        # 任务内容与数量保持不变：前者仍是那一条 doing 任务，后者仍无任务
        self.assertEqual(
            self.list_tasks(self.project_alpha), [self.alpha_task()], detail
        )
        self.assertEqual(self.list_tasks(self.project_beta), [], detail)
        # 按旧名称“研发”筛选：两者名称都仍包含“研发”，故都命中、按 id 升序
        self.assertEqual(
            [p["id"] for p in self.list_projects("--query", SHARED_NAME)],
            [self.project_alpha, self.project_beta],
            "旧名称子串仍应同时命中两个项目",
        )

    def test_rename_persists_saved_name(self):
        # 带前导零的同值标识改名；返回对象即保存后的值
        obj, detail = self.assert_rename_success(NEW_NAME_RAW, NEW_NAME)

        # 另起一次命令行调用查询：结果已落库
        projects = self.list_projects()
        persisted = [p for p in projects if p["id"] == self.project_alpha]
        self.assertEqual(len(persisted), 1, detail)
        self.assertEqual(
            persisted[0], obj,
            f"再次查询到的项目应与改名结果完全一致（结果已保存）：\n"
            f"{detail}\n查询所得={persisted[0]!r}",
        )
        # 保存值逐项核对：内部双空格、大小写、中文、%、_、单引号原样保留
        saved_name = persisted[0]["name"]
        self.assertEqual(saved_name, NEW_NAME, detail)
        for piece in ("升级", "  ", "API", "_", "100%", "'"):
            self.assertIn(piece, saved_name, detail)
        self.assertNotEqual(saved_name, NEW_NAME_RAW, detail)  # 首尾空白已去除

    def test_rename_only_affects_target_project_and_its_tasks_unchanged(self):
        before = self.snapshot()
        self.assert_rename_success(NEW_NAME_RAW, NEW_NAME)
        after = self.snapshot()

        # 只有前者的名称变化，后者的完整对象不变，项目数量与标识排序不变
        expected_projects = [
            {"id": p["id"],
             "name": NEW_NAME if p["id"] == self.project_alpha else p["name"]}
            for p in before["projects"]
        ]
        self.assertEqual(after["projects"], expected_projects)
        self.assertEqual(
            [p["id"] for p in after["projects"]],
            [p["id"] for p in before["projects"]],
        )
        self.assertEqual(len(after["projects"]), len(before["projects"]))
        # 两个项目的任务完整列表（标识、标题、状态、所属项目）完全不变
        self.assertEqual(
            after[self.project_alpha], before[self.project_alpha],
            "改名项目的任务不得改变",
        )
        self.assertEqual(
            after[self.project_beta], before[self.project_beta],
            "另一项目的任务不得受改名影响",
        )

    def test_resubmit_same_name_with_surrounding_whitespace_is_idempotent(self):
        first, detail = self.assert_rename_success(NEW_NAME_RAW, NEW_NAME)
        between = self.snapshot()

        # 仅首尾空白不同（三个前导空格 + 一个尾部制表符）的同一保存名称
        repadded = "   " + NEW_NAME + "\t"
        cli_args = ("project-rename", str(self.project_alpha), repadded)
        proc = self.run_cli(*cli_args)
        again, again_detail = self.parse_single_json_object(
            proc, cli_args,
            expected=f"退出码 0、stderr 为空，返回同一个项目对象 {first!r}，且不新增记录",
        )
        self.assertEqual(proc.returncode, 0, f"同名称再次提交仍应成功：\n{again_detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{again_detail}")
        self.assertEqual(set(again.keys()), PROJECT_FIELDS, again_detail)
        self.assertEqual(
            again, first,
            f"再次提交应返回同一个项目对象：\n首次={first!r}，\n再次={again!r}",
        )

        # 项目与任务列表（数量、标识、对象）与再次提交之前完全一致
        self.assertEqual(
            self.snapshot(), between,
            f"同名称再次提交不得新增或改动任何记录：\n{detail}",
        )

    def test_rename_to_another_projects_name_is_allowed(self):
        # 先改成新名称制造区分，再改成后者的名称“研发”（允许重名、不合并）
        self.assert_rename_success(NEW_NAME_RAW, NEW_NAME)
        obj, detail = self.assert_rename_success(
            SHARED_NAME, SHARED_NAME, raw_id=str(self.project_alpha)
        )
        self.assertEqual(obj["id"], self.project_alpha, detail)

        # 允许重名：两个项目同名，标识不同、数量不变、按标识升序各自保留
        self.assertEqual(
            self.list_projects(),
            [
                {"id": self.project_alpha, "name": SHARED_NAME},
                {"id": self.project_beta, "name": SHARED_NAME},
            ],
            f"重名应被允许且两个项目各自保留：\n{detail}",
        )
        # 任务归属与内容不变
        self.assertEqual(
            self.list_tasks(self.project_alpha), [self.alpha_task()], detail
        )
        self.assertEqual(self.list_tasks(self.project_beta), [], detail)

    # ---------- 拒绝路径：通用断言 ----------

    def assert_rejected(self, cli_args, reason=None, before=None):
        """断言请求被拒绝：退出码 2、stdout 为空、stderr 有说明且无回溯，
        并且失败前后项目列表与两个项目的任务完整列表完全一致。"""
        if before is None:
            before = self.snapshot()
        proc = self.run_cli(*cli_args)
        after = self.snapshot()
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 2、stdout 为空、stderr 说明拒绝原因"
                + (f"（应包含 {reason!r}）" if reason else "")
                + "，项目与任务列表保持不变"
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
            f"被拒绝的请求不得改动任何项目或任务：\n{detail}\n"
            f"失败前={before!r}\n失败后={after!r}",
        )
        return proc

    # ---------- 空 / 纯空白名称 ----------

    def test_empty_and_whitespace_names_rejected(self):
        before = self.snapshot()
        for raw_name in ("", "   ", "\t", "\n \t "):
            with self.subTest(raw_name=raw_name):
                self.assert_rejected(
                    ("project-rename", f"000{self.project_alpha}", raw_name),
                    reason="name",
                    before=before,
                )

    # ---------- 缺少必要参数 ----------

    def test_missing_arguments_rejected(self):
        before = self.snapshot()
        for cli_args in (
            ("project-rename",),
            ("project-rename", f"000{self.project_alpha}"),
        ):
            with self.subTest(cli_args=list(cli_args)):
                # argparse 缺参同样是退出码 2、stdout 为空、数据不变
                proc = self.run_cli(*cli_args)
                after = self.snapshot()
                detail = self._detail(
                    cli_args, proc,
                    expected="退出码 2、stdout 为空、stderr 提示缺少参数、数据不变",
                )
                self.assertEqual(proc.returncode, 2, detail)
                self.assertEqual(proc.stdout, "", detail)
                self.assertNotEqual(proc.stderr.strip(), "", detail)
                self.assertNotIn("Traceback", proc.stderr, detail)
                self.assertEqual(after, before, detail)

    # ---------- 非法项目标识 ----------

    def test_invalid_project_identifiers_rejected(self):
        before = self.snapshot()
        # (标识输入, stderr 应包含的原因片段)
        cases = [
            ("0", "positive integer"),
            ("-1", "positive integer"),
            ("abc", "positive integer"),
            (OVER_MAX, "range"),
            ("999", "does not exist"),       # 范围内但不存在
        ]
        for raw_id, reason in cases:
            with self.subTest(raw_id=raw_id):
                self.assert_rejected(
                    ("project-rename", raw_id, "should not be saved"),
                    reason=reason,
                    before=before,
                )

    # ---------- 自动建空库后项目不存在 ----------

    def test_missing_database_file_is_created_then_project_not_found(self):
        fresh_path = os.path.join(self._tmpdir.name, "fresh.db")
        cli_args = ("project-rename", "1", "不应保存")
        proc = self.run_cli(*cli_args, db_path=fresh_path)
        detail = self._detail(
            cli_args, proc,
            expected="退出码 2、stdout 为空、stderr 说明项目不存在，自动建空库",
        )
        self.assertEqual(proc.returncode, 2, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("does not exist", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)

        # 库被创建但不含任何项目或任务
        conn = sqlite3.connect(fresh_path)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 0
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0
            )
        finally:
            conn.close()

        # 独立进程查询空库：项目列表为空
        empty_proc = self.run_cli("project-list", db_path=fresh_path)
        self.assertEqual(empty_proc.returncode, 0)
        self.assertEqual(json.loads(empty_proc.stdout), [])

    # ---------- 存储失败：退出码 1 ----------

    def test_database_path_being_a_directory_is_storage_failure(self):
        # 以一个已存在的目录作为数据库文件：SQLite 无法打开，应退出码 1，
        # stdout 为空、stderr 说明存储失败，且真正的临时库数据不发生变化
        dir_path = os.path.join(self._tmpdir.name, "cannot_be_a_db")
        os.mkdir(dir_path)
        before = self.snapshot()

        cli_args = ("project-rename", str(self.project_alpha), "should not be saved")
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
        self.assertEqual(
            self.snapshot(), before,
            f"存储失败的请求不得改动既有数据库：\n{detail}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

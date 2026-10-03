#!/usr/bin/env python3
"""task-show 按标识读取单条任务的命令行回归（验收）测试。

覆盖用户指定的验收场景：

- 在两个项目中各准备一条标题为 “整理 API” 的任务，将第二条移到 doing、
  第一条保持 todo；用第二条任务标识及其前导零写法（000x、长前导零）查询，
  都只返回第二条任务，所属项目与 doing 状态准确；
- task-show 的对象与 task-list 中该任务的对象完全一致；
- 目标任务经 task-rename 改名为 “修复 API” 后，task-show 返回新标题及
  原有标识、所属项目与状态。

覆盖成功路径约定：

- 退出码 0、标准错误为空、标准输出只有一个 JSON 对象（不是数组、无多余
  内容），对象只含 id / project_id / title / status 四个字段，类型与其他
  任务命令一致（id、project_id 为整数，title、status 为字符串）；
- 标题按保存值原样返回：内部空白、中文、大小写、%、_ 与引号均不改变；
- 同标题任务按各自跨项目唯一的标识区分；
- 只读：成功查询与业务失败前后 project-list 与两个项目的 task-list
  完整快照不变，重复查询结果一致。

覆盖标识规则（沿用现有正整数规则）：

- 允许前导零并按数值判断，0001 与 1 等价，前导五千个零也不影响有效值；
- 零、全零、负数、非数字、小数/科学计数法、带正号或首尾空白、全角数字
  均为标识无效；超过 9223372036854775807（含带前导零的同值写法、五千个
  连续的 9）为越界；上限本身与范围内不存在的标识为任务不存在；三类失败
  均退出码 2、标准输出为空、标准错误分别说明标识无效、越界或任务不存在，
  且不出现 Python 回溯；
- 缺少任务标识、缺少 --db 或其路径值同样退出码 2。

覆盖存储协议：

- --db 指向已有目录、父目录不存在、文件存在但不是可用的 SQLite 数据库时
  退出码 1、标准输出为空、标准错误说明存储失败、无回溯；
- 父目录存在而数据库文件不存在时沿用自动建库行为，随后按任务不存在
  返回 2；新建的库中不含任何项目或任务（只读连接核对）。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅
使用 project-create / task-create / task-move / task-rename 四个现有
命令；除一处只读连接核对自动建库后的空库外，不直接读写数据库，不调用
产品内部函数，不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用
者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_show.py                       # 运行全部回归用例
    python3 -m unittest test_task_show -v           # 等价写法
    python3 test_task_show.py \
        TaskShowRegression.test_acceptance_two_same_titled_tasks  # 单个用例

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

TASK_FIELDS = {"id", "project_id", "title", "status"}

# 两个项目各准备一条的同标题任务
SHARED_TITLE = "整理 API"
RENAMED_TITLE = "修复 API"

SQLITE_MAX_INT = "9223372036854775807"      # 2^63 - 1：上限值本身合法
OVER_MAX = "9223372036854775808"            # 上限 + 1：越界
FIVE_THOUSAND_NINES = "9" * 5000            # 超长越界值
OVER_MAX_WITH_ZEROS = "0" * 5000 + OVER_MAX        # 前导五千个零，数值仍越界
MAX_WITH_ZEROS = "0" * 5000 + SQLITE_MAX_INT       # 前导五千个零，数值恰为上限
FIVE_THOUSAND_ZEROS_THEN_ONE = "0" * 5000 + "1"    # 数值为 1，前导五千个零


class TaskShowRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-show-regtest-")
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
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"task-list 的 stdout 不是合法 JSON：\n{detail}")
        self.assertIsInstance(data, list, f"task-list 结果应为数组：\n{detail}")
        return data

    def snapshot(self):
        """记录全部项目与两个项目的任务完整列表，用于前后比对。"""
        return {
            "projects": self.cli_json("project-list"),
            "tasks": {
                self.project_alpha: self.list_project(self.project_alpha),
                self.project_beta: self.list_project(self.project_beta),
            },
        }

    def show(self, raw_id):
        """对给定标识执行 task-show。"""
        return self.run_cli("task-show", raw_id)

    def assert_show_object(self, proc, raw_id, expected_obj):
        """断言 task-show 成功：退出码 0、stderr 空、stdout 恰为一个四字段
        JSON 对象，且内容与 expected_obj 完全一致。返回解析后的对象。"""
        cli_args = ("task-show", raw_id)
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 0、stderr 为空、stdout 只有一个 JSON 对象 "
                f"{expected_obj!r}（字段集合 {sorted(TASK_FIELDS)}）"
            ),
        )
        self.assertEqual(proc.returncode, 0, f"查询应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功查询时标准错误应为空：\n{detail}")
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
        self.assertEqual(set(obj.keys()), TASK_FIELDS, f"对象字段结构不符：\n{detail}")
        self.assertIsInstance(obj["id"], int, f"id 应为整数：\n{detail}")
        self.assertIsInstance(obj["project_id"], int, f"project_id 应为整数：\n{detail}")
        self.assertIsInstance(obj["title"], str, f"title 应为字符串：\n{detail}")
        self.assertIsInstance(obj["status"], str, f"status 应为字符串：\n{detail}")
        self.assertEqual(obj, expected_obj, f"查询对象内容不符：\n{detail}")
        return obj

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目一：一条保持 todo 的同标题任务（第一条）
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        first = self.cli_json("task-create", str(self.project_alpha), SHARED_TITLE)
        self.first_id = first["id"]
        self.assertEqual(first["status"], "todo")

        # 项目二：一条同标题任务（第二条），随后移到 doing
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        second = self.cli_json("task-create", str(self.project_beta), SHARED_TITLE)
        self.second_id = second["id"]
        moved = self.cli_json("task-move", str(self.second_id), "doing")
        self.assertEqual(moved["status"], "doing")

    def expected_second(self, title=SHARED_TITLE, status="doing"):
        return {
            "id": self.second_id,
            "project_id": self.project_beta,
            "title": title,
            "status": status,
        }

    def expected_first(self):
        return {
            "id": self.first_id,
            "project_id": self.project_alpha,
            "title": SHARED_TITLE,
            "status": "todo",
        }

    # ---------- 用户验收场景 ----------

    def test_acceptance_two_same_titled_tasks(self):
        # 用第二条任务标识查询：只返回第二条（项目二、doing），不串入第一条
        obj = self.assert_show_object(
            self.show(str(self.second_id)),
            str(self.second_id), self.expected_second(),
        )

        # 前导零写法（000x 与前导五千个零）与原标识完全等价
        self.assert_show_object(
            self.show(f"000{self.second_id}"),
            f"000{self.second_id}", self.expected_second(),
        )
        self.assert_show_object(
            self.show("0" * 5000 + str(self.second_id)),
            "<5000 zeros>", self.expected_second(),
        )

        # 第一条仍是项目一的 todo，同样可按标识读取
        self.assert_show_object(
            self.show("0001"), "0001", self.expected_first(),
        )

        # 与 task-list 中同一任务的对象一致（第一条所在项目、第二条所在项目）
        alpha_tasks = self.list_project(self.project_alpha)
        beta_tasks = self.list_project(self.project_beta)
        self.assertEqual(
            [t for t in alpha_tasks if t["id"] == self.first_id],
            [self.expected_first()],
        )
        self.assertEqual(
            [t for t in beta_tasks if t["id"] == self.second_id],
            [obj],
            f"task-show 与 task-list 中同一任务的对象应一致：\n"
            f"show={obj!r}，task-list={beta_tasks!r}",
        )

    def test_show_after_rename_returns_new_title_with_same_identity(self):
        # 目标任务改名后，task-show 返回新标题及原有标识、项目与状态
        renamed = self.cli_json(
            "task-rename", f"000{self.second_id}", RENAMED_TITLE
        )
        self.assertEqual(renamed["id"], self.second_id)
        proc = self.show(str(self.second_id))
        obj = self.assert_show_object(
            proc, str(self.second_id),
            self.expected_second(title=RENAMED_TITLE),
        )
        # 再用前导零写法查一次，结果一致
        self.assert_show_object(
            self.show(f"000{self.second_id}"),
            f"000{self.second_id}",
            self.expected_second(title=RENAMED_TITLE),
        )
        # 第一条同标题任务不受改名影响
        self.assert_show_object(
            self.show(str(self.first_id)),
            str(self.first_id), self.expected_first(),
        )
        self.assertEqual(obj["title"], RENAMED_TITLE)

    # ---------- 标题按保存值原样返回 ----------

    def test_title_returned_exactly_as_saved(self):
        # 创建时首尾空白被去除，内部空白、中文、大小写、%、_、引号原样保存
        raw_title = "  修复  API_100%'  "
        saved_title = "修复  API_100%'"
        created = self.cli_json(
            "task-create", str(self.project_alpha), raw_title
        )
        obj = self.assert_show_object(
            self.show(str(created["id"])), str(created["id"]),
            {
                "id": created["id"],
                "project_id": self.project_alpha,
                "title": saved_title,
                "status": "todo",
            },
        )
        for piece in ("修复", "  ", "API", "_", "100%", "'"):
            self.assertIn(piece, obj["title"])
        self.assertEqual(obj["title"], created["title"])

    # ---------- 只读：成功与失败均无副作用 ----------

    def test_successful_show_changes_nothing_and_is_repeatable(self):
        before = self.snapshot()
        first = self.show(f"000{self.second_id}")
        second = self.show(f"000{self.second_id}")
        after = self.snapshot()
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.stdout, first.stdout, "重复查询结果应一致")
        self.assertEqual(after, before, "成功查询不得改动任何项目或任务")

    def test_business_failures_change_nothing(self):
        before = self.snapshot()
        for raw_id in ("0", "-1", "abc", OVER_MAX, "999", " 2", "+2", "１"):
            with self.subTest(raw_id=raw_id):
                proc = self.show(raw_id)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, "")
                self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(
            self.snapshot(), before, "业务失败不得改动任何项目或任务"
        )

    # ---------- 标识无效 / 越界 / 不存在 ----------

    def assert_show_rejected(self, raw_id, reason):
        before = self.snapshot()
        proc = self.show(raw_id)
        after = self.snapshot()
        label = raw_id if len(raw_id) <= 40 else f"<{len(raw_id)} chars>"
        detail = self._detail(
            ("task-show", label), proc,
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
        # 非正整数拼写：标识无效
        for raw_id in ("0", "0000", "0" * 5000, "-1", "-0", "abc", "1.5",
                       "1e3", "+1", " 1", "1 ", "", "１"):
            with self.subTest(raw_id=(raw_id if len(raw_id) <= 40
                                      else f"<{len(raw_id)} chars>")):
                self.assert_show_rejected(raw_id, "positive integer")

    def test_out_of_range_identifiers_rejected(self):
        # 超过 SQLite 有符号 64 位整数上限：越界（按数值判断，不按字符串长度）
        for raw_id in (OVER_MAX, FIVE_THOUSAND_NINES, OVER_MAX_WITH_ZEROS):
            with self.subTest(raw_id=(raw_id if len(raw_id) <= 40
                                      else f"<{len(raw_id)} chars>")):
                self.assert_show_rejected(raw_id, "range")

    def test_in_range_but_nonexistent_identifiers_rejected(self):
        # 范围内但不存在（含上限值本身与其前导零写法、普通空缺标识）：任务不存在
        for raw_id in ("999", SQLITE_MAX_INT, MAX_WITH_ZEROS):
            with self.subTest(raw_id=(raw_id if len(raw_id) <= 40
                                      else f"<{len(raw_id)} chars>")):
                self.assert_show_rejected(raw_id, "does not exist")

    def test_leading_zeros_equivalent_to_numeric_value(self):
        # 前导五千个零的 1 等价于 1：返回第一条任务，而非按字符串判为不存在
        self.assert_show_object(
            self.show(FIVE_THOUSAND_ZEROS_THEN_ONE),
            "<5000 zeros>1", self.expected_first(),
        )

    # ---------- 缺少参数：退出码 2、stdout 为空、无回溯 ----------

    def test_missing_task_id_argument_rejected(self):
        before = self.snapshot()
        proc = self.run_cli("task-show")
        after = self.snapshot()
        detail = self._detail(("task-show",), proc)
        self.assertEqual(proc.returncode, 2, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertNotEqual(proc.stderr.strip(), "", detail)
        self.assertNotIn("Traceback", proc.stderr, detail)
        self.assertEqual(after, before, detail)

    def test_missing_db_option_or_value_rejected(self):
        # 完全缺少 --db
        proc = self.run_cli_no_db("task-show", str(self.second_id))
        detail = self._detail(("task-show", str(self.second_id)), proc)
        self.assertEqual(proc.returncode, 2, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertNotEqual(proc.stderr.strip(), "", detail)
        self.assertNotIn("Traceback", proc.stderr, detail)

        # --db 缺少路径值
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
            self.run_cli("task-show", "1", db_path=dir_path),
            ("task-show", "1"),
        )

    def test_missing_parent_directory_is_storage_failure(self):
        missing = os.path.join(self._tmpdir.name, "no_such_dir", "isolated.db")
        self.assert_storage_failure(
            self.run_cli("task-show", "1", db_path=missing),
            ("task-show", "1"),
        )

    def test_file_that_is_not_sqlite_is_storage_failure(self):
        bogus_path = os.path.join(self._tmpdir.name, "bogus.db")
        with open(bogus_path, "w", encoding="utf-8") as fh:
            fh.write("this is definitely not a sqlite database file\n")
        self.assert_storage_failure(
            self.run_cli("task-show", "1", db_path=bogus_path),
            ("task-show", "1"),
        )

    # ---------- 自动建库：空库中任务不存在，退出码 2 且不含任何记录 ----------

    def test_missing_database_file_is_created_then_task_not_found(self):
        fresh_path = os.path.join(self._tmpdir.name, "fresh.db")
        self.assertFalse(os.path.exists(fresh_path))

        proc = self.run_cli("task-show", "1", db_path=fresh_path)
        detail = self._detail(("task-show", "1"), proc)
        self.assertEqual(proc.returncode, 2, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("does not exist", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)

        # 沿用自动建库行为：文件已被创建
        self.assertTrue(os.path.isfile(fresh_path))
        # 只读连接核对：新库不含任何项目或任务
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

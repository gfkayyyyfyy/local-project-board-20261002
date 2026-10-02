#!/usr/bin/env python3
"""task-create 创建任务的命令行回归（验收）测试。

覆盖正常路径与失败路径：

- 正常路径：在两个项目中分别创建任务；标题 "  整理  API_100%'  " 只去除
  首尾空白，保存为 "整理  API_100%'"（内部连续空格、中文、大小写、%、_、
  单引号原样保留）；成功时退出码 0、标准错误为空、标准输出为单个可解析
  JSON 对象，只含 id / project_id / title / status，标识为正整数、所属
  项目正确、状态为 todo；随后由独立命令进程查询同一数据库，任务内容与
  创建结果一致，另一项目不出现该任务。
- 相同标题再次提交保留两条不同标识的任务，列表按标识数值升序；在另一
  项目创建任务时标识仍与已有任务不同；把已有任务移到 doing 后再创建，
  新任务仍为 todo，已有任务的完整内容不变。
- 失败路径：空字符串或纯空白标题、缺少必要参数、项目标识为 0 / 负数 /
  非数字 / 超出 9223372036854775807 / 范围内不存在的值，均退出码 2、
  标准输出为空、标准错误说明相应原因，已有项目和任务在失败前后完全
  相同。项目 1 存在时 0001 与 1 均在该项目创建任务；整数上限本身按
  项目是否存在处理，不误判为越界。有效参数配合指向已有目录的数据库
  路径，退出码 1、标准输出为空、标准错误说明存储失败。

测试只通过 README 公开的 ``python -m kanban --db`` 入口驱动产品：准备
数据用 project-create / task-create / task-move，核对用 task-list /
project-list，不直接读写数据库，不调用产品内部函数，不依赖网络，也
不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理，不会接触
使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_create.py                 # 运行全部回归用例
    python3 -m unittest test_task_create -v     # 等价写法
    python3 test_task_create.py \
        TaskCreateRegression.test_create_success_json_and_persistence

退出码：全部通过为 0，存在失败为 1。失败信息会给出命令输入、实际退出
码、标准输出、标准错误以及预期差异；比较 JSON 时不依赖键顺序，也不要
求错误文案逐字一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TASK_FIELDS = {"id", "project_id", "title", "status"}

# SQLite INTEGER（有符号 64 位）上限，与产品支持的最大标识一致
SQLITE_MAX_INT = 9223372036854775807

# 首尾带空白、内部含连续双空格与特殊字符的标题及其期望保存值
RAW_TITLE = "  整理  API_100%'  "
SAVED_TITLE = "整理  API_100%'"

# 夹具中已有任务的标题，用于确认失败路径与新建不影响既有数据
EXISTING_TITLE = "既有任务 Existing"


class TaskCreateRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-create-regtest-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")
        self._build_fixture()

    def tearDown(self):
        self._tmpdir.cleanup()

    # ---------- 命令行与结果解析辅助 ----------

    def run_cli(self, *cli_args, db_path=None):
        """执行 python -m kanban --db <临时库> ...，返回 CompletedProcess。"""
        return subprocess.run(
            [sys.executable, "-m", "kanban", "--db",
             db_path or self.db_path, *cli_args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def cli_json(self, *cli_args):
        """准备数据用：要求成功并返回解析后的单个 JSON 值。"""
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc, expected="准备数据成功")
        self.assertEqual(proc.returncode, 0, f"准备数据应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"准备数据时标准错误应为空：\n{detail}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"准备数据的 stdout 不是合法 JSON：\n{detail}")

    def _detail(self, cli_args, proc, expected=None):
        """组装失败定位信息：命令输入、实际退出码与输出、预期结果。"""
        lines = [
            f"命令输入={['python', '-m', 'kanban', '--db', self.db_path, *cli_args]!r}",
            f"实际退出码={proc.returncode}",
            f"实际标准输出={proc.stdout!r}",
            f"实际标准错误={proc.stderr!r}",
            f"预期结果={expected!r}",
        ]
        return "\n".join(lines)

    def list_project(self, project_id):
        """通过公开命令查询某项目的全部任务，返回任务对象列表。"""
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
        """记录全部项目及两个项目的任务列表（含顺序与完整对象），供前后比对。"""
        return {
            "projects": self.cli_json("project-list"),
            "tasks": {
                self.project_alpha: self.list_project(self.project_alpha),
                self.project_beta: self.list_project(self.project_beta),
            },
        }

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 两个项目；项目一已有一条任务，项目二为空
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        self.existing = self.cli_json(
            "task-create", str(self.project_alpha), EXISTING_TITLE
        )

    # ---------- 正常路径 ----------

    def _assert_created_object(self, obj, project_id, title, detail):
        """核对创建返回的单个 JSON 对象：字段集合、标识、项目、标题、状态。"""
        self.assertIsInstance(
            obj, dict, f"创建结果应为单个 JSON 对象而非数组：\n{detail}"
        )
        self.assertEqual(
            set(obj.keys()), TASK_FIELDS,
            f"创建结果对象应只含 {sorted(TASK_FIELDS)}：\n{detail}",
        )
        self.assertIsInstance(
            obj["id"], int, f"任务标识应为整数：\n{detail}"
        )
        self.assertGreaterEqual(
            obj["id"], 1, f"任务标识应为正整数：\n{detail}"
        )
        self.assertEqual(
            obj["project_id"], project_id, f"所属项目应正确：\n{detail}"
        )
        self.assertEqual(obj["title"], title, f"保存标题应正确：\n{detail}")
        self.assertEqual(
            obj["status"], "todo", f"新任务状态应为 todo：\n{detail}"
        )

    def _create_and_parse(self, project_id_arg, title, expected_note):
        """执行一次 task-create 并核对成功协议，返回解析后的任务对象。"""
        cli_args = ("task-create", project_id_arg, title)
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc, expected=expected_note)
        self.assertEqual(proc.returncode, 0, f"创建应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        try:
            obj = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"创建结果的 stdout 不是合法 JSON：\n{detail}")
        return obj, detail

    def test_create_success_json_and_persistence(self):
        # 标题只去除首尾空白，内部双空格、中文、大小写、%、_、单引号保留
        before = self.snapshot()
        obj, detail = self._create_and_parse(
            str(self.project_alpha), RAW_TITLE,
            "退出码 0、stderr 为空、stdout 为只含 id/project_id/title/status "
            f"的单个 JSON 对象，标题保存为 {SAVED_TITLE!r}，状态 todo",
        )
        self._assert_created_object(obj, self.project_alpha, SAVED_TITLE, detail)

        # 由独立命令进程查询同一数据库：任务内容与创建结果一致
        after = self.snapshot()
        alpha_after = after["tasks"][self.project_alpha]
        persisted = [o for o in alpha_after if o["id"] == obj["id"]]
        self.assertEqual(
            len(persisted), 1,
            f"查询结果中新任务应恰好出现一次：\n{detail}\n"
            f"创建后项目一列表={alpha_after!r}",
        )
        self.assertEqual(
            persisted[0], obj,
            f"再次查询到的任务对象应与创建结果完全一致（结果已保存）：\n"
            f"{detail}\n查询所得={persisted[0]!r}",
        )
        # 项目一列表 = 失败前列表 + 新任务（按标识升序排在末尾）
        self.assertEqual(
            alpha_after, before["tasks"][self.project_alpha] + [obj],
            f"项目一列表应为原有任务加新任务：\n{detail}\n"
            f"创建前={before['tasks'][self.project_alpha]!r}",
        )
        # 另一项目不出现该任务，项目列表不变
        self.assertEqual(
            after["tasks"][self.project_beta], before["tasks"][self.project_beta],
            f"另一项目不应出现新任务：\n{detail}",
        )
        self.assertEqual(
            after["projects"], before["projects"],
            f"创建任务不应改动项目列表：\n{detail}",
        )

    def test_create_in_second_project_isolated(self):
        # 在项目二创建任务：项目一不变，项目二出现该任务，标识与已有任务不同
        before = self.snapshot()
        obj, detail = self._create_and_parse(
            str(self.project_beta), "beta 项目任务",
            "在项目二创建成功，项目一不变，标识与已有任务不同",
        )
        self._assert_created_object(obj, self.project_beta, "beta 项目任务", detail)
        self.assertNotEqual(
            obj["id"], self.existing["id"],
            f"新任务标识应与已有任务不同：\n{detail}",
        )
        after = self.snapshot()
        self.assertEqual(
            after["tasks"][self.project_beta],
            before["tasks"][self.project_beta] + [obj],
            f"项目二列表应为原有任务加新任务：\n{detail}",
        )
        self.assertEqual(
            after["tasks"][self.project_alpha],
            before["tasks"][self.project_alpha],
            f"项目一的任务不应受项目二创建影响：\n{detail}",
        )

    def test_create_duplicate_title_keeps_both(self):
        # 相同标题再次提交：保留两条不同标识的任务，列表按标识数值升序
        first, detail1 = self._create_and_parse(
            str(self.project_alpha), "重复标题", "第一次创建成功"
        )
        second, detail2 = self._create_and_parse(
            str(self.project_alpha), "重复标题", "第二次创建成功"
        )
        detail = detail1 + "\n" + detail2
        self.assertNotEqual(
            first["id"], second["id"],
            f"相同标题的两次创建应得到不同标识：\n{detail}",
        )
        tasks = self.list_project(self.project_alpha)
        duplicates = [o for o in tasks if o["title"] == "重复标题"]
        self.assertEqual(
            duplicates, [first, second],
            f"两条同标题任务都应保留且内容正确：\n{detail}\n列表={tasks!r}",
        )
        ids = [o["id"] for o in tasks]
        self.assertEqual(
            ids, sorted(ids),
            f"任务列表应按标识数值升序返回：\n{detail}\n标识顺序={ids!r}",
        )

    def test_create_after_move_keeps_new_todo_and_existing_intact(self):
        # 把已有任务移到 doing 后再创建：新任务仍为 todo，已有任务完整内容不变
        moved = self.cli_json("task-move", str(self.existing["id"]), "doing")
        self.assertEqual(moved["status"], "doing", "准备数据：移动应成功")
        before = self.snapshot()

        obj, detail = self._create_and_parse(
            str(self.project_alpha), "移动后新建",
            "已有任务处于 doing 时创建成功，新任务为 todo，已有任务不变",
        )
        self._assert_created_object(obj, self.project_alpha, "移动后新建", detail)

        after = self.snapshot()
        alpha_after = after["tasks"][self.project_alpha]
        self.assertEqual(
            alpha_after, before["tasks"][self.project_alpha] + [obj],
            f"项目一列表应为原有任务加新任务：\n{detail}\n"
            f"创建前={before['tasks'][self.project_alpha]!r}",
        )
        # 已有任务（含 doing 状态）的完整内容不变
        existing_after = [
            o for o in alpha_after if o["id"] == self.existing["id"]
        ]
        self.assertEqual(
            existing_after, [moved],
            f"已有任务的完整内容（含 doing 状态）不得改变：\n{detail}\n"
            f"移动后={moved!r}\n创建后查询={existing_after!r}",
        )

    def test_create_leading_zeros_same_project(self):
        # 项目 1 存在时，0001 与 1 均应在该项目创建任务（前导零按数值等价）
        plain, detail1 = self._create_and_parse(
            str(self.project_alpha), "前导零-普通写法",
            "普通写法标识创建成功",
        )
        padded_arg = "0" * 3 + str(self.project_alpha)
        padded, detail2 = self._create_and_parse(
            padded_arg, "前导零-带前导零写法",
            f"带前导零的标识 {padded_arg!r} 应在同一项目创建成功",
        )
        detail = detail1 + "\n" + detail2
        self._assert_created_object(
            padded, self.project_alpha, "前导零-带前导零写法", detail
        )
        self.assertNotEqual(
            plain["id"], padded["id"],
            f"两次创建应得到不同标识：\n{detail}",
        )
        tasks = self.list_project(self.project_alpha)
        created = [o for o in tasks if o["id"] in (plain["id"], padded["id"])]
        self.assertEqual(
            created, [plain, padded],
            f"两种写法的任务都应保存在项目一中：\n{detail}\n列表={tasks!r}",
        )

    def test_create_max_int_project_id_handled_by_existence(self):
        # 整数上限本身按项目是否存在处理：范围内不存在 -> 说明不存在，
        # 不能误判为越界；数据保持不变
        before = self.snapshot()
        cli_args = ("task-create", str(SQLITE_MAX_INT), "上限项目")
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc,
            expected="退出码 2、stdout 为空、stderr 说明项目不存在"
            "（而非超出范围），已有数据不变",
        )
        self.assertEqual(proc.returncode, 2, f"应返回退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"失败时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"失败时标准错误应说明原因：\n{detail}",
        )
        self.assertIn(
            "exist", proc.stderr.lower(),
            f"标准错误应说明项目不存在而非越界：\n{detail}",
        )
        self.assertNotIn(
            "range", proc.stderr.lower(),
            f"整数上限本身不应被误判为超出范围：\n{detail}",
        )
        self.assertNotIn(
            "Traceback", proc.stderr,
            f"标准错误不应包含 Python 异常回溯：\n{detail}",
        )
        self.assertEqual(
            self.snapshot(), before,
            f"失败后已有项目和任务不得改变：\n{detail}\n失败前={before!r}",
        )

    # ---------- 失败路径：退出码 2、stdout 为空、数据不变 ----------

    def _check_usage_error(self, cli_args, label, stderr_hints=()):
        """非法输入：退出码 2、stdout 为空、stderr 说明原因且数据不变。"""
        before = self.snapshot()
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc,
            expected=(
                f"{label}：退出码 2、stdout 为空、stderr 说明原因"
                "（含 " + "、".join(repr(h) for h in stderr_hints) + "）"
                "且无 Python 异常回溯，已有项目和任务与失败前完全一致"
            ),
        )
        self.assertEqual(proc.returncode, 2, f"应返回退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"失败时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"失败时标准错误应说明原因：\n{detail}",
        )
        for hint in stderr_hints:
            self.assertIn(
                hint, proc.stderr.lower(),
                f"标准错误应说明相应原因（{hint!r}）：\n{detail}",
            )
        self.assertNotIn(
            "Traceback", proc.stderr,
            f"标准错误不应包含 Python 异常回溯：\n{detail}",
        )
        self.assertEqual(
            self.snapshot(), before,
            f"失败后已有项目和任务不得发生任何变化：\n{detail}\n"
            f"失败前={before!r}",
        )

    def test_empty_title_rejected(self):
        self._check_usage_error(
            ("task-create", str(self.project_alpha), ""),
            "空字符串标题", ("title",),
        )

    def test_whitespace_only_title_rejected(self):
        self._check_usage_error(
            ("task-create", str(self.project_alpha), "   \t  "),
            "纯空白标题", ("title",),
        )

    def test_missing_title_argument_rejected(self):
        self._check_usage_error(
            ("task-create", str(self.project_alpha)),
            "缺少标题参数", ("error",),
        )

    def test_missing_all_arguments_rejected(self):
        self._check_usage_error(
            ("task-create",),
            "缺少全部位置参数", ("error",),
        )

    def test_project_id_zero_rejected(self):
        self._check_usage_error(
            ("task-create", "0", "有效标题"),
            "项目标识为 0", ("positive integer",),
        )

    def test_project_id_negative_rejected(self):
        self._check_usage_error(
            ("task-create", "-1", "有效标题"),
            "项目标识为负数", ("positive integer",),
        )

    def test_project_id_non_numeric_rejected(self):
        self._check_usage_error(
            ("task-create", "abc", "有效标题"),
            "项目标识非数字", ("positive integer",),
        )

    def test_project_id_above_max_rejected(self):
        self._check_usage_error(
            ("task-create", str(SQLITE_MAX_INT + 1), "有效标题"),
            "项目标识超出 9223372036854775807", ("range",),
        )

    def test_project_id_nonexistent_in_range_rejected(self):
        missing = self.project_beta + 1000
        self._check_usage_error(
            ("task-create", str(missing), "有效标题"),
            f"范围内不存在的项目标识 {missing}", ("exist",),
        )

    # ---------- 存储失败：退出码 1 ----------

    def test_db_path_is_directory_storage_failure(self):
        # 有效参数配合指向已有目录的数据库路径：退出码 1、stdout 为空、
        # 标准错误说明存储失败
        dir_db = os.path.join(self._tmpdir.name, "a-directory")
        os.mkdir(dir_db)
        cli_args = ("task-create", str(self.project_alpha), "有效标题")
        proc = self.run_cli(*cli_args, db_path=dir_db)
        detail = self._detail(
            cli_args, proc,
            expected="数据库路径指向已有目录：退出码 1、stdout 为空、"
            "stderr 说明存储失败且无 Python 异常回溯",
        )
        self.assertEqual(proc.returncode, 1, f"应返回退出码 1：\n{detail}")
        self.assertEqual(proc.stdout, "", f"失败时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"失败时标准错误应说明存储失败：\n{detail}",
        )
        self.assertIn(
            "storage", proc.stderr.lower(),
            f"标准错误应说明存储失败：\n{detail}",
        )
        self.assertNotIn(
            "Traceback", proc.stderr,
            f"标准错误不应包含 Python 异常回溯：\n{detail}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""task-create 创建任务的命令行回归（验收）测试。

覆盖成功路径（在两个项目中创建任务）：

- 标题输入为 "  整理  API_100%'  " 时，保存值为 "整理  API_100%'"：
  只去除首尾空白，内部连续空格、中文、大小写与特殊字符原样保留；
- 成功时退出码为 0、标准错误为空、标准输出是单个可解析的 JSON 对象，
  且只含 id / project_id / title / status 四个字段：任务标识为正整数、
  所属项目正确、状态为 todo；
- 随后由独立的命令进程（task-list）查询同一数据库：任务内容与创建结果
  完全一致，另一项目不出现该任务；
- 再次提交相同标题会保留两条不同标识的任务，列表按标识数值升序返回；
  在另一项目创建任务时，新标识仍与已有任务不同；
- 把已有任务移到 doing 后再创建，新任务仍为 todo，已有任务的完整内容
  （标识、所属项目、标题、状态）保持不变；
- 项目 1 存在时，0001 与 1 都在该项目创建任务（前导零按数值等价）。

覆盖拒绝路径（退出码 2、标准输出为空、标准错误说明原因、无 Python
回溯，失败前后已有项目与任务完全相同）：

- 标题为空字符串或纯空白；
- 缺少必要参数；
- 项目标识为 0、负数、非数字、9223372036854775808（超出 SQLite 有符号
  64 位整数上限）或范围内不存在的值；
- 整数上限 9223372036854775807 本身按项目是否存在处理，不误判为越界；
- 数据库路径指向已有目录：退出码 1、标准输出为空、标准错误说明存储失败。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品：用
project-create 准备项目、task-move 准备任务状态、task-list 查询核对
落库结果。不直接读写数据库，不调用产品内部函数，不依赖网络，也不依赖
任何预先保存的项目或使用者自己的数据。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_create.py                  # 运行全部回归用例
    python3 -m unittest test_task_create -v      # 等价写法
    python3 -m unittest discover -p 'test_task_create.py' -v
    python3 test_task_create.py \
        TaskCreateRegression.test_create_returns_json_object_and_persists  # 单用例

退出码：全部通过为 0，存在断言失败为非零。失败信息会给出命令行输入、
实际退出码、标准输出、标准错误以及预期差异；JSON 比较按对象内容进行，
不依赖键顺序，也不要求错误文案逐字一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TASK_FIELDS = {"id", "project_id", "title", "status"}

# 创建标题输入：首尾各两个空格；内部为中文 + 双空格 + 大小写英文 + % + _ + 单引号
TITLE_RAW = "  整理  API_100%'  "
TITLE = "整理  API_100%'"

# SQLite 有符号 64 位整数上限：上限值本身合法，按项目是否存在处理
SQLITE_MAX_INT = "9223372036854775807"
OVER_MAX = "9223372036854775808"            # 上限 + 1：越界


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
        """执行 python -m kanban --db <临时库> ...，返回 CompletedProcess。

        每次调用都是独立的命令进程，因此查询核对与创建不在同一进程内完成。
        """
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

    def list_project(self, project_id):
        """通过独立命令进程查询项目任务，返回任务对象列表。"""
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
        """记录项目列表与两个项目的当前任务（完整对象、按标识升序）。"""
        return {
            "projects": self.cli_json("project-list"),
            self.project_alpha: self.list_project(self.project_alpha),
            self.project_beta: self.list_project(self.project_beta),
        }

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 全新库中依次创建两个项目：项目一 alpha（标识为 1）、项目二 beta
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        # 每个项目各放一条种子任务，用于核对失败请求与新项目隔离
        alpha_seed = self.cli_json(
            "task-create", str(self.project_alpha), "seed alpha"
        )
        beta_seed = self.cli_json(
            "task-create", str(self.project_beta), "seed beta"
        )
        self.alpha_seed_id = alpha_seed["id"]
        self.beta_seed_id = beta_seed["id"]

    def assert_create_success(self, raw_project_id, raw_title, expected_project_id):
        """task-create 成功的通用断言：返回（结果对象, 失败明细）。"""
        cli_args = ("task-create", raw_project_id, raw_title)
        proc = self.run_cli(*cli_args)
        expected_title = raw_title.strip()
        obj, detail = self.parse_single_json_object(
            proc, cli_args,
            expected=(
                "退出码 0、stderr 为空、stdout 为单个 JSON 任务对象，"
                f"只含字段 {sorted(TASK_FIELDS)}，project_id="
                f"{expected_project_id}、title={expected_title!r}、status=todo"
            ),
        )
        self.assertEqual(proc.returncode, 0, f"创建应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功创建时标准错误应为空：\n{detail}")
        self.assertEqual(
            set(obj.keys()), TASK_FIELDS,
            f"创建结果对象字段结构不符：\n{detail}",
        )
        self.assertIsInstance(
            obj["id"], int, f"任务标识应为整数：\n{detail}"
        )
        self.assertGreater(obj["id"], 0, f"任务标识应为正整数：\n{detail}")
        self.assertEqual(
            obj["project_id"], expected_project_id,
            f"所属项目标识不符：\n{detail}",
        )
        self.assertEqual(
            obj["title"], expected_title,
            f"返回标题应为去除首尾空白后的保存值：\n{detail}",
        )
        self.assertEqual(obj["status"], "todo", f"新任务状态应为 todo：\n{detail}")
        return obj, detail

    # ---------- 成功路径：标题处理、JSON 返回值、落库核对 ----------

    def test_create_returns_json_object_and_persists(self):
        # 输入带首尾空白、内部双空格、中文、大小写、%、_、单引号的标题
        obj, detail = self.assert_create_success(
            str(self.project_alpha), TITLE_RAW, self.project_alpha
        )
        # 保存值：只去首尾空白，内部内容原样保留
        self.assertEqual(obj["title"], TITLE, detail)
        self.assertNotEqual(obj["title"], TITLE_RAW, detail)
        for piece in ("整理", "  ", "API", "_", "100%", "'"):
            self.assertIn(piece, obj["title"], detail)

        # 独立命令进程查询同一数据库：落库内容与创建结果完全一致
        alpha_tasks = self.list_project(self.project_alpha)
        persisted = [t for t in alpha_tasks if t["id"] == obj["id"]]
        self.assertEqual(
            persisted, [obj],
            f"项目一中应能查到与创建结果完全一致的任务：\n{detail}\n"
            f"项目一列表={alpha_tasks!r}",
        )
        # 项目一完整列表为种子任务加新任务，且按标识数值升序
        self.assertEqual(
            [(t["id"], t["title"], t["status"]) for t in alpha_tasks],
            [
                (self.alpha_seed_id, "seed alpha", "todo"),
                (obj["id"], TITLE, "todo"),
            ],
            f"项目一任务内容或排序不符：\n{detail}",
        )
        self.assertEqual(
            [t["id"] for t in alpha_tasks],
            sorted(t["id"] for t in alpha_tasks),
            detail,
        )
        # 另一项目不出现该任务：标识与标题都不在项目二中
        beta_tasks = self.list_project(self.project_beta)
        self.assertEqual(
            [t["id"] for t in beta_tasks], [self.beta_seed_id],
            f"新任务不得出现在另一项目：\n{detail}\n项目二列表={beta_tasks!r}",
        )
        self.assertFalse(
            any(t["title"] == TITLE for t in beta_tasks), detail
        )

    def test_duplicate_title_keeps_distinct_ids_sorted_ascending(self):
        # 在项目一再次提交相同标题：保留两条不同标识的任务
        first, detail = self.assert_create_success(
            str(self.project_alpha), TITLE_RAW, self.project_alpha
        )
        second, second_detail = self.assert_create_success(
            str(self.project_alpha), TITLE_RAW, self.project_alpha
        )
        self.assertNotEqual(
            first["id"], second["id"],
            f"重复标题应产生不同任务标识：\n首次={first!r}\n再次={second!r}",
        )
        self.assertEqual(first["title"], second["title"], TITLE)

        # 列表按标识数值升序返回两条同标题任务
        alpha_tasks = self.list_project(self.project_alpha)
        same_title = [t for t in alpha_tasks if t["title"] == TITLE]
        self.assertEqual(
            same_title, [first, second],
            f"两条同标题任务应按标识升序返回：\n{detail}\n实际={alpha_tasks!r}",
        )
        self.assertEqual(
            [t["id"] for t in same_title], sorted(t["id"] for t in same_title),
        )

        # 在另一项目创建同标题任务：标识仍与已有任务不同，且归属项目二
        other, other_detail = self.assert_create_success(
            str(self.project_beta), TITLE_RAW, self.project_beta
        )
        self.assertNotIn(
            other["id"], {first["id"], second["id"]},
            f"跨项目创建的任务标识仍须唯一：\n{other_detail}",
        )
        beta_tasks = self.list_project(self.project_beta)
        self.assertEqual(
            [(t["id"], t["project_id"], t["title"], t["status"])
             for t in beta_tasks],
            [
                (self.beta_seed_id, self.project_beta, "seed beta", "todo"),
                (other["id"], self.project_beta, TITLE, "todo"),
            ],
            f"项目二应只含自己的种子任务与新任务：\n{other_detail}",
        )
        # 项目一的任务集合不受项目二创建影响
        self.assertEqual(
            self.list_project(self.project_alpha), alpha_tasks,
            "在另一项目创建任务不得改动项目一的任务",
        )

    def test_create_after_move_new_task_todo_existing_unchanged(self):
        # 先把已有任务移到 doing，记录移动后的完整内容
        moved = self.cli_json(
            "task-move", str(self.alpha_seed_id), "doing"
        )
        self.assertEqual(moved["status"], "doing")

        # 再创建新任务：新任务为 todo
        created, detail = self.assert_create_success(
            str(self.project_alpha), TITLE_RAW, self.project_alpha
        )

        alpha_tasks = self.list_project(self.project_alpha)
        by_id = {t["id"]: t for t in alpha_tasks}
        # 已有任务的完整内容（标识、所属项目、标题、doing 状态）不变
        self.assertEqual(
            by_id[self.alpha_seed_id], moved,
            f"创建新任务后已有任务的完整内容不得改变：\n{detail}\n"
            f"移动后={moved!r}\n创建后={by_id.get(self.alpha_seed_id)!r}",
        )
        # 新任务仍为 todo，且与已有任务标识不同、列表按标识升序
        self.assertEqual(by_id[created["id"]]["status"], "todo", detail)
        self.assertEqual(
            [t["id"] for t in alpha_tasks],
            sorted(t["id"] for t in alpha_tasks),
            detail,
        )
        # 项目二保持只有自己的种子任务
        self.assertEqual(
            [t["id"] for t in self.list_project(self.project_beta)],
            [self.beta_seed_id],
        )

    def test_leading_zero_project_id_creates_in_same_project(self):
        # 项目 1 存在：1 与 0001 都在该项目创建任务
        via_plain, detail = self.assert_create_success(
            "1", "created via 1", self.project_alpha
        )
        self.assertEqual(via_plain["project_id"], 1)
        via_zeros, zeros_detail = self.assert_create_success(
            "0001", "created via 0001", self.project_alpha
        )
        self.assertEqual(via_zeros["project_id"], 1)
        self.assertNotEqual(via_plain["id"], via_zeros["id"])

        alpha_tasks = self.list_project(self.project_alpha)
        self.assertEqual(
            [t["id"] for t in alpha_tasks],
            [self.alpha_seed_id, via_plain["id"], via_zeros["id"]],
            f"两次创建都应落入项目一并按标识升序：\n{zeros_detail}",
        )
        # 项目二不受影响
        self.assertEqual(
            [t["id"] for t in self.list_project(self.project_beta)],
            [self.beta_seed_id],
        )

    # ---------- 拒绝路径：通用断言 ----------

    def assert_rejected(self, cli_args, reason=None):
        """断言请求被拒绝：退出码 2、stdout 为空、stderr 有说明且无回溯，
        并且失败前后已有项目与任务完全相同。"""
        before = self.snapshot()
        proc = self.run_cli(*cli_args)
        after = self.snapshot()
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 2、stdout 为空、stderr 说明拒绝原因"
                + (f"（应包含 {reason!r}）" if reason else "")
                + "，已有项目与任务保持不变"
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
            f"失败前={before!r}\n失败后={after!r}",
        )
        return proc

    # ---------- 空 / 纯空白标题 ----------

    def test_empty_and_whitespace_titles_rejected(self):
        for raw_title in ("", "   ", "\t", "\n \t "):
            with self.subTest(raw_title=raw_title):
                self.assert_rejected(
                    ("task-create", str(self.project_alpha), raw_title),
                    reason="title",
                )

    # ---------- 缺少必要参数 ----------

    def test_missing_arguments_rejected(self):
        for cli_args in (
            ("task-create",),
            ("task-create", str(self.project_alpha)),
        ):
            with self.subTest(cli_args=list(cli_args)):
                # argparse 缺参同样是退出码 2、stdout 为空、数据不变
                before = self.snapshot()
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
        # (项目标识输入, stderr 应包含的原因片段)
        cases = [
            ("0", "positive integer"),
            ("0000", "positive integer"),
            ("-1", "positive integer"),
            ("abc", "positive integer"),
            (OVER_MAX, "range"),                  # 超出 SQLite 整数上限
            ("999", "does not exist"),            # 范围内但项目不存在
        ]
        for raw_project_id, reason in cases:
            with self.subTest(raw_project_id=raw_project_id):
                self.assert_rejected(
                    ("task-create", raw_project_id, "should not be saved"),
                    reason=reason,
                )

    def test_max_int_project_id_checked_by_existence_not_range(self):
        # 上限值本身合法：项目不存在时应说明“不存在”，不能误判为越界
        proc = self.assert_rejected(
            ("task-create", SQLITE_MAX_INT, TITLE_RAW),
            reason="does not exist",
        )
        self.assertNotIn(
            "out of the supported range", proc.stderr.lower(),
            f"上限值本身不应被判为越界：\n{proc.stderr!r}",
        )

    # ---------- 存储失败：退出码 1 ----------

    def test_database_path_being_a_directory_is_storage_failure(self):
        # 有效参数配合指向已有目录的 --db：SQLite 无法打开，应退出码 1，
        # stdout 为空、stderr 说明存储失败，真正的临时库数据不发生变化
        dir_path = os.path.join(self._tmpdir.name, "cannot_be_a_db")
        os.mkdir(dir_path)
        before = self.snapshot()

        cli_args = ("task-create", str(self.project_alpha), TITLE_RAW)
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

#!/usr/bin/env python3
"""task-rename 任务改名的命令行回归（验收）测试。

覆盖成功路径：

- 在两个项目中准备同标题任务（目标项目内另有一条同标题的兄弟任务），
  把目标任务置为 doing，再用带前导零的同值任务标识把它改名为
  "  修复  API_100%'  "：退出码 0、标准错误为空、标准输出为单个 JSON
  对象且仍只含 id / project_id / title / status 四个字段；
- 保存后的标题为 "修复  API_100%'"：首尾空白被去除，内部双空格、
  大小写、中文、百分号、下划线与单引号原样保留；标识、所属项目与
  doing 状态不变；
- 改名只影响指定任务：两个项目中其他任务的完整对象、任务数量与按
  标识升序的顺序保持原样；
- 目标项目按新标题关键词筛选能找到它，按旧标题筛选只剩未改名任务；
- 再次提交仅首尾空白不同的同一标题仍成功，返回同一任务且不新增记录；
- 改成另一条任务的标题也成功（允许重名）。

覆盖拒绝路径（退出码 2、标准输出为空、标准错误说明原因、无 Python
回溯，两个项目的任务完整列表保持不变）：

- 新标题为空字符串或纯空白；
- 缺少必要参数；
- 任务标识为 0、负数、非数字、9223372036854775808（超出 SQLite 有符号
  64 位整数上限）或范围内不存在的标识；
- 数据库路径指向已有目录：退出码 1、标准输出为空、标准错误说明存储失败。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品：用
project-create / task-create / task-move 准备数据，用 task-rename 改名，
用 task-list 查询验证结果已落库。不直接读写数据库，不调用产品内部函数，
不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用
者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_rename.py                       # 运行全部回归用例
    python3 -m unittest test_task_rename -v           # 等价写法
    python3 -m unittest discover -p 'test_task_rename.py' -v
    python3 test_task_rename.py \
        TaskRenameRegression.test_rename_persists_saved_title  # 只跑单个用例

退出码：全部通过为 0，存在断言失败为非零。失败信息会给出输入参数、实际
退出码、标准输出、标准错误以及预期结果，不依赖 JSON 键顺序或错误文案的
逐字拼写。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TASK_FIELDS = {"id", "project_id", "title", "status"}

# 三条任务共用的旧标题，用于确认同标题任务不会被连带改动
SHARED_TITLE = "Sync design doc"

# 改名输入：首尾各两个空格，内部为中文 + 双空格 + 大小写英文 + % + _ + 单引号
NEW_TITLE_RAW = "  修复  API_100%'  "
NEW_TITLE = "修复  API_100%'"

# SQLite 有符号 64 位整数上限 + 1：超出支持范围
OVER_MAX = "9223372036854775808"


class TaskRenameRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-rename-regtest-")
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

    def list_project(self, project_id, *extra_args):
        """通过公开命令查询项目任务（可附加 --status / --query），返回对象列表。"""
        cli_args = ("task-list", str(project_id), *extra_args)
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
        """记录两个项目的当前任务列表（含顺序与完整对象），用于前后比对。"""
        return {
            self.project_alpha: self.list_project(self.project_alpha),
            self.project_beta: self.list_project(self.project_beta),
        }

    def target_object(self, title=NEW_TITLE, status="doing"):
        return {
            "id": self.target_id,
            "project_id": self.project_alpha,
            "title": title,
            "status": status,
        }

    def rename_target(self, raw_title, raw_id=None):
        """对目标任务执行 task-rename；默认用带前导零的同值任务标识。"""
        if raw_id is None:
            raw_id = f"000{self.target_id}"
        return self.run_cli("task-rename", raw_id, raw_title)

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
        # 项目一：先建目标任务 T，再建同标题兄弟任务 S（列表顺序即 T、S）
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        target = self.cli_json(
            "task-create", str(self.project_alpha), SHARED_TITLE
        )
        sibling = self.cli_json(
            "task-create", str(self.project_alpha), SHARED_TITLE
        )
        self.target_id = target["id"]
        self.sibling_id = sibling["id"]

        # 项目二：另一条同标题任务 O，验证跨项目隔离
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        other = self.cli_json(
            "task-create", str(self.project_beta), SHARED_TITLE
        )
        self.other_id = other["id"]

        # 把目标任务置为 doing：改名后该状态必须原样保留
        moved = self.cli_json(
            "task-move", str(self.target_id), "doing"
        )
        self.assertEqual(moved["id"], self.target_id)
        self.assertEqual(moved["status"], "doing")

    def assert_rename_success(self, raw_title, expected_title, raw_id=None):
        """改名成功的通用断言，返回（结果对象, 失败明细）。"""
        cli_args = (
            "task-rename",
            raw_id if raw_id is not None else f"000{self.target_id}",
            raw_title,
        )
        proc = self.run_cli(*cli_args)
        expected_obj = self.target_object(title=expected_title)
        obj, detail = self.parse_single_json_object(
            proc, cli_args,
            expected=(
                "退出码 0、stderr 为空、stdout 为单个 JSON 任务对象，"
                f"对象为 {expected_obj!r}（字段集合 {sorted(TASK_FIELDS)}）"
            ),
        )
        self.assertEqual(proc.returncode, 0, f"改名应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功改名时标准错误应为空：\n{detail}")
        self.assertEqual(
            set(obj.keys()), TASK_FIELDS,
            f"改名结果对象字段结构不符：\n{detail}",
        )
        self.assertEqual(obj, expected_obj, f"改名结果对象内容不符：\n{detail}")
        return obj, detail

    # ---------- 成功路径 ----------

    def test_rename_persists_saved_title(self):
        # 带前导零的同值标识改名；返回对象即保存后的值
        obj, detail = self.assert_rename_success(NEW_TITLE_RAW, NEW_TITLE)

        # 另起一次命令行调用查询：结果已落库，且 doing 状态保留
        tasks = self.list_project(self.project_alpha)
        persisted = [t for t in tasks if t["id"] == self.target_id]
        self.assertEqual(
            len(persisted), 1,
            f"查询结果中目标任务应恰好出现一次：\n{detail}\n"
            f"改名后项目一列表={tasks!r}",
        )
        self.assertEqual(
            persisted[0], obj,
            f"再次查询到的目标任务应与改名结果完全一致（结果已保存）：\n"
            f"{detail}\n查询所得={persisted[0]!r}",
        )
        # 保存值逐项核对：内部双空格、大小写、中文、%、_、单引号原样保留
        saved_title = persisted[0]["title"]
        self.assertEqual(saved_title, NEW_TITLE, detail)
        for piece in ("修复", "  ", "API", "_", "100%", "'"):
            self.assertIn(piece, saved_title, detail)
        self.assertNotEqual(saved_title, NEW_TITLE_RAW, detail)  # 首尾空白已去除
        self.assertEqual(persisted[0]["status"], "doing", detail)

    def test_rename_only_affects_target_task(self):
        before = self.snapshot()
        self.assert_rename_success(NEW_TITLE_RAW, NEW_TITLE)
        after = self.snapshot()

        alpha_before = before[self.project_alpha]
        alpha_after = after[self.project_alpha]

        # 项目一：只有目标任务的标题变化，兄弟任务完整对象不变
        expected_alpha = [
            self.target_object() if task["id"] == self.target_id else task
            for task in alpha_before
        ]
        self.assertEqual(
            alpha_after, expected_alpha,
            f"项目一中除目标任务外的其他任务不得改变：\n"
            f"改名前={alpha_before!r}\n改名后={alpha_after!r}",
        )
        # 任务数量与按标识升序的顺序不变
        self.assertEqual(len(alpha_after), len(alpha_before))
        self.assertEqual(
            [t["id"] for t in alpha_after],
            [t["id"] for t in alpha_before],
            f"改名后项目一任务标识排序不得改变：改名前="
            f"{[t['id'] for t in alpha_before]!r}，"
            f"改名后={[t['id'] for t in alpha_after]!r}",
        )
        # 项目二的同标题任务完整对象、数量与顺序完全不变
        self.assertEqual(
            after[self.project_beta], before[self.project_beta],
            f"另一项目的任务不得受改名影响：\n"
            f"改名前={before[self.project_beta]!r}，"
            f"改名后={after[self.project_beta]!r}",
        )

    def test_task_list_filters_by_new_and_old_title(self):
        self.assert_rename_success(NEW_TITLE_RAW, NEW_TITLE)

        # 新标题关键词（含内部双空格、%、_、单引号）在目标项目命中目标任务
        found_new = self.list_project(self.project_alpha, "--query", NEW_TITLE)
        self.assertEqual(
            found_new, [self.target_object()],
            f"按新标题筛选应只找到改名后的目标任务，实际={found_new!r}",
        )
        # 旧标题关键词在目标项目只剩未改名的兄弟任务
        found_old = self.list_project(self.project_alpha, "--query", SHARED_TITLE)
        sibling = {
            "id": self.sibling_id,
            "project_id": self.project_alpha,
            "title": SHARED_TITLE,
            "status": "todo",
        }
        self.assertEqual(
            found_old, [sibling],
            f"按旧标题筛选应只剩未改名任务，实际={found_old!r}",
        )
        # 新标题不串到另一项目；另一项目按旧标题仍能找到原样任务
        self.assertEqual(
            self.list_project(self.project_beta, "--query", NEW_TITLE), [],
            "新标题关键词不应在另一项目命中任何任务",
        )
        other = {
            "id": self.other_id,
            "project_id": self.project_beta,
            "title": SHARED_TITLE,
            "status": "todo",
        }
        self.assertEqual(
            self.list_project(self.project_beta, "--query", SHARED_TITLE),
            [other],
            "另一项目的旧标题任务应保持可查询且对象不变",
        )

    def test_resubmit_same_title_with_surrounding_whitespace_is_idempotent(self):
        first, detail = self.assert_rename_success(NEW_TITLE_RAW, NEW_TITLE)
        between = self.snapshot()

        # 仅首尾空白不同（改为三个前导空格 + 一个尾部制表符）的同一保存标题
        repadded = "   " + NEW_TITLE + "\t"
        cli_args = ("task-rename", str(self.target_id), repadded)
        proc = self.run_cli(*cli_args)
        again, again_detail = self.parse_single_json_object(
            proc, cli_args,
            expected=(
                "退出码 0、stderr 为空，返回同一个任务对象 "
                f"{first!r}，且不新增记录"
            ),
        )
        self.assertEqual(proc.returncode, 0, f"同标题再次提交仍应成功：\n{again_detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{again_detail}")
        self.assertEqual(set(again.keys()), TASK_FIELDS, again_detail)
        self.assertEqual(
            again, first,
            f"再次提交应返回同一个任务对象：\n首次={first!r}，\n再次={again!r}",
        )

        # 两个项目的任务列表（数量、标识、对象）与再次提交之前完全一致
        after = self.snapshot()
        self.assertEqual(
            after, between,
            f"同标题再次提交不得新增或改动任何记录：\n"
            f"提交前={between!r}\n提交后={after!r}\n{detail}",
        )

    def test_rename_to_another_tasks_title_is_allowed(self):
        # 先改成新标题制造区分，再改成兄弟任务（也是另一项目任务）的标题
        self.assert_rename_success(NEW_TITLE_RAW, NEW_TITLE)
        obj, detail = self.assert_rename_success(
            SHARED_TITLE, SHARED_TITLE, raw_id=str(self.target_id)
        )
        self.assertEqual(obj["status"], "doing", detail)

        # 允许重名：项目一内两条任务同名，标识不同、数量不变、按标识升序
        alpha_tasks = self.list_project(self.project_alpha)
        self.assertEqual(
            [(t["id"], t["title"], t["status"]) for t in alpha_tasks],
            [
                (self.target_id, SHARED_TITLE, "doing"),
                (self.sibling_id, SHARED_TITLE, "todo"),
            ],
            f"重名应被允许且只改目标任务：\n实际={alpha_tasks!r}\n{detail}",
        )
        # 按共享标题筛选，目标项目内两条任务都命中且按标识升序
        self.assertEqual(
            [t["id"] for t in self.list_project(
                self.project_alpha, "--query", SHARED_TITLE)],
            [self.target_id, self.sibling_id],
            "重名后按该标题筛选应同时命中两条任务并按标识升序",
        )
        # 另一项目的任务不受影响
        self.assertEqual(
            [t["id"] for t in self.list_project(self.project_beta)],
            [self.other_id],
            "改名重名不得影响另一项目",
        )

    # ---------- 拒绝路径：通用断言 ----------

    def assert_rejected(self, cli_args, reason=None, before=None):
        """断言请求被拒绝：退出码 2、stdout 为空、stderr 有说明且无回溯，
        并且失败前后两个项目的任务完整列表完全一致。"""
        if before is None:
            before = self.snapshot()
        proc = self.run_cli(*cli_args)
        after = self.snapshot()
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 2、stdout 为空、stderr 说明拒绝原因"
                + (f"（应包含 {reason!r}）" if reason else "")
                + "，两个项目的任务列表保持不变"
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
            f"被拒绝的请求不得改动任何任务：\n{detail}\n"
            f"失败前={before!r}\n失败后={after!r}",
        )
        return proc

    # ---------- 空 / 纯空白标题 ----------

    def test_empty_and_whitespace_titles_rejected(self):
        before = self.snapshot()
        for raw_title in ("", "   ", "\t", "\n \t "):
            with self.subTest(raw_title=raw_title):
                self.assert_rejected(
                    ("task-rename", f"000{self.target_id}", raw_title),
                    reason="title",
                    before=before,
                )

    # ---------- 缺少必要参数 ----------

    def test_missing_arguments_rejected(self):
        before = self.snapshot()
        for cli_args in (
            ("task-rename",),
            ("task-rename", f"000{self.target_id}"),
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

    # ---------- 非法任务标识 ----------

    def test_invalid_task_identifiers_rejected(self):
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
                    ("task-rename", raw_id, "should not be saved"),
                    reason=reason,
                    before=before,
                )

    # ---------- 存储失败：退出码 1 ----------

    def test_database_path_being_a_directory_is_storage_failure(self):
        # 以一个已存在的目录作为数据库文件：SQLite 无法打开，应退出码 1，
        # stdout 为空、stderr 说明存储失败，且真正的临时库数据不发生变化
        dir_path = os.path.join(self._tmpdir.name, "cannot_be_a_db")
        os.mkdir(dir_path)
        before = self.snapshot()

        cli_args = ("task-rename", str(self.target_id), "should not be saved")
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

#!/usr/bin/env python3
"""task-rename 任务改名的命令行回归（验收）测试。

成功路径：在两个项目中各准备同标题任务（目标项目内另有一条同标题任务），
把目标任务置为 doing 后，用带前导零的同值任务标识把标题改为
"  修复  API_100%'  "。验证保存后的标题为 "修复  API_100%'"（内部双空格、
大小写、中文、%、_、单引号原样保留），标识、所属项目与 doing 状态不变；
另一项目与同项目其他任务的完整对象、任务数量与标识排序均不变；按新标题
关键词能查到目标任务，按旧标题只剩未改名任务。再次提交仅首尾空白不同的
同一标题仍成功且不新增记录；改成另一条任务的标题也成功（允许重名）。

拒绝路径：空字符串/纯空白新标题、缺少必要参数、任务标识为 0、负数、非数字、
超出 SQLite 整数上限（9223372036854775808）或范围内不存在的标识，均退出码
2、标准输出为空、标准错误说明原因，且两个项目的任务完整列表不变。数据库
路径指向已有目录时退出码 1、标准输出为空、标准错误说明存储失败。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品：用
project-create / task-create / task-move 准备数据，用 task-rename 改名，
用 task-list（含 --query）查询验证。不直接读写数据库，不调用产品内部
函数，不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用
者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_rename.py                    # 运行全部回归用例
    python3 -m unittest test_task_rename -v        # 等价写法
    python3 test_task_rename.py \
        TaskRenameRegression.test_rename_success_persists_and_isolates

退出码：全部通过为 0，存在失败为 1。失败信息会给出输入参数、实际退出码、
标准输出、标准错误以及预期结果，不依赖 JSON 键顺序或错误文案逐字一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TASK_FIELDS = {"id", "project_id", "title", "status"}

# 目标任务与两条对照任务共用的原始标题，用于确认同标题任务不会被连带改动
SHARED_TITLE = "Sync design doc"

# 新标题：首尾空白应被去除，内部双空格、大小写、中文、%、_、单引号原样保留
NEW_TITLE_RAW = "  修复  API_100%'  "
NEW_TITLE = "修复  API_100%'"

# SQLite 有符号 64 位整数上限 + 1，超出支持范围
OUT_OF_RANGE_ID = "9223372036854775808"


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
        """组装失败定位信息：输入参数、退出码、标准输出、标准错误与预期。"""
        lines = [
            f"输入参数={list(cli_args)!r}",
            f"实际退出码={proc.returncode}",
            f"实际标准输出={proc.stdout!r}",
            f"实际标准错误={proc.stderr!r}",
            f"预期结果={expected!r}",
        ]
        return "\n".join(lines)

    def list_project(self, project_id, query=None):
        """通过公开 task-list 命令查询项目任务，返回任务对象列表。"""
        cli_args = ["task-list", str(project_id)]
        if query is not None:
            cli_args += ["--query", query]
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

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目一：先建目标任务 T，再建同标题任务 S（列表顺序即 T、S）
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

        # 把目标任务置为 doing，验证改名不改变状态
        moved = self.cli_json("task-move", str(self.target_id), "doing")
        self.assertEqual(moved["status"], "doing")

    def expected_target_object(self, title):
        return {
            "id": self.target_id,
            "project_id": self.project_alpha,
            "title": title,
            "status": "doing",
        }

    # ---------- 成功路径 ----------

    def test_rename_success_persists_and_isolates(self):
        """带前导零标识改名成功：只改目标任务标题，其余数据不变。"""
        before = self.snapshot()

        # 用带前导零的同值任务标识提交改名
        padded_id = "000" + str(self.target_id)
        cli_args = ("task-rename", padded_id, NEW_TITLE_RAW)
        proc = self.run_cli(*cli_args)
        expected_obj = self.expected_target_object(NEW_TITLE)
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 0、stderr 为空、stdout 只有一个 JSON 任务对象，"
                f"对象为 {expected_obj!r}（字段集合 {sorted(TASK_FIELDS)}），"
                "标题已去除首尾空白并保留内部字符；再次查询结果一致，"
                "其他任务、任务数量与标识排序不变"
            ),
        )

        # 成功：退出码 0、标准错误为空
        self.assertEqual(proc.returncode, 0, f"改名应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功改名时标准错误应为空：\n{detail}")

        # 标准输出只有一个 JSON 任务对象，字段仍为 id/project_id/title/status
        try:
            renamed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"改名结果的 stdout 不是合法 JSON：\n{detail}")
        self.assertIsInstance(
            renamed, dict, f"改名结果应为单个 JSON 对象而非数组：\n{detail}"
        )
        self.assertEqual(
            set(renamed.keys()), TASK_FIELDS,
            f"改名结果对象字段结构不符：\n{detail}",
        )
        # 标题已保存为新值（首尾空白去除、内部字符原样保留），
        # 标识、所属项目与 doing 状态不变
        self.assertEqual(renamed, expected_obj, f"改名结果对象内容不符：\n{detail}")

        # 以另一次命令行调用查询同一数据库：目标对象应与改名结果一致（已落库）
        after = self.snapshot()
        alpha_after = after[self.project_alpha]
        persisted = [obj for obj in alpha_after if obj["id"] == self.target_id]
        self.assertEqual(
            len(persisted), 1,
            f"查询结果中目标任务应恰好出现一次：\n{detail}\n"
            f"改名后项目一列表={alpha_after!r}",
        )
        self.assertEqual(
            persisted[0], renamed,
            f"再次查询到的目标任务对象应与改名结果完全一致（结果已保存）：\n"
            f"{detail}\n查询所得={persisted[0]!r}",
        )

        # 只有指定标识的任务可能改变：项目一预期列表仅替换目标对象
        expected_alpha = [
            expected_obj if obj["id"] == self.target_id else obj
            for obj in before[self.project_alpha]
        ]
        self.assertEqual(
            alpha_after, expected_alpha,
            f"项目一中除目标任务外的其他任务不得改变：\n{detail}\n"
            f"改名前={before[self.project_alpha]!r}",
        )
        # 任务数量与标识排序保持不变
        self.assertEqual(
            [obj["id"] for obj in alpha_after],
            [obj["id"] for obj in before[self.project_alpha]],
            f"改名后项目一任务数量与标识排序不得改变：\n{detail}",
        )
        # 另一项目的同标题任务完整对象与列表保持不变
        self.assertEqual(
            after[self.project_beta], before[self.project_beta],
            f"另一项目的任务不得受改名影响：\n{detail}\n"
            f"改名前={before[self.project_beta]!r}",
        )

        # 目标项目按新标题关键词筛选能找到目标任务（内部双空格保留）
        hits_new = self.list_project(self.project_alpha, query="修复  API")
        self.assertEqual(
            hits_new, [expected_obj],
            f"按新标题关键词筛选应只命中改名后的目标任务：\n{detail}\n"
            f"实际命中={hits_new!r}",
        )
        # 按旧标题筛选只剩未改名的同标题任务
        hits_old = self.list_project(self.project_alpha, query=SHARED_TITLE)
        self.assertEqual(
            hits_old,
            [obj for obj in before[self.project_alpha]
             if obj["id"] != self.target_id],
            f"按旧标题筛选应只剩未改名的任务：\n{detail}\n实际命中={hits_old!r}",
        )

    def test_rename_same_title_whitespace_only_difference(self):
        """再次提交仅首尾空白不同的同一标题：成功、返回同一任务、不新增记录。"""
        first = self.run_cli("task-rename", str(self.target_id), NEW_TITLE_RAW)
        self.assertEqual(
            first.returncode, 0,
            f"首次改名应成功：\n{self._detail(('task-rename', str(self.target_id), NEW_TITLE_RAW), first)}",
        )
        before = self.snapshot()

        # 换一种首尾空白组合，去除首尾空白后与已保存标题相同
        cli_args = ("task-rename", str(self.target_id), "\t" + NEW_TITLE + " \n ")
        proc = self.run_cli(*cli_args)
        expected_obj = self.expected_target_object(NEW_TITLE)
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 0、stderr 为空、返回同一任务对象 "
                f"{expected_obj!r}，且不新增记录、两个项目列表完全不变"
            ),
        )
        self.assertEqual(proc.returncode, 0, f"重复改名应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"重复改名时标准错误应为空：\n{detail}")
        try:
            renamed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"重复改名结果的 stdout 不是合法 JSON：\n{detail}")
        self.assertEqual(
            renamed, expected_obj,
            f"重复改名应返回同一任务对象：\n{detail}",
        )

        # 不新增记录：两个项目的任务数量、对象与顺序与重复改名前完全一致
        after = self.snapshot()
        self.assertEqual(
            after, before,
            f"仅首尾空白不同的重复改名不得新增或改动任何记录：\n{detail}\n"
            f"改名前={before!r}",
        )

    def test_rename_to_existing_title_allows_duplicates(self):
        """改成另一条任务已使用的标题也成功：允许重名。"""
        before = self.snapshot()

        # SHARED_TITLE 已被同项目任务 S 与另一项目任务 O 使用
        cli_args = ("task-rename", str(self.target_id), SHARED_TITLE)
        proc = self.run_cli(*cli_args)
        expected_obj = self.expected_target_object(SHARED_TITLE)
        detail = self._detail(
            cli_args, proc,
            expected=(
                "退出码 0、stderr 为空、返回标题改为SHARED_TITLE的目标任务，"
                "项目一出现两条同标题任务，另一项目不变"
            ),
        )
        self.assertEqual(proc.returncode, 0, f"改成重名标题应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"改成重名标题时标准错误应为空：\n{detail}")
        try:
            renamed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"改名结果的 stdout 不是合法 JSON：\n{detail}")
        self.assertEqual(renamed, expected_obj, f"改名结果对象内容不符：\n{detail}")

        # 项目一现在有两条同标题任务，按标识升序，其余字段不变
        after = self.snapshot()
        expected_alpha = [
            expected_obj if obj["id"] == self.target_id else obj
            for obj in before[self.project_alpha]
        ]
        self.assertEqual(
            after[self.project_alpha], expected_alpha,
            f"项目一应出现两条同标题任务且其余对象不变：\n{detail}\n"
            f"改名前={before[self.project_alpha]!r}",
        )
        self.assertEqual(
            after[self.project_beta], before[self.project_beta],
            f"另一项目不得受改名影响：\n{detail}",
        )
        # 按该标题筛选能同时找到两条重名任务
        hits = self.list_project(self.project_alpha, query=SHARED_TITLE)
        self.assertEqual(
            hits, expected_alpha,
            f"按重名标题筛选应命中项目一全部两条任务：\n{detail}\n"
            f"实际命中={hits!r}",
        )

    # ---------- 拒绝路径（退出码 2，数据不变） ----------

    def _check_reject(self, cli_args, label, stderr_keyword=None):
        """非法输入：退出码 2、stdout 为空、stderr 说明原因、数据不变。"""
        before = self.snapshot()
        proc = self.run_cli(*cli_args)
        detail = self._detail(
            cli_args, proc,
            expected=(
                f"{label}：退出码 2、stdout 为空、stderr 说明原因"
                "且无 Python 异常回溯，两个项目的任务完整列表保持不变"
            ),
        )
        self.assertEqual(proc.returncode, 2, f"{label}应返回退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"{label}时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"{label}时标准错误应说明原因：\n{detail}",
        )
        if stderr_keyword is not None:
            # 不依赖错误文案逐字一致，但错误说明应指向问题所在
            self.assertIn(
                stderr_keyword, proc.stderr.lower(),
                f"{label}时标准错误应说明相应原因：\n{detail}",
            )
        self.assertNotIn(
            "Traceback", proc.stderr,
            f"{label}时标准错误不应包含 Python 异常回溯：\n{detail}",
        )

        # 失败后两个项目的任务完整列表（对象与顺序）与失败前完全一致
        after = self.snapshot()
        self.assertEqual(
            after, before,
            f"{label}失败后数据不得发生任何变化：\n{detail}\n失败前={before!r}",
        )

    def test_reject_empty_title(self):
        # 新标题为空字符串
        self._check_reject(
            ("task-rename", str(self.target_id), ""),
            "空字符串新标题", stderr_keyword="title",
        )

    def test_reject_whitespace_only_title(self):
        # 新标题为纯空白（去除首尾空白后为空）
        self._check_reject(
            ("task-rename", str(self.target_id), "  \t  "),
            "纯空白新标题", stderr_keyword="title",
        )

    def test_reject_missing_all_arguments(self):
        # 缺少任务标识与新标题
        self._check_reject(("task-rename",), "缺少全部必要参数")

    def test_reject_missing_title_argument(self):
        # 只有任务标识，缺少新标题
        self._check_reject(
            ("task-rename", str(self.target_id)), "缺少新标题参数"
        )

    def test_reject_zero_task_id(self):
        # 任务标识为 0：非正整数
        self._check_reject(
            ("task-rename", "0", NEW_TITLE), "任务标识为 0",
            stderr_keyword="task",
        )

    def test_reject_negative_task_id(self):
        # 任务标识为负数
        self._check_reject(
            ("task-rename", "-1", NEW_TITLE), "负数任务标识",
            stderr_keyword="task",
        )

    def test_reject_non_numeric_task_id(self):
        # 任务标识非数字
        self._check_reject(
            ("task-rename", "abc", NEW_TITLE), "非数字任务标识",
            stderr_keyword="task",
        )

    def test_reject_out_of_range_task_id(self):
        # 任务标识超出 SQLite 整数支持范围（上限 + 1）
        self._check_reject(
            ("task-rename", OUT_OF_RANGE_ID, NEW_TITLE), "超上限任务标识",
            stderr_keyword="task",
        )

    def test_reject_nonexistent_task_id(self):
        # 范围内但不存在的任务标识
        missing_id = str(self.other_id + 100)
        self._check_reject(
            ("task-rename", missing_id, NEW_TITLE), "不存在的任务标识",
            stderr_keyword="task",
        )

    # ---------- 存储失败（退出码 1） ----------

    def test_db_path_is_directory(self):
        """数据库路径指向已有目录：退出码 1、stdout 为空、stderr 说明存储失败。"""
        dir_path = os.path.join(self._tmpdir.name, "a-directory")
        os.mkdir(dir_path)

        cli_args = ("task-rename", str(self.target_id), NEW_TITLE)
        proc = self.run_cli(*cli_args, db_path=dir_path)
        detail = self._detail(
            ("--db", dir_path, *cli_args), proc,
            expected=(
                "数据库路径为已有目录：退出码 1、stdout 为空、"
                "stderr 说明存储失败且无 Python 异常回溯"
            ),
        )
        self.assertEqual(
            proc.returncode, 1, f"存储失败应返回退出码 1：\n{detail}"
        )
        self.assertEqual(proc.stdout, "", f"存储失败时标准输出应为空：\n{detail}")
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"存储失败时标准错误应说明原因：\n{detail}",
        )
        # 不依赖错误文案逐字一致，但错误说明应指向存储问题
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

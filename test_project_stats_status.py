#!/usr/bin/env python3
"""project-stats 可选 --status 状态筛选的命令行回归测试。

覆盖用户指定的验收场景与边界行为：

- 项目 1 有两条 doing 的 Fix API、一条 todo 的 Fix API 和一条 doing 的
  Fix api；项目 2 另有一条 doing 的 Fix API。``project-stats 1 --status
  doing --query API`` 恰为 {"project_id":1,"total":2,"todo":0,"doing":2,
  "done":0}；同条件的 task-list 恰好只列出这两条任务；项目 2 不受影响。
- --status 只接受 todo / doing / done 的原样拼写，大小写变化或带首尾空白
  均为非法状态；与 --query 同时使用时取交集，关键词沿用现有规则（去首尾
  空白、保留内部空白、大小写敏感的连续子串，中文、%、_、引号为普通字符，
  不匹配项目名称）。
- 带状态筛选时 total 等于命中任务数，所选状态的数量与 total 相等，另外
  两个状态的数量为 0；输出对象不增加字段。
- 省略 --status 时，现有全量统计和关键词统计的行为保持不变。
- 空项目或筛选无命中仍成功返回，四个数量均为 0；同标题任务按各自标识
  分别计数。
- 统计反映当前保存的标题与状态：移动、改名后按最新值计数，其他项目不受
  影响；重复查询结果一致，查询不改动任何项目与任务。
- 成功时退出码 0、stdout 为只含 project_id / total / todo / doing / done
  的单个 JSON 对象，stderr 为空。
- 错误路径：非法状态（含大小写变化、带首尾空白、空字符串）、--status
  缺值、空关键词、缺少项目标识、标识非正整数或超出 SQLite 整数范围、
  项目不存在，均退出码 2、stdout 为空、stderr 说明原因且无回溯，已有
  项目和任务不变；前导零按数值等价。
- 数据库无法打开时沿用退出码 1 的存储失败协议，stdout 为空、stderr 说明
  存储失败。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create / task-move / task-rename 四个现有命令，不直接
读写数据库，不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时文件），
可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_stats_status.py                  # 运行全部回归用例
    python3 -m unittest test_project_stats_status -v      # 等价写法
    python3 test_project_stats_status.py \\
        ProjectStatsStatusRegression.test_acceptance_scenario  # 只跑单个用例

退出码：全部通过为 0，存在失败为 1。失败信息会给出具体命令行输入、退出码、
标准输出、标准错误与预期结果，便于定位差异。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

SQLITE_MAX_INT = "9223372036854775807"      # 2^63 - 1：上限值本身合法
OVER_MAX = "9223372036854775808"            # 上限 + 1：越界
STATS_FIELDS = {"project_id", "total", "todo", "doing", "done"}


class ProjectStatsStatusRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-statss-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")

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
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"stdout 不是合法 JSON：{detail}")

    @staticmethod
    def _detail(cli_args, proc):
        return (
            f"输入参数={list(cli_args)!r}\n"
            f"退出码={proc.returncode}\n"
            f"标准输出={proc.stdout!r}\n"
            f"标准错误={proc.stderr!r}"
        )

    def assert_stats_ok(self, cli_args, expected):
        """断言 project-stats 成功并返回与 expected 完全一致的统计对象。"""
        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, f"预期成功退出码 0：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"标准输出不是合法 JSON（应只有一个 JSON 对象）：\n{detail}")
        self.assertEqual(data, expected, f"统计结果不符：\n{detail}")
        self.assertEqual(set(data.keys()), STATS_FIELDS, detail)
        for key in STATS_FIELDS:
            self.assertIsInstance(data[key], int, f"{key} 应为整数：\n{detail}")
            self.assertGreaterEqual(data[key], 0, f"{key} 应非负：\n{detail}")
        self.assertNotIsInstance(data["total"], bool, detail)
        self.assertEqual(
            data["todo"] + data["doing"] + data["done"], data["total"], detail
        )
        return data

    def assert_rejected(self, cli_args, db_path=None):
        """断言请求被拒绝：退出码 2、stdout 为空、stderr 有说明且无回溯。"""
        proc = self.run_cli(*cli_args, db_path=db_path)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 2, f"预期退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"被拒绝时标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"被拒绝时标准错误应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr,
                         f"不得出现 Python 异常回溯：\n{detail}")
        return proc

    def snapshot(self, *project_ids):
        """记录若干项目的完整任务列表与全量统计，用于校验查询无副作用。"""
        result = {}
        for pid in project_ids:
            result[pid] = (
                self.cli_json("task-list", str(pid)),
                self.cli_json("project-stats", str(pid)),
            )
        return result

    # ---------- 通过公开命令准备夹具 ----------

    def build_acceptance_fixture(self):
        """用户验收夹具：

        项目 1：Fix API(doing) × 2、Fix API(todo)、Fix api(doing)；
        项目 2：Fix API(doing)。返回两个项目标识与项目 1 四条任务标识。
        """
        p1 = self.cli_json("project-create", "p1")["id"]
        p2 = self.cli_json("project-create", "p2")["id"]
        doing1 = self.cli_json("task-create", str(p1), "Fix API")["id"]
        doing2 = self.cli_json("task-create", str(p1), "Fix API")["id"]
        todo1 = self.cli_json("task-create", str(p1), "Fix API")["id"]
        lower = self.cli_json("task-create", str(p1), "Fix api")["id"]
        other = self.cli_json("task-create", str(p2), "Fix API")["id"]
        for tid in (doing1, doing2, lower, other):
            self.cli_json("task-move", str(tid), "doing")
        return p1, p2, [doing1, doing2, todo1, lower], other

    # ---------- 用户指定的验收场景 ----------

    def test_acceptance_scenario(self):
        p1, p2, ids, other = self.build_acceptance_fixture()
        doing1, doing2, _todo1, _lower = ids

        # project-stats 1 --status doing --query API：恰为 total=2、doing=2
        self.assert_stats_ok(
            ["project-stats", str(p1), "--status", "doing", "--query", "API"],
            {"project_id": p1, "total": 2, "todo": 0, "doing": 2, "done": 0},
        )

        # 同条件的 task-list 恰好只列出这两条任务
        tasks = self.cli_json(
            "task-list", str(p1), "--status", "doing", "--query", "API")
        self.assertEqual(
            [(t["id"], t["title"], t["status"]) for t in tasks],
            [(doing1, "Fix API", "doing"), (doing2, "Fix API", "doing")],
        )

        # 项目 2 不受影响：其 doing 的 Fix API 只计入项目 2 自己
        self.assert_stats_ok(
            ["project-stats", str(p2), "--status", "doing", "--query", "API"],
            {"project_id": p2, "total": 1, "todo": 0, "doing": 1, "done": 0},
        )
        tasks2 = self.cli_json(
            "task-list", str(p2), "--status", "doing", "--query", "API")
        self.assertEqual([t["id"] for t in tasks2], [other])

    def test_move_then_rename_updates_stats(self):
        p1, p2, ids, _other = self.build_acceptance_fixture()
        doing1, _doing2, _todo1, _lower = ids
        # 移动其中一条命中任务到 done：doing 命中数随之减少
        self.cli_json("task-move", str(doing1), "done")
        self.assert_stats_ok(
            ["project-stats", str(p1), "--status", "doing", "--query", "API"],
            {"project_id": p1, "total": 1, "todo": 0, "doing": 1, "done": 0},
        )
        self.assert_stats_ok(
            ["project-stats", str(p1), "--status", "done", "--query", "API"],
            {"project_id": p1, "total": 1, "todo": 0, "doing": 0, "done": 1},
        )
        # 改名另一条命中任务使其不再含大写 API：doing 命中数再减
        self.cli_json("task-rename", str(_doing2), "Fixed")
        self.assert_stats_ok(
            ["project-stats", str(p1), "--status", "doing", "--query", "API"],
            {"project_id": p1, "total": 0, "todo": 0, "doing": 0, "done": 0},
        )
        # 其他项目不受影响
        self.assert_stats_ok(
            ["project-stats", str(p2), "--status", "doing", "--query", "API"],
            {"project_id": p2, "total": 1, "todo": 0, "doing": 1, "done": 0},
        )

    # ---------- 状态筛选本身的语义 ----------

    def test_each_status_selects_only_itself(self):
        p1 = self.cli_json("project-create", "p")["id"]
        t1 = self.cli_json("task-create", str(p1), "a")["id"]
        t2 = self.cli_json("task-create", str(p1), "b")["id"]
        t3 = self.cli_json("task-create", str(p1), "c")["id"]
        t4 = self.cli_json("task-create", str(p1), "d")["id"]
        self.cli_json("task-move", str(t2), "doing")
        self.cli_json("task-move", str(t3), "doing")
        self.cli_json("task-move", str(t4), "done")
        self.assert_stats_ok(
            ["project-stats", str(p1), "--status", "todo"],
            {"project_id": p1, "total": 1, "todo": 1, "doing": 0, "done": 0},
        )
        self.assert_stats_ok(
            ["project-stats", str(p1), "--status", "doing"],
            {"project_id": p1, "total": 2, "todo": 0, "doing": 2, "done": 0},
        )
        self.assert_stats_ok(
            ["project-stats", str(p1), "--status", "done"],
            {"project_id": p1, "total": 1, "todo": 0, "doing": 0, "done": 1},
        )

    def test_omit_status_keeps_original_scope(self):
        p1, _p2, _ids, _other = self.build_acceptance_fixture()
        # 不带 --status：全量统计与关键词统计行为与扩展前一致
        self.assert_stats_ok(
            ["project-stats", str(p1)],
            {"project_id": p1, "total": 4, "todo": 1, "doing": 3, "done": 0},
        )
        self.assert_stats_ok(
            ["project-stats", str(p1), "--query", "API"],
            {"project_id": p1, "total": 3, "todo": 1, "doing": 2, "done": 0},
        )

    def test_status_and_query_intersect_case_sensitively(self):
        p1, _p2, _ids, _other = self.build_acceptance_fixture()
        # doing + 小写 api：只命中 doing 的 Fix api
        self.assert_stats_ok(
            ["project-stats", str(p1), "--status", "doing", "--query", "api"],
            {"project_id": p1, "total": 1, "todo": 0, "doing": 1, "done": 0},
        )
        # todo + 大写 API：只命中 todo 的 Fix API
        self.assert_stats_ok(
            ["project-stats", str(p1), "--status", "todo", "--query", "API"],
            {"project_id": p1, "total": 1, "todo": 1, "doing": 0, "done": 0},
        )
        # done + API：无命中，四个数量均为 0
        self.assert_stats_ok(
            ["project-stats", str(p1), "--status", "done", "--query", "API"],
            {"project_id": p1, "total": 0, "todo": 0, "doing": 0, "done": 0},
        )

    def test_query_rules_unchanged_with_status(self):
        p1 = self.cli_json("project-create", "API team")["id"]
        titles = {"100%": "doing", "item_name": "doing", "Bob's task": "done",
                  "修复登录": "done", "plain": "todo"}
        for title, status in titles.items():
            tid = self.cli_json("task-create", str(p1), title)["id"]
            if status != "todo":
                self.cli_json("task-move", str(tid), status)
        # 特殊字符按普通字符处理，且与状态取交集
        for keyword, status, expected_status in (
            ("%", "doing", "doing"),
            ("_", "doing", "doing"),
            ("'", "done", "done"),
            ("登录", "done", "done"),
        ):
            with self.subTest(keyword=keyword, status=status):
                counts = {"todo": 0, "doing": 0, "done": 0}
                counts[expected_status] = 1
                self.assert_stats_ok(
                    ["project-stats", str(p1),
                     "--status", status, "--query", keyword],
                    {"project_id": p1, "total": 1, **counts},
                )
        # 关键词去首尾空白、保留内部空白
        self.assert_stats_ok(
            ["project-stats", str(p1),
             "--status", "doing", "--query", "  %  "],
            {"project_id": p1, "total": 1, "todo": 0, "doing": 1, "done": 0},
        )
        # 不匹配项目名称：项目名含 API，但标题不含时命中数为 0
        self.assert_stats_ok(
            ["project-stats", str(p1),
             "--status", "doing", "--query", "API"],
            {"project_id": p1, "total": 0, "todo": 0, "doing": 0, "done": 0},
        )

    def test_no_match_and_empty_project_return_all_zero(self):
        p1, _p2, _ids, _other = self.build_acceptance_fixture()
        # 筛选无命中：成功返回四个 0
        self.assert_stats_ok(
            ["project-stats", str(p1), "--status", "done"],
            {"project_id": p1, "total": 0, "todo": 0, "doing": 0, "done": 0},
        )
        # 存在但没有任务的项目：同样四个 0
        p3 = self.cli_json("project-create", "empty")["id"]
        self.assert_stats_ok(
            ["project-stats", str(p3), "--status", "doing"],
            {"project_id": p3, "total": 0, "todo": 0, "doing": 0, "done": 0},
        )

    def test_duplicate_titles_count_separately(self):
        p1 = self.cli_json("project-create", "dup")["id"]
        ids = [self.cli_json("task-create", str(p1), "same title")["id"]
               for _ in range(3)]
        for tid in ids:
            self.cli_json("task-move", str(tid), "doing")
        self.assert_stats_ok(
            ["project-stats", str(p1), "--status", "doing", "--query", "same"],
            {"project_id": p1, "total": 3, "todo": 0, "doing": 3, "done": 0},
        )

    # ---------- 只读与重复查询一致 ----------

    def test_repeated_queries_identical_and_read_only(self):
        p1, p2, _ids, _other = self.build_acceptance_fixture()
        before = self.snapshot(p1, p2)
        first = self.run_cli(
            "project-stats", str(p1), "--status", "doing", "--query", "API")
        for _ in range(3):
            again = self.run_cli(
                "project-stats", str(p1), "--status", "doing", "--query", "API")
            self.assertEqual(
                (again.returncode, again.stdout, again.stderr),
                (first.returncode, first.stdout, first.stderr),
            )
        # 查询前后两个项目的完整任务列表与全量统计完全不变
        self.assertEqual(self.snapshot(p1, p2), before)
        projects_once = self.cli_json("project-list")
        self.assertEqual(self.cli_json("project-list"), projects_once)

    def test_independent_process_reads_same_result(self):
        p1, _p2, _ids, _other = self.build_acceptance_fixture()
        proc = subprocess.run(
            [sys.executable, "-m", "kanban", "--db", self.db_path,
             "project-stats", str(p1), "--status", "doing", "--query", "API"],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        detail = self._detail(
            ("project-stats", str(p1), "--status", "doing", "--query", "API"),
            proc)
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(json.loads(proc.stdout), {
            "project_id": p1, "total": 2, "todo": 0, "doing": 2, "done": 0,
        })

    # ---------- 标识规则与错误路径 ----------

    def test_leading_zeros_are_equivalent(self):
        p1, _p2, _ids, _other = self.build_acceptance_fixture()
        expected = {
            "project_id": 1, "total": 2, "todo": 0, "doing": 2, "done": 0,
        }
        self.assert_stats_ok(
            ["project-stats", "0001", "--status", "doing", "--query", "API"],
            expected)
        self.assert_stats_ok(
            ["project-stats", "0" * 5000 + "1",
             "--status", "doing", "--query", "API"],
            expected)

    def test_invalid_status_rejected(self):
        p1, p2, _ids, _other = self.build_acceptance_fixture()
        before = self.snapshot(p1, p2)
        for raw in ("Doing", "DOING", "Todo", "DONE", " doing", "doing ",
                    "\ttodo\t", "to do", "in-progress", "", "0", "待办"):
            with self.subTest(raw=raw):
                proc = self.assert_rejected(
                    ["project-stats", "1", "--status", raw])
                self.assertIn("invalid status", proc.stderr)
        # 拒绝不改动任何项目与任务
        self.assertEqual(self.snapshot(p1, p2), before)

    def test_status_missing_value_rejected(self):
        self.build_acceptance_fixture()
        self.assert_rejected(["project-stats", "1", "--status"])

    def test_invalid_identifiers_rejected(self):
        self.build_acceptance_fixture()
        for raw in ("0", "0000", "-1", "abc", "1.5", "1e3", "+1", "",
                    " 1", "１", OVER_MAX, "9" * 5000,
                    "0" * 5000 + OVER_MAX):
            with self.subTest(raw=raw):
                proc = self.assert_rejected(
                    ["project-stats", raw, "--status", "doing"])
                # 拒绝前后全量统计不变
                self.assertEqual(json.loads(
                    self.run_cli("project-stats", "1").stdout), {
                        "project_id": 1, "total": 4,
                        "todo": 1, "doing": 3, "done": 0,
                })
                if raw in (OVER_MAX, "9" * 5000,
                           "0" * 5000 + OVER_MAX):
                    self.assertIn("range", proc.stderr.lower())

    def test_max_int_checked_by_existence(self):
        self.build_acceptance_fixture()
        for raw in (SQLITE_MAX_INT, "0" * 5000 + SQLITE_MAX_INT):
            with self.subTest(raw=f"<{len(raw)} chars>"):
                proc = self.assert_rejected(
                    ["project-stats", raw, "--status", "doing"])
                self.assertIn("does not exist", proc.stderr)

    def test_nonexistent_in_range_project_rejected(self):
        self.build_acceptance_fixture()
        proc = self.assert_rejected(
            ["project-stats", "999", "--status", "doing"])
        self.assertIn("does not exist", proc.stderr)

    def test_missing_arguments_rejected(self):
        self.build_acceptance_fixture()
        # 缺少项目标识
        self.assert_rejected(["project-stats", "--status", "doing"])
        # --query 缺值
        self.assert_rejected(
            ["project-stats", "1", "--status", "doing", "--query"])

    def test_empty_or_whitespace_query_rejected(self):
        self.build_acceptance_fixture()
        before = self.snapshot(1, 2)
        for raw in ("", "   ", "\t\t", " \t "):
            with self.subTest(raw=raw):
                self.assert_rejected(
                    ["project-stats", "1", "--status", "doing",
                     "--query", raw])
        # 拒绝不改动任何项目与任务
        self.assertEqual(self.snapshot(1, 2), before)

    # ---------- 存储失败 ----------

    def test_unopenable_database_is_storage_failure(self):
        dir_path = os.path.join(self._tmpdir.name, "cannot_be_a_db")
        os.mkdir(dir_path)
        proc = self.run_cli(
            "project-stats", "1", "--status", "doing", db_path=dir_path)
        detail = self._detail(
            ("project-stats", "1", "--status", "doing"), proc)
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)

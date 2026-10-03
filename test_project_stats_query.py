#!/usr/bin/env python3
"""project-stats 可选 --query 标题关键词筛选的命令行回归测试。

覆盖用户指定的验收场景与边界行为：

- 项目 1 有两条标题为 Fix API 的任务（todo / doing 各一），另有 Fix api 与
  Docs 两条 done；项目 2 有一条 Fix API 的 done。``project-stats 1 --query API``
  恰为 {"project_id":1,"total":2,"todo":1,"doing":1,"done":0}；
  ``task-list 1 --query API`` 恰好返回统计对应的两条任务；项目 2 的同名任务
  不计入。
- 关键词语义与 task-list --query 完全一致：先去首尾空白、保留内部空白，
  大小写敏感的连续子串匹配；中文、%、_、引号均为普通字符；不匹配项目名称。
- 省略 --query 时统计范围与输出保持原样（项目全部任务）。
- 已有项目没有任务或关键词没有命中时仍成功返回，四个数量均为 0。
- 统计反映当前保存的标题与状态：改名、移动后按最新值计数；重复查询结果一致，
  查询不改动任何项目与任务。
- 成功时退出码 0、stdout 为只含 project_id / total / todo / doing / done 的
  单个 JSON 对象，三状态数量之和等于 total，stderr 为空。
- 错误路径：空字符串或纯空白关键词、--query 缺值、缺少项目标识、标识非正整数
  或超出 SQLite 整数范围、项目不存在，均退出码 2、stdout 为空、stderr 说明
  原因；前导零按数值等价，范围内但不存在按项目不存在处理。
- 数据库无法打开时沿用退出码 1 的存储失败协议，stdout 为空、stderr 说明
  存储失败。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create / task-move / task-rename 四个现有命令，不直接
读写数据库，不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时文件），
可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_project_stats_query.py                  # 运行全部回归用例
    python3 -m unittest test_project_stats_query -v      # 等价写法
    python3 test_project_stats_query.py \\
        ProjectStatsQueryRegression.test_acceptance_scenario  # 只跑单个用例

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


class ProjectStatsQueryRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-statsq-")
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

        项目 1：Fix API(todo)、Fix API(doing)、Fix api(done)、Docs(done)；
        项目 2：Fix API(done)。返回两个项目标识与四条项目 1 任务标识。
        """
        p1 = self.cli_json("project-create", "p1")["id"]
        p2 = self.cli_json("project-create", "p2")["id"]
        fix1 = self.cli_json("task-create", str(p1), "Fix API")["id"]
        fix2 = self.cli_json("task-create", str(p1), "Fix API")["id"]
        fix_lower = self.cli_json("task-create", str(p1), "Fix api")["id"]
        docs = self.cli_json("task-create", str(p1), "Docs")["id"]
        self.cli_json("task-move", str(fix2), "doing")
        self.cli_json("task-move", str(fix_lower), "done")
        self.cli_json("task-move", str(docs), "done")
        other = self.cli_json("task-create", str(p2), "Fix API")["id"]
        self.cli_json("task-move", str(other), "done")
        return p1, p2, [fix1, fix2, fix_lower, docs], other

    # ---------- 用户指定的验收场景 ----------

    def test_acceptance_scenario(self):
        p1, p2, ids, _other = self.build_acceptance_fixture()
        fix1, fix2, _fix_lower, _docs = ids

        # project-stats 1 --query API：只命中两条大写 Fix API
        self.assert_stats_ok(
            ["project-stats", str(p1), "--query", "API"],
            {"project_id": p1, "total": 2, "todo": 1, "doing": 1, "done": 0},
        )

        # task-list 1 --query API 恰好返回统计对应的两条任务
        tasks = self.cli_json("task-list", str(p1), "--query", "API")
        self.assertEqual(
            [(t["id"], t["title"], t["status"]) for t in tasks],
            [(fix1, "Fix API", "todo"), (fix2, "Fix API", "doing")],
        )

        # 项目 2 的同名 done 任务不串入项目 1；项目 2 自身查询只统计它自己
        self.assert_stats_ok(
            ["project-stats", str(p2), "--query", "API"],
            {"project_id": p2, "total": 1, "todo": 0, "doing": 0, "done": 1},
        )

    def test_omit_query_keeps_original_scope(self):
        p1, _p2, ids, _other = self.build_acceptance_fixture()
        # 不带 --query：仍是项目全部 4 条任务的统计，行为与扩展前一致
        self.assert_stats_ok(
            ["project-stats", str(p1)],
            {"project_id": p1, "total": 4, "todo": 1, "doing": 1, "done": 2},
        )

    # ---------- 关键词语义与 task-list 一致 ----------

    def test_case_sensitive_substring(self):
        p1, _p2, _ids, _other = self.build_acceptance_fixture()
        # 小写 api 只命中 Fix api（done），不命中大写 Fix API
        self.assert_stats_ok(
            ["project-stats", str(p1), "--query", "api"],
            {"project_id": p1, "total": 1, "todo": 0, "doing": 0, "done": 1},
        )

    def test_trims_surrounding_whitespace_but_keeps_internal(self):
        p1, _p2, _ids, _other = self.build_acceptance_fixture()
        # "  API  " 去首尾空白后等价 API
        self.assert_stats_ok(
            ["project-stats", str(p1), "--query", "  API  "],
            {"project_id": p1, "total": 2, "todo": 1, "doing": 1, "done": 0},
        )
        # 内部单空格连续子串：只命中 "Fix API" 两条
        self.assert_stats_ok(
            ["project-stats", str(p1), "--query", "Fix API"],
            {"project_id": p1, "total": 2, "todo": 1, "doing": 1, "done": 0},
        )

    def test_special_characters_are_literal(self):
        p1 = self.cli_json("project-create", "lit")["id"]
        titles = {"100%": "todo", "item_name": "doing", "Bob's task": "done",
                  "修复登录": "done", "plain": "todo"}
        for title, status in titles.items():
            tid = self.cli_json("task-create", str(p1), title)["id"]
            if status != "todo":
                self.cli_json("task-move", str(tid), status)
        for keyword, expected_total, expected_status in (
            ("%", 1, "todo"),
            ("_", 1, "doing"),
            ("'", 1, "done"),
            ("登录", 1, "done"),
        ):
            with self.subTest(keyword=keyword):
                counts = {"todo": 0, "doing": 0, "done": 0}
                counts[expected_status] = 1
                self.assert_stats_ok(
                    ["project-stats", str(p1), "--query", keyword],
                    {"project_id": p1, "total": expected_total, **counts},
                )

    def test_query_matches_title_not_project_name(self):
        # 项目名称含 API，但没有任何标题含 API：统计必须全为 0
        p1 = self.cli_json("project-create", "API team")["id"]
        self.cli_json("task-create", str(p1), "Docs")
        self.assert_stats_ok(
            ["project-stats", str(p1), "--query", "API"],
            {"project_id": p1, "total": 0, "todo": 0, "doing": 0, "done": 0},
        )

    def test_no_match_and_empty_project_return_all_zero(self):
        p1, _p2, _ids, _other = self.build_acceptance_fixture()
        # 关键词没有命中：成功返回四个 0
        self.assert_stats_ok(
            ["project-stats", str(p1), "--query", "NO_SUCH_KEYWORD"],
            {"project_id": p1, "total": 0, "todo": 0, "doing": 0, "done": 0},
        )
        # 存在但没有任务的项目：同样四个 0
        p3 = self.cli_json("project-create", "empty")["id"]
        self.assert_stats_ok(
            ["project-stats", str(p3), "--query", "API"],
            {"project_id": p3, "total": 0, "todo": 0, "doing": 0, "done": 0},
        )

    def test_duplicate_titles_count_separately(self):
        p1 = self.cli_json("project-create", "dup")["id"]
        for _ in range(3):
            self.cli_json("task-create", str(p1), "same title")
        self.cli_json("task-create", str(p1), "different")
        self.assert_stats_ok(
            ["project-stats", str(p1), "--query", "same"],
            {"project_id": p1, "total": 3, "todo": 3, "doing": 0, "done": 0},
        )

    # ---------- 改名 / 移动后按最新值计数、重复查询只读一致 ----------

    def test_stats_reflect_rename_and_move(self):
        p1, _p2, ids, _other = self.build_acceptance_fixture()
        fix1, _fix2, fix_lower, _docs = ids
        # 把 todo 的 "Fix API" 改名移出命中集合：大写 API 只剩 doing 的一条
        self.cli_json("task-rename", str(fix1), "Fixed")
        self.assert_stats_ok(
            ["project-stats", str(p1), "--query", "API"],
            {"project_id": p1, "total": 1, "todo": 0, "doing": 1, "done": 0},
        )
        # done 的 "Fix api" 改名为大写 "Fix API"：命中集合变为 doing/done 各一
        self.cli_json("task-rename", str(fix_lower), "Fix API")
        self.assert_stats_ok(
            ["project-stats", str(p1), "--query", "API"],
            {"project_id": p1, "total": 2, "todo": 0, "doing": 1, "done": 1},
        )
        # 移动 doing 那条到 done：两条命中均为 done
        self.cli_json("task-move", str(_fix2), "done")
        self.assert_stats_ok(
            ["project-stats", str(p1), "--query", "API"],
            {"project_id": p1, "total": 2, "todo": 0, "doing": 0, "done": 2},
        )

    def test_repeated_queries_identical_and_read_only(self):
        p1, p2, _ids, _other = self.build_acceptance_fixture()
        before = self.snapshot(p1, p2)
        first = self.run_cli("project-stats", str(p1), "--query", "API")
        for _ in range(3):
            again = self.run_cli("project-stats", str(p1), "--query", "API")
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
             "project-stats", str(p1), "--query", "API"],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        detail = self._detail(
            ("project-stats", str(p1), "--query", "API"), proc)
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(json.loads(proc.stdout), {
            "project_id": p1, "total": 2, "todo": 1, "doing": 1, "done": 0,
        })

    # ---------- 标识规则与错误路径 ----------

    def test_leading_zeros_are_equivalent(self):
        p1, _p2, _ids, _other = self.build_acceptance_fixture()
        expected = {
            "project_id": 1, "total": 2, "todo": 1, "doing": 1, "done": 0,
        }
        self.assert_stats_ok(["project-stats", "0001", "--query", "API"],
                             expected)
        self.assert_stats_ok(
            ["project-stats", "0" * 5000 + "1", "--query", "API"], expected)

    def test_invalid_identifiers_rejected(self):
        self.build_acceptance_fixture()
        for raw in ("0", "0000", "-1", "abc", "1.5", "1e3", "+1", "",
                    " 1", "１", OVER_MAX, "9" * 5000,
                    "0" * 5000 + OVER_MAX):
            with self.subTest(raw=raw):
                proc = self.assert_rejected(
                    ["project-stats", raw, "--query", "API"])
                # 拒绝前后全量统计不变
                self.assertEqual(json.loads(
                    self.run_cli("project-stats", "1").stdout), {
                        "project_id": 1, "total": 4,
                        "todo": 1, "doing": 1, "done": 2,
                })
                if raw in (OVER_MAX, "9" * 5000,
                           "0" * 5000 + OVER_MAX):
                    self.assertIn("range", proc.stderr.lower())

    def test_max_int_checked_by_existence(self):
        self.build_acceptance_fixture()
        for raw in (SQLITE_MAX_INT, "0" * 5000 + SQLITE_MAX_INT):
            with self.subTest(raw=f"<{len(raw)} chars>"):
                proc = self.assert_rejected(
                    ["project-stats", raw, "--query", "API"])
                self.assertIn("does not exist", proc.stderr)

    def test_nonexistent_in_range_project_rejected(self):
        self.build_acceptance_fixture()
        proc = self.assert_rejected(
            ["project-stats", "999", "--query", "API"])
        self.assertIn("does not exist", proc.stderr)

    def test_missing_arguments_rejected(self):
        self.build_acceptance_fixture()
        # 缺少项目标识
        self.assert_rejected(["project-stats", "--query", "API"])
        # --query 缺值
        self.assert_rejected(["project-stats", "1", "--query"])

    def test_empty_or_whitespace_query_rejected(self):
        self.build_acceptance_fixture()
        before = self.snapshot(1, 2)
        for raw in ("", "   ", "\t\t", " \t "):
            with self.subTest(raw=raw):
                self.assert_rejected(
                    ["project-stats", "1", "--query", raw])
        # 拒绝不改动任何项目与任务
        self.assertEqual(self.snapshot(1, 2), before)

    # ---------- 存储失败 ----------

    def test_unopenable_database_is_storage_failure(self):
        dir_path = os.path.join(self._tmpdir.name, "cannot_be_a_db")
        os.mkdir(dir_path)
        proc = self.run_cli(
            "project-stats", "1", "--query", "API", db_path=dir_path)
        detail = self._detail(
            ("project-stats", "1", "--query", "API"), proc)
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)

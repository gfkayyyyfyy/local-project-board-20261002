#!/usr/bin/env python3
"""task-transfer 后组合筛选结果的命令行回归（验收）测试。

针对转移后的组合筛选：``--status todo --status doing --query " API "``
（两个状态取并集，再与标题关键词取交集），端到端验证任务跨项目转移前后
``task-list`` 与 ``project-stats`` 的命中结果，并以重复转移与拒绝转移确认
筛选结果稳定。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品：用
project-create / task-create / task-move 准备真实数据，用 task-transfer
执行转移，再用 task-list / project-stats / task-show / project-list 查询
验证结果已落库。不直接读写数据库，不调用产品内部函数，不依赖网络或第三方库，
也不依赖任何预先保存的数据。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时
文件），可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用者
自己的数据库。

固定夹具（每个用例独立构建，标识全部取自创建命令的返回值，不写死编号）：

- 源项目 source：
    - 两条标题为 ``Fix API`` 的任务，分别为 doing（转移目标）与 todo；
    - 一条 doing 的 ``Fix api``（小写 api，不应命中关键词）；
    - 一条 done 的 ``Docs``（状态与关键词都不应命中）。
- 目标项目 target：已有一条 todo 的 ``Fix API``。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_transfer_filters.py                  # 运行全部回归用例
    python3 -m unittest test_task_transfer_filters -v      # 等价写法
    python3 test_task_transfer_filters.py \\
        TaskTransferFilterRegression.test_acceptance_ownership_change_updates_combined_filter

退出码：全部通过为 0，存在失败为 1。失败信息会给出命令输入、实际退出码、
标准输出、标准错误与预期结果，不依赖 JSON 键顺序或错误文案逐字一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TITLE_API = "Fix API"
TITLE_LOWER_API = "Fix api"
TITLE_DOCS = "Docs"

# 组合筛选参数：todo/doing 并集，再与标题关键词取交集。
# 关键词首尾带空白，规范化后为大小写敏感的连续子串 "API"。
FILTER_ARGS = ["--status", "todo", "--status", "doing", "--query", " API "]

TASK_FIELDS = {"id", "project_id", "title", "status"}
STATS_FIELDS = {"project_id", "total", "todo", "doing", "done"}

# 正整数范围内、确定不存在的项目标识（夹具只有两个项目）
MISSING_PROJECT_ID = 999999


class TaskTransferFilterRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(
            prefix="kanban-transfer-filter-regtest-"
        )
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")
        self._build_fixture()

    def tearDown(self):
        self._tmpdir.cleanup()

    # ---------- 命令行与结果解析辅助 ----------

    def run_cli(self, *cli_args):
        """执行 python -m kanban --db <临时库> ...，返回 CompletedProcess。"""
        return subprocess.run(
            [sys.executable, "-m", "kanban", "--db", self.db_path, *cli_args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def _detail(self, cli_args, proc, expected=None):
        """组装失败定位信息：输入参数、退出码、实际输出与预期结果。"""
        return (
            f"输入参数={list(cli_args)!r}\n"
            f"实际退出码={proc.returncode}\n"
            f"实际标准输出={proc.stdout!r}\n"
            f"实际标准错误={proc.stderr!r}\n"
            f"预期结果={expected!r}"
        )

    def _load_single_json(self, proc, cli_args, expected):
        """要求 stdout 恰好包含一个可解析的 JSON 值（无多余内容）。"""
        detail = self._detail(cli_args, proc, expected)
        try:
            value, end = json.JSONDecoder().raw_decode(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"标准输出不是合法的单个 JSON 值：\n{detail}")
        if proc.stdout[end:].strip() != "":
            self.fail(f"标准输出在单个 JSON 值之后还有多余内容：\n{detail}")
        return value, detail

    def cli_json(self, *cli_args):
        """准备/修改数据用：要求退出码 0、stderr 为空，返回单个 JSON 值。"""
        proc = self.run_cli(*cli_args)
        value, detail = self._load_single_json(
            proc, cli_args, expected="退出码 0、stderr 为空、stdout 为单个 JSON 值"
        )
        self.assertEqual(proc.returncode, 0, f"命令应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        return value

    def task_obj(self, task_id, project_id, title, status):
        return {"id": task_id, "project_id": project_id,
                "title": title, "status": status}

    def stats_obj(self, project_id, total, todo, doing, done):
        return {"project_id": project_id, "total": total,
                "todo": todo, "doing": doing, "done": done}

    def filtered_list(self, project_id):
        """对指定项目执行组合筛选的 task-list，返回任务对象列表。"""
        cli_args = ("task-list", str(project_id), *FILTER_ARGS)
        proc = self.run_cli(*cli_args)
        data, detail = self._load_single_json(
            proc, cli_args,
            expected="退出码 0、stderr 为空、stdout 为单个 JSON 数组",
        )
        self.assertEqual(proc.returncode, 0, f"筛选查询应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"查询时标准错误应为空：\n{detail}")
        self.assertIsInstance(data, list, f"task-list 结果应为 JSON 数组：\n{detail}")
        for obj in data:
            self.assertIsInstance(
                obj, dict, f"列表元素应为 JSON 对象：\n{detail}",
            )
            self.assertEqual(
                set(obj.keys()), TASK_FIELDS,
                f"任务对象字段结构不符：\n{detail}",
            )
        return data

    def filtered_stats(self, project_id):
        """对指定项目执行组合筛选的 project-stats，返回统计对象。"""
        cli_args = ("project-stats", str(project_id), *FILTER_ARGS)
        proc = self.run_cli(*cli_args)
        data, detail = self._load_single_json(
            proc, cli_args,
            expected="退出码 0、stderr 为空、stdout 为单个统计 JSON 对象",
        )
        self.assertEqual(proc.returncode, 0, f"筛选统计应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"统计时标准错误应为空：\n{detail}")
        self.assertIsInstance(
            data, dict, f"project-stats 结果应为 JSON 对象：\n{detail}",
        )
        self.assertEqual(
            set(data.keys()), STATS_FIELDS,
            f"统计对象字段结构不符：\n{detail}",
        )
        self.assertEqual(
            data["todo"] + data["doing"] + data["done"], data["total"],
            f"各状态数量之和应等于 total：\n{detail}",
        )
        return data

    def full_list(self, project_id):
        """不带筛选的 task-list，返回项目全部任务。"""
        cli_args = ("task-list", str(project_id))
        proc = self.run_cli(*cli_args)
        data, detail = self._load_single_json(
            proc, cli_args, expected="退出码 0、stderr 为空、stdout 为 JSON 数组"
        )
        self.assertEqual(proc.returncode, 0, f"全量查询应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"查询时标准错误应为空：\n{detail}")
        self.assertIsInstance(data, list, f"全量列表应为 JSON 数组：\n{detail}")
        return data

    def show_task(self, task_id):
        """task-show 读取单条任务。"""
        cli_args = ("task-show", str(task_id))
        proc = self.run_cli(*cli_args)
        data, detail = self._load_single_json(
            proc, cli_args, expected="退出码 0、stderr 为空、stdout 为单个任务对象"
        )
        self.assertEqual(proc.returncode, 0, f"task-show 应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"task-show 时标准错误应为空：\n{detail}")
        self.assertIsInstance(data, dict, f"task-show 结果应为 JSON 对象：\n{detail}")
        self.assertEqual(
            set(data.keys()), TASK_FIELDS,
            f"task-show 对象字段结构不符：\n{detail}",
        )
        return data

    def project_names(self):
        """project-list 读取全部项目（按标识升序的 {id, name} 列表）。"""
        proc = self.run_cli("project-list")
        detail = self._detail(
            ("project-list",), proc, expected="退出码 0、stderr 为空"
        )
        self.assertEqual(proc.returncode, 0, f"项目查询应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"项目查询时标准错误应为空：\n{detail}")
        return json.loads(proc.stdout)

    def assert_tasks_ascending(self, tasks, cli_args):
        """列表必须按任务标识数值升序。"""
        ids = [obj["id"] for obj in tasks]
        self.assertEqual(
            ids, sorted(ids),
            f"列表应按任务标识升序：输入={list(cli_args)!r}，实际标识序列={ids!r}",
        )

    def filtered_view(self):
        """两个项目的组合筛选列表与统计（用于无副作用对比）。"""
        return {
            "src_list": self.filtered_list(self.src),
            "src_stats": self.filtered_stats(self.src),
            "dst_list": self.filtered_list(self.dst),
            "dst_stats": self.filtered_stats(self.dst),
        }

    def full_snapshot(self):
        """完整任务列表、组合筛选结果、项目名称与目标任务 task-show 的快照。"""
        return {
            "src_full": self.full_list(self.src),
            "dst_full": self.full_list(self.dst),
            "filtered": self.filtered_view(),
            "projects": self.project_names(),
            "moved_show": self.show_task(self.t_doing),
        }

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 源项目：先建 doing 的 Fix API（转移目标），再建 todo 的 Fix API，
        # 再建 doing 的 Fix api 与 done 的 Docs
        self.src = self.cli_json("project-create", "source")["id"]

        doing = self.cli_json("task-create", str(self.src), TITLE_API)
        self.cli_json("task-move", str(doing["id"]), "doing")
        self.t_doing = doing["id"]

        todo = self.cli_json("task-create", str(self.src), TITLE_API)
        self.t_todo = todo["id"]  # 保持 todo

        lower = self.cli_json("task-create", str(self.src), TITLE_LOWER_API)
        self.cli_json("task-move", str(lower["id"]), "doing")
        self.t_lower = lower["id"]

        docs = self.cli_json("task-create", str(self.src), TITLE_DOCS)
        self.cli_json("task-move", str(docs["id"]), "done")
        self.t_docs = docs["id"]

        # 目标项目：已有一条 todo 的 Fix API
        self.dst = self.cli_json("project-create", "target")["id"]
        existing = self.cli_json("task-create", str(self.dst), TITLE_API)
        self.t_existing = existing["id"]  # 保持 todo

    def _assert_excluded_tasks_absent(self, tasks, project_label):
        """小写 Fix api 与 Docs 绝不进入组合筛选结果。"""
        ids = {obj["id"] for obj in tasks}
        self.assertNotIn(
            self.t_lower, ids,
            f"{project_label} 筛选结果不应包含小写 api 的 Fix api：{tasks!r}",
        )
        self.assertNotIn(
            self.t_docs, ids,
            f"{project_label} 筛选结果不应包含 done 的 Docs：{tasks!r}",
        )

    # ---------- 固定场景：归属变化前后的组合筛选结果 ----------

    def test_acceptance_ownership_change_updates_combined_filter(self):
        # ---- 转移前 ----
        src_list_before = self.filtered_list(self.src)
        src_stats_before = self.filtered_stats(self.src)
        dst_list_before = self.filtered_list(self.dst)
        dst_stats_before = self.filtered_stats(self.dst)

        # 源项目命中两条 doing/todo 的 Fix API，按标识升序
        expected_src_before = [
            self.task_obj(self.t_doing, self.src, TITLE_API, "doing"),
            self.task_obj(self.t_todo, self.src, TITLE_API, "todo"),
        ]
        expected_src_before.sort(key=lambda obj: obj["id"])
        self.assertEqual(
            src_list_before, expected_src_before,
            "转移前源项目应命中两条 Fix API（doing + todo），按标识升序，"
            "小写 api 与 Docs 不进入结果",
        )
        self.assert_tasks_ascending(
            src_list_before, ("task-list", str(self.src), *FILTER_ARGS)
        )
        # 同标题任务各自保留：两条同名、不同标识
        self.assertEqual(
            [obj["title"] for obj in src_list_before].count(TITLE_API), 2
        )
        self.assertEqual(
            len({obj["id"] for obj in src_list_before}), len(src_list_before)
        )
        self._assert_excluded_tasks_absent(src_list_before, "源项目")
        self.assertEqual(
            src_stats_before,
            self.stats_obj(self.src, total=2, todo=1, doing=1, done=0),
            f"转移前源项目统计应为 total=2/todo=1/doing=1/done=0："
            f"{src_stats_before!r}",
        )

        # 目标项目只命中原有的一条 todo
        self.assertEqual(
            dst_list_before,
            [self.task_obj(self.t_existing, self.dst, TITLE_API, "todo")],
            f"转移前目标项目应只命中一条 todo 的 Fix API：{dst_list_before!r}",
        )
        self.assertEqual(
            dst_stats_before,
            self.stats_obj(self.dst, total=1, todo=1, doing=0, done=0),
            f"转移前目标项目统计应为 total=1/todo=1：{dst_stats_before!r}",
        )

        # 健全性：不带筛选时源项目确有四条任务（小写 api 与 Docs 确实存在，
        # 只是不进入组合筛选结果）
        self.assertEqual(
            sorted(obj["id"] for obj in self.full_list(self.src)),
            sorted([self.t_doing, self.t_todo, self.t_lower, self.t_docs]),
        )

        # ---- 执行转移：源项目 doing 的 Fix API -> 目标项目 ----
        transfer_args = ("task-transfer", str(self.t_doing), str(self.dst))
        proc = self.run_cli(*transfer_args)
        moved, detail = self._load_single_json(
            proc, transfer_args,
            expected=(
                "退出码 0、stderr 为空、stdout 为单个任务对象，"
                f"id={self.t_doing}、标题 {TITLE_API!r}、状态 doing 不变，"
                f"仅 project_id 变为目标项目 {self.dst}"
            ),
        )
        self.assertEqual(proc.returncode, 0, f"转移应成功（退出码 0）：\n{detail}")
        self.assertEqual(proc.stderr, "", f"转移成功时标准错误应为空：\n{detail}")
        self.assertIsInstance(
            moved, dict, f"转移结果应为单个 JSON 对象而非数组：\n{detail}"
        )
        expected_moved = self.task_obj(
            self.t_doing, self.dst, TITLE_API, "doing"
        )
        self.assertEqual(
            set(moved.keys()), TASK_FIELDS,
            f"转移结果对象字段结构应与 task-show 一致：\n{detail}",
        )
        self.assertEqual(moved, expected_moved, f"转移只应改变所属项目：\n{detail}")

        # 转移响应与随后 task-show 读到的对象一致
        shown = self.show_task(self.t_doing)
        self.assertEqual(
            shown, moved,
            f"转移后的 task-show 应与转移响应完全一致：\n{detail}\n"
            f"task-show 所得={shown!r}",
        )

        # ---- 转移后 ----
        src_list_after = self.filtered_list(self.src)
        src_stats_after = self.filtered_stats(self.src)
        dst_list_after = self.filtered_list(self.dst)
        dst_stats_after = self.filtered_stats(self.dst)

        # 源项目只命中原来的 todo
        self.assertEqual(
            src_list_after,
            [self.task_obj(self.t_todo, self.src, TITLE_API, "todo")],
            f"转移后源项目应只命中原来的 todo Fix API：{src_list_after!r}",
        )
        self.assertEqual(
            src_stats_after,
            self.stats_obj(self.src, total=1, todo=1, doing=0, done=0),
            f"转移后源项目统计应为 total=1/todo=1：{src_stats_after!r}",
        )
        self._assert_excluded_tasks_absent(src_list_after, "源项目")

        # 目标项目命中两条：转入的 doing 与原有的 todo，按标识升序
        expected_dst_after = [
            self.task_obj(self.t_doing, self.dst, TITLE_API, "doing"),
            self.task_obj(self.t_existing, self.dst, TITLE_API, "todo"),
        ]
        expected_dst_after.sort(key=lambda obj: obj["id"])
        self.assertEqual(
            dst_list_after, expected_dst_after,
            f"转移后目标项目应命中两条 Fix API（doing + todo）："
            f"{dst_list_after!r}",
        )
        self.assert_tasks_ascending(
            dst_list_after, ("task-list", str(self.dst), *FILTER_ARGS)
        )
        self.assertEqual(
            [obj["title"] for obj in dst_list_after].count(TITLE_API), 2,
            "同标题任务应各自保留（转入任务与目标原有任务并存）",
        )
        self._assert_excluded_tasks_absent(dst_list_after, "目标项目")
        self.assertEqual(
            dst_stats_after,
            self.stats_obj(self.dst, total=2, todo=1, doing=1, done=0),
            f"转移后目标项目统计应为 total=2/todo=1/doing=1/done=0："
            f"{dst_stats_after!r}",
        )

        # 其他任务完整对象保持原值
        self.assertEqual(
            self.show_task(self.t_todo),
            self.task_obj(self.t_todo, self.src, TITLE_API, "todo"),
        )
        self.assertEqual(
            self.show_task(self.t_lower),
            self.task_obj(self.t_lower, self.src, TITLE_LOWER_API, "doing"),
        )
        self.assertEqual(
            self.show_task(self.t_docs),
            self.task_obj(self.t_docs, self.src, TITLE_DOCS, "done"),
        )
        self.assertEqual(
            self.show_task(self.t_existing),
            self.task_obj(self.t_existing, self.dst, TITLE_API, "todo"),
        )
        # 项目名称保持原值
        self.assertEqual(
            self.project_names(),
            [{"id": self.src, "name": "source"},
             {"id": self.dst, "name": "target"}],
        )
        # 不带筛选的完整列表：源项目剩三条、目标项目两条，均按标识升序
        expected_src_full = [
            self.task_obj(self.t_todo, self.src, TITLE_API, "todo"),
            self.task_obj(self.t_lower, self.src, TITLE_LOWER_API, "doing"),
            self.task_obj(self.t_docs, self.src, TITLE_DOCS, "done"),
        ]
        expected_src_full.sort(key=lambda obj: obj["id"])
        src_full = self.full_list(self.src)
        self.assertEqual(src_full, expected_src_full)
        self.assert_tasks_ascending(src_full, ("task-list", str(self.src)))
        dst_full = self.full_list(self.dst)
        self.assertEqual(dst_full, expected_dst_after)
        self.assert_tasks_ascending(dst_full, ("task-list", str(self.dst)))

        # 重复查询不改动数据：同样的筛选再查数次，结果逐字节一致，
        # 且完整快照前后相同
        snapshot = self.full_snapshot()
        for _ in range(3):
            again_list = self.run_cli(
                "task-list", str(self.src), *FILTER_ARGS
            )
            self.assertEqual(
                (again_list.returncode, again_list.stderr), (0, "")
            )
            self.assertEqual(again_list.stdout, json.dumps(
                src_list_after, ensure_ascii=False) + "\n")
            self.assertEqual(self.filtered_view(), snapshot["filtered"])
        self.assertEqual(self.full_snapshot(), snapshot)

    # ---------- 重复转移到当前所属项目：筛选结果不变 ----------

    def test_repeat_transfer_to_current_project_keeps_results(self):
        # 先完成 source -> target 的转移
        moved = self.cli_json(
            "task-transfer", str(self.t_doing), str(self.dst)
        )
        expected_moved = self.task_obj(
            self.t_doing, self.dst, TITLE_API, "doing"
        )
        self.assertEqual(moved, expected_moved)
        self.assertEqual(self.show_task(self.t_doing), expected_moved)

        before = self.full_snapshot()

        # 再次把同一任务转移到当前所属项目：成功返回原任务
        repeat_args = ("task-transfer", str(self.t_doing), str(self.dst))
        proc = self.run_cli(*repeat_args)
        repeated, detail = self._load_single_json(
            proc, repeat_args,
            expected=f"退出码 0、stderr 为空、stdout 仍为 {expected_moved!r}",
        )
        self.assertEqual(proc.returncode, 0, f"原地重复转移应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        self.assertEqual(
            repeated, expected_moved,
            f"重复转移应返回同一个原任务对象：\n{detail}",
        )
        self.assertEqual(
            self.show_task(self.t_doing), expected_moved,
            f"重复转移后 task-show 不应变化：\n{detail}",
        )

        # 组合筛选列表与统计不变，完整列表与项目名称也不变
        after = self.full_snapshot()
        self.assertEqual(
            after, before,
            f"重复转移后所有数据应保持不变：\n{detail}\n"
            f"之前={before!r}\n之后={after!r}",
        )

        # 再重复一次仍然稳定
        proc = self.run_cli(*repeat_args)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(json.loads(proc.stdout), expected_moved)
        self.assertEqual(self.full_snapshot(), before)

    # ---------- 转移到确定不存在的项目：拒绝且数据不变 ----------

    def test_transfer_to_nonexistent_project_rejected(self):
        # 任务已在目标项目中
        self.cli_json("task-transfer", str(self.t_doing), str(self.dst))

        before = self.full_snapshot()

        bad_args = ("task-transfer", str(self.t_doing), str(MISSING_PROJECT_ID))
        proc = self.run_cli(*bad_args)
        detail = self._detail(
            bad_args, proc,
            expected=(
                "退出码 2、stdout 为空、stderr 说明项目 "
                f"{MISSING_PROJECT_ID} 不存在且无 Python 回溯；"
                "前后完整任务列表、组合筛选列表与统计完全相同"
            ),
        )
        self.assertEqual(
            proc.returncode, 2,
            f"目标项目不存在应返回退出码 2：\n{detail}",
        )
        self.assertEqual(
            proc.stdout, "", f"拒绝时标准输出应为空：\n{detail}",
        )
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"拒绝时标准错误应说明原因：\n{detail}",
        )
        # 不逐字依赖错误文案：只要指出 project 与该标识即可
        self.assertIn(
            "project", proc.stderr.lower(),
            f"标准错误应说明是项目不存在：\n{detail}",
        )
        self.assertIn(
            str(MISSING_PROJECT_ID), proc.stderr,
            f"标准错误应包含不存在的项目标识 {MISSING_PROJECT_ID}：\n{detail}",
        )
        self.assertNotIn(
            "Traceback", proc.stderr,
            f"标准错误不应包含 Python 异常回溯：\n{detail}",
        )

        # 前后完整任务列表与组合筛选统计完全相同
        after = self.full_snapshot()
        self.assertEqual(
            after, before,
            f"拒绝转移后数据不得发生任何变化：\n{detail}\n"
            f"之前={before!r}\n之后={after!r}",
        )
        # 目标任务归属与状态不变
        self.assertEqual(
            self.show_task(self.t_doing),
            self.task_obj(self.t_doing, self.dst, TITLE_API, "doing"),
        )
        # 组合筛选结果仍为转移后的稳定值
        self.assertEqual(
            before["filtered"]["src_stats"],
            self.stats_obj(self.src, total=1, todo=1, doing=0, done=0),
        )
        self.assertEqual(
            before["filtered"]["dst_stats"],
            self.stats_obj(self.dst, total=2, todo=1, doing=1, done=0),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

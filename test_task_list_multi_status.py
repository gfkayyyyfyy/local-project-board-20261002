#!/usr/bin/env python3
"""task-list 可重复 --status 多状态并集筛选的命令行回归测试。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，数据准备仅使用
project-create / task-create / task-move 三个现有命令，不直接读写数据库，
不依赖网络，也不依赖任何预先保存的项目。

每个用例在独立的临时目录中使用全新的隔离 SQLite 数据库（--db 指向临时文件），
可单独执行、可重复执行，结果一致，结束后自动清理，不会接触使用者自己的数据库。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_list_multi_status.py                  # 运行全部回归用例
    python3 -m unittest test_task_list_multi_status -v      # 等价写法
    python3 test_task_list_multi_status.py \\
        TaskListMultiStatusRegression.test_two_statuses_union_with_query   # 只跑单个用例

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

# 项目一（alpha）内的四个标题，创建顺序即任务标识升序
ALPHA_TITLES = ["API准备", "API实现", "API归档", "文档整理"]
# 仅首尾保持 todo：API实现 -> doing，API归档 -> done
ALPHA_STATUSES = {
    "API实现": "doing",
    "API归档": "done",
}
TASK_FIELDS = {"id", "project_id", "title", "status"}


class TaskListMultiStatusRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-regtest-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")
        self.alpha_task_ids = {}
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

    def assert_task_list(self, cli_args, expected_titles, project_id=None):
        """断言 task-list 成功并恰好返回 expected_titles 对应的任务（含顺序）。

        expected_titles 中的标题必须属于 project_id（默认项目一）；
        预期对象由建数阶段的返回值构造，字段值与产品输出逐一比对。
        """
        if project_id is None:
            project_id = self.project_alpha
            id_map = self.alpha_task_ids
            status_map = ALPHA_STATUSES
        else:
            id_map = self.other_task_ids
            status_map = self.other_statuses

        proc = self.run_cli(*cli_args)
        detail = self._detail(cli_args, proc)
        self.assertEqual(proc.returncode, 0, f"预期成功退出码 0：\n{detail}")
        self.assertEqual(proc.stderr, "", f"成功时标准错误应为空：\n{detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"标准输出不是合法 JSON（应只有一个 JSON 数组）：\n{detail}")
        self.assertIsInstance(data, list, f"输出应为 JSON 数组：\n{detail}")

        expected = [
            {
                "id": id_map[title],
                "project_id": project_id,
                "title": title,
                "status": status_map.get(title, "todo"),
            }
            for title in expected_titles
        ]
        self.assertEqual(data, expected, f"返回任务集合或顺序不符：\n{detail}")

        for obj in data:
            self.assertIsInstance(obj, dict, f"数组元素应为任务对象：\n{detail}")
            self.assertEqual(
                set(obj.keys()), TASK_FIELDS,
                f"任务对象字段结构不符：\n{detail}",
            )
        ids = [obj["id"] for obj in data]
        self.assertEqual(ids, sorted(ids), f"结果应按任务标识升序：\n{detail}")

    def assert_usage_error(self, cli_args):
        """断言参数/业务校验失败：退出码 2、stdout 为空、stderr 有原因说明
        且无异常回溯，失败前后各项目任务集合与状态完全不变。"""
        before = self._snapshot_all_projects()
        proc = self.run_cli(*cli_args)
        after = self._snapshot_all_projects()
        detail = self._detail(cli_args, proc)

        self.assertEqual(proc.returncode, 2, f"预期参数错误退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"参数错误时标准输出应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "",
                            f"参数错误时标准错误应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr,
                         f"标准错误不应包含异常回溯：\n{detail}")
        self.assertEqual(after, before, f"参数错误不得改动已有任务：\n{detail}")

    def _snapshot_all_projects(self):
        """记录全部项目当前任务（对象集合），用于校验错误请求不产生副作用。"""
        snapshot = {}
        for pid in (self.project_alpha, self.project_beta, self.project_empty):
            snapshot[pid] = self.cli_json("task-list", str(pid))
        return snapshot

    # ---------- 通过公开命令准备夹具 ----------

    def _build_fixture(self):
        # 项目一：四个标题；API实现 -> doing，API归档 -> done，其余 todo
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        for title in ALPHA_TITLES:
            created = self.cli_json("task-create", str(self.project_alpha), title)
            self.alpha_task_ids[title] = created["id"]
        self.cli_json("task-move", str(self.alpha_task_ids["API实现"]), "doing")
        self.cli_json("task-move", str(self.alpha_task_ids["API归档"]), "done")

        # 项目二：同名 "API实现" 且为 doing，用于验证项目隔离与交集
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        beta_task = self.cli_json(
            "task-create", str(self.project_beta), "API实现"
        )
        self.cli_json("task-move", str(beta_task["id"]), "doing")
        self.other_task_ids = {"API实现": beta_task["id"]}
        self.other_statuses = {"API实现": "doing"}

        # 项目三：存在但没有任何任务
        self.project_empty = self.cli_json("project-create", "empty")["id"]

    # ---------- 多状态并集，再与项目范围及关键词取交集 ----------

    def test_two_statuses_union_with_query(self):
        # 输入 --status todo --status doing --query API：
        # 只返回项目一前两任务；done 的 API归档 被状态并集排除，
        # todo 的 文档整理 被关键词排除，项目二同名 doing 任务被项目范围排除
        self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--status", "todo", "--status", "doing", "--query", "API"],
            ["API准备", "API实现"],
        )

    def test_status_order_and_duplicates_do_not_change_result(self):
        # 交换状态顺序并追加重复状态，结果与上一用例完全相同
        self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--status", "doing", "--status", "todo", "--status", "todo",
             "--query", "API"],
            ["API准备", "API实现"],
        )

    def test_two_statuses_union_without_query(self):
        # 输入 --status doing --status done：并集命中两条，按任务标识升序、不分组
        self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--status", "doing", "--status", "done"],
            ["API实现", "API归档"],
        )

    def test_all_three_statuses_equal_no_status(self):
        # 三个状态全部传入与省略 --status 的结果一致
        self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--status", "done", "--status", "todo", "--status", "doing"],
            ALPHA_TITLES,
        )

    def test_duplicate_same_status_matches_single_status(self):
        # 重复传入同一状态与只传一次结果一致，任务不会重复返回
        self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--status", "doing", "--status", "doing"],
            ["API实现"],
        )

    def test_multi_status_scoped_to_project(self):
        # 项目二只有一条 doing 任务：多状态并集仍只命中本项目任务
        self.assert_task_list(
            ["task-list", str(self.project_beta),
             "--status", "todo", "--status", "doing"],
            ["API实现"],
            project_id=self.project_beta,
        )

    def test_multi_status_empty_project_returns_empty_array(self):
        # 空项目按多状态筛选：成功返回空数组
        self.assert_task_list(
            ["task-list", str(self.project_empty),
             "--status", "todo", "--status", "doing"],
            [],
            project_id=self.project_empty,
        )

    def test_multi_status_no_match_returns_empty_array(self):
        # 状态并集与关键词交集无命中：成功返回空数组
        self.assert_task_list(
            ["task-list", str(self.project_alpha),
             "--status", "doing", "--status", "done", "--query", "文档"],
            [],
        )

    def test_single_status_result_unchanged(self):
        # 只传一次 --status 时保持原有单状态筛选结果
        self.assert_task_list(
            ["task-list", str(self.project_alpha), "--status", "done"],
            ["API归档"],
        )

    def test_omit_status_result_unchanged(self):
        # 省略 --status 时仍返回项目全部状态的任务
        self.assert_task_list(
            ["task-list", str(self.project_alpha)],
            ALPHA_TITLES,
        )

    # ---------- 每次出现的状态都按原样校验，任一非法即拒绝整次查询 ----------

    def test_error_invalid_status_before_valid_one(self):
        # 非法状态在前、合法状态在后：仍拒绝整次查询
        self.assert_usage_error(
            ["task-list", str(self.project_alpha),
             "--status", "bogus", "--status", "todo"]
        )

    def test_error_invalid_status_after_valid_one(self):
        # 合法状态在前、非法状态在后：同样拒绝整次查询
        self.assert_usage_error(
            ["task-list", str(self.project_alpha),
             "--status", "todo", "--status", "Done"]
        )

    def test_error_status_case_sensitive(self):
        # 大小写变化（TODO）不是合法状态
        self.assert_usage_error(
            ["task-list", str(self.project_alpha), "--status", "TODO"]
        )

    def test_error_status_with_surrounding_whitespace(self):
        # 首尾空白不剔除，" todo" 不是合法状态
        self.assert_usage_error(
            ["task-list", str(self.project_alpha), "--status", " todo "]
        )

    def test_error_empty_status(self):
        # 空字符串不是合法状态
        self.assert_usage_error(
            ["task-list", str(self.project_alpha), "--status", ""]
        )

    def test_error_comma_joined_status(self):
        # "todo,doing" 不是合法状态：每次出现只接收一个状态
        self.assert_usage_error(
            ["task-list", str(self.project_alpha), "--status", "todo,doing"]
        )

    def test_error_status_missing_value(self):
        # --status 出现在末尾但未给值：参数错误
        self.assert_usage_error(
            ["task-list", str(self.project_alpha), "--status"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

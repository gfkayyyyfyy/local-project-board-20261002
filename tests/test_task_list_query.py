"""task-list 关键词筛选及其与状态筛选取交集的命令行回归测试。

通过 README 公开的入口 ``python -m kanban --db <临时数据库>`` 以子进程方式运行，
每个用例使用独立的临时 SQLite 数据库，数据仅通过 project-create / task-create /
task-move 准备，不依赖网络、预先保存的项目或使用者的数据库。

运行方式（仓库根目录下）：
    python -m unittest tests.test_task_list_query -v     # 全部用例
    python tests/test_task_list_query.py                 # 直接执行
    python tests/test_task_list_query.py -k special      # 按名称子串挑选用例
    python -m unittest tests.test_task_list_query.TaskListQueryTest.test_query_case_sensitive_substring
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 关键词用例共用的四个标题：大小写、内部空白与中文各不相同
FOUR_TITLES = ["Fix API", "fix api", "Fix  API", "修复登录"]


def run_kanban(db_path, *args):
    """以子进程运行 CLI，返回 CompletedProcess（不检查退出码）。"""
    return subprocess.run(
        [sys.executable, "-m", "kanban", "--db", str(db_path), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


class KanbanCase(unittest.TestCase):
    """每个用例一个独立的临时数据库，保证可单独、重复执行。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="kanban-test-")
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "kanban.db"

    # ---- 数据准备与断言辅助 ----

    def run_ok(self, *args):
        """断言成功契约：退出码 0、标准错误为空、标准输出是单个 JSON 值。"""
        proc = run_kanban(self.db, *args)
        self.assertEqual(
            proc.returncode, 0,
            f"期望退出码 0，实际 {proc.returncode}；输入: {args!r}；stderr: {proc.stderr!r}",
        )
        self.assertEqual(
            proc.stderr, "",
            f"成功时标准错误应为空；输入: {args!r}；stderr: {proc.stderr!r}",
        )
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"成功时标准输出应为可解析的 JSON；输入: {args!r}；stdout: {proc.stdout!r}")

    def run_error(self, *args, stderr_part=None):
        """断言参数错误契约：退出码 2、标准输出为空、标准错误说明原因。"""
        proc = run_kanban(self.db, *args)
        self.assertEqual(
            proc.returncode, 2,
            f"期望退出码 2，实际 {proc.returncode}；输入: {args!r}；stdout: {proc.stdout!r}",
        )
        self.assertEqual(
            proc.stdout, "",
            f"参数错误时标准输出应为空；输入: {args!r}；stdout: {proc.stdout!r}",
        )
        self.assertNotEqual(
            proc.stderr.strip(), "",
            f"参数错误时标准错误应说明原因；输入: {args!r}",
        )
        if stderr_part is not None:
            self.assertIn(
                stderr_part, proc.stderr,
                f"标准错误应包含 {stderr_part!r}；输入: {args!r}；stderr: {proc.stderr!r}",
            )
        return proc

    def create_project(self, name):
        return self.run_ok("project-create", name)["id"]

    def create_task(self, project_id, title):
        return self.run_ok("task-create", str(project_id), title)["id"]

    def move_task(self, task_id, status):
        self.run_ok("task-move", str(task_id), status)

    def list_tasks(self, project_id, *extra_args):
        result = self.run_ok("task-list", str(project_id), *extra_args)
        self.assertIsInstance(result, list, f"task-list 应输出 JSON 数组；输入: {extra_args!r}")
        return result

    def create_four_title_project(self):
        """创建含 Fix API / fix api / Fix  API / 修复登录 四个任务的项目。"""
        project_id = self.create_project("关键字项目")
        ids = {title: self.create_task(project_id, title) for title in FOUR_TITLES}
        return project_id, ids

    def assert_titles(self, tasks, expected_titles, context):
        """核对实际任务集合与顺序（按任务标识升序），不依赖 JSON 键序。"""
        actual_titles = [task["title"] for task in tasks]
        self.assertEqual(
            actual_titles, list(expected_titles),
            f"{context}：期望标题序列 {list(expected_titles)!r}，实际 {actual_titles!r}",
        )
        actual_ids = [task["id"] for task in tasks]
        self.assertEqual(
            actual_ids, sorted(actual_ids),
            f"{context}：结果应按任务标识升序，实际标识序列 {actual_ids!r}",
        )

    def assert_task_fields(self, task, project_id, title, status):
        """任务对象沿用现有结构与字段值（不依赖键序）。"""
        self.assertEqual(set(task), {"id", "project_id", "title", "status"})
        self.assertIsInstance(task["id"], int)
        self.assertEqual(task["project_id"], project_id)
        self.assertEqual(task["title"], title)
        self.assertEqual(task["status"], status)


class TaskListQueryTest(KanbanCase):
    """关键词匹配语义：大小写敏感的标题连续子串。"""

    def test_query_case_sensitive_substring(self):
        project_id, ids = self.create_four_title_project()

        tasks = self.list_tasks(project_id, "--query", "API")
        self.assert_titles(tasks, ["Fix API", "Fix  API"],
                           "查询 API 应只命中含大写 API 的两个标题")

        tasks = self.list_tasks(project_id, "--query", "Fix API")
        self.assert_titles(tasks, ["Fix API"],
                           "查询 Fix API 只匹配单空格标题，不匹配双空格标题")

        tasks = self.list_tasks(project_id, "--query", "登录")
        self.assert_titles(tasks, ["修复登录"],
                           "查询 登录 只匹配中文标题")

    def test_query_strips_surrounding_whitespace_keeps_inner(self):
        project_id, ids = self.create_four_title_project()

        tasks = self.list_tasks(project_id, "--query", "  API  ")
        self.assert_titles(tasks, ["Fix API", "Fix  API"],
                           "关键词首尾空白应被去除，等价于查询 API")

        tasks = self.list_tasks(project_id, "--query", " Fix  API ")
        self.assert_titles(tasks, ["Fix  API"],
                           "关键词内部空白保留，双空格只匹配双空格标题")

    def test_query_special_characters_are_literal(self):
        project_id = self.create_project("特殊字符项目")
        for title in ["100%", "item_name", "Bob's task"]:
            self.create_task(project_id, title)

        tasks = self.list_tasks(project_id, "--query", "%")
        self.assert_titles(tasks, ["100%"], "%% 应按普通字符匹配，只命中 100%%")

        tasks = self.list_tasks(project_id, "--query", "_")
        self.assert_titles(tasks, ["item_name"], "_ 应按普通字符匹配，只命中 item_name")

        tasks = self.list_tasks(project_id, "--query", "'")
        self.assert_titles(tasks, ["Bob's task"], "单引号应按普通字符匹配，只命中 Bob's task")

    def test_query_no_match_returns_empty_array(self):
        project_id, _ = self.create_four_title_project()
        tasks = self.list_tasks(project_id, "--query", "不存在的关键词")
        self.assertEqual(tasks, [], "无匹配标题时应返回空数组")

    def test_query_on_project_without_tasks_returns_empty_array(self):
        project_id = self.create_project("空项目")
        tasks = self.list_tasks(project_id, "--query", "API")
        self.assertEqual(tasks, [], "项目没有任务时应返回空数组")

    def test_success_output_contract(self):
        """成功查询：退出码 0、stderr 为空、单个 JSON 数组、字段结构与值正确。"""
        project_id, ids = self.create_four_title_project()
        tasks = self.list_tasks(project_id, "--query", "API")
        self.assertEqual(len(tasks), 2)
        self.assert_task_fields(tasks[0], project_id, "Fix API", "todo")
        self.assertEqual(tasks[0]["id"], ids["Fix API"])
        self.assert_task_fields(tasks[1], project_id, "Fix  API", "todo")
        self.assertEqual(tasks[1]["id"], ids["Fix  API"])


class TaskListQueryStatusIntersectionTest(KanbanCase):
    """--query 与 --status 取交集，且只作用于当前项目。"""

    def setUp(self):
        super().setUp()
        self.project_id, self.ids = self.create_four_title_project()
        # Fix API -> doing，Fix  API -> done，其余保持 todo
        self.move_task(self.ids["Fix API"], "doing")
        self.move_task(self.ids["Fix  API"], "done")
        # 另一项目中的同名 doing 任务，用于验证项目隔离
        self.other_project = self.create_project("其他项目")
        other_task = self.create_task(self.other_project, "Fix API")
        self.move_task(other_task, "doing")

    def test_query_intersects_status(self):
        tasks = self.list_tasks(self.project_id, "--query", "API", "--status", "doing")
        self.assert_titles(tasks, ["Fix API"],
                           "查询 API 且 --status doing 只返回当前项目的 Fix API")
        self.assertEqual(tasks[0]["project_id"], self.project_id)
        self.assertEqual(tasks[0]["status"], "doing")

    def test_query_intersects_other_statuses(self):
        tasks = self.list_tasks(self.project_id, "--query", "API", "--status", "done")
        self.assert_titles(tasks, ["Fix  API"], "查询 API 且 --status done")

        tasks = self.list_tasks(self.project_id, "--query", "API", "--status", "todo")
        self.assert_titles(tasks, [], "查询 API 且 --status todo 无交集，返回空数组")

    def test_omit_query_keeps_list_and_status_semantics(self):
        tasks = self.list_tasks(self.project_id)
        self.assert_titles(tasks, FOUR_TITLES, "省略 --query 与 --status 返回全部任务")

        tasks = self.list_tasks(self.project_id, "--status", "todo")
        self.assert_titles(tasks, ["fix api", "修复登录"],
                           "省略 --query 时 --status 仍按原语义筛选")

        tasks = self.list_tasks(self.project_id, "--status", "doing")
        self.assert_titles(tasks, ["Fix API"], "省略 --query 时 --status doing")

        other = self.list_tasks(self.other_project, "--status", "doing")
        self.assert_titles(other, ["Fix API"], "其他项目的同名任务不受影响")


class TaskListQueryErrorTest(KanbanCase):
    """非法输入：退出码 2、stdout 为空、stderr 说明原因、数据不变。"""

    def setUp(self):
        super().setUp()
        self.project_id, self.ids = self.create_four_title_project()
        self.move_task(self.ids["Fix API"], "doing")

    def assert_data_unchanged(self, *bad_args, stderr_part=None):
        before = self.list_tasks(self.project_id)
        self.run_error(*bad_args, stderr_part=stderr_part)
        after = self.list_tasks(self.project_id)
        self.assertEqual(
            before, after,
            f"错误输入不应改变已有任务及状态；输入: {bad_args!r}",
        )

    def test_empty_query_rejected(self):
        self.assert_data_unchanged(
            "task-list", str(self.project_id), "--query", "",
            stderr_part="query",
        )

    def test_whitespace_only_query_rejected(self):
        self.assert_data_unchanged(
            "task-list", str(self.project_id), "--query", "   ",
            stderr_part="query",
        )

    def test_missing_query_value_rejected(self):
        self.assert_data_unchanged(
            "task-list", str(self.project_id), "--query",
            stderr_part="--query",
        )

    def test_invalid_status_rejected(self):
        self.assert_data_unchanged(
            "task-list", str(self.project_id), "--query", "API", "--status", "archived",
            stderr_part="status",
        )

    def test_zero_project_id_rejected(self):
        self.assert_data_unchanged(
            "task-list", "0", "--query", "API",
            stderr_part="project id",
        )

    def test_non_numeric_project_id_rejected(self):
        self.assert_data_unchanged(
            "task-list", "abc", "--query", "API",
            stderr_part="project id",
        )

    def test_nonexistent_project_rejected(self):
        self.assert_data_unchanged(
            "task-list", "9999", "--query", "API",
            stderr_part="does not exist",
        )


if __name__ == "__main__":
    unittest.main()

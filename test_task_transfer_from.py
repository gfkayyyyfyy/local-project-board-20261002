#!/usr/bin/env python3
"""task-transfer --from 预期来源项目的命令行回归（验收）测试。

覆盖：

- 用户验收场景：两个项目（标识 1、2）各有一条同标题 ``整理 API`` 任务，
  项目 1 的任务 1 为 doing、项目 2 的任务 2 为 todo；
  ``task-transfer 1 2 --from 0001`` 成功（退出码 0、stderr 为空、stdout 为
  与 task-show 同结构的单个任务 JSON，仅 project_id 变为 2，标识/标题/状态
  原样），再次执行同一命令退出码 2、stdout 为空、stderr 同时指出当前项目 2
  与预期项目 1；任务 1 仍属于项目 2，任务 2 与两个项目名称不变。
- 成功转移后，task-show、task-list、project-stats 均按新归属返回或统计；
  重复失败不改变任何查询结果。
- 即使目标项目就是当前所属项目，仍检查来源：来源相符则成功返回原任务、
  不新增记录；来源不符则拒绝且不写库。
- 来源标识合法但与当前归属不同时拒绝；合法却不存在的预期来源同样按归属
  不匹配拒绝，且不会创建该项目；不复制任务、不改变其他任务与项目名称。
- ``--from`` 只接受正整数的 ASCII 数字拼写：空字符串、纯空白、零、负数、
  非数字、越界（9223372036854775808）及 ``--from`` 缺值均退出码 2、
  stdout 为空、stderr 说明原因且无 Python 回溯；允许前导零
  （``0001`` 与 ``1`` 等价，前导零不影响是否越界）。
- 任务或目标项目不存在时退出码 2、stdout 为空、stderr 说明不存在；
  省略 ``--from`` 时保留不加来源校验的现有转移规则（含原地转移成功）。
- 数据库无法打开时退出码 1、stdout 为空、stderr 说明存储失败。

测试只通过 README 公开的 ``python -m kanban`` 入口驱动产品，不直接读写
数据库，不调用产品内部函数，不依赖网络或任何预先保存的项目。每个用例在
独立临时目录中使用全新的隔离 SQLite 数据库，结束后自动清理。

用法（在仓库根目录，即 kanban/ 包所在目录）::

    python3 test_task_transfer_from.py
    python3 -m unittest test_task_transfer_from -v
    python3 test_task_transfer_from.py \
        TaskTransferFromRegression.test_acceptance_match_then_repeat_conflicts

退出码：全部通过为 0，存在失败为 1。不依赖 JSON 键顺序或错误文案逐字一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TASK_FIELDS = {"id", "project_id", "title", "status"}
SHARED_TITLE = "整理 API"

# SQLite 有符号 64 位整数上限
SQLITE_MAX_INT = 9223372036854775807


class TaskTransferFromRegression(unittest.TestCase):
    """每个测试方法使用一个全新临时数据库，用例之间互不依赖。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="kanban-transfer-from-")
        self.db_path = os.path.join(self._tmpdir.name, "isolated.db")
        # 项目 1：任务 1（随后置为 doing）；项目 2：任务 2（保持 todo）；
        # 两条任务同标题
        self.project_alpha = self.cli_json("project-create", "alpha")["id"]
        self.project_beta = self.cli_json("project-create", "beta")["id"]
        self.task1 = self.cli_json(
            "task-create", str(self.project_alpha), SHARED_TITLE
        )["id"]
        self.task2 = self.cli_json(
            "task-create", str(self.project_beta), SHARED_TITLE
        )["id"]
        self.assertEqual((self.project_alpha, self.project_beta), (1, 2))
        self.assertEqual((self.task1, self.task2), (1, 2))
        proc = self.run_cli("task-move", str(self.task1), "doing")
        self.assertEqual(proc.returncode, 0, self.detail(
            ("task-move", "1", "doing"), proc, expected="准备任务 1 为 doing"))

    def tearDown(self):
        self._tmpdir.cleanup()

    # ---------- 命令行与解析辅助 ----------

    def run_cli(self, *cli_args, db_path=None):
        return subprocess.run(
            [sys.executable, "-m", "kanban",
             "--db", db_path or self.db_path, *cli_args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def cli_json(self, *cli_args):
        proc = self.run_cli(*cli_args)
        detail = self.detail(cli_args, proc, expected="准备数据成功")
        self.assertEqual(proc.returncode, 0, f"准备数据应成功：\n{detail}")
        self.assertEqual(proc.stderr, "", f"准备数据时 stderr 应为空：\n{detail}")
        return json.loads(proc.stdout)

    def detail(self, cli_args, proc, expected=None):
        return "\n".join([
            f"输入参数={list(cli_args)!r}",
            f"实际退出码={proc.returncode}",
            f"实际标准输出={proc.stdout!r}",
            f"实际标准错误={proc.stderr!r}",
            f"预期结果={expected!r}",
        ])

    def show(self, task_id):
        proc = self.run_cli("task-show", str(task_id))
        self.assertEqual(proc.returncode, 0, self.detail(
            ("task-show", str(task_id)), proc, expected="task-show 成功"))
        self.assertEqual(proc.stderr, "")
        return json.loads(proc.stdout)

    def list_project(self, project_id):
        proc = self.run_cli("task-list", str(project_id))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        return json.loads(proc.stdout)

    def stats(self, project_id):
        proc = self.run_cli("project-stats", str(project_id))
        self.assertEqual(proc.returncode, 0, self.detail(
            ("project-stats", str(project_id)), proc, expected="stats 成功"))
        self.assertEqual(proc.stderr, "")
        return json.loads(proc.stdout)

    def project_names(self):
        proc = self.run_cli("project-list")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        return json.loads(proc.stdout)

    def task_obj(self, task_id, project_id, status="doing"):
        return {"id": task_id, "project_id": project_id,
                "title": SHARED_TITLE, "status": status}

    def snapshot(self):
        return {
            "show1": self.show(self.task1),
            "show2": self.show(self.task2),
            "alpha": self.list_project(self.project_alpha),
            "beta": self.list_project(self.project_beta),
            "alpha_stats": self.stats(self.project_alpha),
            "beta_stats": self.stats(self.project_beta),
            "projects": self.project_names(),
        }

    def assert_rejected(self, proc, cli_args):
        """来源不匹配/非法参数的统一拒绝协议：rc2、stdout 空、stderr 非空无回溯。"""
        detail = self.detail(cli_args, proc, expected="退出码 2、stdout 为空")
        self.assertEqual(proc.returncode, 2, f"应退出码 2：\n{detail}")
        self.assertEqual(proc.stdout, "", f"失败时 stdout 应为空：\n{detail}")
        self.assertNotEqual(proc.stderr.strip(), "", f"stderr 应说明原因：\n{detail}")
        self.assertNotIn("Traceback", proc.stderr, f"不应出现回溯：\n{detail}")
        return detail

    # ---------- 用户验收场景 ----------

    def test_acceptance_match_then_repeat_conflicts(self):
        # 第一次：任务 1 当前属于项目 1，与 --from 0001（数值 1）相同，转入项目 2
        cli_args = ("task-transfer", "1", "2", "--from", "0001")
        proc = self.run_cli(*cli_args)
        detail = self.detail(
            cli_args, proc,
            expected="退出码 0、stderr 空、stdout 为属于项目 2、doing 的任务 1 对象",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        moved = json.loads(proc.stdout)
        self.assertEqual(set(moved), TASK_FIELDS, detail)
        self.assertEqual(moved, self.task_obj(1, 2, "doing"), detail)

        # 独立查询：任务 1 归属项目 2，标识/标题/doing 原样；任务 2 不变
        self.assertEqual(self.show(1), self.task_obj(1, 2, "doing"))
        self.assertEqual(self.show(2), self.task_obj(2, 2, "todo"))
        self.assertEqual(
            self.list_project(self.project_beta),
            [self.task_obj(1, 2, "doing"), self.task_obj(2, 2, "todo")],
        )
        self.assertEqual(self.list_project(self.project_alpha), [])
        self.assertEqual(
            self.stats(self.project_alpha),
            {"project_id": 1, "total": 0,
             "todo": 0, "doing": 0, "done": 0},
        )
        self.assertEqual(
            self.stats(self.project_beta),
            {"project_id": 2, "total": 2,
             "todo": 1, "doing": 1, "done": 0},
        )

        # 再次执行同一命令：当前归属项目 2，与预期项目 1 不符，拒绝
        before = self.snapshot()
        proc2 = self.run_cli(*cli_args)
        detail2 = self.assert_rejected(proc2, cli_args)
        low = proc2.stderr.lower()
        self.assertIn("project 2", low,
                      f"stderr 应指出当前项目标识 2：\n{detail2}")
        self.assertIn("project 1", low,
                      f"stderr 应指出预期项目标识 1：\n{detail2}")

        # 全部项目与任务数据不变；重复失败也不改变查询结果
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.show(1), self.task_obj(1, 2, "doing"))
        self.assertEqual(self.show(2), self.task_obj(2, 2, "todo"))
        self.assertEqual(
            self.stats(self.project_alpha),
            {"project_id": 1, "total": 0,
             "todo": 0, "doing": 0, "done": 0},
        )
        self.assertEqual(
            self.stats(self.project_beta),
            {"project_id": 2, "total": 2,
             "todo": 1, "doing": 1, "done": 0},
        )
        self.assertEqual(
            self.project_names(),
            [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}],
        )
        # 再来一次失败，结果仍稳定
        proc3 = self.run_cli(*cli_args)
        self.assert_rejected(proc3, cli_args)
        self.assertEqual(self.snapshot(), before)

    def test_leading_zero_source_equivalent(self):
        # --from 1 与 --from 0001 等价：先成功转入项目 2
        proc = self.run_cli("task-transfer", "1", "2", "--from", "1")
        detail = self.detail(
            ("task-transfer", "1", "2", "--from", "1"), proc,
            expected="--from 1 按数值匹配，转移成功",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, 2, "doing"))
        # 再以前导零写法确认当前已属项目 2：--from 2 允许原地转移
        proc2 = self.run_cli("task-transfer", "0001", "0002", "--from", "0002")
        detail2 = self.detail(
            ("task-transfer", "0001", "0002", "--from", "0002"), proc2,
            expected="0001/0002 按数值处理，来源相符的原地转移成功",
        )
        self.assertEqual(proc2.returncode, 0, detail2)
        self.assertEqual(proc2.stderr, "", detail2)
        self.assertEqual(json.loads(proc2.stdout),
                         self.task_obj(1, 2, "doing"))

    # ---------- 目标就是当前项目时仍检查来源 ----------

    def test_same_project_matching_source_is_noop_success(self):
        # 任务 1 在项目 1，目标也是项目 1，--from 1：成功返回原任务，不写库
        before = self.snapshot()
        cli_args = ("task-transfer", "1", "1", "--from", "1")
        proc = self.run_cli(*cli_args)
        detail = self.detail(
            cli_args, proc,
            expected="来源相符且目标等于当前：退出码 0、返回原任务、不新增记录",
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertEqual(proc.stderr, "", detail)
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, 1, "doing"))
        self.assertEqual(self.snapshot(), before)

    def test_same_project_mismatching_source_rejected(self):
        # 任务 1 在项目 1，目标也是项目 1，但 --from 2：仍拒绝且不写库
        before = self.snapshot()
        cli_args = ("task-transfer", "1", "1", "--from", "2")
        proc = self.run_cli(*cli_args)
        detail = self.assert_rejected(proc, cli_args)
        low = proc.stderr.lower()
        self.assertIn("project 1", low, f"stderr 应指出当前项目 1：\n{detail}")
        self.assertIn("project 2", low, f"stderr 应指出预期项目 2：\n{detail}")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.show(1), self.task_obj(1, 1, "doing"))

    # ---------- 合法但不存在的预期来源：拒绝且不创建项目 ----------

    def test_legal_but_nonexistent_source_rejected_and_not_created(self):
        for source in ("999", "000999", str(SQLITE_MAX_INT)):
            with self.subTest(source=source):
                before = self.snapshot()
                cli_args = ("task-transfer", "1", "2", "--from", source)
                proc = self.run_cli(*cli_args)
                detail = self.assert_rejected(proc, cli_args)
                # 按归属不匹配拒绝：同时给出当前项目与预期项目标识
                low = proc.stderr.lower()
                self.assertIn("project 1", low,
                              f"stderr 应指出当前项目 1：\n{detail}")
                self.assertIn(str(int(source)), proc.stderr,
                              f"stderr 应指出预期项目标识 {source}：\n{detail}")
                # 不创建预期来源项目，全部数据不变
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(
                    self.project_names(),
                    [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}],
                )
                self.assertEqual(self.show(1), self.task_obj(1, 1, "doing"))

    def test_mismatch_changes_nothing_else(self):
        # 来源合法（项目 2）但任务 1 当前属于项目 1：拒绝，
        # 不转移、不复制、不新增任务或项目
        before = self.snapshot()
        cli_args = ("task-transfer", "1", "2", "--from", "2")
        proc = self.run_cli(*cli_args)
        self.assert_rejected(proc, cli_args)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(
            self.list_project(self.project_alpha), [self.task_obj(1, 1, "doing")],
        )
        self.assertEqual(
            self.list_project(self.project_beta), [self.task_obj(2, 2, "todo")],
        )

    # ---------- --from 标识的原样拼写与边界校验 ----------

    def test_invalid_source_values(self):
        for bad in ("", "   ", "\t", "0", "000", "-1", "abc", "1.5",
                    "1x", " 1", "1 ", str(SQLITE_MAX_INT + 1),
                    "9999999999999999999999"):
            with self.subTest(bad=bad):
                before = self.snapshot()
                cli_args = ("task-transfer", "1", "2", "--from", bad)
                proc = self.run_cli(*cli_args)
                detail = self.assert_rejected(proc, cli_args)
                self.assertIn("project", proc.stderr.lower(),
                              f"stderr 应说明来源项目标识非法：\n{detail}")
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.show(1), self.task_obj(1, 1, "doing"))

    def test_source_missing_value(self):
        for cli_args in (
            ("task-transfer", "1", "2", "--from"),
            ("task-transfer", "1", "2", "--from", "--from", "1"),
        ):
            with self.subTest(cli_args=cli_args):
                proc = self.run_cli(*cli_args)
                self.assert_rejected(proc, cli_args)
                self.assertEqual(self.show(1), self.task_obj(1, 1, "doing"))

    # ---------- 任务 / 目标项目不存在 ----------

    def test_task_not_exist_with_from(self):
        before = self.snapshot()
        cli_args = ("task-transfer", "999", "2", "--from", "1")
        proc = self.run_cli(*cli_args)
        detail = self.assert_rejected(proc, cli_args)
        self.assertIn("task 999", proc.stderr.lower(),
                      f"stderr 应说明任务不存在：\n{detail}")
        self.assertEqual(self.snapshot(), before)

    def test_target_project_not_exist_with_matching_source(self):
        # 来源相符但目标项目不存在：同样 rc2，数据不变
        before = self.snapshot()
        cli_args = ("task-transfer", "1", "999", "--from", "1")
        proc = self.run_cli(*cli_args)
        detail = self.assert_rejected(proc, cli_args)
        self.assertIn("project 999", proc.stderr.lower(),
                      f"stderr 应说明目标项目 999 不存在：\n{detail}")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.show(1), self.task_obj(1, 1, "doing"))

    def test_source_mismatch_takes_precedence_over_missing_target(self):
        # 来源不匹配且目标项目也不存在：先按来源不匹配拒绝，
        # stderr 同时指出当前项目与预期项目；不创建任何项目
        cli_args = ("task-transfer", "1", "999", "--from", "2")
        proc = self.run_cli(*cli_args)
        detail = self.assert_rejected(proc, cli_args)
        low = proc.stderr.lower()
        self.assertIn("project 1", low, f"stderr 应指出当前项目 1：\n{detail}")
        self.assertIn("project 2", low, f"stderr 应指出预期项目 2：\n{detail}")
        self.assertEqual(self.show(1), self.task_obj(1, 1, "doing"))
        self.assertEqual(
            self.project_names(),
            [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}],
        )

    def test_invalid_task_and_target_ids_with_from(self):
        for cli_args in (
            ("task-transfer", "abc", "2", "--from", "1"),
            ("task-transfer", "0", "2", "--from", "1"),
            ("task-transfer", "1", "xyz", "--from", "1"),
            ("task-transfer", "1", "0", "--from", "1"),
        ):
            with self.subTest(cli_args=cli_args):
                proc = self.run_cli(*cli_args)
                self.assert_rejected(proc, cli_args)
        self.assertEqual(self.show(1), self.task_obj(1, 1, "doing"))
        self.assertEqual(self.show(2), self.task_obj(2, 2, "todo"))

    # ---------- 省略 --from 时保留现有转移规则 ----------

    def test_without_from_rules_unchanged(self):
        # 不加 --from：任务 1 可直接从项目 1 转入项目 2
        proc = self.run_cli("task-transfer", "1", "2")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(json.loads(proc.stdout),
                         self.task_obj(1, 2, "doing"))
        # 原地转移（目标即当前所属项目）仍成功返回原任务
        proc2 = self.run_cli("task-transfer", "1", "2")
        self.assertEqual(proc2.returncode, 0)
        self.assertEqual(proc2.stderr, "")
        self.assertEqual(json.loads(proc2.stdout),
                         self.task_obj(1, 2, "doing"))
        # 不带 --from 时目标项目不存在仍按原规则拒绝
        proc3 = self.run_cli("task-transfer", "1", "999")
        self.assertEqual(proc3.returncode, 2)
        self.assertEqual(proc3.stdout, "")
        self.assertIn("project 999", proc3.stderr.lower())
        self.assertEqual(self.show(1), self.task_obj(1, 2, "doing"))

    # ---------- 存储失败 ----------

    def test_storage_failure_exit_code_one(self):
        proc = self.run_cli(
            "task-transfer", "1", "2", "--from", "1",
            db_path=os.path.join(self._tmpdir.name, "missing_dir", "x.db"),
        )
        cli_args = ("task-transfer", "1", "2", "--from", "1")
        detail = self.detail(cli_args, proc,
                             expected="退出码 1、stdout 空、stderr 说明存储失败")
        self.assertEqual(proc.returncode, 1, detail)
        self.assertEqual(proc.stdout, "", detail)
        self.assertIn("storage failure", proc.stderr, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)

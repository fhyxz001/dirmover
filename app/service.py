"""任务执行：加锁 -> 调用迁移核心 -> 写入执行记录。"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from . import config
from .locks import FileLock
from .migrator import PathValidationError, migrate

logger = logging.getLogger("dirmover.runner")


class TaskNotFound(LookupError):
    pass


class TaskBusy(RuntimeError):
    pass


def _empty_result(dry_run: bool) -> dict:
    return {
        "dry_run": dry_run,
        "moved_entries": 0,
        "files": 0,
        "dirs": 0,
        "skipped": 0,
        "overwritten": 0,
        "renamed": 0,
        "failures": [],
        "warnings": [],
        "items": [],
        "items_truncated": False,
    }


class TaskService:
    def __init__(self, store):
        self.store = store

    def run_task(self, task_id: str, dry_run: bool = False, trigger: str = "manual") -> dict:
        task = self.store.get_task(task_id)
        if task is None:
            raise TaskNotFound(task_id)

        # 同一任务的定时执行与手动执行互斥
        lock = FileLock(config.LOCK_DIR / f"{task_id}.lock")
        if not lock.acquire():
            raise TaskBusy("任务正在执行中，请稍后再试")

        started = time.time()
        started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        logger.info("开始执行[%s]任务「%s」（dry_run=%s）", trigger, task["name"], dry_run)

        try:
            error = None
            try:
                result = migrate(
                    task["source"],
                    task["target"],
                    conflict=task.get("conflict", "skip"),
                    dry_run=dry_run,
                )
            except PathValidationError as exc:
                result = _empty_result(dry_run)
                error = str(exc)
            except Exception as exc:  # 兜底：任何意外都记录，而不是让线程静默死掉
                logger.exception("任务「%s」执行异常", task["name"])
                result = _empty_result(dry_run)
                error = f"执行异常：{exc}"

            duration_ms = int((time.time() - started) * 1000)
            failures = result["failures"]
            run = {
                "at": started_at,
                "trigger": trigger,
                "dry_run": dry_run,
                "ok": error is None and not failures,
                "duration_ms": duration_ms,
                "error": error,
                "moved_entries": result["moved_entries"],
                "files": result["files"],
                "dirs": result["dirs"],
                "skipped": result["skipped"],
                "overwritten": result["overwritten"],
                "renamed": result["renamed"],
                # 只保留前 50 条，避免 UI 返回过大
                "warnings": result["warnings"][:50],
                "failures": failures[:50],
                "items": result["items"] if dry_run else [],
                "items_truncated": result["items_truncated"],
            }
            self.store.record_run(task_id, run)

            if error:
                logger.error("任务「%s」执行失败：%s", task["name"], error)
            elif not run["ok"]:
                logger.warning("任务「%s」部分失败：%d 项", task["name"], len(failures))
            else:
                logger.info(
                    "任务「%s」完成：移动 %s 项（%s 文件 / %s 目录），跳过 %s，耗时 %s ms",
                    task["name"], run["moved_entries"], run["files"], run["dirs"],
                    run["skipped"], duration_ms,
                )
            return run
        finally:
            lock.release()

"""APScheduler 封装：每个启用任务对应一个 cron job，增删改后立即同步。"""

from __future__ import annotations

import logging

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("dirmover.scheduler")


def validate_cron(expr: str) -> str:
    """校验并规范化 5 段标准 cron 表达式（分 时 日 月 周）。"""
    expr = " ".join(str(expr).split())
    if len(expr.split()) != 5:
        raise ValueError("cron 表达式需要 5 个字段：分 时 日 月 周")
    try:
        CronTrigger.from_crontab(expr)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"无效的 cron 表达式「{expr}」：{exc}") from exc
    return expr


class TaskScheduler:
    def __init__(self, runner):
        self._runner = runner  # callable(task_id) -> None
        self._scheduler = BackgroundScheduler(
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60}
        )
        self._started = False

    @property
    def running(self) -> bool:
        return self._started

    def start(self, tasks: list[dict]) -> None:
        """启动调度器并恢复所有已启用任务（服务重启后自动恢复）。"""
        if self._started:
            return
        self._scheduler.start()
        self._started = True
        for task in tasks:
            self.sync(task)
        logger.info("调度器已启动，加载启用任务 %d 个", len(tasks))

    def sync(self, task: dict) -> None:
        """新增/修改/启用任务时调用：先移除旧 job，再按最新配置添加。"""
        if not self._started:
            return
        self.remove(task["id"])
        if not task.get("enabled"):
            return

        try:
            trigger = CronTrigger.from_crontab(task["cron"])  # 使用本机时区
        except (ValueError, TypeError):
            logger.error("任务「%s」的 cron 无效，未加入调度：%s", task["name"], task["cron"])
            return

        self._scheduler.add_job(
            self._runner,
            trigger=trigger,
            args=[task["id"]],
            id=task["id"],
            replace_existing=True,
        )
        logger.info("任务「%s」已调度：%s", task["name"], task["cron"])

    def remove(self, task_id: str) -> None:
        try:
            self._scheduler.remove_job(task_id)
        except JobLookupError:
            pass
        except Exception:  # 调度器未启动等边界情况
            logger.debug("移除 job 失败（忽略）：%s", task_id, exc_info=True)

    def next_run_time(self, task_id: str) -> str | None:
        if not self._started:
            return None
        job = self._scheduler.get_job(task_id)
        if job is None or job.next_run_time is None:
            return None
        return job.next_run_time.isoformat(timespec="seconds")

    def shutdown(self) -> None:
        if not self._started:
            return
        self._scheduler.shutdown(wait=False)
        self._started = False
        logger.info("调度器已停止")

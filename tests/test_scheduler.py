"""文件锁与调度器测试。"""

from __future__ import annotations

import pytest

from app.locks import FileLock
from app.scheduler import TaskScheduler, validate_cron

TASK = {
    "id": "task1",
    "name": "测试",
    "source": "/tmp/src",
    "target": "/tmp/dst",
    "cron": "*/5 * * * *",
    "conflict": "skip",
    "enabled": True,
}


def test_validate_cron_normalizes_and_rejects():
    assert validate_cron("  */5   *  *  *  * ") == "*/5 * * * *"
    with pytest.raises(ValueError):
        validate_cron("*/5 * * *")  # 只有 4 段
    with pytest.raises(ValueError):
        validate_cron("61 * * * *")  # 分钟越界


def test_file_lock_is_exclusive(tmp_path):
    first = FileLock(tmp_path / "demo.lock")
    second = FileLock(tmp_path / "demo.lock")

    assert first.acquire() is True
    assert second.acquire() is False  # 已被占用
    first.release()

    assert second.acquire() is True
    second.release()


def test_scheduler_adds_removes_and_restores_jobs():
    fired: list[str] = []
    scheduler = TaskScheduler(fired.append)
    try:
        scheduler.start([TASK])
        assert scheduler.next_run_time("task1") is not None

        # 禁用后立即移除调度
        scheduler.sync({**TASK, "enabled": False})
        assert scheduler.next_run_time("task1") is None

        # 修改 cron / 重新启用后立即恢复
        scheduler.sync({**TASK, "cron": "0 3 * * *"})
        assert scheduler.next_run_time("task1") is not None

        scheduler.remove("task1")
        assert scheduler.next_run_time("task1") is None
    finally:
        scheduler.shutdown()


def test_scheduler_ignores_invalid_cron():
    scheduler = TaskScheduler(lambda _task_id: None)
    try:
        scheduler.start([])
        scheduler.sync({**TASK, "cron": "bad cron"})
        assert scheduler.next_run_time("task1") is None
    finally:
        scheduler.shutdown()

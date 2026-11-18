"""JSON 文件持久化：任务配置 + 执行历史，无需数据库。"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("dirmover.store")

TASK_FIELDS = ("name", "source", "target", "cron", "conflict", "enabled")


def _now() -> str:
    # 带时区的本地时间（ISO 8601），前端直接 new Date() 即可
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _load(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
        logger.warning("数据文件格式不是对象，已忽略：%s", path)
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("读取数据文件失败（使用空数据，原文件保留）：%s：%s", path, exc)
    return {}


def _save(path: Path, data: dict) -> None:
    """先写临时文件再原子替换，避免进程中断写坏 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Store:
    def __init__(self, tasks_file: Path | str, runs_file: Path | str, history_limit: int = 50):
        self.tasks_file = Path(tasks_file)
        self.runs_file = Path(runs_file)
        self.history_limit = max(1, int(history_limit))
        self._lock = threading.RLock()
        self._tasks: dict[str, dict] = _load(self.tasks_file)
        self._runs: dict[str, list] = _load(self.runs_file)

        # 容错：清理字段不完整的历史记录
        for task_id, task in list(self._tasks.items()):
            if not isinstance(task, dict) or not {"name", "source", "target", "cron", "conflict"} <= task.keys():
                logger.warning("忽略字段不完整的历史任务：%s", task_id)
                self._tasks.pop(task_id, None)
                continue
            task.setdefault("last_run", None)
            task.setdefault("updated_at", task.get("created_at"))
            task.setdefault("enabled", True)

    # ---------------- 任务 ----------------

    def list_tasks(self) -> list[dict]:
        with self._lock:
            tasks = [dict(t) for t in self._tasks.values()]
        tasks.sort(key=lambda t: (t.get("created_at") or "", t.get("name") or ""))
        return tasks

    def get_task(self, task_id: str) -> dict | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def add_task(self, *, name: str, source: str, target: str, cron: str, conflict: str, enabled: bool = True) -> dict:
        now = _now()
        task = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "source": source,
            "target": target,
            "cron": cron,
            "conflict": conflict,
            "enabled": bool(enabled),
            "created_at": now,
            "updated_at": now,
            "last_run": None,
        }
        with self._lock:
            self._tasks[task["id"]] = task
            _save(self.tasks_file, self._tasks)
            return dict(task)

    def update_task(self, task_id: str, **fields) -> dict | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            for key in TASK_FIELDS:
                if key in fields and fields[key] is not None:
                    task[key] = fields[key]
            task["updated_at"] = _now()
            _save(self.tasks_file, self._tasks)
            return dict(task)

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self._tasks:
                return False
            self._tasks.pop(task_id, None)
            self._runs.pop(task_id, None)
            _save(self.tasks_file, self._tasks)
            _save(self.runs_file, self._runs)
            return True

    # ---------------- 执行记录 ----------------

    def record_run(self, task_id: str, run: dict) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                # 任务已被删除，不再保留它的记录
                return
            runs = self._runs.setdefault(task_id, [])
            runs.insert(0, run)
            del runs[self.history_limit:]

            # 试运行不刷新任务上的“上次执行”状态
            if not run.get("dry_run"):
                task["last_run"] = {
                    "at": run["at"],
                    "ok": run["ok"],
                    "trigger": run.get("trigger"),
                    "moved_entries": run["moved_entries"],
                    "files": run["files"],
                    "dirs": run["dirs"],
                    "skipped": run["skipped"],
                    "failures": len(run.get("failures") or []),
                    "duration_ms": run.get("duration_ms"),
                    "error": run.get("error"),
                }
            _save(self.runs_file, self._runs)
            _save(self.tasks_file, self._tasks)

    def get_runs(self, task_id: str, limit: int = 20) -> list[dict]:
        with self._lock:
            runs = self._runs.get(task_id, [])
            return [dict(r) for r in runs[: max(1, int(limit))]]

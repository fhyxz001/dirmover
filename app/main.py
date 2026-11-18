"""FastAPI 入口：REST API + 单页 UI + 内置调度器。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, config
from .logging_setup import setup_logging
from .migrator import PathValidationError, validate_paths
from .scheduler import TaskScheduler, validate_cron
from .service import TaskBusy, TaskNotFound, TaskService
from .store import Store

setup_logging()
logger = logging.getLogger("dirmover.api")

STATIC_DIR = Path(__file__).resolve().parent / "static"

store = Store(config.TASKS_FILE, config.RUNS_FILE, history_limit=config.RUN_HISTORY_LIMIT)
service = TaskService(store)


def _run_scheduled(task_id: str) -> None:
    """调度器回调：在后台线程里执行任务。"""
    try:
        service.run_task(task_id, dry_run=False, trigger="schedule")
    except TaskNotFound:
        logger.warning("定时执行时任务已不存在：%s", task_id)
    except TaskBusy:
        logger.warning("上次执行尚未结束，跳过本次定时执行：%s", task_id)
    except Exception:
        logger.exception("定时执行任务失败：%s", task_id)


scheduler = TaskScheduler(_run_scheduled)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 服务重启后自动恢复所有启用任务的调度
    if config.SCHEDULER_ENABLED:
        scheduler.start([t for t in store.list_tasks() if t["enabled"]])
    logger.info("DirMover %s 已启动，数据目录：%s", __version__, config.DATA_DIR)
    try:
        yield
    finally:
        scheduler.shutdown()
        logger.info("DirMover 已停止")


app = FastAPI(title="DirMover", version=__version__, lifespan=lifespan)


# ---------------- 数据模型 ----------------

class TaskPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    cron: str = "*/5 * * * *"
    conflict: Literal["skip", "overwrite", "rename"] = "skip"
    enabled: bool = True


class TogglePayload(BaseModel):
    enabled: bool


class RunPayload(BaseModel):
    dry_run: bool = False


def _clean_payload(payload: TaskPayload) -> dict:
    """把用户输入规范化并校验，不合法直接返回 400。"""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="任务名称不能为空")

    try:
        cron = validate_cron(payload.cron)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        source, target = validate_paths(payload.source, payload.target)
    except PathValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "name": name,
        "source": str(source),
        "target": str(target),
        "cron": cron,
        "conflict": payload.conflict,
        "enabled": payload.enabled,
    }


def _task_view(task: dict) -> dict:
    view = dict(task)
    view["next_run"] = scheduler.next_run_time(task["id"]) if task.get("enabled") else None
    return view


# ---------------- 任务 CRUD ----------------

@app.get("/api/tasks")
def list_tasks():
    return {"tasks": [_task_view(t) for t in store.list_tasks()]}


@app.post("/api/tasks", status_code=201)
def create_task(payload: TaskPayload):
    data = _clean_payload(payload)
    task = store.add_task(**data)
    scheduler.sync(task)
    logger.info("新增任务「%s」：%s -> %s", task["name"], task["source"], task["target"])
    return _task_view(task)


@app.put("/api/tasks/{task_id}")
def update_task(task_id: str, payload: TaskPayload):
    if store.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    data = _clean_payload(payload)
    task = store.update_task(task_id, **data)
    scheduler.sync(task)  # 修改 cron / 启停后立即生效
    logger.info("更新任务「%s」：cron=%s，enabled=%s", task["name"], task["cron"], task["enabled"])
    return _task_view(task)


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str):
    scheduler.remove(task_id)
    if not store.delete_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    logger.info("删除任务：%s", task_id)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/toggle")
def toggle_task(task_id: str, payload: TogglePayload):
    if store.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = store.update_task(task_id, enabled=payload.enabled)
    scheduler.sync(task)
    logger.info("%s任务「%s」", "启用" if payload.enabled else "禁用", task["name"])
    return _task_view(task)


# ---------------- 执行 ----------------

@app.post("/api/tasks/{task_id}/run")
def run_task(task_id: str, payload: RunPayload | None = None):
    dry_run = bool(payload.dry_run) if payload else False
    try:
        run = service.run_task(task_id, dry_run=dry_run, trigger="manual")
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except TaskBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run": run, "task": _task_view(store.get_task(task_id))}


@app.get("/api/tasks/{task_id}/runs")
def task_runs(task_id: str, limit: int = Query(20, ge=1, le=100)):
    if store.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"runs": store.get_runs(task_id, limit)}


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": __version__,
        "tasks": len(store.list_tasks()),
        "scheduler": scheduler.running,
    }


# 静态单页（放在最后，避免遮挡 /api 路由）
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT)

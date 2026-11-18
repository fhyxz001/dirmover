"""API 测试：CRUD、启停、试运行、实际执行、执行记录、路径校验。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.scheduler import TaskScheduler
from app.service import TaskService
from app.store import Store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    store = Store(tmp_path / "tasks.json", tmp_path / "runs.json", history_limit=10)
    scheduler = TaskScheduler(lambda task_id: None)
    service = TaskService(store)
    # 用临时数据目录替换模块级实例，测试之间互不影响
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "scheduler", scheduler)
    monkeypatch.setattr(main, "service", service)
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture()
def dirs(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "one.txt").write_text("1", encoding="utf-8")
    (src / "folder").mkdir()
    (src / "folder" / "two.txt").write_text("2", encoding="utf-8")
    return src, tmp_path / "dst"


def payload(src, dst, **overrides):
    body = {
        "name": "测试任务",
        "source": str(src),
        "target": str(dst),
        "cron": "*/5 * * * *",
        "conflict": "skip",
        "enabled": True,
    }
    body.update(overrides)
    return body


def test_create_list_update_toggle_delete(client, dirs):
    src, dst = dirs

    created = client.post("/api/tasks", json=payload(src, dst))
    assert created.status_code == 201
    task = created.json()
    assert task["name"] == "测试任务"
    assert task["enabled"] is True
    assert task["last_run"] is None
    task_id = task["id"]

    listed = client.get("/api/tasks").json()["tasks"]
    assert len(listed) == 1
    assert listed[0]["id"] == task_id

    updated = client.put(
        f"/api/tasks/{task_id}",
        json=payload(src, dst, name="改名任务", cron="0 2 * * *", conflict="rename"),
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "改名任务"
    assert updated.json()["cron"] == "0 2 * * *"
    assert updated.json()["conflict"] == "rename"

    toggled = client.post(f"/api/tasks/{task_id}/toggle", json={"enabled": False})
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False

    deleted = client.delete(f"/api/tasks/{task_id}")
    assert deleted.status_code == 200
    assert client.get("/api/tasks").json()["tasks"] == []
    # 已经删除的任务再操作返回 404
    assert client.delete(f"/api/tasks/{task_id}").status_code == 404
    assert client.post(f"/api/tasks/{task_id}/toggle", json={"enabled": True}).status_code == 404


def test_dry_run_then_real_run(client, dirs):
    src, dst = dirs
    task_id = client.post("/api/tasks", json=payload(src, dst)).json()["id"]

    dry = client.post(f"/api/tasks/{task_id}/run", json={"dry_run": True})
    assert dry.status_code == 200
    dry_run = dry.json()["run"]
    assert dry_run["ok"] is True
    assert dry_run["dry_run"] is True
    assert dry_run["moved_entries"] == 2
    assert len(dry_run["items"]) == 2
    assert (src / "one.txt").exists()  # 试运行不移动
    assert not dst.exists()  # 试运行不创建目标目录

    real = client.post(f"/api/tasks/{task_id}/run", json={"dry_run": False})
    assert real.status_code == 200
    run = real.json()["run"]
    assert run["ok"] is True
    assert run["moved_entries"] == 2
    assert run["files"] == 2
    assert (dst / "one.txt").read_text(encoding="utf-8") == "1"
    assert (dst / "folder" / "two.txt").read_text(encoding="utf-8") == "2"
    assert list(src.iterdir()) == []

    # 任务上的最近执行状态已更新
    listed = client.get("/api/tasks").json()["tasks"][0]
    assert listed["last_run"]["ok"] is True
    assert listed["last_run"]["moved_entries"] == 2

    # 历史记录：最新的实际执行在前
    runs = client.get(f"/api/tasks/{task_id}/runs").json()["runs"]
    assert [r["dry_run"] for r in runs] == [False, True]


def test_conflict_skip_recorded(client, dirs):
    src, dst = dirs
    dst.mkdir()
    (dst / "one.txt").write_text("OLD", encoding="utf-8")
    task_id = client.post("/api/tasks", json=payload(src, dst)).json()["id"]

    run = client.post(f"/api/tasks/{task_id}/run").json()["run"]
    assert run["ok"] is True
    assert run["skipped"] == 1
    assert (dst / "one.txt").read_text(encoding="utf-8") == "OLD"
    assert (src / "one.txt").exists()


def test_rejects_invalid_cron(client, dirs):
    src, dst = dirs
    res = client.post("/api/tasks", json=payload(src, dst, cron="not a cron"))
    assert res.status_code == 400
    assert "cron" in res.json()["detail"]


def test_rejects_target_inside_source(client, dirs):
    src, _dst = dirs
    res = client.post("/api/tasks", json=payload(src, src / "inside"))
    assert res.status_code == 400
    assert "内部" in res.json()["detail"]


def test_rejects_missing_source(client, tmp_path):
    res = client.post("/api/tasks", json=payload(tmp_path / "missing", tmp_path / "dst"))
    assert res.status_code == 400


def test_run_unknown_task_returns_404(client):
    assert client.post("/api/tasks/nope/run", json={"dry_run": False}).status_code == 404
    assert client.get("/api/tasks/nope/runs").status_code == 404


def test_run_returns_409_when_task_is_locked(client, dirs):
    """同一任务已有实例在跑时，手动执行返回 409。"""
    from app import config
    from app.locks import FileLock

    src, dst = dirs
    task_id = client.post("/api/tasks", json=payload(src, dst)).json()["id"]

    lock = FileLock(config.LOCK_DIR / f"{task_id}.lock")
    assert lock.acquire()
    try:
        res = client.post(f"/api/tasks/{task_id}/run")
        assert res.status_code == 409
    finally:
        lock.release()

    # 锁释放后可以正常执行
    assert client.post(f"/api/tasks/{task_id}/run").status_code == 200


def test_run_records_validation_failure(client, dirs):
    """任务创建后源目录被删，执行时不应 500，而是记录失败。"""
    import shutil

    src, dst = dirs
    task_id = client.post("/api/tasks", json=payload(src, dst)).json()["id"]
    shutil.rmtree(src)

    res = client.post(f"/api/tasks/{task_id}/run")
    assert res.status_code == 200
    run = res.json()["run"]
    assert run["ok"] is False
    assert run["error"]
    assert client.get(f"/api/tasks/{task_id}/runs").status_code == 200


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_index_page_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "DirMover" in res.text

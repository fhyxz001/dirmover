"""全局配置：数据目录、日志、端口等，全部可用环境变量覆盖。"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _path(env_name: str, default: Path) -> Path:
    return Path(os.environ.get(env_name, str(default))).expanduser().resolve()


# 数据与日志目录（容器里分别挂载到 /data 和 /logs）
DATA_DIR = _path("DIRMOVER_DATA", BASE_DIR / "data")
LOG_DIR = _path("DIRMOVER_LOG_DIR", BASE_DIR / "logs")

TASKS_FILE = DATA_DIR / "tasks.json"
RUNS_FILE = DATA_DIR / "runs.json"
LOCK_DIR = DATA_DIR / "locks"
LOG_FILE = LOG_DIR / "dirmover.log"

HOST = os.environ.get("DIRMOVER_HOST", "0.0.0.0")
PORT = int(os.environ.get("DIRMOVER_PORT", "8000"))
LOG_LEVEL = os.environ.get("DIRMOVER_LOG_LEVEL", "INFO").upper()

# 每个任务最多保留多少条执行记录
RUN_HISTORY_LIMIT = int(os.environ.get("DIRMOVER_RUN_HISTORY", "50"))

# 测试或只跑 API 时可关闭内置调度器：DIRMOVER_SCHEDULER=0
SCHEDULER_ENABLED = os.environ.get("DIRMOVER_SCHEDULER", "1").lower() not in ("0", "false", "no")

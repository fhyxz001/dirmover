"""日志：控制台 + 滚动日志文件（logs/dirmover.log）。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from . import config

_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if getattr(root, "_dirmover_ready", False):  # 避免重复初始化
        return

    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    formatter = logging.Formatter(_FORMAT)

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        config.LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root.addHandler(stream)
    root.addHandler(file_handler)
    root._dirmover_ready = True  # type: ignore[attr-defined]

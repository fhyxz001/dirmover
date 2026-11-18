"""跨平台文件锁：保证同一任务不会被并发执行。

Linux/macOS 用 fcntl.flock，Windows 用 msvcrt.locking；
进程内的线程再用 threading.Lock 兜底。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


class FileLock:
    """非阻塞排他锁。acquire() 返回 False 表示已有执行中的实例。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._fh = None
        self._guard = threading.Lock()

    def acquire(self) -> bool:
        if not self._guard.acquire(blocking=False):
            return False

        fh = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(self.path, "a+", encoding="utf-8")
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:  # pragma: no cover - Windows
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover
                raise RuntimeError("当前平台不支持文件锁")
            # 写入 pid，出问题时方便排查是谁占用
            fh.seek(0)
            fh.truncate()
            fh.write(str(os.getpid()))
            fh.flush()
            self._fh = fh
            return True
        except OSError:
            if fh is not None:
                fh.close()
            self._guard.release()
            return False

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            fh.close()
            self._guard.release()

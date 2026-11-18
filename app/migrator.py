"""迁移核心：把源目录下一级的全部内容移动到目标目录。

语义等价于 Linux 上的：
    find "$SOURCE" -mindepth 1 -maxdepth 1 -exec mv -t "$TARGET" -- {} +
隐藏文件（.xxx）同样会被处理，子目录内部层级原样保留。
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("dirmover.migrator")

CONFLICTS = ("skip", "overwrite", "rename")
PREVIEW_LIMIT = 200


class PathValidationError(ValueError):
    """路径不合法：不存在、不可读、目标位于源内部等。"""


def normalize_path(value: str) -> Path:
    """展开 ~ 并转成绝对路径。"""
    return Path(os.path.abspath(os.path.expanduser(str(value).strip())))


def _resolved(path: Path) -> Path:
    # 用 realpath 跟随已存在的软链接，防止通过软链接绕过“目标不能在源内部”的检查
    return Path(os.path.realpath(str(path)))


def validate_paths(source: str, target: str) -> tuple[Path, Path]:
    """校验源/目标路径，返回解析后的绝对路径。"""
    if not str(source).strip():
        raise PathValidationError("源目录不能为空")
    if not str(target).strip():
        raise PathValidationError("目标目录不能为空")

    src = _resolved(normalize_path(source))
    tgt = _resolved(normalize_path(target))

    if not src.exists():
        raise PathValidationError(f"源目录不存在：{src}")
    if not src.is_dir():
        raise PathValidationError(f"源路径不是目录：{src}")

    # 真正尝试列一次目录，比 os.access 更可靠
    try:
        with os.scandir(src) as it:
            next(it, None)
    except PermissionError as exc:
        raise PathValidationError(f"源目录不可读：{src}") from exc
    except OSError as exc:
        raise PathValidationError(f"无法访问源目录：{src}（{exc.strerror or exc}）") from exc

    if tgt.exists() and not tgt.is_dir():
        raise PathValidationError(f"目标路径已存在且不是目录：{tgt}")
    if src == tgt:
        raise PathValidationError("目标目录不能与源目录相同")
    if src in tgt.parents:
        # 例如 src=/data/inbox，tgt=/data/inbox/archive，会导致无限递归
        raise PathValidationError(f"目标目录不能位于源目录内部：{tgt}")
    return src, tgt


def _unique_name(target_dir: Path, name: str) -> Path:
    """生成不冲突的新名字：a.txt -> a_1.txt，.env -> .env_1。"""
    base, suffix = name, ""
    dot = name.rfind(".")
    if dot > 0:  # 隐藏文件（.env）不拆后缀
        base, suffix = name[:dot], name[dot:]
    index = 1
    while True:
        candidate = target_dir / f"{base}_{index}{suffix}"
        if not os.path.lexists(candidate):
            return candidate
        index += 1


def _count_entry(path: Path, is_dir: bool) -> tuple[int, int]:
    """返回 (文件数, 目录数)；目录数包含被移动的顶层目录本身。"""
    if not is_dir:
        return 1, 0
    files, dirs = 0, 1
    for _root, dirnames, filenames in os.walk(path, followlinks=False):
        files += len(filenames)
        dirs += len(dirnames)
    return files, dirs


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _add_item(items: list[dict], name: str, is_dir: bool, action: str, target_name: str, limit: int) -> None:
    if len(items) < limit:
        items.append(
            {
                "name": name,
                "type": "目录" if is_dir else "文件",
                "action": action,
                "target_name": target_name,
            }
        )


def migrate(
    source: str,
    target: str,
    conflict: str = "skip",
    dry_run: bool = False,
    preview_limit: int = PREVIEW_LIMIT,
) -> dict:
    """执行一次迁移（或试运行），返回统计结果，不抛移动异常（逐项记录失败）。"""
    if conflict not in CONFLICTS:
        raise ValueError(f"未知的冲突策略：{conflict}")

    src, tgt = validate_paths(source, target)

    # 试运行不创建目标目录，避免“只看一眼”也改动文件系统
    if not dry_run:
        tgt.mkdir(parents=True, exist_ok=True)

    stats: dict = {
        "dry_run": dry_run,
        "source": str(src),
        "target": str(tgt),
        "moved_entries": 0,  # 顶层条目数（文件 + 目录）
        "files": 0,  # 递归文件数
        "dirs": 0,  # 递归目录数
        "skipped": 0,
        "overwritten": 0,
        "renamed": 0,
        "failures": [],
        "warnings": [],
    }
    items: list[dict] = []
    processed = 0

    with os.scandir(src) as it:
        entries = sorted(it, key=lambda e: e.name)

    for entry in entries:
        name = entry.name
        src_path = Path(entry.path)
        is_dir = entry.is_dir(follow_symlinks=False)  # 软链接按“文件”处理，只移链接本身
        dest = tgt / name
        action = "move"
        target_name = name
        processed += 1

        if os.path.lexists(dest):
            if conflict == "skip":
                stats["skipped"] += 1
                message = f"目标已存在同名项，已跳过：{name}"
                stats["warnings"].append(message)
                logger.warning("[%s] %s", src, message)
                _add_item(items, name, is_dir, "skip", name, preview_limit)
                continue
            if conflict == "overwrite":
                action = "overwrite"
                stats["overwritten"] += 1
                if not dry_run:
                    _remove(dest)
            else:  # rename
                dest = _unique_name(tgt, name)
                action = "rename"
                target_name = dest.name
                stats["renamed"] += 1

        files, dirs = _count_entry(src_path, is_dir)

        if not dry_run:
            try:
                # shutil.move 支持跨磁盘/分区（内部先复制再删除），目录层级原样保留
                shutil.move(str(src_path), str(dest))
            except OSError as exc:
                reason = exc.strerror or str(exc)
                stats["failures"].append({"name": name, "error": reason})
                logger.error("[%s] 移动失败 %s -> %s：%s", src, src_path, dest, reason)
                continue

        stats["moved_entries"] += 1
        stats["files"] += files
        stats["dirs"] += dirs
        _add_item(items, name, is_dir, action, target_name, preview_limit)

    stats["items"] = items
    stats["items_truncated"] = processed > len(items)

    if dry_run:
        logger.info(
            "[试运行] %s -> %s：将移动 %s 项（%s 文件 / %s 目录），跳过 %s，覆盖 %s，重命名 %s",
            src, tgt, stats["moved_entries"], stats["files"], stats["dirs"],
            stats["skipped"], stats["overwritten"], stats["renamed"],
        )
    else:
        logger.info(
            "%s -> %s：移动 %s 项（%s 文件 / %s 目录），跳过 %s，覆盖 %s，重命名 %s，失败 %s",
            src, tgt, stats["moved_entries"], stats["files"], stats["dirs"],
            stats["skipped"], stats["overwritten"], stats["renamed"], len(stats["failures"]),
        )
    return stats

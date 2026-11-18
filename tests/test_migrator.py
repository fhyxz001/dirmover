"""迁移核心测试：移动语义、冲突策略、试运行、路径校验。"""

from __future__ import annotations

import pytest

from app.migrator import PathValidationError, migrate, validate_paths


def make_source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("A", encoding="utf-8")
    (src / ".hidden").write_text("H", encoding="utf-8")
    nested = src / "sub" / "deep"
    nested.mkdir(parents=True)
    (nested / "b.txt").write_text("B", encoding="utf-8")
    return src


def test_moves_all_top_level_items_with_hidden_files(tmp_path):
    src = make_source(tmp_path)
    tgt = tmp_path / "tgt"

    result = migrate(str(src), str(tgt))

    assert result["failures"] == []
    assert result["moved_entries"] == 3  # a.txt / .hidden / sub
    assert result["files"] == 3
    assert result["dirs"] == 2  # sub + deep
    assert result["skipped"] == 0
    assert (tgt / "a.txt").read_text(encoding="utf-8") == "A"
    assert (tgt / ".hidden").read_text(encoding="utf-8") == "H"
    # 子目录内部层级保留
    assert (tgt / "sub" / "deep" / "b.txt").read_text(encoding="utf-8") == "B"
    assert list(src.iterdir()) == []


def test_creates_target_directory(tmp_path):
    src = make_source(tmp_path)
    tgt = tmp_path / "not" / "exists" / "yet"

    migrate(str(src), str(tgt))

    assert tgt.is_dir()


def test_conflict_skip_keeps_both(tmp_path):
    src = make_source(tmp_path)
    tgt = tmp_path / "tgt"
    tgt.mkdir()
    (tgt / "a.txt").write_text("OLD", encoding="utf-8")

    result = migrate(str(src), str(tgt), conflict="skip")

    assert result["skipped"] == 1
    assert result["moved_entries"] == 2
    assert result["warnings"]
    assert (tgt / "a.txt").read_text(encoding="utf-8") == "OLD"
    assert (src / "a.txt").exists()  # 源文件保留


def test_conflict_overwrite_replaces_target(tmp_path):
    src = make_source(tmp_path)
    tgt = tmp_path / "tgt"
    tgt.mkdir()
    (tgt / "a.txt").write_text("OLD", encoding="utf-8")
    (tgt / "sub" / "deep").mkdir(parents=True)  # 目录也能被覆盖

    result = migrate(str(src), str(tgt), conflict="overwrite")

    assert result["overwritten"] == 2
    assert result["failures"] == []
    assert (tgt / "a.txt").read_text(encoding="utf-8") == "A"
    assert (tgt / "sub" / "deep" / "b.txt").read_text(encoding="utf-8") == "B"


def test_conflict_rename_uses_suffix(tmp_path):
    src = make_source(tmp_path)
    tgt = tmp_path / "tgt"
    tgt.mkdir()
    (tgt / "a.txt").write_text("OLD", encoding="utf-8")
    (tgt / "a_1.txt").write_text("OLD2", encoding="utf-8")

    result = migrate(str(src), str(tgt), conflict="rename")

    assert result["renamed"] == 1
    assert (tgt / "a.txt").read_text(encoding="utf-8") == "OLD"
    assert (tgt / "a_1.txt").read_text(encoding="utf-8") == "OLD2"
    assert (tgt / "a_2.txt").read_text(encoding="utf-8") == "A"


def test_dry_run_changes_nothing(tmp_path):
    src = make_source(tmp_path)
    tgt = tmp_path / "tgt"

    result = migrate(str(src), str(tgt), dry_run=True)

    assert result["dry_run"] is True
    assert result["moved_entries"] == 3
    assert result["files"] == 3
    assert not tgt.exists()  # 不创建目标目录
    assert (src / "a.txt").exists()
    assert len(result["items"]) == 3
    assert {item["action"] for item in result["items"]} == {"move"}


def test_target_inside_source_is_rejected(tmp_path):
    src = make_source(tmp_path)
    with pytest.raises(PathValidationError):
        validate_paths(str(src), str(src / "nested"))


def test_same_source_and_target_is_rejected(tmp_path):
    src = make_source(tmp_path)
    with pytest.raises(PathValidationError):
        validate_paths(str(src), str(src))


def test_missing_source_is_rejected(tmp_path):
    with pytest.raises(PathValidationError):
        validate_paths(str(tmp_path / "nope"), str(tmp_path / "tgt"))


def test_target_file_is_rejected(tmp_path):
    src = make_source(tmp_path)
    target_file = tmp_path / "file.txt"
    target_file.write_text("x", encoding="utf-8")
    with pytest.raises(PathValidationError):
        validate_paths(str(src), str(target_file))


def test_unknown_conflict_strategy(tmp_path):
    src = make_source(tmp_path)
    with pytest.raises(ValueError):
        migrate(str(src), str(tmp_path / "tgt"), conflict="nope")


def test_source_is_symlinked_target_check(tmp_path):
    """通过软链接指向源目录内部的路径也应被拒绝。"""
    src = make_source(tmp_path)
    link = tmp_path / "link"
    try:
        link.symlink_to(src, target_is_directory=True)
    except OSError:
        pytest.skip("当前环境不支持创建软链接")
    with pytest.raises(PathValidationError):
        validate_paths(str(src), str(link / "inside"))

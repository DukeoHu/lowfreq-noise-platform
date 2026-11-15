"""备份 SQLite 数据库与数据目录（音频、结果、照片）。

用法：
    python deploy/backup.py
    python deploy/backup.py --data-dir backend/data --out-dir backups --keep 14

PostgreSQL 部署请改用 pg_dump 定时备份数据库，本脚本仍可用于文件目录备份。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "backend" / "data"
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "backups"
SUBDIRS = ("audio", "results", "photos")


def sqlite_snapshot(db_path: Path, target: Path) -> None:
    source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def create_backup(data_dir: Path, out_dir: Path) -> Path:
    if not data_dir.exists():
        raise SystemExit(f"数据目录不存在：{data_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = out_dir / f"lowfreq-backup-{stamp}.zip"

    with tempfile.TemporaryDirectory(prefix="lowfreq-backup-") as temp:
        temp_dir = Path(temp)
        db_path = data_dir / "lowfreq.db"
        if db_path.exists():
            sqlite_snapshot(db_path, temp_dir / "lowfreq.db")
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if (temp_dir / "lowfreq.db").exists():
                archive.write(temp_dir / "lowfreq.db", "lowfreq.db")
            for name in SUBDIRS:
                folder = data_dir / name
                if not folder.exists():
                    continue
                for item in folder.rglob("*"):
                    if item.is_file():
                        archive.write(item, Path(name) / item.relative_to(folder))

    return archive_path


def prune(out_dir: Path, keep: int) -> list[Path]:
    if keep <= 0:
        return []
    backups = sorted(out_dir.glob("lowfreq-backup-*.zip"))
    removed = backups[:-keep]
    for item in removed:
        item.unlink(missing_ok=True)
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="备份低频噪音平台数据目录")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--keep", type=int, default=14, help="保留最近 N 个备份，0 表示不清理")
    args = parser.parse_args()

    archive = create_backup(args.data_dir, args.out_dir)
    removed = prune(args.out_dir, args.keep)
    size_mb = archive.stat().st_size / 1024 / 1024
    print(f"备份完成：{archive}（{size_mb:.1f} MB）")
    if removed:
        print(f"已清理 {len(removed)} 个旧备份")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except shutil.Error as exc:
        raise SystemExit(f"备份失败：{exc}") from exc

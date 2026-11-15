"""本地文件存储。后续切换到对象存储时只需替换本模块实现。"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

CHUNK_SIZE = 1024 * 1024


class UploadTooLargeError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredFile:
    relative_path: str
    absolute_path: Path
    sha256: str
    size_bytes: int


def save_stream(
    source: BinaryIO,
    original_filename: str,
    target_dir: Path,
    data_dir: Path,
    max_bytes: int,
) -> StoredFile:
    """流式落盘，同时计算 sha256 并限制大小。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(original_filename).suffix.lower()
    absolute_path = target_dir / f"{uuid.uuid4().hex}{suffix}"
    digest = hashlib.sha256()
    size = 0
    try:
        with absolute_path.open("wb") as handle:
            while chunk := source.read(CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLargeError(f"文件超过上限 {max_bytes} 字节")
                digest.update(chunk)
                handle.write(chunk)
    except Exception:
        absolute_path.unlink(missing_ok=True)
        raise
    return StoredFile(
        relative_path=absolute_path.relative_to(data_dir).as_posix(),
        absolute_path=absolute_path,
        sha256=digest.hexdigest(),
        size_bytes=size,
    )


def resolve(relative_path: str, data_dir: Path) -> Path:
    return (data_dir / relative_path).resolve()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

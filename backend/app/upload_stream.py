"""FastAPI ``UploadFile`` 异步读流封装，避免在路由里重复写 ``await file.read`` 循环。"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, UploadFile

CHUNK_DEFAULT = 1024 * 1024


async def write_uploadfile_to_path(
    file: UploadFile,
    path: Path,
    *,
    chunk_size: int = CHUNK_DEFAULT,
    max_bytes: int | None = None,
    detail_over: str = "文件超过大小限制",
) -> int:
    """将上传流写入本地路径；可选总大小上限（超出则删文件并 400）。返回写入字节数。"""
    total = 0
    with path.open("wb") as out:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                return total
            if max_bytes is not None and total + len(chunk) > max_bytes:
                path.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail=detail_over)
            out.write(chunk)
            total += len(chunk)


async def read_uploadfile_bytes(
    file: UploadFile,
    *,
    chunk_size: int = CHUNK_DEFAULT,
    max_bytes: int | None = None,
    detail_over: str = "文件超过大小限制",
) -> bytes:
    """读入全部字节（分块 ``await``）；可选总上限。"""
    parts: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            return b"".join(parts)
        if max_bytes is not None and total + len(chunk) > max_bytes:
            raise HTTPException(status_code=400, detail=detail_over)
        parts.append(chunk)
        total += len(chunk)

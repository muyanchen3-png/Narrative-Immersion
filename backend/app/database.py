from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

import logging
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _sqlite_connect_pragma(dbapi_conn, _connection_record) -> None:
    """WAL 允许读写并发；busy_timeout 在锁竞争时重试而非立刻报错。"""
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA foreign_keys=ON")
    finally:
        cur.close()


_sqlite_connect_args = (
    {
        "check_same_thread": False,
        # sqlite3 连接级等待锁的秒数（与 busy_timeout 叠加，避免 OperationalError: database is locked）
        "timeout": 30.0,
    }
    if settings.db_url.startswith("sqlite")
    else {}
)

# 单文件 SQLite 用 StaticPool 复用同一条连接，减少多连接争用导致的「database is locked」
if settings.db_url.startswith("sqlite"):
    _engine = create_engine(
        settings.db_url,
        echo=False,
        future=True,
        connect_args=_sqlite_connect_args,
        poolclass=StaticPool,
    )
    event.listen(_engine, "connect", _sqlite_connect_pragma)
else:
    _engine = create_engine(
        settings.db_url,
        echo=False,
        future=True,
    )

SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)

T = TypeVar("T")


def is_sqlite_lock_error(exc: BaseException) -> bool:
    """判断是否为 SQLite 锁竞争（可重试）。"""
    if isinstance(exc, OperationalError):
        return "database is locked" in str(exc.orig or exc).lower()
    return False


def run_with_sqlite_lock_retry(
    op: Callable[[], T],
    *,
    db: Session | None = None,
    max_attempts: int = 12,
    base_delay_s: float = 0.08,
) -> T:
    """
    在长时间事务（如干预生成）与写库请求并发时，SQLite 可能短暂报 locked；有限次退避重试。
    非锁定类 OperationalError 直接抛出。若传入 db，重试前会 rollback 以清掉半开事务。
    """
    last: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return op()
        except OperationalError as e:
            last = e
            if not is_sqlite_lock_error(e) or attempt == max_attempts - 1:
                raise
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    pass
            delay = min(2.0, base_delay_s * (2**attempt))
            logger.warning(
                "SQLite 短暂锁定，%s 后重试 (%s/%s)",
                f"{delay:.2f}s",
                attempt + 1,
                max_attempts,
            )
            time.sleep(delay)
    assert last is not None
    raise last


def _migrate_sqlite_video_characters() -> None:
    """已有库里增量添加 video_characters 列（SQLite）。"""
    if not settings.db_url.startswith("sqlite"):
        return
    cols = [
        ("agent_profile", "TEXT"),
        ("reference_shot_id", "VARCHAR(36)"),
        ("reference_image_path", "VARCHAR(512)"),
        ("reference_video_path", "VARCHAR(512)"),
        ("three_views", "TEXT"),
        ("enrichment_status", "VARCHAR(32)"),
        ("user_notes", "TEXT"),
    ]
    with _engine.connect() as conn:
        for name, typ in cols:
            try:
                conn.execute(text(f"ALTER TABLE video_characters ADD COLUMN {name} {typ}"))
                conn.commit()
            except Exception:
                conn.rollback()
        try:
            conn.execute(
                text(
                    "UPDATE video_characters SET enrichment_status = 'pending' "
                    "WHERE enrichment_status IS NULL OR enrichment_status = ''"
                )
            )
            conn.commit()
        except Exception:
            conn.rollback()


def _migrate_model_configs_priority() -> None:
    """已有库为 model_configs 增加 priority（SQLite / 其它引擎均尝试 ALTER）。"""
    with _engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE model_configs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"))
            conn.commit()
        except Exception:
            conn.rollback()


def init_db() -> None:
    from . import models  # noqa: F401  ensure model classes are registered

    Base.metadata.create_all(_engine)
    try:
        _migrate_sqlite_video_characters()
    except Exception as exc:  # noqa: BLE001
        logger.warning("SQLite 增量迁移跳过：%s", exc)
    try:
        _migrate_model_configs_priority()
    except Exception as exc:  # noqa: BLE001
        logger.warning("model_configs.priority 迁移跳过：%s", exc)


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

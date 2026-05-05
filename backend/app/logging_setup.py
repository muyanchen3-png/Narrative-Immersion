"""集中配置日志：控制台 + storage/logs/hermes.log 轮转文件。

业务与各路由模块使用 logging.getLogger(__name__) 即可自动写入上述目标。
环境变量：HERMES_LOG_LEVEL、HERMES_LOG_MAX_BYTES、HERMES_LOG_BACKUP_COUNT
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Settings

_configured = False


def configure_logging(settings: "Settings") -> None:
    global _configured
    if _configured:
        return
    _configured = True

    level_name = (settings.log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_file = settings.logs_dir / "hermes.log"
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    fh = RotatingFileHandler(
        str(log_file),
        maxBytes=int(settings.log_max_bytes),
        backupCount=int(settings.log_backup_count),
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(level)
    sh.setFormatter(fmt)

    root.handlers.clear()
    root.addHandler(fh)
    root.addHandler(sh)

    # 第三方库降噪（仍可通过 logger 名单独调高）
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logging.getLogger("hermes").info("日志文件：%s（级别 %s）", log_file.resolve(), level_name)

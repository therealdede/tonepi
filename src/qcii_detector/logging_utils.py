from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LoggingConfig


def configure_logging(cfg: LoggingConfig) -> None:
    level = getattr(logging, cfg.level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(message)s")
    if cfg.file:
        log_path = Path(cfg.file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path, maxBytes=cfg.max_bytes, backupCount=cfg.backup_count
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(handler)

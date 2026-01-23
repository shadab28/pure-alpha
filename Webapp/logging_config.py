"""Central logging configuration used by the webapp.

Provides:
- setup_logging(level) -> configures root logger, console and per-component file handlers
- get_logger(name, filename) -> convenience to obtain a logger that writes to date-based foldered files

Log files will be created under ./logs/YYYY-MM-DD/<filename> and rotated daily by the handler logic.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from logging import Handler
from typing import Optional

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
LOG_BASE_DIR = os.path.join(REPO_ROOT, 'logs')
os.makedirs(LOG_BASE_DIR, exist_ok=True)


def _get_date_log_dir() -> str:
    today = datetime.now().strftime('%Y-%m-%d')
    date_dir = os.path.join(LOG_BASE_DIR, today)
    os.makedirs(date_dir, exist_ok=True)
    return date_dir


class DateFolderFileHandler(logging.FileHandler):
    """File handler that writes into date-based subfolders and rolls file path when date changes."""
    def __init__(self, filename: str, mode: str = 'a', encoding: Optional[str] = 'utf-8') -> None:
        self.base_filename = filename
        self._current_date = None
        # set initial path
        self._update_stream()
        super().__init__(self._get_current_path(), mode=mode, encoding=encoding)

    def _get_current_path(self) -> str:
        return os.path.join(_get_date_log_dir(), self.base_filename)

    def _update_stream(self) -> bool:
        today = datetime.now().strftime('%Y-%m-%d')
        if self._current_date != today:
            self._current_date = today
            return True
        return False

    def emit(self, record: logging.LogRecord) -> None:
        if self._update_stream():
            # switch file
            try:
                if getattr(self, 'stream', None):
                    self.stream.close()
            except Exception:
                pass
            self.baseFilename = self._get_current_path()
            os.makedirs(os.path.dirname(self.baseFilename), exist_ok=True)
            self.stream = self._open()
        super().emit(record)


def setup_logging(level: str | int = logging.INFO) -> None:
    """Configure root logging and create common loggers and handlers.

    level can be a logging level int or a string like 'INFO'.
    This should be called early in application startup (before importing modules that log).
    """
    # normalize
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler (simple)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s - %(message)s', '%Y-%m-%d %H:%M:%S'))
        root.addHandler(ch)

    # Quiet some noisy libraries by default
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('kiteconnect').setLevel(logging.WARNING)
    logging.getLogger('werkzeug').setLevel(logging.INFO)


def get_logger(name: str, filename: str, level: Optional[int] = None) -> logging.Logger:
    """Return a logger writing to a date-foldered file named `filename`.

    Example: get_logger('access', 'access.log')
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    if level is None:
        level = logging.INFO
    logger.setLevel(level)

    fh = DateFolderFileHandler(filename)
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s - %(message)s'))
    logger.addHandler(fh)

    # Also propagate to root so console shows important messages
    logger.propagate = True
    return logger

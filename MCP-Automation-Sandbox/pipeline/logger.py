"""Structured logging for the MCP Adapter Generator pipeline.

Provides a consistent, coloured logger that shows each pipeline stage
with timing information.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Generator


# ── Colour formatter ──────────────────────────────────────────────────────


class _ColourFormatter(logging.Formatter):
    """ANSI-coloured log formatter for terminal output."""

    GREY = "\033[90m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    LEVEL_COLOURS = {
        logging.DEBUG: GREY,
        logging.INFO: CYAN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: RED + BOLD,
    }

    def format(self, record: logging.LogRecord) -> str:
        colour = self.LEVEL_COLOURS.get(record.levelno, self.RESET)
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        ms = f"{record.created % 1:.3f}"[1:]
        prefix = f"{self.GREY}{ts}{ms}{self.RESET}"
        stage = getattr(record, "stage", "")
        if stage:
            stage_str = f" {self.BOLD}[{stage}]{self.RESET}"
        else:
            stage_str = ""
        msg = f"{prefix}{stage_str} {colour}{record.getMessage()}{self.RESET}"
        return msg


# ── Module-level logger ───────────────────────────────────────────────────

_logger = logging.getLogger("mcp_adapter")


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure and return the pipeline logger."""
    level = logging.DEBUG if verbose else logging.INFO
    _logger.setLevel(level)

    if not _logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_ColourFormatter())
        _logger.addHandler(handler)

    _logger.propagate = False
    return _logger


def get_logger() -> logging.Logger:
    """Return the pipeline logger (call setup_logging first)."""
    if not _logger.handlers:
        setup_logging()
    return _logger


# ── Stage context manager ─────────────────────────────────────────────────


@contextmanager
def log_stage(stage_name: str) -> Generator[logging.Logger, None, None]:
    """Context manager that logs stage entry/exit with timing."""
    logger = get_logger()
    start = time.perf_counter()
    logger.info("━━ %s started", stage_name, extra={"stage": stage_name})
    try:
        yield logger
    except Exception:
        elapsed = time.perf_counter() - start
        logger.error(
            "━━ %s FAILED (%.2fs)", stage_name, elapsed,
            extra={"stage": stage_name},
        )
        raise
    else:
        elapsed = time.perf_counter() - start
        logger.info(
            "━━ %s completed (%.2fs)", stage_name, elapsed,
            extra={"stage": stage_name},
        )

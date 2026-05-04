import os
import sys

from loguru import logger


def setup_logging(debug: bool = False, verbose: bool = False) -> None:
    level = (
        "DEBUG" if debug
        else "INFO" if verbose
        else os.environ.get("LOG_LEVEL", "WARNING")
    )
    logger.remove()
    logger.add(sys.stderr, level=level, format="[{level}] {message}")
    logger.debug("startup: logging configured at level={level}", level=level)

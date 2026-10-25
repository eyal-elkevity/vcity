import logging
import sys

from loguru import logger


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

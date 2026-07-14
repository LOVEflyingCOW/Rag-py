from __future__ import annotations

import logging
import sys
from typing import Optional
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logger(name: str = "rag_system", log_file: Optional[str] = "./data/rag_system.log",
                 level: int = logging.INFO) -> logging.Logger:
    """配置统一的日志系统

    同时输出到控制台和文件
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


logger = setup_logger()
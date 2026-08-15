"""结构化日志 — structlog JSON 输出 + trace_id 注入

设计:
1. 使用 structlog 输出 JSON 格式日志, 便于 ELK/Loki 聚合
2. 每个请求自动注入 trace_id, 贯穿整个请求链路
3. 降级兼容: structlog 未安装时使用标准 logging
4. 同时输出到控制台和文件 (RotatingFileHandler)
5. 保留原有 logger 接口, 不破坏已有代码

日志格式 (JSON):
{
    "event": "User registered",
    "level": "info",
    "timestamp": "2026-08-15T10:30:00Z",
    "trace_id": "abc123",
    "user_id": 42,
    "method": "POST",
    "path": "/api/v1/auth/register"
}
"""

from __future__ import annotations

import logging
import sys
import uuid
from typing import Optional, Any

from logging.handlers import RotatingFileHandler
from pathlib import Path

# 尝试导入 structlog
try:
    import structlog
    _HAS_STRUCTLOG = True
except ImportError:
    _HAS_STRUCTLOG = False


# ============================================================
#  structlog 配置
# ============================================================

def setup_logging(
    name: str = "rag_system",
    log_file: Optional[str] = "./data/rag_system.log",
    level: int = logging.INFO,
    json_output: bool = True,
) -> logging.Logger:
    """配置统一的日志系统

    Args:
        name: logger 名称
        log_file: 日志文件路径 (None 不写文件)
        level: 日志级别
        json_output: 是否输出 JSON 格式 (需要 structlog)

    Returns:
        配置好的 Logger 实例
    """
    if _HAS_STRUCTLOG and json_output:
        return _setup_structlog(name, log_file, level)
    else:
        return _setup_stdlib_logging(name, log_file, level)


def _setup_structlog(
    name: str,
    log_file: Optional[str],
    level: int,
) -> logging.Logger:
    """配置 structlog JSON 日志"""
    # 先配置标准 logging (structlog 底层使用标准 logging)
    std_logger = logging.getLogger(name)
    std_logger.setLevel(level)

    if std_logger.handlers:
        return std_logger

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    std_logger.addHandler(console_handler)

    # 文件 handler
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(level)
        std_logger.addHandler(file_handler)

    std_logger.propagate = False

    # 配置 structlog
    processors = [
        structlog.contextvars.merge_contextvars,       # 合并 trace_id 等上下文
        structlog.stdlib.add_log_level,               # 添加 level 字段
        structlog.processors.TimeStamper(fmt="iso"),   # ISO 时间戳
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,          # 异常信息格式化
    ]

    if level <= logging.DEBUG:
        # DEBUG 模式下使用可读的控制台输出
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        # 生产模式 JSON 输出
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    return std_logger


def _setup_stdlib_logging(
    name: str,
    log_file: Optional[str],
    level: int,
) -> logging.Logger:
    """降级: 标准库 logging (structlog 不可用时)"""
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


# ============================================================
#  初始化全局 logger
# ============================================================

logger = setup_logging()

# 如果有 structlog, 获取 structlog logger (支持 .info("event", key=value) 语法)
if _HAS_STRUCTLOG:
    struct_logger = structlog.get_logger("rag_system")
else:
    struct_logger = logger


# ============================================================
#  trace_id 上下文管理
# ============================================================

def bind_request_context(
    trace_id: Optional[str] = None,
    user_id: Optional[int] = None,
    method: str = "",
    path: str = "",
) -> str:
    """绑定请求上下文到 structlog contextvars

    在请求中间件中调用, 将 trace_id 等信息注入到日志上下文。
    后续所有日志都会自动包含这些字段。

    Args:
        trace_id: 追踪 ID (不传则自动生成)
        user_id: 用户 ID
        method: HTTP 方法
        path: 请求路径

    Returns:
        trace_id (用于响应头)
    """
    if not _HAS_STRUCTLOG:
        return trace_id or uuid.uuid4().hex

    if not trace_id:
        trace_id = uuid.uuid4().hex

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        trace_id=trace_id,
        user_id=user_id,
        method=method,
        path=path,
    )
    return trace_id


def bind_user_context(user_id: int, username: str = ""):
    """在认证后绑定用户上下文

    Args:
        user_id: 用户 ID
        username: 用户名
    """
    if _HAS_STRUCTLOG:
        structlog.contextvars.bind_contextvars(
            user_id=user_id,
            username=username,
        )


def clear_request_context():
    """清除请求上下文 (请求结束时调用)"""
    if _HAS_STRUCTLOG:
        structlog.contextvars.clear_contextvars()


# ============================================================
#  兼容接口: 供已有代码使用 logger.debug/info/warning/error
# ============================================================

def get_logger(name: str = "rag_system"):
    """获取 logger 实例

    有 structlog 时返回 structlog logger (支持结构化日志)
    否则返回标准 logging logger
    """
    if _HAS_STRUCTLOG:
        return structlog.get_logger(name)
    return logging.getLogger(name)

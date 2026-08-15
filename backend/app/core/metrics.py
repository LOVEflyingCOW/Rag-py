"""Prometheus 指标采集

设计:
1. HTTP 请求指标: 请求数、延迟、状态码分布
2. RAG 业务指标: 查询数、检索延迟、LLM 延迟
3. 系统指标: 活跃用户、DB 连接池使用率
4. 降级: prometheus_client 未安装时所有操作 no-op
5. /metrics 端点供 Prometheus 抓取

指标列表:
  rag_http_requests_total{method, path, status}     — HTTP 请求总数
  rag_http_request_duration_seconds{method, path}   — HTTP 请求延迟
  rag_queries_total{kb_id, provider}               — RAG 查询总数
  rag_query_duration_seconds{kb_id}                 — RAG 查询延迟
  rag_retrieval_duration_seconds{kb_id}             — 检索延迟
  rag_llm_duration_seconds{provider}                — LLM 生成延迟
  rag_active_users                                — 活跃用户数
  rag_db_pool_in_use                              — DB 连接池使用数
"""

from __future__ import annotations

import time
from typing import Optional

from app.core.logging import logger

# 依赖检测
try:
    from prometheus_client import (
        Counter, Histogram, Gauge, generate_latest,
        CONTENT_TYPE_LATEST,
    )
    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False
    logger.info("prometheus-client not available, metrics will be no-op")


# ============================================================
#  指标定义
# ============================================================

if _HAS_PROMETHEUS:
    # HTTP 请求指标
    HTTP_REQUEST_COUNT = Counter(
        "rag_http_requests_total",
        "Total HTTP requests",
        ["method", "path", "status"]
    )
    HTTP_REQUEST_LATENCY = Histogram(
        "rag_http_request_duration_seconds",
        "HTTP request duration",
        ["method", "path"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
    )

    # RAG 业务指标
    RAG_QUERY_COUNT = Counter(
        "rag_queries_total",
        "Total RAG queries",
        ["kb_id", "provider"]
    )
    RAG_QUERY_LATENCY = Histogram(
        "rag_query_duration_seconds",
        "RAG query duration",
        ["kb_id"],
        buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)
    )
    RAG_RETRIEVAL_LATENCY = Histogram(
        "rag_retrieval_duration_seconds",
        "Retrieval duration",
        ["kb_id"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0)
    )
    RAG_LLM_LATENCY = Histogram(
        "rag_llm_duration_seconds",
        "LLM generation duration",
        ["provider"],
        buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
    )

    # 系统指标
    ACTIVE_USERS = Gauge(
        "rag_active_users",
        "Active users in last 5 min"
    )
    DB_POOL_IN_USE = Gauge(
        "rag_db_pool_in_use",
        "Database connections in use"
    )
    DB_POOL_SIZE = Gauge(
        "rag_db_pool_size",
        "Database connection pool size"
    )
    CACHE_HIT_RATE = Gauge(
        "rag_cache_hit_rate",
        "Cache hit rate",
        ["cache_type"]
    )
else:
    # 降级: 所有指标为 None
    HTTP_REQUEST_COUNT = None
    HTTP_REQUEST_LATENCY = None
    RAG_QUERY_COUNT = None
    RAG_QUERY_LATENCY = None
    RAG_RETRIEVAL_LATENCY = None
    RAG_LLM_LATENCY = None
    ACTIVE_USERS = None
    DB_POOL_IN_USE = None
    DB_POOL_SIZE = None
    CACHE_HIT_RATE = None


# ============================================================
#  指标记录接口 (安全的 no-op 降级)
# ============================================================

def record_http_request(method: str, path: str, status: int, duration: float):
    """记录 HTTP 请求指标"""
    if not _HAS_PROMETHEUS:
        return
    try:
        HTTP_REQUEST_COUNT.labels(method=method, path=path, status=str(status)).inc()
        HTTP_REQUEST_LATENCY.labels(method=method, path=path).observe(duration)
    except Exception as e:
        logger.debug("Failed to record HTTP metric: %s", str(e))


def record_rag_query(kb_id: int, provider: str, duration: float):
    """记录 RAG 查询指标"""
    if not _HAS_PROMETHEUS:
        return
    try:
        RAG_QUERY_COUNT.labels(kb_id=str(kb_id), provider=provider).inc()
        RAG_QUERY_LATENCY.labels(kb_id=str(kb_id)).observe(duration)
    except Exception as e:
        logger.debug("Failed to record RAG query metric: %s", str(e))


def record_retrieval(kb_id: int, duration: float):
    """记录检索延迟"""
    if not _HAS_PROMETHEUS:
        return
    try:
        RAG_RETRIEVAL_LATENCY.labels(kb_id=str(kb_id)).observe(duration)
    except Exception:
        pass


def record_llm(provider: str, duration: float):
    """记录 LLM 生成延迟"""
    if not _HAS_PROMETHEUS:
        return
    try:
        RAG_LLM_LATENCY.labels(provider=provider).observe(duration)
    except Exception:
        pass


def set_active_users(count: int):
    """设置活跃用户数"""
    if not _HAS_PROMETHEUS:
        return
    try:
        ACTIVE_USERS.set(count)
    except Exception:
        pass


def set_db_pool_metrics(in_use: int, pool_size: int):
    """设置 DB 连接池指标"""
    if not _HAS_PROMETHEUS:
        return
    try:
        DB_POOL_IN_USE.set(in_use)
        DB_POOL_SIZE.set(pool_size)
    except Exception:
        pass


def set_cache_hit_rate(cache_type: str, rate: float):
    """设置缓存命中率"""
    if not _HAS_PROMETHEUS:
        return
    try:
        CACHE_HIT_RATE.labels(cache_type=cache_type).set(rate)
    except Exception:
        pass


def get_metrics() -> tuple:
    """获取 Prometheus 指标数据

    Returns:
        (metrics_bytes, content_type) 或 (b"", "text/plain")
    """
    if not _HAS_PROMETHEUS:
        return b"prometheus-client not installed", "text/plain"
    try:
        return generate_latest(), CONTENT_TYPE_LATEST
    except Exception as e:
        logger.error("Failed to generate metrics: %s", str(e))
        return b"error generating metrics", "text/plain"


def is_metrics_enabled() -> bool:
    """检查 Prometheus 是否可用"""
    return _HAS_PROMETHEUS

from __future__ import annotations

"""外部渠道集成服务 —— 接入 Shopify / 微信 / Slack / 通用 Webhook 等客服场景

核心能力:
  1. 渠道(channel)抽象 —— 每个商家可以配置多个"接入渠道"
     - Shopify: 解析 Shopify Chat / Inbox / API 消息，调用 RAG 后回复
     - GENERIC_HTTP: 通用 HTTP Webhook（任意平台接入）
  2. 入站消息路由 —— 接收外部平台的消息 → 调用 RAG/Agent → 生成回复
  3. 出站消息适配 —— 把 RAG 的回答格式化为各平台需要的响应格式

典型接入流程(Shopify):
  ┌──────────────┐   HTTP POST   ┌──────────────────┐    RAG/Agent    ┌──────────┐
  │  Shopify     │ ─────────────▶│  /integration/   │ ──────────────▶ │知识库/LLM│
  │  客户消息     │   (Webhook)   │  webhook/{token} │                 └──────────┘
  └──────────────┘               └──────────────────┘                        │
                                        │                                     │
                                        ▼                                     │
                               解析 + 提取 query_text                         │
                                        │                                     │
                                        └─────────────────────────────────────┘
                                                                 返回回复

   或者 (主动拉取模式，Shopify 可在前端用 App Proxy):
     customer → Shopify Storefront → (App Proxy) → /integration/shopify
"""

import json
import time
import hashlib
import hmac
from typing import List, Optional, Dict, Any
from datetime import datetime
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import logger
from app.models.entities.knowledge_base import KnowledgeBase


# ============ 渠道类型枚举 ============

CHANNEL_SHOPIFY = "shopify"
CHANNEL_GENERIC = "generic_http"
CHANNEL_WECHAT = "wechat"
CHANNEL_SLACK = "slack"
CHANNEL_CUSTOM = "custom"

VALID_CHANNELS = {CHANNEL_SHOPIFY, CHANNEL_GENERIC, CHANNEL_WECHAT, CHANNEL_SLACK, CHANNEL_CUSTOM}


# ============ 数据结构 ============

@dataclass
class InboundMessage:
    """统一的入站消息结构（从任意渠道解析后产生）"""
    channel: str                        # 渠道类型
    channel_config_id: Optional[int]    # 渠道配置ID（DB里保存的）
    kb_id: int                          # 目标知识库 ID
    external_user_id: str               # 外部用户标识（Shopify customer_id / openid 等）
    external_conversation_id: Optional[str]  # 外部会话ID（用于多轮）
    query_text: str                     # 客户原始消息文本
    raw_payload: Dict[str, Any]         # 原始请求体（调试用）
    metadata: Dict[str, Any] = None     # 额外上下文

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class OutboundReply:
    """统一的回复结构 —— 各渠道 render 成自己的格式"""
    answer_text: str
    sources: List[Dict[str, Any]] = None
    agent_steps: List[Dict[str, Any]] = None
    latency_ms: float = 0.0
    raw_context: Dict[str, Any] = None

    def __post_init__(self):
        if self.sources is None:
            self.sources = []
        if self.agent_steps is None:
            self.agent_steps = []
        if self.raw_context is None:
            self.raw_context = {}


# ============ 主服务类 ============

class IntegrationService:
    """外部渠道集成主服务"""

    # 内置演示用的 secret（生产环境请配置在 settings 中）
    DEFAULT_GENERIC_SECRET = "rag-demo-generic-secret-change-me"

    def __init__(self, db: Optional[Session] = None):
        self.db = db

    # ---------- 1) 工具: 生成 Webhook Token ----------
    @staticmethod
    def generate_webhook_token(channel: str, kb_id: int, salt: Optional[str] = None) -> str:
        """生成一个不可猜测的 webhook 访问令牌
        格式: {channel}_{kb_id}_{hmac_signature}
        注意: channel 可能包含下划线 (如 generic_http)，所以解析时从右侧拆分
        """
        salt = salt or settings.SECRET_KEY or IntegrationService.DEFAULT_GENERIC_SECRET
        raw = f"{channel}|{kb_id}|{salt}|{int(time.time() / 86400)}"
        sig = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        return f"{channel}_{kb_id}_{sig}"

    @staticmethod
    def verify_webhook_token(token: str) -> Optional[tuple[str, int]]:
        """校验 token，并返回 (channel, kb_id)；失败返回 None"""
        try:
            if not token or "_" not in token:
                return None
            sig_len = 24
            if len(token) <= sig_len + 2:
                return None
            sig = token[-sig_len:]
            rest = token[:-(sig_len + 1)]
            parts = rest.rsplit("_", 1)
            if len(parts) != 2:
                return None
            channel, kb_id_str = parts
            if channel not in VALID_CHANNELS:
                return None
            kb_id = int(kb_id_str)
            # 容灾: 允许多个 salt（含默认 salt）与 today/yesterday 两个 day-key（防止深夜跨天）
            today = int(time.time() / 86400)
            salts = [settings.SECRET_KEY, IntegrationService.DEFAULT_GENERIC_SECRET]
            salts = [s for s in salts if s]
            for salt in salts:
                for day_key in (today, today - 1):
                    raw = f"{channel}|{kb_id}|{salt}|{day_key}"
                    candidate = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
                    if sig == candidate:
                        return channel, kb_id
            return None
        except Exception:
            return None

    # ---------- 2) 通用 HTTP Webhook 入站解析 ----------
    def parse_generic_http(self, payload: Dict[str, Any], kb_id: int) -> Optional[InboundMessage]:
        """解析通用 HTTP webhook。推荐请求体格式:
        {
            "query": "你们支持什么支付方式？",        # 必填
            "user_id": "shopify_customer_12345",      # 建议：外部用户标识
            "conversation_id": "conv_abc",            # 可选：用于多轮上下文
            "metadata": { "shop": "mystore.myshopify.com" }
        }
        为了兼容任意格式，也接受: {"message": "...", "text": "...", "content": "..."}
        """
        if not isinstance(payload, dict):
            return None

        # 宽容地找 query 字段
        query = (
            payload.get("query")
            or payload.get("message")
            or payload.get("text")
            or payload.get("content")
            or payload.get("msg")
            or ""
        )
        if not query or not isinstance(query, str) or not query.strip():
            return None

        return InboundMessage(
            channel=CHANNEL_GENERIC,
            channel_config_id=None,
            kb_id=kb_id,
            external_user_id=str(payload.get("user_id") or payload.get("userId") or "anon"),
            external_conversation_id=payload.get("conversation_id") or payload.get("session_id") or None,
            query_text=query.strip(),
            raw_payload=payload,
            metadata=payload.get("metadata") or {},
        )

    # ---------- 3) Shopify Webhook 入站解析（Chat / App Proxy / 自定义） ----------
    def parse_shopify_webhook(self, payload: Dict[str, Any], headers: Dict[str, str],
                              kb_id: int) -> Optional[InboundMessage]:
        """解析 Shopify 发来的消息。

        Shopify 常见的消息来源(任选其一接入):
          a) Shopify Inbox 自定义 App —— 消息格式随 App 定义
          b) Shopify Storefront App Proxy —— 前端 Liquid 把客户消息 POST 过来
          c) 自建前端使用 Shopify Storefront API

        此函数兼容常用的几种字段名。
        """
        if not isinstance(payload, dict):
            return None

        query = (
            payload.get("query")
            or payload.get("message")
            or payload.get("text")
            or payload.get("content")
            or ""
        )

        # Shopify App Proxy 常见: customer 字段
        customer = payload.get("customer") or {}
        if isinstance(customer, dict):
            user_id = str(customer.get("id") or customer.get("email") or customer.get("phone") or "")
        else:
            user_id = str(payload.get("customer_id") or payload.get("userId") or "")
        if not user_id:
            user_id = headers.get("X-Shopify-Customer-Id") or headers.get("x-shopify-customer-id") or "shopify_anon"

        conv_id = payload.get("conversation_id") or payload.get("session_id") or payload.get("cart_token")

        if not query or not str(query).strip():
            return None

        # 可选: 校验 HMAC（Shopify 官方 Webhook 带 X-Shopify-Hmac-Sha256）
        # 这里只记录 log，不强制失败
        hmac_sig = headers.get("X-Shopify-Hmac-Sha256") or headers.get("x-shopify-hmac-sha256")
        if hmac_sig:
            logger.info("[Integration] Shopify webhook 携带 HMAC=%s (若需严格校验请配置 SHOPIFY_WEBHOOK_SECRET)",
                         hmac_sig[:8] + "...")

        shop_domain = (
            headers.get("X-Shopify-Shop-Domain")
            or headers.get("x-shopify-shop-domain")
            or payload.get("shop_domain")
            or payload.get("shop")
            or ""
        )

        return InboundMessage(
            channel=CHANNEL_SHOPIFY,
            channel_config_id=None,
            kb_id=kb_id,
            external_user_id=user_id,
            external_conversation_id=conv_id,
            query_text=str(query).strip(),
            raw_payload=payload,
            metadata={
                "shop": shop_domain,
                "theme": payload.get("theme") or "",
                "locale": payload.get("locale") or "zh-CN",
            },
        )

    # ---------- 4) 回复格式适配 ----------
    def render_reply_for_channel(self, channel: str, reply: OutboundReply,
                                 compact: bool = False) -> Dict[str, Any]:
        """把统一的 OutboundReply 渲染为不同渠道所需的响应格式"""
        if channel == CHANNEL_GENERIC:
            return self._render_generic(reply, compact)
        elif channel == CHANNEL_SHOPIFY:
            return self._render_shopify(reply, compact)
        else:
            return self._render_generic(reply, compact)

    @staticmethod
    def _render_generic(reply: OutboundReply, compact: bool = False) -> Dict[str, Any]:
        """通用 HTTP 响应"""
        data: Dict[str, Any] = {
            "ok": True,
            "answer": reply.answer_text,
            "latency_ms": round(reply.latency_ms, 1),
        }
        if not compact:
            data["sources"] = reply.sources
            if reply.agent_steps:
                data["agent_steps"] = reply.agent_steps
            if reply.raw_context:
                data["debug"] = {k: v for k, v in list(reply.raw_context.items())[:10]}
        return data

    @staticmethod
    def _render_shopify(reply: OutboundReply, compact: bool = False) -> Dict[str, Any]:
        """Shopify 友好格式 —— 可以直接在 Liquid 模板里渲染

        返回:
          - message_html: 可直接插入 Shopify 主题的 HTML
          - plain: 纯文本版本（fallback）
          - sources_html: 引用来源 HTML 列表
        """
        answer_text = reply.answer_text or "(暂时无法回答您的问题，请稍后再试)"
        # 做最小化 HTML 转义
        import html as _html
        safe_text = _html.escape(answer_text).replace("\n", "<br>")

        sources_html = ""
        if reply.sources and not compact:
            items = []
            seen = set()
            for s in reply.sources:
                name = s.get("document_filename") or s.get("source") or "商家文档"
                if name in seen:
                    continue
                seen.add(name)
                items.append(f"<li>{_html.escape(str(name))}</li>")
            if items:
                sources_html = "<p style='font-size:12px;color:#666;margin-top:12px'><b>参考来源：</b></p><ul style='font-size:12px;color:#666'>" + "".join(items) + "</ul>"

        html_out = f"<div class='rag-customer-service-reply'>{safe_text}{sources_html}</div>"

        data: Dict[str, Any] = {
            "ok": True,
            "plain": answer_text,
            "message_html": html_out,
            "latency_ms": round(reply.latency_ms, 1),
        }
        if not compact:
            data["sources"] = reply.sources
        return data


__all__ = [
    "IntegrationService",
    "InboundMessage",
    "OutboundReply",
    "CHANNEL_SHOPIFY",
    "CHANNEL_GENERIC",
    "CHANNEL_WECHAT",
    "CHANNEL_SLACK",
    "CHANNEL_CUSTOM",
]
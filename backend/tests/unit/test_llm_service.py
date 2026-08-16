"""B1 · 单元测试 — LLM Service 层:
  • ChatMessage / ChatResult 数据契约
  • MockLLMProvider 直接本地生成 (无 API)
  • LLMService 调度逻辑 (mock / deepseek / openai / custom, 无 key 时回退 mock)
  • chat_stream 迭代逻辑

对应模块: app.processors.llm.llm_service
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.processors.llm.llm_service import (  # noqa: E402
    ChatMessage,
    ChatResult,
    MockLLMProvider,
    LLMService,
    get_llm_service,
    BaseLLMProvider,
    HTTPLLMProvider,
)


# ---- ChatMessage / ChatResult dataclass 契约 ----

def test_chat_message_basic():
    m = ChatMessage(role="user", content="hi")
    assert m.role == "user" and m.content == "hi"
    assert m.to_dict() == {"role": "user", "content": "hi"}
    assert ChatMessage.from_dict({"role": "assistant", "content": "x"}).content == "x"


def test_chat_result_usage_defaults_are_none_but_sane():
    """ChatResult 只给必填三项，其它字段用默认值。"""
    r = ChatResult(content="hello", model="mock-llm", provider="mock")
    assert r.content == "hello"
    assert r.success is True
    assert r.error is None
    assert r.latency_ms == 0.0  # 默认 0.0


def test_chat_result_usage_when_present():
    usage = {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}
    r = ChatResult(content="x", model="m", provider="p", usage=usage)
    assert r.usage["total_tokens"] == 18


# ---- BaseLLMProvider token 估算 ----

def test_base_provider_count_tokens():
    p = MockLLMProvider()  # 子类继承 count_tokens
    # 纯空字符
    assert p.count_tokens("") == 0
    # 正常文本
    assert p.count_tokens("hello world how are you today") > 0
    # _total_tokens 累加
    total = p._total_tokens([ChatMessage(role="user", content="hi"),
                              ChatMessage(role="assistant", content="ok")])
    assert total >= 4  # 至少有字符


# ---- MockLLMProvider 生成 ----

def test_mock_provider_generate_ok():
    """MockLLMProvider 的对外入口是 chat() / chat_stream(), generate() 是内部私有方法。"""
    p = MockLLMProvider()
    r = p.chat(
        [ChatMessage(role="system", content="sys"),
         ChatMessage(role="user", content="用户问题")],
        temperature=0.0, max_tokens=50,
    )
    assert isinstance(r, ChatResult)
    assert r.success
    assert r.provider == "mock"
    assert r.model.startswith("mock-llm")
    assert isinstance(r.content, str) and len(r.content) > 0


def test_mock_provider_chat_deterministic_enough():
    """两次调用都返回非空 ChatResult (Mock 带随机, 所以只断言数据结构不变)。"""
    p = MockLLMProvider()
    msgs = [ChatMessage(role="user", content="x")]
    r1 = p.chat(msgs, temperature=0.1, max_tokens=50)
    r2 = p.chat(msgs, temperature=0.1, max_tokens=50)
    assert isinstance(r1, ChatResult) and isinstance(r2, ChatResult)


def test_mock_stream_returns_iterable_of_chat_result():
    """chat_stream 默认调用 generate 并 yield 一次，但 HTTP Provider 会 yield 多次。"""
    p = MockLLMProvider()
    chunks = list(p.chat_stream([ChatMessage(role="user", content="stream test")]))
    # 至少 1 块
    assert len(chunks) >= 1
    assert all(isinstance(c, ChatResult) for c in chunks)


# ---- LLMService 调度 ----

def test_llm_service_default_provider_is_mock():
    """settings.LLM_PROVIDER 默认是空或 mock，且没有 API key → 必回退 Mock。"""
    svc = LLMService()
    assert isinstance(svc.provider, MockLLMProvider)


def test_llm_service_chat_dispatches():
    svc = LLMService()
    r = svc.chat([ChatMessage(role="user", content="ping")])
    assert isinstance(r, ChatResult)
    assert r.success


def test_get_llm_service_singleton():
    a = get_llm_service()
    b = get_llm_service()
    assert a is b


# ---- Base class 接口约束 ----

def test_base_provider_chat_not_implemented():
    class Dumb(BaseLLMProvider):
        pass

    with pytest.raises(NotImplementedError):
        Dumb().chat([ChatMessage(role="user", content="x")])


# ---- HTTPLLMProvider 参数结构 ----

def test_http_provider_requires_api_url():
    # API url 必需，key 可以为空串（实际会在调用时报错）
    p = HTTPLLMProvider(
        api_url="http://fake-host.invalid/v1/chat/completions",
        api_key="",
        model="fake",
        provider_name="custom",
    )
    assert p.provider_name == "custom"

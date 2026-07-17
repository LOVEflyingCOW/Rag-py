from __future__ import annotations

"""LLM 大语言模型服务

提供统一接口：
  - MockLLMProvider    ：本地回退，用于无 API Key 环境
  - DeepSeekLLMProvider ：DeepSeek Chat / Reasoner
  - OpenAILLMProvider  ：OpenAI GPT 兼容接口
  - CustomLLMProvider  ：自定义 OpenAI 兼容接口（Ollama/自建服务）

所有 provider 统一实现：
  - chat(messages, **kwargs) -> ChatResult
  - count_tokens(text) -> int   （粗略估算）
  - get_provider_name() -> str

并发安全：
  - provider 内部使用 requests.Session，外部用 threading.Lock 保护。
  - 每个 provider 实例是可重入的。
"""

import json
import time
import random
import threading
from typing import List, Dict, Optional, Any
from dataclasses import dataclass


from app.core.config import settings
from app.core.logging import logger as _logger


# ============ 数据结构 ============

@dataclass
class ChatMessage:
    """一条消息 —— role: system / user / assistant"""
    role: str
    content: str

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ChatMessage":
        return cls(role=d["role"], content=d["content"])


@dataclass
class ChatResult:
    """LLM 调用结果

    content : LLM 回答字符串
    model   : 实际使用的模型名
    usage   : {prompt_tokens, completion_tokens, total_tokens}，可能 None
    success : True/False
    error   : 失败时的错误信息
    latency_ms : 调用耗时 (ms)
    """
    content: str
    model: str
    provider: str
    success: bool = True
    error: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    latency_ms: float = 0.0


# ============ Base Provider ============

class BaseLLMProvider:
    """LLM Provider 基类 —— 所有 provider 都必须实现 chat()"""

    provider_name: str = "base"

    def __init__(self):
        self._lock = threading.RLock()

    def chat(
        self,
        messages: List[ChatMessage],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        stop: Optional[List[str]] = None,
        **kwargs,
    ) -> ChatResult:
        raise NotImplementedError("subclass must implement chat()")

    def count_tokens(self, text: str) -> int:
        """非常粗略的 token 估算：≈ 1 token = 4 chars / 1 词"""
        if not text:
            return 0
        return max(1, len(text) // 4 + len(text.split()) // 2)

    def _total_tokens(self, messages: List[ChatMessage]) -> int:
        return sum(self.count_tokens(m.content) + len(m.role) + 2 for m in messages)


# ============ Mock Provider (无 API Key 环境) ============

class MockLLMProvider(BaseLLMProvider):
    """Mock LLM —— 不联网，基于规则的简单回复，用于开发测试

    策略：
      1. 从 system prompt 中解析出结构化的 chunks（[#1], [#2]...）
      2. 基于 user query 的关键词，从 chunks 中筛选最相关的 1-2 条
      3. 生成一个"摘要式"的简短回答，带 [来源 #N] 引用标记
      4. 如果无 chunks 或 chunks 与 query 完全无关，明确拒绝回答（幻觉抑制）
    """

    provider_name = "mock"

    TEMPLATE_REJECT = (
        "很抱歉，在知识库中未能检索到足够的相关信息来回答您的问题。\n"
        "（提示：当前为 Mock 模式——本回答由本地规则引擎生成，不依赖外部 LLM API。"
        "配置 LLM API Key 后可获得更高质量的回答）"
    )

    def _parse_chunks(self, system_text: str) -> List[Dict[str, Any]]:
        """从 system prompt 中解析 chunks: [{idx, source, content}]

        支持两种格式：
          格式 A（完整）：[#1] (来源: doc_1.txt, 相似度: 0.789)\n内容
          格式 B（简化）：[#1] 内容直接跟在标记后面
        """
        import re
        chunks = []

        # 尝试从"【知识库片段】"标记之后开始解析，避免受 system prompt 规则文本干扰
        fragment_markers = ["【知识库片段】", "---", "知识库片段"]
        start_idx = 0
        for marker in fragment_markers:
            idx = system_text.find(marker)
            if idx >= 0:
                start_idx = idx + len(marker)
                break
        search_text = system_text[start_idx:]

        # 格式 A：[#1] (来源: xxx, 相似度: 0.xxx)\n内容
        # 格式 B：[#1] 内容...
        # 通用匹配：[#数字] 后面跟可选元信息括号，然后是内容直到下一个 [#数字] 或结束
        pattern = re.compile(
            r"\[#(\d+)\]"          # [#1] 编号
            r"(?:\s*\(([^)]*)\))?" # 可选：(来源: xxx, 相似度: xxx)
            r"\s*\n?"              # 可选换行
            r"(.*?)"               # 内容（非贪婪）
            r"(?=\[#\d+\]|\Z)",    # 下一个 [#N] 或结尾
            re.DOTALL
        )
        for m in pattern.finditer(search_text):
            idx = int(m.group(1))
            meta = m.group(2) or ""
            content = m.group(3).strip()
            if content:
                source = ""
                if "来源" in meta:
                    source_parts = meta.split(",")
                    for sp in source_parts:
                        if "来源" in sp:
                            source = sp.split(":", 1)[-1].strip()
                            break
                chunks.append({"idx": idx, "source": source, "content": content})

        return chunks

    def _extract_keywords(self, text: str) -> List[str]:
        """从文本中提取关键词（用于粗略判断相关性）"""
        import re
        if not text:
            return []
        # 中文：保留 2+ 字符的片段；英文：保留 3+ 字符的词
        tokens = []
        # 中文连续片段
        for seg in re.findall(r"[\u4e00-\u9fa5A-Za-z]+", text):
            if len(seg) >= 2:
                tokens.append(seg.lower())
        # 英文单词
        for w in re.findall(r"[A-Za-z]{3,}", text):
            tokens.append(w.lower())
        # 去重，返回前 10 个
        seen = []
        for t in tokens:
            if t not in seen and t not in {"是什么", "的", "是", "在", "有", "吗", "如何", "怎么", "什么", "哪个", "哪里", "请问", "the", "and", "for", "are", "was", "not", "how", "what", "why", "when"}:
                seen.append(t)
                if len(seen) >= 10:
                    break
        return seen

    def _relevance_score(self, chunk_text: str, query_keywords: List[str]) -> float:
        """判断 chunk 与 query 的相关性（mock 用的粗略关键词匹配）"""
        if not query_keywords:
            return 0.0
        chunk_lower = chunk_text.lower()
        hits = 0
        for kw in query_keywords:
            if kw in chunk_lower:
                hits += 1
        return hits / len(query_keywords)

    def chat(
        self,
        messages: List[ChatMessage],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        stop: Optional[List[str]] = None,
        **kwargs,
    ) -> ChatResult:
        with self._lock:
            start = time.perf_counter()

            # 找到 user 最后一条 message + system message
            user_query = ""
            system_text = ""
            for m in reversed(messages):
                if m.role == "user" and not user_query:
                    user_query = m.content[:200]
                if m.role == "system" and not system_text:
                    system_text = m.content

            # --- 幻觉抑制第 1 层：system prompt 本身就说无内容 ---
            if "未能检索到任何相关片段" in system_text or "知识库中无相关内容" in system_text:
                content = self.TEMPLATE_REJECT
                latency = (time.perf_counter() - start) * 1000
                tokens = self._total_tokens(messages)
                return ChatResult(
                    content=content, model="mock-llm", provider=self.provider_name,
                    success=True,
                    usage={"prompt_tokens": tokens, "completion_tokens": len(content) // 4, "total_tokens": tokens + len(content) // 4},
                    latency_ms=latency,
                )

            # --- 解析 chunks ---
            chunks = self._parse_chunks(system_text)
            query_kws = self._extract_keywords(user_query)

            # --- 幻觉抑制第 2 层：没有 chunks → 拒绝 ---
            if not chunks:
                content = self.TEMPLATE_REJECT
                latency = (time.perf_counter() - start) * 1000
                tokens = self._total_tokens(messages)
                return ChatResult(
                    content=content, model="mock-llm", provider=self.provider_name,
                    success=True,
                    usage={"prompt_tokens": tokens, "completion_tokens": len(content) // 4, "total_tokens": tokens + len(content) // 4},
                    latency_ms=latency,
                )

            # --- 选最相关的 1-2 条 chunks ---
            scored = []
            for c in chunks:
                rel = self._relevance_score(c["content"], query_kws)
                scored.append((rel, c))
            scored.sort(key=lambda x: x[0], reverse=True)

            # --- 幻觉抑制第 3 层：最高相关性为 0（完全无关键词重叠） → 拒绝 ---
            top_relevance = scored[0][0] if scored else 0.0
            if top_relevance <= 0 and query_kws:
                content = self.TEMPLATE_REJECT
                latency = (time.perf_counter() - start) * 1000
                tokens = self._total_tokens(messages)
                return ChatResult(
                    content=content, model="mock-llm", provider=self.provider_name,
                    success=True,
                    usage={"prompt_tokens": tokens, "completion_tokens": len(content) // 4, "total_tokens": tokens + len(content) // 4},
                    latency_ms=latency,
                )

            # --- 生成简短回答：基于最相关 chunk 内容摘要 + [来源 #N] ---
            top_chunks = [s[1] for s in scored[:2] if s[0] > 0]
            if not top_chunks:
                top_chunks = [scored[0][1]]  # 实在没有命中关键词的就用第一条（但前面已过滤 relevance=0）

            answer_parts = []
            for c in top_chunks[:2]:
                # 取 chunk 内容的前 80 字作为摘要
                summary = c["content"][:80].strip()
                if len(c["content"]) > 80:
                    summary += "..."
                answer_parts.append("%s [来源 #%d]" % (summary, c["idx"]))

            answer_body = "\n".join(answer_parts)
            content = (
                "根据知识库中的信息：\n"
                "%s\n"
                "\n（Mock 模式回答——实际环境请配置 LLM API Key 以获得更自然的问答体验）"
            ) % answer_body

            latency = (time.perf_counter() - start) * 1000
            tokens = self._total_tokens(messages)

            return ChatResult(
                content=content,
                model="mock-llm",
                provider=self.provider_name,
                success=True,
                usage={"prompt_tokens": tokens, "completion_tokens": len(content) // 4, "total_tokens": tokens + len(content) // 4},
                latency_ms=latency,
            )


# ============ HTTP Provider (DeepSeek / OpenAI / Custom) ============

class HTTPLLMProvider(BaseLLMProvider):
    """OpenAI 兼容协议的通用 LLM Provider —— DeepSeek / OpenAI / Custom 共享"""

    def __init__(self, api_url: str, api_key: str, model: str, provider_name: str = "http"):
        super().__init__()
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.provider_name = provider_name
        try:
            import requests
            self._session = requests.Session()
        except Exception:
            self._session = None

    # ---------- 核心调用 ----------
    def chat(
        self,
        messages: List[ChatMessage],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        stop: Optional[List[str]] = None,
        **kwargs,
    ) -> ChatResult:
        with self._lock:
            return self._call_with_retry(
                messages,
                temperature=temperature if temperature is not None else settings.LLM_TEMPERATURE,
                max_tokens=max_tokens if max_tokens is not None else settings.LLM_MAX_TOKENS,
                top_p=top_p if top_p is not None else settings.LLM_TOP_P,
                stop=stop,
            )

    # ---------- 内部：带重试 ----------
    def _call_with_retry(self, messages: List[ChatMessage], **gen_params) -> ChatResult:
        start = time.perf_counter()

        if self._session is None:
            return ChatResult(
                content="",
                model=self.model,
                provider=self.provider_name,
                success=False,
                error="requests 未安装，请 pip install requests",
                latency_ms=(time.perf_counter() - start) * 1000,
            )

        payload = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": gen_params.get("temperature", 0.3),
            "max_tokens": gen_params.get("max_tokens", 1024),
            "top_p": gen_params.get("top_p", 0.9),
            "stream": False,
        }
        if gen_params.get("stop"):
            payload["stop"] = gen_params["stop"]

        last_error: Optional[str] = None
        for attempt in range(settings.LLM_MAX_RETRIES):
            try:
                response = self._session.post(
                    "%s/chat/completions" % self.api_url,
                    headers={
                        "Authorization": "Bearer %s" % self.api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=settings.LLM_TIMEOUT,
                )
                if response.status_code != 200:
                    last_error = "HTTP %d: %s" % (response.status_code, response.text[:500])
                    # 429 / 5xx 才重试，其他错误不重试
                    if response.status_code not in (429, 500, 502, 503, 504):
                        break
                    time.sleep(settings.LLM_RETRY_BACKOFF * (attempt + 1))
                    continue

                data = response.json()
                choices = data.get("choices", [])
                if not choices or "message" not in choices[0]:
                    last_error = "响应格式异常: %s" % json.dumps(data, ensure_ascii=False)[:300]
                    break

                content = choices[0]["message"].get("content", "")
                usage = data.get("usage")

                return ChatResult(
                    content=content,
                    model=data.get("model", self.model),
                    provider=self.provider_name,
                    success=True,
                    usage=usage,
                    latency_ms=(time.perf_counter() - start) * 1000,
                )

            except Exception as exc:
                last_error = "请求异常 (%s): %s" % (type(exc).__name__, str(exc)[:300])
                time.sleep(settings.LLM_RETRY_BACKOFF * (attempt + 1))

        return ChatResult(
            content="",
            model=self.model,
            provider=self.provider_name,
            success=False,
            error=last_error,
            latency_ms=(time.perf_counter() - start) * 1000,
        )


# ============ 工厂方法 ============

class LLMService:
    """统一 LLM 服务入口 —— 根据配置自动选择 provider"""

    def __init__(self):
        self.provider = self._build_provider()

    def _build_provider(self) -> BaseLLMProvider:
        provider = (settings.LLM_PROVIDER or "mock").strip().lower()

        if provider == "deepseek" and settings.DEEPSEEK_API_KEY:
            return HTTPLLMProvider(
                api_url=settings.DEEPSEEK_API_URL,
                api_key=settings.DEEPSEEK_API_KEY,
                model=settings.DEEPSEEK_MODEL,
                provider_name="deepseek",
            )
        if provider == "openai" and settings.OPENAI_API_KEY:
            return HTTPLLMProvider(
                api_url=settings.OPENAI_API_URL,
                api_key=settings.OPENAI_API_KEY,
                model=settings.OPENAI_MODEL,
                provider_name="openai",
            )
        if provider == "custom" and settings.LLM_CUSTOM_API_URL:
            return HTTPLLMProvider(
                api_url=settings.LLM_CUSTOM_API_URL,
                api_key=settings.LLM_CUSTOM_API_KEY,
                model=settings.LLM_CUSTOM_MODEL,
                provider_name="custom",
            )

        # 默认为 mock
        return MockLLMProvider()

    def chat(self, messages: List[ChatMessage], **kwargs) -> ChatResult:
        return self.provider.chat(messages, **kwargs)

    def provider_name(self) -> str:
        return self.provider.provider_name


# ============ 全局唯一实例（单例懒加载） ============
_llm_service: Optional[LLMService] = None
_service_lock = threading.Lock()


def get_llm_service() -> LLMService:
    global _llm_service
    if _llm_service is None:
        with _service_lock:
            if _llm_service is None:
                _llm_service = LLMService()
    return _llm_service
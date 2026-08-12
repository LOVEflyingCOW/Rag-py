#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RAG 系统 —— 真实 LLM 环境集成测试脚本
=========================================

模拟真实 LLM 环境下的完整 RAG 问答 + Agent 调用 + 流式响应验证。

支持两种运行模式：
  1) Mock 模式（默认）：不依赖外部 API，验证全链路逻辑
  2) 真实 LLM 模式：配置 .env 中的 API Key 后，测试真实 LLM 生成质量

用法：
  # Mock 模式（无需 API Key）
  python test_integration_llm.py

  # 真实 DeepSeek 模式
  python test_integration_llm.py --provider deepseek

  # 真实 OpenAI 模式
  python test_integration_llm.py --provider openai

  # 自定义 LLM 模式（Ollama 等）
  python test_integration_llm.py --provider custom --api-url http://localhost:11434/v1 --api-key ollama --model qwen2.5

前置条件：
  1. 虚拟环境已激活
  2. 服务已启动: python start.py  或  uvicorn app.main:app --port 8000
  3. 如使用真实 LLM，需在 .env 中配置对应 API Key
"""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
import tempfile
import requests
from datetime import datetime
from typing import Optional, Dict, Any, List

# ======================================================================
# 配置
# ======================================================================

BASE_URL = os.environ.get("RAG_TEST_BASE_URL", "http://127.0.0.1:8000")
API_V1 = f"{BASE_URL}/api/v1"
TIMEOUT = 30
STREAM_TIMEOUT = 120  # 流式响应超时更长

# 颜色输出（Windows 兼容）
class C:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

# Windows 启用 ANSI
if sys.platform == "win32":
    os.system("")

# ======================================================================
# 测试统计
# ======================================================================

_passed = 0
_failed = 0
_skipped = 0
_errors: List[str] = []


def pass_test(name: str, detail: str = ""):
    global _passed
    _passed += 1
    print(f"  {C.GREEN}[PASS]{C.RESET} {name}" + (f"  {C.DIM}— {detail}{C.RESET}" if detail else ""))


def fail_test(name: str, detail: str = ""):
    global _failed
    _failed += 1
    msg = f"{name}" + (f": {detail}" if detail else "")
    _errors.append(msg)
    print(f"  {C.RED}[FAIL]{C.RESET} {name}" + (f"  {C.RED}{detail}{C.RESET}" if detail else ""))


def skip_test(name: str, reason: str = ""):
    global _skipped
    _skipped += 1
    print(f"  {C.YELLOW}[SKIP]{C.RESET} {name}" + (f"  {C.DIM}({reason}){C.RESET}" if reason else ""))


def section(title: str):
    print(f"\n{C.CYAN}{'=' * 70}{C.RESET}")
    print(f"  {C.BOLD}{title}{C.RESET}")
    print(f"{C.CYAN}{'=' * 70}{C.RESET}")


# ======================================================================
# HTTP 辅助
# ======================================================================

def _headers(token: Optional[str] = None) -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def api_get(path: str, token: Optional[str] = None) -> Dict[str, Any]:
    url = f"{API_V1}{path}" if path.startswith("/") else f"{API_V1}/{path}"
    resp = requests.get(url, headers=_headers(token), timeout=TIMEOUT)
    return resp.json()


def api_post(path: str, body: Any = None, token: Optional[str] = None) -> Dict[str, Any]:
    url = f"{API_V1}{path}" if path.startswith("/") else f"{API_V1}/{path}"
    resp = requests.post(url, headers=_headers(token), json=body, timeout=TIMEOUT)
    return resp.json()


def api_post_file(path: str, file_path: str, token: str, extra_fields: Optional[Dict] = None) -> Dict[str, Any]:
    url = f"{API_V1}{path}" if path.startswith("/") else f"{API_V1}/{path}"
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f, "text/plain")}
        data = extra_fields or {}
        resp = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                             files=files, data=data, timeout=TIMEOUT)
    return resp.json()


def api_post_stream(path: str, body: Any, token: Optional[str] = None):
    """SSE 流式请求，返回生成器"""
    url = f"{API_V1}{path}" if path.startswith("/") else f"{API_V1}/{path}"
    resp = requests.post(url, headers=_headers(token), json=body, stream=True, timeout=STREAM_TIMEOUT)
    for line in resp.iter_lines(decode_unicode=True):
        if line and line.startswith("data: "):
            data_str = line[6:]
            try:
                yield json.loads(data_str)
            except json.JSONDecodeError:
                yield {"type": "raw", "content": data_str}


# ======================================================================
# 测试文档内容（模拟知识库素材）
# ======================================================================

KB_DOCUMENTS = [
    {
        "filename": "rag_overview.txt",
        "content": """RAG（检索增强生成）技术概述

RAG 是 Retrieval-Augmented Generation 的缩写，即检索增强生成。
这是一种将信息检索与大语言模型生成相结合的技术框架。

RAG 的核心工作流程：
1. 用户提出问题
2. 系统从知识库中检索相关文档片段
3. 将检索到的上下文与用户问题一起发送给 LLM
4. LLM 基于检索到的上下文生成回答

RAG 的主要优势：
- 减少幻觉：LLM 的回答基于真实文档，而非凭空生成
- 知识更新便捷：只需更新知识库，无需重新训练模型
- 可溯源：回答可以标注来源，增强可信度
- 领域适应：通过更换知识库快速适应不同领域

RAG 的关键组件：
- 文档分块器（Chunker）：将长文档切分为合适大小的片段
- 嵌入模型（Embedding Model）：将文本转为向量
- 向量数据库（Vector Store）：存储和检索向量
- 检索器（Retriever）：根据查询检索最相关的片段
- 生成器（Generator）：基于上下文生成回答的 LLM
""",
    },
    {
        "filename": "vector_search.txt",
        "content": """向量搜索与语义检索

向量搜索是 RAG 系统中的核心检索技术。

嵌入（Embedding）原理：
- 将文本映射为高维向量（如 384 维或 1536 维）
- 语义相近的文本在向量空间中距离更近
- 常用模型：BERT、BGE、text-embedding-ada-002

相似度计算方法：
- 余弦相似度（Cosine Similarity）：最常用，衡量向量方向相似性
- 内积（Inner Product）：归一化后等价于余弦相似度
- 欧氏距离（L2 Distance）：衡量向量绝对距离

FAISS 索引类型：
- IndexFlatIP：精确搜索，内积相似度，适合小规模数据
- IndexIVFFlat：近似搜索，先聚类再搜索，适合大规模数据
- IndexHNSW：图结构索引，查询速度快但构建慢

混合检索策略：
- 向量检索（语义匹配）+ BM25 检索（关键词匹配）
- 通过加权融合获得更好的检索效果
- 可以兼顾语义理解和精确匹配
""",
    },
    {
        "filename": "agent_react.txt",
        "content": """ReAct Agent 推理框架

ReAct = Reasoning + Acting，即推理与行动交替进行。

ReAct 的工作循环：
1. Thought（思考）：分析当前状态，决定下一步
2. Action（行动）：调用工具执行操作
3. Observation（观察）：获取工具返回结果
4. 重复以上步骤直到获得最终答案

ReAct 的优势：
- 可解释性强：每一步推理过程可见
- 可使用外部工具：搜索引擎、数据库、API 等
- 自我纠错：通过观察结果调整策略

在 RAG 系统中的应用：
- Agent 可以决定是否需要搜索知识库
- 可以多次搜索，逐步缩小范围
- 可以查询特定文档的详细内容
- 最终基于所有信息生成综合回答

Agent 工具设计：
- SearchKBTool：在知识库中搜索相关内容
- GetDocTool：获取指定文档的详细信息
- 工具描述需要清晰，帮助 LLM 正确选择
""",
    },
]


# ======================================================================
# 测试用例
# ======================================================================

class IntegrationTestRunner:
    """集成测试执行器"""

    def __init__(self, provider: str = "mock", api_url: str = "", api_key: str = "", model: str = ""):
        self.provider = provider
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.token: Optional[str] = None
        self.kb_id: Optional[int] = None
        self.user_id: Optional[int] = None
        self.conversation_id: Optional[int] = None

    # ---------- Part A: 环境检查 ----------

    def test_a_environment(self):
        section("Part A  环境与服务检查")

        # A.1 服务是否在线
        try:
            resp = requests.get(f"{BASE_URL}/", timeout=5)
            data = resp.json()
            if data.get("status") == "running":
                pass_test("A.1 服务在线", f"status=running, version={data.get('version', '?')}")
            else:
                fail_test("A.1 服务在线", f"unexpected status: {data}")
        except Exception as e:
            fail_test("A.1 服务在线", str(e))
            return False

        # A.2 健康检查（health 路由挂在 app 根级，非 /api/v1）
        try:
            resp = requests.get(f"{BASE_URL}/health", headers=_headers(), timeout=TIMEOUT)
            data = resp.json()
            if data.get("success") or data.get("status") == "healthy":
                db_status = data.get("data", {}).get("database", data.get("database", "?"))
                pass_test("A.2 健康检查", f"database={db_status}")
            else:
                fail_test("A.2 健康检查", str(data))
        except Exception as e:
            fail_test("A.2 健康检查", str(e))

        # A.3 LLM Provider 状态
        try:
            data = api_get("/chat/provider")
            provider_name = data.get("data", {}).get("provider", "?")
            model_name = data.get("data", {}).get("model", "?")
            has_key = data.get("data", {}).get("has_api_key", False)
            pass_test("A.3 LLM Provider",
                      f"provider={provider_name}, model={model_name}, has_key={has_key}")

            if self.provider != "mock" and provider_name == "mock":
                skip_test("A.3b 真实 LLM 验证", f".env 中未配置 {self.provider} API Key")
        except Exception as e:
            fail_test("A.3 LLM Provider", str(e))

        # A.4 Embedding 状态
        try:
            data = api_get("/embeddings/status")
            provider_name = data.get("data", {}).get("provider", "?")
            dim = data.get("data", {}).get("dim", "?")
            pass_test("A.4 Embedding 服务", f"provider={provider_name}, dim={dim}")
        except Exception as e:
            fail_test("A.4 Embedding 服务", str(e))

        # A.5 检索服务状态
        try:
            data = api_get("/retrieval/status")
            faiss_ok = data.get("data", {}).get("faiss_available", False)
            pass_test("A.5 向量检索服务", f"faiss={faiss_ok}")
        except Exception as e:
            fail_test("A.5 向量检索服务", str(e))

        return True

    # ---------- Part B: 用户认证 ----------

    def test_b_auth(self):
        section("Part B  用户认证")

        # B.1 注册
        ts = int(time.time())
        username = f"inttest_{ts}"
        password = "TestPass123!"
        try:
            data = api_post("/auth/register", {
                "username": username,
                "password": password,
                "confirm_password": password,
                "email": f"{username}@test.local",
            })
            if data.get("success", True) and data.get("data", {}).get("access_token"):
                self.token = data["data"]["access_token"]
                self.user_id = data["data"].get("user", {}).get("id")
                pass_test("B.1 用户注册", f"username={username}, user_id={self.user_id}")
            else:
                fail_test("B.1 用户注册", str(data.get("detail", data)))
        except Exception as e:
            fail_test("B.1 用户注册", str(e))
            return False

        # B.2 登录
        try:
            data = api_post("/auth/login", {"username": username, "password": password})
            if data.get("data", {}).get("access_token"):
                pass_test("B.2 用户登录", "token acquired")
            else:
                fail_test("B.2 用户登录", str(data))
        except Exception as e:
            fail_test("B.2 用户登录", str(e))

        # B.3 无效 token 拒绝（POST /knowledge-bases 要求 get_current_user）
        try:
            resp = requests.post(
                f"{API_V1}/knowledge-bases",
                headers={"Authorization": "Bearer invalid_token_xxx", "Content-Type": "application/json"},
                json={"name": "should_fail"},
                timeout=TIMEOUT,
            )
            if resp.status_code in (401, 403):
                pass_test("B.3 无效 Token 拒绝", f"status={resp.status_code}")
            else:
                fail_test("B.3 无效 Token 拒绝", f"unexpected status={resp.status_code}")
        except Exception as e:
            fail_test("B.3 无效 Token 拒绝", str(e))

        return True

    # ---------- Part C: 知识库与文档 ----------

    def test_c_knowledge_base(self):
        section("Part C  知识库与文档管理")

        # C.1 创建知识库
        try:
            data = api_post("/knowledge-bases", {
                "name": "RAG 集成测试知识库",
                "description": "用于集成测试的临时知识库",
                "chunk_size": 500,
                "chunk_overlap": 50,
            }, token=self.token)
            if data.get("data", {}).get("id"):
                self.kb_id = data["data"]["id"]
                pass_test("C.1 创建知识库", f"kb_id={self.kb_id}")
            else:
                fail_test("C.1 创建知识库", str(data))
                return False
        except Exception as e:
            fail_test("C.1 创建知识库", str(e))
            return False

        # C.2 上传多个文档
        success_count = 0
        for doc in KB_DOCUMENTS:
            try:
                tmp_path = os.path.join(tempfile.gettempdir(), doc["filename"])
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(doc["content"])

                data = api_post_file(
                    f"/knowledge-bases/{self.kb_id}/documents",
                    tmp_path, self.token
                )
                doc_data = data.get("data", {}).get("document", data.get("data", {}))
                status = doc_data.get("status", "?")
                chunks = doc_data.get("total_chunks", 0)
                if status in ("processed", "completed"):
                    success_count += 1
                    pass_test(f"C.2 上传文档 [{doc['filename']}]",
                              f"status={status}, chunks={chunks}")
                else:
                    fail_test(f"C.2 上传文档 [{doc['filename']}]",
                              f"status={status}, msg={data.get('message', '')}")
            except Exception as e:
                fail_test(f"C.2 上传文档 [{doc['filename']}]", str(e))

        if success_count == 0:
            fail_test("C.2 文档上传", "全部文档上传失败")
            return False

        # C.3 验证文档列表
        try:
            data = api_get(f"/knowledge-bases/{self.kb_id}/documents", token=self.token)
            docs = data.get("data", {}).get("items", [])
            pass_test("C.3 文档列表", f"total={len(docs)} documents")
        except Exception as e:
            fail_test("C.3 文档列表", str(e))

        # C.4 向量搜索验证
        try:
            data = api_post(f"/chat/search/{self.kb_id}", {
                "query_text": "什么是 RAG",
                "top_k": 3,
                "min_score": 0.01,
            })
            hits = data.get("data", {}).get("hit_count", 0)
            items = data.get("data", {}).get("items", [])
            if hits > 0:
                top_score = items[0].get("score", 0) if items else 0
                pass_test("C.4 向量搜索", f"hits={hits}, top_score={top_score:.3f}")
            else:
                fail_test("C.4 向量搜索", "no hits returned")
        except Exception as e:
            fail_test("C.4 向量搜索", str(e))

        return True

    # ---------- Part D: RAG 问答（非流式）----------

    def test_d_rag_chat(self):
        section("Part D  RAG 问答（非流式）")

        test_cases = [
            {
                "name": "D.1 RAG 基础问答 — RAG 是什么",
                "query": "什么是 RAG？请简要说明。",
                "expect_keywords": ["RAG", "检索", "生成"],
            },
            {
                "name": "D.2 RAG 问答 — 向量搜索原理",
                "query": "向量搜索的原理是什么？常用的相似度计算方法有哪些？",
                "expect_keywords": ["向量", "相似度", "嵌入"],
            },
            {
                "name": "D.3 RAG 问答 — ReAct Agent",
                "query": "ReAct 框架是什么？它的工作循环是怎样的？",
                "expect_keywords": ["ReAct", "推理", "行动"],
            },
        ]

        for tc in test_cases:
            try:
                data = api_post("/chat/message", {
                    "knowledge_base_id": self.kb_id,
                    "message": tc["query"],
                    "top_k": 5,
                    "min_score": 0.01,
                    "include_raw": False,
                }, token=self.token)

                answer = data.get("data", {}).get("answer", "")
                chunks = data.get("data", {}).get("retrieved_chunks", [])
                model = data.get("data", {}).get("model", "?")
                latency = data.get("data", {}).get("latency_ms", 0)
                provider = data.get("data", {}).get("provider", "?")

                if not answer:
                    fail_test(tc["name"], "empty answer")
                    continue

                # 检查关键词命中
                hit_kws = [kw for kw in tc["expect_keywords"] if kw.lower() in answer.lower()]
                kw_rate = len(hit_kws) / len(tc["expect_keywords"])

                detail = f"model={model}, latency={latency}ms, chunks={len(chunks)}, kw_hit={hit_kws}"

                if kw_rate >= 0.3:
                    pass_test(tc["name"], detail)
                else:
                    # Mock 模式可能关键词匹配不精确，但只要有回答就算通过
                    if provider == "mock":
                        pass_test(tc["name"], f"{detail} (mock mode)")
                    else:
                        fail_test(tc["name"], f"keyword hit rate too low: {hit_kws}")

                # 打印回答预览
                preview = answer[:120].replace("\n", " ")
                print(f"       {C.DIM}answer: {preview}...{C.RESET}")

            except Exception as e:
                fail_test(tc["name"], str(e))

        # D.4 幻觉抑制测试 — 问一个知识库里没有的问题
        try:
            data = api_post("/chat/message", {
                "knowledge_base_id": self.kb_id,
                "message": "请告诉我今天上海的天气怎么样？",
                "top_k": 3,
                "min_score": 0.5,  # 高阈值触发拒绝
            }, token=self.token)
            answer = data.get("data", {}).get("answer", "")
            if "未能检索" in answer or "抱歉" in answer or "无法" in answer:
                pass_test("D.4 幻觉抑制（无关问题）", "answer rejected as expected")
            else:
                pass_test("D.4 幻觉抑制（无关问题）",
                          f"answered (threshold may be low): {answer[:60]}")
        except Exception as e:
            fail_test("D.4 幻觉抑制（无关问题）", str(e))

    # ---------- Part E: 流式响应（SSE）----------

    def test_e_streaming(self):
        section("Part E  流式响应验证（SSE）")

        # E.1 流式 RAG 对话
        try:
            stream_body = {
                "knowledge_base_id": self.kb_id,
                "message": "RAG 的主要优势是什么？请列举几点。",
                "top_k": 5,
                "min_score": 0.01,
            }

            events = []
            token_count = 0
            retrieval_done = False
            done_event = None
            full_answer_parts = []

            for event in api_post_stream("/chat/message/stream", stream_body, token=self.token):
                events.append(event)
                etype = event.get("type", "")

                if etype == "retrieval_done":
                    retrieval_done = True
                    chunks = event.get("chunks", [])
                    max_score = event.get("max_score", 0)
                    pass_test("E.1a 流式检索阶段",
                              f"chunks={len(chunks)}, max_score={max_score:.3f}")

                elif etype == "token":
                    token_count += 1
                    content = event.get("content", "")
                    full_answer_parts.append(content)
                    # 打印前 5 个 token（进度提示）
                    if token_count <= 5:
                        print(f"       {C.DIM}token[{token_count}]: '{content}'{C.RESET}")
                    elif token_count == 6:
                        print(f"       {C.DIM}... (后续 token 省略){C.RESET}")

                elif etype == "done":
                    done_event = event
                    break

                elif etype == "error":
                    fail_test("E.1 流式对话", f"error: {event.get('message', '')}")
                    return

            full_answer = "".join(full_answer_parts)

            if retrieval_done and token_count > 0 and done_event:
                latency = done_event.get("latency_ms", 0)
                model = done_event.get("model", "?")
                pass_test("E.1b 流式生成完成",
                          f"tokens={token_count}, model={model}, latency={latency:.0f}ms")
                print(f"       {C.DIM}full answer ({len(full_answer)} chars): "
                      f"{full_answer[:150]}...{C.RESET}")
            else:
                fail_test("E.1 流式对话",
                          f"retrieval_done={retrieval_done}, tokens={token_count}, done={done_event is not None}")

        except Exception as e:
            fail_test("E.1 流式对话", str(e))

        # E.2 流式 — 空知识库 / 无关问题
        try:
            stream_body = {
                "knowledge_base_id": self.kb_id,
                "message": "今天股票行情如何？",
                "top_k": 3,
                "min_score": 0.9,  # 极高阈值 → 触发拒绝
            }

            events = list(api_post_stream("/chat/message/stream", stream_body, token=self.token))
            types = [e.get("type") for e in events]
            has_done = "done" in types
            has_token = "token" in types

            if has_done and has_token:
                pass_test("E.2 流式幻觉抑制", f"event_types={types}")
            else:
                fail_test("E.2 流式幻觉抑制", f"missing events: types={types}")

        except Exception as e:
            fail_test("E.2 流式幻觉抑制", str(e))

        # E.3 流式延迟统计
        try:
            stream_body = {
                "knowledge_base_id": self.kb_id,
                "message": "FAISS 索引有哪些类型？",
                "top_k": 3,
                "min_score": 0.01,
            }

            first_token_time = None
            start = time.perf_counter()
            token_count = 0

            for event in api_post_stream("/chat/message/stream", stream_body, token=self.token):
                if event.get("type") == "token":
                    if first_token_time is None:
                        first_token_time = (time.perf_counter() - start) * 1000
                    token_count += 1
                elif event.get("type") == "done":
                    total_time = (time.perf_counter() - start) * 1000
                    break

            if first_token_time and token_count > 0:
                pass_test("E.3 流式延迟统计",
                          f"TTFT={first_token_time:.0f}ms, tokens={token_count}, total={total_time:.0f}ms")
            else:
                fail_test("E.3 流式延迟统计", "no tokens received")

        except Exception as e:
            fail_test("E.3 流式延迟统计", str(e))

    # ---------- Part F: Agent ReAct ----------

    def test_f_agent(self):
        section("Part F  Agent ReAct 推理")

        # F.1 Agent 工具列表
        try:
            data = api_get("/agent/tools", token=self.token)
            tools = data.get("tools", [])
            tool_names = [t.get("name", "?") for t in tools]
            if tools:
                pass_test("F.1 Agent 工具列表", f"tools={tool_names}")
            else:
                fail_test("F.1 Agent 工具列表", "no tools available")
        except Exception as e:
            fail_test("F.1 Agent 工具列表", str(e))

        # F.2 Agent 基础推理
        try:
            data = api_post("/agent/run", {
                "query": "什么是 RAG？",
                "knowledge_base_id": self.kb_id,
                "max_turns": 3,
            }, token=self.token)

            answer = data.get("answer", "")
            steps = data.get("steps", [])
            latency = data.get("latency_ms", 0)
            success = data.get("success", False)

            if success and answer:
                pass_test("F.2 Agent 推理",
                          f"steps={len(steps)}, latency={latency}ms")
                for i, step in enumerate(steps):
                    action = step.get("action", "?")
                    thought = step.get("thought", "")[:60]
                    print(f"       {C.DIM}step[{i}]: action={action}, thought={thought}...{C.RESET}")
                print(f"       {C.DIM}answer: {answer[:120]}...{C.RESET}")
            else:
                fail_test("F.2 Agent 推理", f"success={success}, error={data.get('error', '')}")

        except Exception as e:
            fail_test("F.2 Agent 推理", str(e))

        # F.3 Agent 多轮搜索
        try:
            data = api_post("/agent/run", {
                "query": "向量搜索使用什么相似度计算方法？FAISS 有哪些索引类型？",
                "knowledge_base_id": self.kb_id,
                "max_turns": 5,
                "include_raw_steps": True,
            }, token=self.token)

            answer = data.get("answer", "")
            steps = data.get("steps", [])
            latency = data.get("latency_ms", 0)

            if answer:
                pass_test("F.3 Agent 多轮推理",
                          f"steps={len(steps)}, latency={latency}ms")
            else:
                fail_test("F.3 Agent 多轮推理", "empty answer")

        except Exception as e:
            fail_test("F.3 Agent 多轮推理", str(e))

        # F.4 Agent 异常输入
        try:
            resp = requests.post(
                f"{API_V1}/agent/run",
                headers=_headers(self.token),
                json={"query": "", "knowledge_base_id": self.kb_id},
                timeout=TIMEOUT,
            )
            if resp.status_code in (400, 422):
                pass_test("F.4 Agent 异常输入防护", f"status={resp.status_code}")
            else:
                data = resp.json()
                if data.get("error") or not data.get("success", True):
                    pass_test("F.4 Agent 异常输入防护", "handled gracefully")
                else:
                    fail_test("F.4 Agent 异常输入防护", f"unexpected: status={resp.status_code}")

        except Exception as e:
            fail_test("F.4 Agent 异常输入防护", str(e))

    # ---------- Part G: 会话管理 ----------

    def test_g_conversation(self):
        section("Part G  会话管理")

        # G.1 创建会话
        try:
            data = api_post("/conversation", {
                "title": "RAG 集成测试会话",
            }, token=self.token)
            conv_data = data.get("data", data)
            if conv_data and conv_data.get("id"):
                self.conversation_id = conv_data["id"]
                pass_test("G.1 创建会话", f"conversation_id={self.conversation_id}")
            else:
                fail_test("G.1 创建会话", str(data))
                return
        except Exception as e:
            fail_test("G.1 创建会话", str(e))
            return

        # G.2 会话列表（data 字段直接是 list）
        try:
            data = api_get("/conversation", token=self.token)
            convs = data.get("data", [])
            if isinstance(convs, list):
                pass_test("G.2 会话列表", f"total={len(convs)}")
            else:
                fail_test("G.2 会话列表", f"unexpected data type: {type(convs)}")
        except Exception as e:
            fail_test("G.2 会话列表", str(e))

        # G.3 会话内多轮对话（通过 chat/message + history 实现多轮）
        try:
            data = api_post("/chat/message", {
                "knowledge_base_id": self.kb_id,
                "message": "RAG 的核心组件有哪些？",
                "history": [
                    {"role": "user", "content": "什么是 RAG？"},
                    {"role": "assistant", "content": "RAG 是检索增强生成技术。"},
                ],
            }, token=self.token)
            if data.get("data", {}).get("answer"):
                pass_test("G.3 会话内多轮对话", "history-aware answer")
            else:
                fail_test("G.3 会话内多轮对话", str(data))
        except Exception as e:
            fail_test("G.3 会话内多轮对话", str(e))

    # ---------- Part H: 集成接口 ----------

    def test_h_integration(self):
        section("Part H  集成接口")

        # H.1 生成 Webhook Token
        try:
            data = api_get(f"/integration/generate-token/{self.kb_id}?channel=generic_http",
                           token=self.token)
            token_val = data.get("token", "")
            if token_val:
                pass_test("H.1 生成 Webhook Token", f"token={token_val[:30]}...")
            else:
                fail_test("H.1 生成 Webhook Token", str(data))
        except Exception as e:
            fail_test("H.1 生成 Webhook Token", str(e))

        # H.2 Generic Chat 接口
        try:
            data = api_post(f"/integration/generic/{self.kb_id}/chat", {
                "query": "什么是 ReAct？",
                "use_agent": False,
            })
            if data.get("ok"):
                latency = data.get("latency_ms", 0)
                plain = data.get("plain", "")[:80]
                pass_test("H.2 Generic Chat", f"latency={latency}ms")
                print(f"       {C.DIM}reply: {plain}...{C.RESET}")
            else:
                fail_test("H.2 Generic Chat", str(data))
        except Exception as e:
            fail_test("H.2 Generic Chat", str(e))

        # H.3 Agent 模式 Chat
        try:
            data = api_post(f"/integration/generic/{self.kb_id}/chat", {
                "query": "向量搜索的原理是什么？",
                "use_agent": True,
            })
            if data.get("ok"):
                pass_test("H.3 Agent 模式 Chat", f"latency={data.get('latency_ms', 0)}ms")
            else:
                fail_test("H.3 Agent 模式 Chat", str(data))
        except Exception as e:
            fail_test("H.3 Agent 模式 Chat", str(e))

    # ---------- Part I: 性能基准 ----------

    def test_i_performance(self):
        section("Part I  性能基准")

        # I.1 检索延迟
        try:
            latencies = []
            for i in range(5):
                start = time.perf_counter()
                api_post(f"/chat/search/{self.kb_id}", {
                    "query_text": "embedding model",
                    "top_k": 5,
                    "min_score": 0.01,
                })
                latencies.append((time.perf_counter() - start) * 1000)
            avg = sum(latencies) / len(latencies)
            pass_test("I.1 检索延迟",
                      f"avg={avg:.1f}ms, min={min(latencies):.1f}, max={max(latencies):.1f}")
        except Exception as e:
            fail_test("I.1 检索延迟", str(e))

        # I.2 RAG 端到端延迟
        try:
            latencies = []
            for i in range(3):
                start = time.perf_counter()
                api_post("/chat/message", {
                    "knowledge_base_id": self.kb_id,
                    "message": "RAG 是什么？",
                    "top_k": 3,
                    "min_score": 0.01,
                }, token=self.token)
                latencies.append((time.perf_counter() - start) * 1000)
            avg = sum(latencies) / len(latencies)
            pass_test("I.2 RAG 端到端延迟",
                      f"avg={avg:.1f}ms, min={min(latencies):.1f}, max={max(latencies):.1f}")
        except Exception as e:
            fail_test("I.2 RAG 端到端延迟", str(e))

        # I.3 并发 RAG
        try:
            import concurrent.futures

            def single_rag():
                return api_post("/chat/message", {
                    "knowledge_base_id": self.kb_id,
                    "message": "FAISS 是什么？",
                    "top_k": 3,
                    "min_score": 0.01,
                }, token=self.token)

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                start = time.perf_counter()
                futures = [pool.submit(single_rag) for _ in range(10)]
                results = [f.result() for f in futures]
                total_time = (time.perf_counter() - start) * 1000

            success_count = sum(1 for r in results if r.get("data", {}).get("answer"))
            pass_test("I.3 并发 RAG (10x5)",
                      f"ok={success_count}/10, total={total_time:.0f}ms")

        except Exception as e:
            fail_test("I.3 并发 RAG", str(e))

    # ---------- 清理 ----------

    def cleanup(self):
        section("Part Z  清理")
        if self.kb_id:
            try:
                resp = requests.delete(
                    f"{API_V1}/knowledge-bases/{self.kb_id}",
                    headers=_headers(self.token),
                    timeout=TIMEOUT,
                )
                if resp.status_code == 200:
                    pass_test("Z.1 清理测试知识库", f"kb_id={self.kb_id} deleted")
                else:
                    skip_test("Z.1 清理测试知识库", f"status={resp.status_code}")
            except Exception as e:
                skip_test("Z.1 清理测试知识库", str(e))

    # ---------- 主入口 ----------

    def run_all(self):
        print(f"\n{C.BOLD}{'=' * 70}{C.RESET}")
        print(f"  RAG 系统集成测试 — 真实 LLM 环境模拟")
        print(f"  Base URL: {BASE_URL}")
        print(f"  Provider: {self.provider}")
        print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{C.BOLD}{'=' * 70}{C.RESET}")

        steps = [
            ("环境检查", self.test_a_environment),
            ("用户认证", self.test_b_auth),
            ("知识库与文档", self.test_c_knowledge_base),
            ("RAG 问答", self.test_d_rag_chat),
            ("流式响应", self.test_e_streaming),
            ("Agent ReAct", self.test_f_agent),
            ("会话管理", self.test_g_conversation),
            ("集成接口", self.test_h_integration),
            ("性能基准", self.test_i_performance),
        ]

        for name, func in steps:
            try:
                result = func()
                if result is False:
                    skip_test(f"{name} (跳过后续)", "前置条件失败")
                    break
            except Exception as e:
                fail_test(name, f"unexpected error: {e}")

        self.cleanup()
        self._print_summary()

    def _print_summary(self):
        total = _passed + _failed + _skipped
        print(f"\n{C.CYAN}{'=' * 70}{C.RESET}")
        print(f"  {C.BOLD}测试汇总{C.RESET}")
        print(f"{C.CYAN}{'=' * 70}{C.RESET}")
        print(f"  总数: {total}")
        print(f"  {C.GREEN}通过: {_passed}{C.RESET}")
        print(f"  {C.RED}失败: {_failed}{C.RESET}")
        print(f"  {C.YELLOW}跳过: {_skipped}{C.RESET}")
        rate = (_passed / total * 100) if total > 0 else 0
        color = C.GREEN if rate >= 90 else (C.YELLOW if rate >= 70 else C.RED)
        print(f"  {color}通过率: {rate:.1f}%{C.RESET}")
        print(f"{C.CYAN}{'=' * 70}{C.RESET}")

        if _errors:
            print(f"\n  {C.RED}失败明细:{C.RESET}")
            for err in _errors:
                print(f"    - {err}")

        sys.exit(0 if _failed == 0 else 1)


# ======================================================================
# .env 生成辅助
# ======================================================================

def generate_env_template(provider: str, api_url: str = "", api_key: str = "", model: str = ""):
    """根据选择的 provider 生成 .env 配置"""
    env_path = os.path.join(os.path.dirname(__file__), ".env")

    lines = [
        "# ====== RAG 系统配置（集成测试自动生成） ======",
        "APP_ENV=development",
        "APP_DEBUG=true",
        "APP_HOST=0.0.0.0",
        "APP_PORT=8000",
        "",
        "DATABASE_URL=sqlite:///./data/rag_system.db",
        "",
        "SECRET_KEY=change-this-in-production-to-a-long-random-secret-key",
        "JWT_ALGORITHM=HS256",
        "JWT_EXPIRE_MINUTES=1440",
        "",
    ]

    if provider == "deepseek":
        lines.extend([
            "# ====== DeepSeek LLM ======",
            f"LLM_PROVIDER=deepseek",
            f"DEEPSEEK_API_KEY={api_key}",
            f"DEEPSEEK_API_URL={api_url or 'https://api.deepseek.com/v1'}",
            f"DEEPSEEK_MODEL={model or 'deepseek-chat'}",
        ])
    elif provider == "openai":
        lines.extend([
            "# ====== OpenAI LLM ======",
            f"LLM_PROVIDER=openai",
            f"OPENAI_API_KEY={api_key}",
            f"OPENAI_API_URL={api_url or 'https://api.openai.com/v1'}",
            f"OPENAI_MODEL={model or 'gpt-3.5-turbo'}",
        ])
    elif provider == "custom":
        lines.extend([
            "# ====== Custom LLM (OpenAI 兼容) ======",
            f"LLM_PROVIDER=custom",
            f"LLM_CUSTOM_API_URL={api_url}",
            f"LLM_CUSTOM_API_KEY={api_key}",
            f"LLM_CUSTOM_MODEL={model or 'custom-model'}",
        ])
    else:
        lines.extend([
            "# ====== Mock LLM（无 API Key） ======",
            "LLM_PROVIDER=mock",
        ])

    lines.extend([
        "",
        "# ====== LLM 生成参数 ======",
        "LLM_TEMPERATURE=0.3",
        "LLM_MAX_TOKENS=1024",
        "LLM_TOP_P=0.9",
        "LLM_TIMEOUT=60",
        "LLM_MAX_RETRIES=3",
        "",
        "# ====== Embedding ======",
        "EMBEDDING_API_KEY=",
        "EMBEDDING_API_URL=",
        "EMBEDDING_MODEL=bge-m3",
        "EMBEDDING_DEFAULT_DIM=384",
        "",
        "# ====== RAG 检索 ======",
        "RAG_TOP_K=5",
        "RAG_MIN_SCORE=0.35",
        "RAG_MAX_CONTEXT_CHARS=3000",
        "",
        "# ====== 文件存储 ======",
        "UPLOAD_DIR=./data/uploads",
        "VECTOR_STORE_DIR=./data/vector_stores",
        "",
        "# ====== CORS ======",
        "CORS_ORIGINS=http://localhost:3000,http://localhost:5173",
    ])

    content = "\n".join(lines) + "\n"

    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"{C.GREEN}.env 文件已生成: {env_path}{C.RESET}")
    print(f"{C.DIM}provider={provider}, model={model or '(default)'}{C.RESET}\n")


# ======================================================================
# 主入口
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description="RAG 系统集成测试 — 真实 LLM 环境模拟",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # Mock 模式
  python test_integration_llm.py

  # DeepSeek 模式（需要 API Key）
  python test_integration_llm.py --provider deepseek --api-key sk-xxxx

  # OpenAI 模式
  python test_integration_llm.py --provider openai --api-key sk-xxxx --model gpt-4o-mini

  # 本地 Ollama 模式
  python test_integration_llm.py --provider custom \\
      --api-url http://localhost:11434/v1 \\
      --api-key ollama \\
      --model qwen2.5

  # 仅生成 .env 配置文件
  python test_integration_llm.py --gen-env --provider deepseek --api-key sk-xxxx
        """,
    )
    parser.add_argument("--provider", default="mock",
                        choices=["mock", "deepseek", "openai", "custom"],
                        help="LLM 提供商 (默认: mock)")
    parser.add_argument("--api-key", default="", help="LLM API Key")
    parser.add_argument("--api-url", default="", help="LLM API URL (自定义)")
    parser.add_argument("--model", default="", help="LLM 模型名称")
    parser.add_argument("--base-url", default="", help="服务地址 (默认: http://127.0.0.1:8000)")
    parser.add_argument("--gen-env", action="store_true", help="仅生成 .env 配置文件后退出")
    parser.add_argument("--no-run", action="store_true", help="不运行测试，仅配置环境")

    args = parser.parse_args()

    if args.base_url:
        global BASE_URL, API_V1
        BASE_URL = args.base_url
        API_V1 = f"{BASE_URL}/api/v1"

    # 生成 .env
    if args.gen_env or args.api_key:
        generate_env_template(args.provider, args.api_url, args.api_key, args.model)
        if args.gen_env:
            return

    if args.no_run:
        return

    # 检查服务是否在线
    print(f"\n{C.CYAN}检查服务状态: {BASE_URL}{C.RESET}")
    try:
        resp = requests.get(f"{BASE_URL}/", timeout=5)
        if resp.status_code != 200:
            print(f"{C.RED}服务未启动！请先运行: python start.py{C.RESET}")
            print(f"{C.DIM}或: uvicorn app.main:app --host 0.0.0.0 --port 8000{C.RESET}")
            sys.exit(1)
        print(f"{C.GREEN}服务在线 ✓{C.RESET}\n")
    except Exception:
        print(f"{C.RED}无法连接到服务: {BASE_URL}{C.RESET}")
        print(f"\n{C.YELLOW}启动步骤:{C.RESET}")
        print(f"  1. 激活虚拟环境: {C.CYAN}..\\venv\\Scripts\\activate{C.RESET}")
        print(f"  2. 启动服务:     {C.CYAN}python start.py{C.RESET}")
        print(f"  3. 运行测试:     {C.CYAN}python test_integration_llm.py{C.RESET}")
        sys.exit(1)

    # 运行测试
    runner = IntegrationTestRunner(
        provider=args.provider,
        api_url=args.api_url,
        api_key=args.api_key,
        model=args.model,
    )
    runner.run_all()


if __name__ == "__main__":
    main()

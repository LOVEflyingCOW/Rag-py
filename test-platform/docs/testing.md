# RAG 知识库系统 测试文档（testing.md）

> 本文档基于 `c:\Users\LEgion\Desktop\backend\RAG-PY\backend` 目录下的真实源码编写，覆盖系统的测试目标、环境、方案、步骤、代码与判定标准。
> 文档中所有标注【非项目原生代码，为建议实现】的片段均为针对项目未覆盖部分设计的补充测试方案，不会被伪造为已存在实现；其余内容均与项目代码一一对应。

---

## 目录

1. [测试目标与测试范围](#1-测试目标与测试范围)
2. [测试环境准备](#2-测试环境准备)
3. [测试前置条件](#3-测试前置条件)
4. [单元测试方案](#4-单元测试方案)
5. [集成测试方案](#5-集成测试方案)
6. [接口测试方案](#6-接口测试方案)
7. [性能测试方案](#7-性能测试方案)
8. [自动化测试工具选型说明](#8-自动化测试工具选型说明)
9. [完整测试步骤](#9-完整测试步骤)
10. [关键测试代码完整展示](#10-关键测试代码完整展示)
11. [测试数据准备方式](#11-测试数据准备方式)
12. [测试结果判定标准](#12-测试结果判定标准)
13. [常见问题与排查方法](#13-常见问题与排查方法)

---

## 1. 测试目标与测试范围

### 1.1 测试目标

本系统是一套基于 FastAPI 构建的 RAG（Retrieval-Augmented Generation）知识库系统，核心能力包括：用户认证、知识库管理、文档上传与分块、向量化与索引、混合检索（向量 + BM25 + 关键词）、RAG 对话（含幻觉抑制）、ReAct Agent 推理、外部渠道集成（Shopify / 通用 Webhook）。

测试目标如下：

| 目标 | 说明 |
| --- | --- |
| 功能正确性 | 验证各模块（core / models / services / processors / api）的核心函数行为符合预期 |
| 幻觉抑制有效性 | 验证 RAG Pipeline 在空知识库、低相关度、LLM 空回答三种场景下的 L1/L2/L3 拒答策略 |
| 检索质量 | 验证向量检索 + BM25 + 关键词重叠三路混合重排的召回与排序合理性 |
| 鉴权与安全 | 验证 PBKDF2 密码哈希、JWT 签发/校验/过期、Webhook Token 防伪造 |
| 并发与鲁棒性 | 验证多线程下 LLM / RAGPipeline 不崩溃，异常输入不导致 500 |
| API 契约 | 验证 FastAPI 路由的请求/响应结构、状态码、权限控制 |
| 性能基线 | 建立 Embedding / 向量搜索 / Mock LLM / RAG 端到端的延迟基线 |

### 1.2 测试范围

**纳入范围（In Scope）**

- `app/core/`：`config.py`、`security.py`、`logging.py`、`exceptions.py`
- `app/models/`：`database.py`、`schemas.py`、`entities/`（User / KnowledgeBase / Document / Conversation）
- `app/processors/`：
  - `embedding/embedding_service.py`（Mock / LocalNumpy / RemoteAPI / Caching 四种 Provider）
  - `llm/llm_service.py`（Mock / HTTP Provider、递归修复、流式）
  - `retrieval/vector_store.py`（FAISS / IVF / PurePython / VectorStoreManager）
  - `document/`（document_processor / markdown_parser / semantic_chunker / document_pipeline）
- `app/services/`：`chat_service.py`（RAGPipeline）、`retrieval_service.py`（BM25 + 混合重排）、`agent_service.py`、`document_service.py`、`kb_service.py`、`conversation_service.py`、`integration_service.py`
- `app/api/v1/`：auth、knowledge_base、document、chat、agent、retrieval、embedding、conversation、integration
- 测试脚本：`test_all.py`、`test_full.py`、`debug_agent.py`

**不纳入范围（Out of Scope）**

- 真实外部 LLM API（DeepSeek / OpenAI）的端到端联调（项目默认 `LLM_PROVIDER=mock`，外部 API 需要真实 Key）
- 真实 Shopify 商家的线上 Webhook 回调
- 前端 UI 测试（项目仅含后端）
- PostgreSQL 切换后的迁移测试（项目当前使用 SQLite）

### 1.3 测试层级映射

| 层级 | 范围 | 主要执行文件 |
| --- | --- | --- |
| 单元测试 | 纯函数 / 数据结构 / Processor | `test_all.py` 第 0–11 章、`test_full.py` Part A–K |
| 集成测试 | 跨模块链路（文档→分块→向量化→检索→RAG） | `test_full.py` Part I（RAG 端到端）、`test_all.py` 第 12–14 章 |
| 接口测试 | FastAPI 路由 | `test_full.py` Part L（FastAPI TestClient） |
| 性能测试 | 延迟基线 | `test_full.py` Part N |
| 并发鲁棒性 | 多线程 / 异常输入 | `test_full.py` Part M |

---

## 2. 测试环境准备

### 2.1 操作系统与运行时

| 项 | 要求 |
| --- | --- |
| 操作系统 | Windows / Linux / macOS（项目在 Windows `c:\Users\LEgion\Desktop\backend\RAG-PY\backend` 开发） |
| Python | 3.8+（项目使用 `from __future__ import annotations`，建议 3.9+） |
| 包管理 | pip |

### 2.2 依赖安装

`requirements.txt` 关键依赖：

```text
fastapi>=0.100,<0.104
uvicorn>=0.20,<0.23
sqlalchemy>=2.0,<2.1
pydantic>=1.10,<2.0
python-dotenv>=0.20,<2.0
python-multipart>=0.0.5,<0.0.10
aiofiles>=22.0,<23.0
httpx>=0.24,<0.26
numpy>=1.21,<1.27
faiss-cpu>=1.7,<1.9
pytest>=7.0,<8.0
pytest-asyncio>=0.21,<0.22
```

安装步骤（在 `backend/` 目录下执行）：

```bash
# 1. 创建并激活虚拟环境（推荐）
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. （可选）安装测试期辅助库
pip install requests  # HTTPLLMProvider 在 LLM_PROVIDER != mock 时需要
```

> 说明：项目代码中 `HTTPLLMProvider` 通过 `import requests` 动态加载；`requirements.txt` 未显式列出 `requests`，若需测试 `deepseek/openai/custom` provider 需手工补装。Mock 模式下无需 `requests`。

### 2.3 环境变量配置

复制 `.env.example` 为 `.env`，并按需修改。测试期建议保持默认（Mock 模式）：

```bash
# .env 关键配置（测试默认值）
APP_ENV=development
APP_DEBUG=true
DATABASE_URL=sqlite:///./data/rag_system.db
SECRET_KEY=change-this-in-production-to-a-long-random-secret-key
LLM_PROVIDER=mock           # 测试期保持 mock，避免依赖外部 API
EMBEDDING_API_URL=          # 留空 → 使用 LocalNumpyEmbeddingProvider（离线）
UPLOAD_DIR=./data/uploads
VECTOR_STORE_DIR=./data/vector_stores
```

`Settings` 类在 `app/core/config.py` 中定义，会通过 `pydantic.BaseSettings` 自动读取 `.env`。

### 2.4 目录初始化

`Settings.ensure_dirs()` 会在应用启动时创建 `UPLOAD_DIR` 与 `VECTOR_STORE_DIR`。测试脚本（`test_all.py` 0.2、`test_full.py` A.2）也会显式调用 `settings.ensure_dirs()`。

---

## 3. 测试前置条件

### 3.1 项目结构完整性

执行测试前应确认以下目录均存在（`test_full.py` Part A.1 会校验）：

```
app, app/core, app/models, app/models/entities, app/models/schemas,
app/services, app/processors, app/processors/document,
app/processors/embedding, app/processors/llm, app/processors/retrieval,
app/api, app/api/v1, data
```

### 3.2 数据库就绪

- 测试默认使用 SQLite（`./data/rag_system.db`）
- `init_db()` 会在 `Base.metadata.create_all` 时自动建表
- `test_full.py` Part C.1 额外创建了一个 `sqlite:///:memory:` 内存库用于隔离测试
- 涉及 DB 的测试需在测试前 `import app.models.entities` 以触发 SQLAlchemy relationship 字符串引用解析（见 `app/models/database.py` 的 `init_db` 注释）

### 3.3 LLM Provider 就绪

- 测试期保持 `LLM_PROVIDER=mock`，`MockLLMProvider` 不联网，基于规则引擎从 system prompt 中解析 `[#N]` chunks 后生成带 `[来源 #N]` 引用的摘要式回答
- 若要测试 `deepseek/openai/custom`，需在 `.env` 填入有效 API Key，并安装 `requests`

### 3.4 Embedding Provider 就绪

- `EMBEDDING_API_URL` 留空时，`EmbeddingService.from_settings` 使用 `LocalNumpyEmbeddingProvider`（基于字符 n-gram + 哈希投影，离线工作）
- `EMBEDDING_API_URL` 非空时使用 `RemoteAPIEmbeddingProvider`（需 `httpx`，已在 requirements 中）
- 测试期建议保持离线模式

### 3.5 可选依赖

| 库 | 用途 | 缺失影响 |
| --- | --- | --- |
| `numpy` | 向量计算加速 | 仍可运行，自动回退到纯 Python 路径（`_HAS_NUMPY=False`） |
| `faiss-cpu` | FAISS / IVF 索引 | 仍可运行，回退到 `PurePythonVectorStore` |
| `requests` | HTTP LLM Provider | Mock 模式下不影响；切换到 deepseek/openai/custom 时 `HTTPLLMProvider._session=None` 会返回失败结果 |

`test_full.py` Part A.4 会对上述可选依赖做存在性探测并记录（缺失不视为失败）。

### 3.6 pytest 配置

`pytest.ini` 内容：

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
asyncio_mode = auto
addopts = -v
```

> **重要说明**：`pytest.ini` 指向 `tests` 目录，但项目中**不存在** `tests/` 目录，现有测试以根目录脚本形式（`test_all.py` / `test_full.py`）存在。直接运行 `pytest` 会因找不到测试文件而空跑。需用 `python test_all.py` 或 `python test_full.py` 执行，或按第 8 节的建议方案重构。

---

## 4. 单元测试方案

### 4.1 单元测试总览

项目已有的单元测试主要集中在 `test_all.py`（第 0–11 章）与 `test_full.py`（Part A–K）。下表按模块汇总：

| 模块 | 测试点 | 来源 |
| --- | --- | --- |
| 配置 `config.py` | 目录结构、Settings 加载、CORS 解析、active_llm_name | test_all 0.x、test_full A.x |
| 日志 `logging.py` | logger 实例化、info() 调用 | test_all 1.1、test_full B.1 |
| 数据库 `database.py` | engine 初始化、init_db、Session 创建关闭 | test_all 2.x、test_full C.1 |
| 实体 `entities/` | User / KnowledgeBase / Document + Chunk CRUD 与关联 | test_all 3.x、test_full C.2–C.4 |
| 安全 `security.py` | 密码哈希双向校验、JWT 签发/解析/过期拒签 | test_all 4.x、test_full B.2–B.4 |
| Schema `schemas.py` | Pydantic 模型实例化与字段校验 | test_all 5.x、test_full C.5 |
| 文档处理 `document/` | MarkdownParser、SemanticChunker、DocumentPipeline | test_all 6.x、test_full D.x |
| Embedding `embedding_service.py` | encode_single / encode_batch / 相似度 / 空文本 / 归一化 | test_all 7.x、test_full E.x |
| 向量存储 `vector_store.py` | VectorStoreManager CRUD、空存储、多知识库隔离、维度校验 | test_all 8.x、test_full E.4–E.5 |
| LLM `llm_service.py` | ChatMessage/ChatResult、Mock 回复、幻觉抑制、流式、token 估算、递归修复 | test_all 9.x、test_full F.x |
| BM25 `retrieval_service.py` | 分词、打分归一化、空库 | test_all 10.x、test_full G.x |
| 检索服务 `retrieval_service.py` | 关键词提取、重叠分、Jaccard、混合权重 | test_all 11.x、test_full H.x |
| RAG Pipeline `chat_service.py` | 初始化、空库搜索、上下文构建、Messages 组装、幻觉抑制 L1、流式 | test_all 12.x、test_full I.x |
| Agent `agent_service.py` | 初始化、工具注册、Thought/Action 解析、空 query、SearchKBTool、GetDocTool | test_all 13.x、test_full J.x |
| Integration `integration_service.py` | Token 生成/验证/防伪、generic_http 下划线修复、Shopify 解析、渠道渲染 | test_all 14.x、test_full K.x |

### 4.2 单元测试设计原则

1. **纯函数优先**：`_extract_keywords`、`_keyword_overlap_score`、`_text_overlap`、`_parse_agent_output`、`BM25Index.tokenize` 等为纯函数，直接断言输入输出
2. **临时目录隔离**：涉及向量存储的测试使用 `tempfile.TemporaryDirectory()` 避免污染 `./data/vector_stores`
3. **内存 DB 隔离**：`test_full.py` C.1 使用 `sqlite:///:memory:` 隔离 DB 测试
4. **try/except 包裹**：每个测试点独立 try/except，单点失败不中断后续测试，失败信息记入 `_errors`
5. **断言关键不变量**：如混合权重和=1、向量维度一致、向量元素有限、JWT 过期返回 None

### 4.3 已有测试的覆盖度分析

**覆盖良好的部分**：
- 安全模块（密码 + JWT）双向校验
- Embedding 单条/批量/相似度/空文本
- 向量存储 CRUD + 多知识库隔离 + 维度校验
- MockLLM 回复 + 幻觉抑制 + 流式
- BM25 分词 + 打分 + 空库
- RAG Pipeline 幻觉抑制 L1/L2 + 流式 + 端到端
- Agent _parse_agent_output（含 Final Answer 括号修复）
- Integration Token 生成/验证（含 generic_http 下划线修复）/ 渠道渲染（含 XSS 防护）

**覆盖不足或缺失的部分**（需补充，见第 10 节建议代码）：
- `KnowledgeBaseService` 业务层（create/list/get_by_id/update/delete 的权限分支）
- `DocumentService.process_upload` 完整链路（含向量化落盘）
- `ConversationService` 会话超限清理、消息追加、LLM 上下文拼接
- `RetrievalService.search` 端到端（向量+BM25+keyword 三路融合）的有数据场景
- `app/api/v1/embedding.py` 的 `/embeddings/encode`、`/similarity` 接口
- `app/api/v1/retrieval.py` 的 `/retrieval/status`、`/retrieval/search/{kb_id}` 接口
- `app/api/v1/conversation.py` 的会话 CRUD 接口
- `app/core/exceptions.py` 全局异常处理器
- `MarkdownParser` 的代码块 / 表格 / 引用 / 列表 分支
- `DocumentPipeline.process_file` / `process_bytes` 入口

---

## 5. 集成测试方案

### 5.1 集成测试范围

集成测试验证跨模块链路的协同行为，主要覆盖：

| 链路 | 涉及模块 | 测试来源 |
| --- | --- | --- |
| 文档上传 → 分块 → 向量化 → 索引落盘 | DocumentService + DocumentProcessor + EmbeddingService + VectorStoreManager | test_all 6.4 / 8.2、test_full D.3 / E.4 |
| 查询 → 向量化 → 向量搜索 → 上下文组装 → LLM 生成 | RAGPipeline + EmbeddingService + VectorStoreManager + LLMService | test_all 12.7、test_full I.2 / I.6 |
| Agent ReAct 循环（Thought → Action → Observation） | AgentService + RetrievalService + LLMService | test_all 13.6、test_full J.6 |
| Webhook 入站 → 解析 → RAG → 渠道渲染 | IntegrationService + RAGPipeline | test_full L.7 |
| 多轮对话 history 拼接 | RAGPipeline.build_messages | test_full I.9 |

### 5.2 集成测试关键场景

#### 场景 1：RAG 端到端正常回答（test_full.py I.6）

- **前置**：临时目录创建 VectorStoreManager，写入 2 条 chunks（"苹果退款" / "安卓保修"）
- **动作**：`rag.answer(knowledge_base_id=1, query_text="苹果怎么退款", top_k=3)`
- **断言**：`r.success` 为 True，`r.llm_answer` 非空

#### 场景 2：RAG 幻觉抑制 L1（test_full.py I.4）

- **前置**：空知识库（kb_id=9999）
- **动作**：`rag.answer(knowledge_base_id=9999, query_text="任意问题")`
- **断言**：`r.success` 为 True，回答含"未能检索到"或"抱歉"，`r.model == "mock-hallucination-filter"`

#### 场景 3：RAG 幻觉抑制 L2（test_full.py I.5）

- **前置**：写入一条与 query 完全无关的 chunk（"今天天气不错我们去公园散步"）
- **动作**：`rag.answer(knowledge_base_id=1, query_text="苹果手机退款政策")`
- **断言**：max_score < 0.42 触发拒答，回答含"未能检索到"
- **依据**：`chat_service.py` 中 `REJECT_SCORE_THRESHOLD = 0.42`

#### 场景 4：Agent 工具端到端（test_all.py 13.6）

- **前置**：真实 SQLite DB，创建 User + KnowledgeBase + Document + Chunk
- **动作**：`SearchKBTool(db).run({"query": "支付方式", "kb_id": kb.id}, {...})`
- **断言**：`result.success` 为 True

#### 场景 5：混合检索三路融合（test_full.py H.5）

- **前置**：内存 DB + 已写入 Document/Chunk
- **动作**：`RetrievalService(db).search(kb_id, "苹果退款", top_k=5, enable_rerank=True, enable_merge=True)`
- **断言**：返回 list，不抛异常（即使无 chunk 入库）

### 5.3 集成测试数据隔离策略

- 向量存储：`tempfile.TemporaryDirectory()` + 独立 `VectorStoreManager`
- 数据库：`test_full.py` 使用 `sqlite:///:memory:` 内存库；`test_all.py` 使用默认 `./data/rag_system.db` 并在测试后清理（delete + commit）
- 测试用户/知识库/文档：使用 `int(time.time())` 生成唯一用户名/邮箱，避免冲突

---

## 6. 接口测试方案

### 6.1 接口测试总览

接口测试使用 `fastapi.testclient.TestClient`（基于 `httpx`），覆盖 `app/main.py` 注册的全部路由。`test_full.py` Part L 是主要执行来源。

| 路由 | 方法 | 测试点 | 测试 ID |
| --- | --- | --- | --- |
| `/` | GET | 根路径返回 name/version/status | L.2 |
| `/health` | GET | 健康检查 200 | L.3 |
| `/api/v1/chat/provider` | GET | 返回当前 LLM provider | L.4 |
| `/api/v1/chat/message` | POST | 空库触发 L1 拒答 | L.5 |
| `/api/v1/chat/message/stream` | POST | SSE 流式返回 data: + done | L.6 |
| `/api/v1/integration/generic/{kb_id}/chat` | POST | 不存在的 kb_id 不崩溃 | L.7 |
| `/api/v1/integration/generate-token/{kb_id}` | GET | 返回 token 或 404 | L.8 |
| `/api/v1/agent/run` | POST | Agent 推理返回 success/answer | L.9 |
| `/api/v1/auth/register` | POST | 注册返回 200/201/400/409/422 | L.10 |

### 6.2 接口测试未覆盖部分（建议补充）

以下接口在 `test_full.py` Part L 中未直接覆盖，【非项目原生代码，为建议实现】的补充测试见第 10.6 节：

- `/api/v1/auth/login`、`/api/v1/auth/me`
- `/api/v1/knowledge-bases` CRUD
- `/api/v1/knowledge-bases/{kb_id}/documents` 上传/列表/详情/分块/删除/搜索
- `/api/v1/embeddings/status`、`/encode`、`/encode-single`、`/similarity`
- `/api/v1/retrieval/status`、`/index/{kb_id}`、`/search/{kb_id}`
- `/api/v1/conversation` CRUD

### 6.3 接口测试鉴权说明

项目鉴权依赖（`app/api/dependencies.py`）：
- `get_current_user`：必须携带 `Authorization: Bearer <token>`，否则 401
- `get_current_user_optional`：未登录返回 None，不抛 401
- `get_current_admin`：非管理员 403

接口测试中需登录的路由（如创建知识库、上传文档、删除）应先调用 `/api/v1/auth/register` 获取 token，再在后续请求头中携带。`test_full.py` Part L 当前仅测试了无需鉴权或允许匿名访问的接口。

---

## 7. 性能测试方案

### 7.1 性能测试目标

`test_full.py` Part N 建立了 Mock 模式下的延迟基线，目的在于：
1. 发现性能回归（如某次重构后延迟突增）
2. 评估纯 Python 回退 vs numpy/FAISS 加速的差异
3. 估算 SSE 流式 token 速率

### 7.2 性能测试点

| 测试 ID | 测试内容 | 方法 | 判定 |
| --- | --- | --- | --- |
| N.1 | Embedding 单次延迟 | `encode_single` 重复 20 次取平均 | 记录 ms，不设硬阈值 |
| N.2 | 向量搜索延迟 | 200 条库 × 100 次 search 取平均 | 记录 ms/call |
| N.3 | Mock LLM 单次延迟 | `mock.chat` 重复 50 次取平均 | 记录 ms |
| N.4 | RAG 端到端延迟（Mock） | `rag.answer` 重复 20 次取平均 | 记录 ms/call |
| N.5 | 流式响应 token 速率 | `chat_stream` 5 轮统计 token 数与耗时 | 记录 ms/run + token 总数 |

### 7.3 性能测试注意事项

- 性能测试**不设硬性通过阈值**（`_record` 始终记录为 True），仅作基线观察
- 测试结果受机器负载影响，建议在空闲机器上多次运行取中位数
- 真实 LLM API（deepseek/openai）的延迟远高于 Mock，不应与 Mock 基线直接对比
- 大规模知识库（>10000 chunks）应启用 `IVFVectorStore`，性能测试未覆盖此场景

### 7.4 性能测试建议扩展

【非项目原生代码，为建议实现】针对大规模场景的压测建议：

- 使用 `locust` 或 `wrk` 对 `/api/v1/chat/message` 进行并发压测
- 构造 10k+ chunks 的知识库，对比 `FAISSVectorStore` vs `PurePythonVectorStore` 的 search 延迟
- 监控内存占用（`faiss.IndexFlatIP` 全量加载 vs `IVF` 的内存差异）

---

## 8. 自动化测试工具选型说明

### 8.1 当前测试框架

项目当前**未使用 pytest 作为主执行框架**，而是采用**自研脚本式测试**：

| 文件 | 框架 | 执行方式 | 结果收集 |
| --- | --- | --- | --- |
| `test_all.py` | 纯 Python（无框架） | `python test_all.py` | 全局 `_results` 列表 + `_passed`/`_failed` 计数 + 控制台打印 |
| `test_full.py` | 纯 Python（无框架） | `python test_full.py` | 同上，`sys.exit(0 if _failed==0 else 1)` |
| `debug_agent.py` | 纯 Python 调试脚本 | `python debug_agent.py` | 直接 print |

**脚本式测试的核心结构**（以 `test_full.py` 为例）：

```python
_results: List[Dict[str, Any]] = []
_passed = 0
_failed = 0
_errors: List[str] = []

def _record(name: str, ok: bool, detail: str = ""):
    global _passed, _failed
    if ok:
        _passed += 1
    else:
        _failed += 1
        _errors.append(f"[FAIL] {name} | {detail}")
    icon = "PASS" if ok else "FAIL"
    print(f"  [{icon}] {name}" + (f"  — {detail}" if detail else ""))

def _section(title: str):
    print(f"\n{'='*70}\n  {title}\n{'='*70}")

# 每个测试点：
try:
    # ... 测试逻辑 ...
    _record("测试名", True, "详情")
except Exception as e:
    _record("测试名", False, str(e))
```

### 8.2 pytest 配置现状

`pytest.ini` 已配置但**未实际使用**：

```ini
[pytest]
testpaths = tests           # 目录不存在
python_files = test_*.py
python_classes = Test*
python_functions = test_*
asyncio_mode = auto
addopts = -v
```

`requirements.txt` 已包含 `pytest>=7.0,<8.0` 和 `pytest-asyncio>=0.21,<0.22`，说明项目预期支持 pytest，但未提供 `tests/` 目录与 pytest 风格的测试文件。

### 8.3 工具选型建议

【非项目原生代码，为建议实现】推荐的 pytest 改造方案：

1. **创建 `tests/` 目录**，将 `test_all.py` / `test_full.py` 的测试点拆分为独立的 `test_*.py` 文件，每个测试点改为 `def test_xxx():` 函数
2. **使用 fixture 替代临时目录/DB 创建**：
   - `@pytest.fixture` 提供 `tmp_vector_manager`、`in_memory_db`、`test_client`
3. **使用 `pytest.mark.parametrize`** 覆盖多输入场景（如 Token 防伪的多种非法输入）
4. **保留脚本式测试作为冒烟测试**：`test_full.py` 可继续作为一键全量回归脚本

优点：
- pytest 提供更好的失败定位、断言反射、并行执行（`pytest-xdist`）
- fixture 复用减少重复代码
- CI 集成更成熟（JUnit XML、覆盖率插件）

### 8.4 其他工具

| 工具 | 用途 | 是否已集成 |
| --- | --- | --- |
| `fastapi.testclient.TestClient` | API 接口测试 | 是（`test_full.py` Part L） |
| `httpx` | TestClient 底层 + RemoteAPIEmbeddingProvider | 是（requirements） |
| `threading` | 并发鲁棒性测试 | 是（`test_full.py` Part M） |
| `tempfile` | 临时目录隔离 | 是 |
| `coverage` | 测试覆盖率 | 否（建议补充 `pip install pytest-cov`） |

---

## 9. 完整测试步骤

### 9.1 步骤一：环境校验

```bash
cd c:\Users\LEgion\Desktop\backend\RAG-PY\backend

# 1. 确认 Python 版本
python --version

# 2. 确认依赖已安装
python -c "import fastapi, sqlalchemy, pydantic, httpx, numpy; print('core deps ok')"
python -c "import faiss; print('faiss ok')" 2>nul || echo "faiss not installed (will use fallback)"

# 3. 确认 .env 存在（或使用默认值）
if not exist .env copy .env.example .env
```

### 9.2 步骤二：执行全量测试套件（test_full.py）

```bash
python test_full.py
```

预期输出（节选）：

```
======================================================================
  Part A  环境与配置
======================================================================
  [PASS] A.1 项目关键目录完整性  — all ok
  [PASS] A.2 settings 加载 & 目录创建  — provider=mock, top_k=5
  ...
======================================================================
  Part L  API 端点 (FastAPI TestClient)
======================================================================
  [PASS] L.1 FastAPI TestClient 初始化
  [PASS] L.2 GET / 根路径  — keys=['name', 'version', 'status', 'docs', 'health']
  ...
======================================================================
  测试汇总
======================================================================
  总数 : 70+
  通过 : 60+
  失败 : 0~10  （部分测试点存在 API 漂移，见第 13 节）
```

退出码：`0` 表示全通过，`1` 表示有失败。

### 9.3 步骤三：执行原始测试套件（test_all.py）

```bash
python test_all.py
```

> 注意：`test_all.py` 存在与当前 API 不一致的测试点（详见第 13 节），部分测试会失败。建议优先以 `test_full.py` 为准。

### 9.4 步骤四：执行 Agent 调试脚本

```bash
python debug_agent.py
```

该脚本分 4 步：
1. 调用 `get_llm_service().chat()` 观察 Mock LLM 对 Agent system prompt 的反应
2. 调用 `AgentService(db=None).run("请介绍一下你自己", kb_id=1, max_turns=1)`
3. 测试 `_parse_agent_output` 对三种输入的解析
4. 结束

### 9.5 步骤五：单独验证 pytest（可选）

```bash
pytest
```

预期：因 `tests/` 目录不存在，会输出 `no tests ran`。如需 pytest 执行，需按第 8.3 节建议改造。

### 9.6 步骤六：启动应用并手工冒烟

```bash
python start.py
# 或
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

打开浏览器访问：
- `http://localhost:8000/` — 根路径
- `http://localhost:8000/docs` — Swagger UI
- `http://localhost:8000/health` — 健康检查

### 9.7 步骤七：清理测试残留

测试可能在 `./data/rag_system.db` 与 `./data/vector_stores/` 留下测试数据。如需完全清理：

```bash
# 谨慎执行：会删除所有知识库与向量索引
del data\rag_system.db
rmdir /s /q data\vector_stores
rmdir /s /q data\uploads
```

> `test_all.py` 的 DB 测试会自行 delete + commit 清理创建的临时 User/KB/Document；`test_full.py` 使用内存 DB 与临时目录，不污染磁盘。但 `test_full.py` L.9 会在真实 DB 中创建测试知识库（用户名前缀 `_test_agent_`），需手工清理或忽略。

---

## 10. 关键测试代码完整展示

本节完整展示项目原生测试代码的关键片段，以及针对未覆盖部分的【非项目原生代码，为建议实现】补充方案。

### 10.1 项目原生：测试结果收集器（test_full.py）

```python
# ========================= 结果收集器 =========================
_results: List[Dict[str, Any]] = []
_passed = 0
_failed = 0
_errors: List[str] = []


def _record(name: str, ok: bool, detail: str = ""):
    global _passed, _failed
    if ok:
        _passed += 1
    else:
        _failed += 1
        _errors.append(f"[FAIL] {name} | {detail}")
    icon = "PASS" if ok else "FAIL"
    print(f"  [{icon}] {name}" + (f"  — {detail}" if detail else ""))


def _section(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")
```

### 10.2 项目原生：安全模块测试（test_full.py Part B）

```python
# B.2 密码哈希
try:
    from app.core.security import hash_password, verify_password
    pwd = "MyP@ssw0rd_测试"
    h = hash_password(pwd)
    assert h != pwd, "哈希不应等于明文"
    assert verify_password(pwd, h), "正确密码应通过"
    assert not verify_password("wrong_pwd", h), "错误密码应被拒绝"
    _record("B.2 密码哈希双向校验", True, f"hash_prefix={h[:20]}")
except Exception as e:
    _record("B.2 密码哈希", False, str(e))

# B.3 JWT 签发与校验
try:
    from app.core.security import create_access_token, decode_access_token
    tok = create_access_token(user_id=123, username="alice")
    decoded = decode_access_token(tok)
    assert decoded is not None, "Token 应可解析"
    assert str(decoded.get("sub")) == "123"
    assert decoded.get("username") == "alice"
    assert decoded.get("type") == "access"
    _record("B.3 JWT 签发/解析", True, f"token_prefix={tok[:30]}")
except Exception as e:
    _record("B.3 JWT", False, str(e))

# B.4 JWT 过期拒绝
try:
    from app.core.security import create_jwt_token, decode_jwt_token
    tok = create_jwt_token({"sub": "1", "username": "x"}, expire_minutes=0)
    time.sleep(1.1)
    decoded = decode_jwt_token(tok)
    assert decoded is None, "过期 token 应返回 None"
    _record("B.4 JWT 过期拒签", True, f"decoded={decoded}")
except Exception as e:
    _record("B.4 JWT 过期", False, str(e))
```

> 说明：`app/core/security.py` 中 `create_access_token(user_id, username)` 接收位置参数；`decode_access_token` 为真实存在的函数。`test_all.py` 4.2 使用的 `create_access_token(data=...)` 与 `verify_access_token` 与当前 API 不一致，会失败。

### 10.3 项目原生：RAG 幻觉抑制测试（test_full.py Part I）

```python
# I.4 幻觉抑制 L1：空知识库 → 直接拒答
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        r = rag.answer(knowledge_base_id=9999, query_text="任意问题")
        assert r.success
        assert "未能检索到" in r.llm_answer or "抱歉" in r.llm_answer
        assert r.model == "mock-hallucination-filter"
        _record("I.4 幻觉抑制 L1 (空库拒答)", True, f"answer[:50]={r.llm_answer[:50]}")
except Exception as e:
    _record("I.4 幻觉抑制 L1", False, str(e))

# I.5 幻觉抑制 L2：max_score < 0.42 → 拒答
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        vecs = es.encode_batch(["今天天气不错我们去公园散步"])
        metas = [{"chunk_id": 1, "document_id": 1, "document_filename": "x.txt",
                  "content": "今天天气不错我们去公园散步"}]
        vm.get_store(1, dim=es.dim).add(vecs, metas)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        r = rag.answer(knowledge_base_id=1, query_text="苹果手机退款政策")
        assert r.success
        assert "未能检索到" in r.llm_answer or "抱歉" in r.llm_answer
        _record("I.5 幻觉抑制 L2 (低分拒答)", True, f"answer[:60]={r.llm_answer[:60]}")
except Exception as e:
    _record("I.5 幻觉抑制 L2", False, str(e))

# I.7 RAG 流式 answer_stream
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        vecs = es.encode_batch(["苹果手机 14 天无理由退款政策"])
        metas = [{"chunk_id": 1, "document_id": 1, "document_filename": "faq.txt",
                  "content": "苹果手机 14 天无理由退款政策"}]
        vm.get_store(1, dim=es.dim).add(vecs, metas)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        events = list(rag.answer_stream(1, "苹果退款"))
        assert len(events) >= 2
        types = [e["type"] for e in events]
        assert "retrieval_done" in types
        assert "done" in types
        done = [e for e in events if e["type"] == "done"][0]
        assert done.get("answer")
        _record("I.7 RAG answer_stream 流式", True,
                f"events={len(events)}, types={types}")
except Exception as e:
    _record("I.7 RAG 流式", False, str(e))
```

### 10.4 项目原生：Agent 解析修复验证（test_full.py Part J）

```python
# J.3 Agent _parse_agent_output —— Final Answer 带括号后缀
# 目的: 验证 "Final Answer [summary]" 不会被截断成 "Final"
try:
    from app.services.agent_service import AgentService
    sample = (
        "Thought: 已获取足够信息\n"
        "Action: Final Answer [summary]\n"
        "Action Input: 根据知识库，苹果支持 14 天无理由退款。"
    )
    t, a, ai = AgentService._parse_agent_output(sample)
    assert a.lower() == "final answer", f"action 应为 Final Answer，实际 {a!r}"
    assert "14 天" in ai or "退款" in ai
    _record("J.3 Agent Final Answer 括号修复验证", True, f"action={a!r}")
except Exception as e:
    _record("J.3 Agent Final Answer 解析", False, str(e))

# J.4 Agent system prompt 变量替换（花括号安全）
try:
    from app.services.agent_service import AgentService
    tool_list = "- search_kb: 搜索知识库\n- get_doc: 获取文档"
    prompt = AgentService.SYSTEM_PROMPT.replace("$TOOL_LIST", tool_list).replace("$MAX_TURNS", "3")
    assert "$TOOL_LIST" not in prompt
    assert "$MAX_TURNS" not in prompt
    assert "search_kb" in prompt
    _record("J.4 Agent system prompt 变量替换", True, f"len={len(prompt)}")
except Exception as e:
    _record("J.4 Agent system prompt 替换", False, str(e))
```

### 10.5 项目原生：Integration Token 下划线修复验证（test_full.py Part K）

```python
# K.2 含下划线渠道 (generic_http) token 解析
# 目的: 验证历史 bug fix —— 之前 generic_http 会解析失败
try:
    from app.services.integration_service import IntegrationService
    tok = IntegrationService.generate_webhook_token("generic_http", 7)
    assert "generic_http_7_" in tok
    parsed = IntegrationService.verify_webhook_token(tok)
    assert parsed is not None, f"generic_http token 校验失败: {tok[:40]}"
    channel, kb_id = parsed
    assert channel == "generic_http", f"channel 应为 generic_http，实际 {channel}"
    assert kb_id == 7
    _record("K.2 generic_http 渠道 token 解析 (下划线修复)", True)
except Exception as e:
    _record("K.2 generic_http token 解析", False, str(e))

# K.9 渲染 reply —— Shopify HTML（含 XSS 防护）
try:
    from app.services.integration_service import IntegrationService, OutboundReply, CHANNEL_SHOPIFY
    svc = IntegrationService()
    reply = OutboundReply(answer_text="<script>alert(1)</script> 退款政策",
                          sources=[{"document_filename": "faq.html"}])
    out = svc.render_reply_for_channel(CHANNEL_SHOPIFY, reply)
    assert "message_html" in out
    assert "<script>" not in out["message_html"], "HTML 注入风险"
    assert "退款政策" in out["message_html"]
    assert "参考来源" in out["message_html"]
    _record("K.9 Shopify HTML 渲染 (含 XSS 防护)", True)
except Exception as e:
    _record("K.9 Shopify HTML 渲染", False, str(e))
```

### 10.6 项目原生：FastAPI 接口测试（test_full.py Part L）

```python
try:
    from fastapi.testclient import TestClient
    from app.main import app
    _client = TestClient(app)
    _record("L.1 FastAPI TestClient 初始化", True)
except Exception as e:
    _record("L.1 TestClient 初始化", False, str(e))
    _client = None

if _client is not None:
    # L.5 Chat /chat/message (RAG) —— 空库触发 L1 拒答
    try:
        payload = {
            "knowledge_base_id": 9999,  # 空库 → 触发 L1 拒答
            "message": "测试消息",
        }
        r = _client.post("/api/v1/chat/message", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data.get("success") is True
        ans = data.get("data", {}).get("answer", "")
        assert "未能检索到" in ans or "抱歉" in ans
        _record("L.5 POST /chat/message (L1 拒答)", True, f"answer[:60]={ans[:60]}")
    except Exception as e:
        _record("L.5 /chat/message", False, str(e))

    # L.6 Chat /chat/message/stream (SSE)
    try:
        with _client.stream("POST", "/api/v1/chat/message/stream",
                             json={"knowledge_base_id": 9999, "message": "hi"}) as resp:
            assert resp.status_code == 200
            body = b""
            for chunk in resp.iter_bytes():
                body += chunk
            text = body.decode("utf-8", errors="ignore")
            assert "data:" in text
            assert "done" in text
        _record("L.6 POST /chat/message/stream (SSE)", True)
    except Exception as e:
        _record("L.6 /chat/message/stream", False, str(e))
```

### 10.7 项目原生：并发鲁棒性测试（test_full.py Part M）

```python
# M.1 并发 LLM 调用不崩
try:
    from app.processors.llm.llm_service import MockLLMProvider, ChatMessage
    mock = MockLLMProvider()
    errors = []
    def worker(i):
        try:
            msgs = [ChatMessage(role="user", content=f"test {i}")]
            _ = mock.chat(msgs)
        except Exception as e:
            errors.append(str(e))
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(errors) == 0, f"并发错误: {errors[:3]}"
    _record("M.1 并发 20 次 LLM 调用", True)
except Exception as e:
    _record("M.1 并发 LLM", False, str(e))

# M.4 非法 token 注入防护
try:
    from app.services.integration_service import IntegrationService
    bad = [
        "' OR 1=1--",
        "a" * 10000,
        "../../etc/passwd_xx",
        "",
    ]
    for t in bad:
        r = IntegrationService.verify_webhook_token(t)
        assert r is None
    _record("M.4 非法 token 注入防护", True)
except Exception as e:
    _record("M.4 非法 token 注入防护", False, str(e))
```

### 10.8 项目原生：性能基准测试（test_full.py Part N）

```python
# N.1 Embedding 单条延迟
try:
    from app.processors.embedding.embedding_service import EmbeddingService
    es = EmbeddingService()
    t0 = time.perf_counter()
    for _ in range(20):
        es.encode_single("苹果手机退款政策 hello world" * 3)
    dt = (time.perf_counter() - t0) / 20 * 1000
    _record(f"N.1 Embedding 单次延迟 ({dt:.2f}ms)", True)
except Exception as e:
    _record("N.1 Embedding 性能", False, str(e))

# N.2 向量搜索延迟（小规模）
try:
    from app.processors import EmbeddingService, VectorStoreManager
    es = EmbeddingService()
    with tempfile.TemporaryDirectory() as td:
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        vecs = es.encode_batch([f"测试文档内容第{i}条" for i in range(200)])
        metas = [{"chunk_id": i + 1, "document_id": 1,
                  "document_filename": "doc.txt",
                  "content": f"测试文档内容第{i}条"}
                 for i in range(200)]
        vm.get_store(1, dim=es.dim).add(vecs, metas)
        q = es.encode_single("测试文档内容第 50 条")
        t0 = time.perf_counter()
        for _ in range(100):
            vm.get_store(1, dim=es.dim).search(q, top_k=5)
        dt = (time.perf_counter() - t0) / 100 * 1000
        _record(f"N.2 向量搜索 200 条库 x100 次 ({dt:.2f}ms/call)", True)
except Exception as e:
    _record("N.2 向量搜索性能", False, str(e))
```

### 10.9 【非项目原生代码，为建议实现】知识库业务层测试

针对 `app/services/kb_service.py` 的 `KnowledgeBaseService`，项目现有测试未直接覆盖其业务方法。建议补充如下测试（需配合 pytest fixture 或在脚本式框架中扩展）：

```python
# 【非项目原生代码，为建议实现】
# 文件建议位置：tests/test_kb_service.py（需先创建 tests/ 目录）

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.database import Base
import app.models.entities  # noqa: F401  触发实体注册
from app.services.kb_service import KnowledgeBaseService
from app.models.entities.user import User
from app.models.schemas import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.core.security import hash_password


@pytest.fixture
def db_session():
    """内存 SQLite 会话 fixture"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def owner_user(db_session):
    u = User(username="kb_owner", email="owner@x.com",
             password_hash=hash_password("pass"), is_active=True)
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


def test_create_kb(db_session, owner_user):
    svc = KnowledgeBaseService(db_session)
    payload = KnowledgeBaseCreate(name="我的知识库", description="测试", is_public=False)
    kb = svc.create(payload, user_id=owner_user.id)
    assert kb.id is not None
    assert kb.user_id == owner_user.id
    assert kb.chunk_size == 500  # 默认值
    assert kb.status == "active"


def test_get_by_id_permission(db_session, owner_user):
    svc = KnowledgeBaseService(db_session)
    payload = KnowledgeBaseCreate(name="私有库", is_public=False)
    kb = svc.create(payload, user_id=owner_user.id)
    # 所有者可访问
    assert svc.get_by_id(kb.id, user_id=owner_user.id) is not None
    # 其他用户不可访问私有库
    assert svc.get_by_id(kb.id, user_id=99999) is None


def test_list_only_own_and_public(db_session, owner_user):
    svc = KnowledgeBaseService(db_session)
    svc.create(KnowledgeBaseCreate(name="私有", is_public=False), user_id=owner_user.id)
    svc.create(KnowledgeBaseCreate(name="公开", is_public=True), user_id=owner_user.id)
    # 所有者看到 2 条
    items, total = svc.list(user_id=owner_user.id)
    assert total == 2
    # 匿名只看到 1 条公开
    items_pub, total_pub = svc.list(user_id=None)
    assert total_pub == 1
    assert items_pub[0].is_public is True


def test_update_only_owner(db_session, owner_user):
    svc = KnowledgeBaseService(db_session)
    kb = svc.create(KnowledgeBaseCreate(name="原名称"), user_id=owner_user.id)
    # 所有者可改
    updated = svc.update(kb.id, KnowledgeBaseUpdate(name="新名称"), user_id=owner_user.id)
    assert updated is not None
    assert updated.name == "新名称"
    # 非所有者不可改
    assert svc.update(kb.id, KnowledgeBaseUpdate(name="hack"), user_id=99999) is None


def test_delete_only_owner(db_session, owner_user):
    svc = KnowledgeBaseService(db_session)
    kb = svc.create(KnowledgeBaseCreate(name="待删"), user_id=owner_user.id)
    assert svc.delete(kb.id, user_id=99999) is False  # 非所有者
    assert svc.delete(kb.id, user_id=owner_user.id) is True
    assert svc.get_by_id(kb.id, user_id=owner_user.id) is None
```

### 10.10 【非项目原生代码，为建议实现】文档上传端到端测试

针对 `app/services/document_service.py` 的 `process_upload` 完整链路：

```python
# 【非项目原生代码，为建议实现】
# 文件建议位置：tests/test_document_service.py

import os
import tempfile
import pytest
from app.services.document_service import DocumentService
from app.services.kb_service import KnowledgeBaseService
from app.models.entities.user import User
from app.models.schemas import KnowledgeBaseCreate
from app.core.security import hash_password


@pytest.fixture
def document_setup(db_session, owner_user):
    """创建知识库 + DocumentService"""
    kb_svc = KnowledgeBaseService(db_session)
    kb = kb_svc.create(KnowledgeBaseCreate(name="文档测试库", is_public=False),
                       user_id=owner_user.id)
    return kb, DocumentService(db_session)


def test_process_upload_txt(document_setup, owner_user):
    kb, svc = document_setup
    content = "RAG 是检索增强生成技术。\n" * 30  # 足够长以分块
    doc = svc.process_upload(
        kb_id=kb.id,
        user_id=owner_user.id,
        filename="rag_intro.txt",
        file_content=content.encode("utf-8"),
    )
    assert doc is not None
    assert doc.status == "processed"
    assert doc.total_chunks > 0
    # 文件应落盘
    assert os.path.isfile(doc.file_path)


def test_process_upload_permission_denied(document_setup, owner_user):
    kb, svc = document_setup
    # 非所有者上传 → 返回 None
    doc = svc.process_upload(
        kb_id=kb.id,
        user_id=99999,
        filename="hack.txt",
        file_content=b"content",
    )
    assert doc is None


def test_search_in_kb_after_upload(document_setup, owner_user):
    kb, svc = document_setup
    content = "我们支持支付宝和微信支付，7 天无理由退换货。"
    svc.process_upload(
        kb_id=kb.id,
        user_id=owner_user.id,
        filename="faq.txt",
        file_content=content.encode("utf-8"),
    )
    results = svc.search_in_kb(
        kb_id=kb.id,
        user_id=owner_user.id,
        query_text="怎么付款",
        top_k=5,
        min_score=0.0,
    )
    assert isinstance(results, list)
    # 入库后应能检索到结果
    assert len(results) >= 1
    assert "支付" in results[0]["content"]
```

### 10.11 【非项目原生代码，为建议实现】Embedding/Retrieval API 接口测试

针对 `app/api/v1/embedding.py` 与 `app/api/v1/retrieval.py`：

```python
# 【非项目原生代码，为建议实现】
# 文件建议位置：tests/test_api_embedding_retrieval.py

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_embeddings_status():
    r = client.get("/api/v1/embeddings/status")
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    payload = data["data"]
    assert "provider" in payload
    assert "dim" in payload
    assert isinstance(payload["sample_similarity_matrix"], list)


def test_embeddings_encode():
    r = client.post("/api/v1/embeddings/encode",
                    json={"texts": ["你好", "世界"]})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["count"] == 2
    assert len(data["items"]) == 2
    assert data["dim"] > 0
    for item in data["items"]:
        assert len(item["sample_preview"]) <= 5


def test_embeddings_similarity():
    r = client.post("/api/v1/embeddings/similarity",
                    json={"text_a": "支付方式", "text_b": "付款方法"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert "score" in data
    assert "interpretation" in data
    assert -1.0 <= data["score"] <= 1.0


def test_retrieval_global_status():
    r = client.get("/api/v1/retrieval/status")
    assert r.status_code == 200
    data = r.json()["data"]
    assert "base_dir" in data
    assert "faiss_available" in data
    assert "numpy_available" in data


def test_retrieval_search_empty_kb():
    # 不存在的知识库 → 返回 hits=0，不报错
    r = client.post("/api/v1/retrieval/search/99999",
                    json={"query_text": "测试", "top_k": 5, "min_score": 0.0})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["hits"] == 0
    assert data["items"] == []
```

### 10.12 【非项目原生代码，为建议实现】Conversation 服务测试

针对 `app/services/conversation_service.py` 的会话超限清理与消息追加：

```python
# 【非项目原生代码，为建议实现】
# 文件建议位置：tests/test_conversation_service.py

import pytest
from app.services.conversation_service import ConversationService
from app.models.entities.conversation import Conversation, Message


def test_create_and_list_conversations(db_session, owner_user):
    svc = ConversationService(db_session)
    conv = svc.create_conversation(user_id=owner_user.id, title="第一次对话")
    assert conv.id is not None
    assert conv.title == "第一次对话"

    items = svc.list_conversations(owner_user.id)
    assert len(items) == 1
    assert items[0]["id"] == conv.id


def test_append_message_and_get(db_session, owner_user):
    svc = ConversationService(db_session)
    conv = svc.create_conversation(user_id=owner_user.id)
    msg = svc.append_user_message(conv.id, "你好", owner_user.id)
    assert msg is not None
    assert msg.role == "user"
    assert msg.content == "你好"

    msgs = svc.get_messages(conv.id, owner_user.id)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"


def test_max_conversations_cleanup(db_session, owner_user):
    """超过 MAX_CONVERSATIONS_PER_USER(50) 时自动清理最旧会话"""
    svc = ConversationService(db_session)
    # 创建 51 个会话
    for i in range(ConversationService.MAX_CONVERSATIONS_PER_USER + 1):
        svc.create_conversation(user_id=owner_user.id, title=f"conv_{i}")
    items = svc.list_conversations(owner_user.id)
    assert len(items) <= ConversationService.MAX_CONVERSATIONS_PER_USER


def test_conversation_ownership(db_session, owner_user):
    svc = ConversationService(db_session)
    conv = svc.create_conversation(user_id=owner_user.id)
    # 其他用户不可访问
    assert svc.get_conversation(conv.id, user_id=99999) is None
    assert svc.delete_conversation(conv.id, user_id=99999) is False
```

---

## 11. 测试数据准备方式

### 11.1 数据来源分类

| 数据类型 | 准备方式 | 示例 |
| --- | --- | --- |
| 配置数据 | `.env` 文件 + `Settings` 默认值 | `LLM_PROVIDER=mock` |
| 实体数据（DB） | 测试代码内 `db.add()` + `commit()` 动态创建 | User / KnowledgeBase / Document / Chunk |
| 向量数据 | `EmbeddingService.encode_batch()` 实时生成 | 见 test_full E.4 |
| 文本数据 | 代码内硬编码字符串 | `"苹果手机支持 14 天无理由退款"` |
| 文件数据 | `tempfile` + 临时写入 | DocumentPipeline.process_bytes |
| 性能基准数据 | 循环生成 N 条 chunks | `[f"测试文档内容第{i}条" for i in range(200)]` |

### 11.2 实体数据准备模板

`test_full.py` Part C 与 `test_all.py` 第 3 章使用如下模式：

```python
from app.models.database import SessionLocal
from app.models.entities.user import User
from app.models.entities.knowledge_base import KnowledgeBase
from app.models.entities.document import Document, DocumentChunk  # 或 Chunk
from app.core.security import hash_password

db = SessionLocal()
ts = int(time.time())
u = User(username=f"test_{ts}", email=f"test_{ts}@t.com",
         password_hash=hash_password("pass"))
db.add(u); db.commit(); db.refresh(u)

kb = KnowledgeBase(name="TestKB", user_id=u.id, is_public=True)
db.add(kb); db.commit(); db.refresh(kb)

doc = Document(filename="faq.txt", knowledge_base_id=kb.id, total_chunks=2)
db.add(doc); db.commit(); db.refresh(doc)

chunks = [
    DocumentChunk(document_id=doc.id, knowledge_base_id=kb.id,
                  content="第一段内容", chunk_index=0),
    DocumentChunk(document_id=doc.id, knowledge_base_id=kb.id,
                  content="第二段内容", chunk_index=1),
]
db.add_all(chunks); db.commit()

# 测试结束后清理
db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete()
db.delete(doc); db.delete(kb); db.delete(u)
db.commit()
```

### 11.3 向量数据准备模板

```python
from app.processors import EmbeddingService, VectorStoreManager
import tempfile

es = EmbeddingService()
with tempfile.TemporaryDirectory() as td:
    vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
    texts = ["苹果退款政策", "安卓保修说明", "订单查询流程"]
    vecs = es.encode_batch(texts)
    metas = [
        {"chunk_id": i + 1, "document_id": 1, "document_filename": "faq.txt",
         "content": c}
        for i, c in enumerate(texts)
    ]
    vm.get_store(1, dim=es.dim).add(vecs, metas)
    # ... 后续检索测试 ...
```

### 11.4 标准测试语料

项目中反复使用的标准测试语料（建议固化为常量以便复用）：

| 语料 | 用途 |
| --- | --- |
| `"我们支持支付宝和微信支付方式。"` | 支付相关检索 |
| `"7天无理由退换货，需保持商品完好。"` | 退货相关检索 |
| `"苹果手机支持 14 天无理由退款"` | RAG 端到端 |
| `"今天天气不错我们去公园散步"` | 幻觉抑制 L2 的无关 chunk |
| `"测试文档内容第{i}条"` | 性能基准（200 条） |
| `"' OR 1=1--"` | SQL 注入防护 |

### 11.5 数据清理策略

| 测试类型 | 清理方式 |
| --- | --- |
| 内存 DB（test_full C.1） | 会话结束自动释放，无需清理 |
| 磁盘 DB（test_all 3.x） | 测试代码内 `db.delete()` + `commit()` |
| 临时向量目录 | `with tempfile.TemporaryDirectory()` 上下文退出自动删除 |
| 真实 DB 残留（test_full L.9） | 需手工清理或定期重置 `./data/rag_system.db` |

---

## 12. 测试结果判定标准

### 12.1 通用判定规则

| 测试点类型 | 判定标准 |
| --- | --- |
| 功能正确性 | 断言全部通过（`assert` 不抛异常） |
| 幻觉抑制 | 回答包含"未能检索到"或"抱歉"，且 `success=True`，`model="mock-hallucination-filter"` |
| 鉴权 | 正确密码通过、错误密码拒绝；合法 token 解析成功、过期/伪造 token 返回 None |
| 向量维度 | `len(vec) == es.dim`，且所有元素 `math.isfinite(v)` |
| 混合权重 | `VECTOR_WEIGHT + BM25_WEIGHT + KEYWORD_WEIGHT == 1.0`（容差 1e-6） |
| 接口测试 | HTTP 状态码符合预期（200/201/400/404/409/422 任一可接受场景） |
| 并发测试 | `errors` 列表为空（所有线程无异常） |
| 性能测试 | **不设硬阈值**，仅记录延迟数值供基线对比 |
| 鲁棒性 | 非法输入返回 None / False / 空列表，不抛未捕获异常 |

### 12.2 最终汇总判定

`test_full.py` 末尾的汇总逻辑：

```python
print(f"  总数 : {_passed + _failed}")
print(f"  通过 : {_passed}")
print(f"  失败 : {_failed}")
if _failed > 0:
    print(f"\n  --- 失败详情 ---")
    for err in _errors:
        print(f"  ❌ {err}")
print(f"\n 通过率: {_passed / (_passed + _failed) * 100:.2f}%")
sys.exit(0 if _failed == 0 else 1)
```

**CI 集成判定**：
- 退出码 `0` → 测试通过，可继续部署
- 退出码 `1` → 有失败，阻断部署

### 12.3 性能基线参考值（Mock 模式，仅供参考）

| 指标 | 参考范围 | 说明 |
| --- | --- | --- |
| N.1 Embedding 单次 | 1–10 ms | LocalNumpy + numpy 加速；纯 Python 回退会慢 5–10 倍 |
| N.2 向量搜索（200 条库） | < 5 ms/call | FAISS 应 < 1ms；PurePython < 5ms |
| N.3 Mock LLM 单次 | < 5 ms | 纯规则引擎，无网络 |
| N.4 RAG 端到端 | 5–30 ms | 含 embedding + search + mock LLM |
| N.5 流式响应 | 50–200 ms/run | Mock 有 `time.sleep(0.02)` 节流 |

> 实际值取决于机器配置与是否安装 numpy/faiss。若某次运行延迟突增 10 倍以上，应排查回归。

### 12.4 已知失败点（API 漂移，非真实 Bug）

下列测试点因测试代码与当前 API 不一致会失败，**不应视为系统 Bug**（详见第 13 节）：

| 测试 ID | 失败原因 |
| --- | --- |
| test_all 3.1 / 3.2 / 3.3 | User 字段使用 `hashed_password`，实际为 `password_hash` |
| test_all 4.2 | 使用 `create_access_token(data=...)` 与 `verify_access_token`，实际为 `(user_id, username)` 位置参数 + `decode_access_token` |
| test_all 5.4 | `ChatRequest(query=...)`，实际字段为 `message`；检查 `r.stream`，实际无此字段 |
| test_all 6.4 | `process_text(..., doc_id=1, kb_id=1)`，实际无 `doc_id` 参数 |
| test_all 9.4 | 语法错误：`content("怎么付款")` 应为 `content="怎么付款"` |
| test_full C.5 | `UserRegister(..., confirm_password=...)`，实际无此字段 |
| test_full C.5 | `KnowledgeBaseCreate(name="test", title="t")`，实际无 `title` 字段 |
| test_full C.5 | `DocumentUploadRequest` 导入，实际 `schemas.py` 未定义 |

---

## 13. 常见问题与排查方法

### 13.1 测试运行类问题

#### 问题 1：`pytest` 输出 `no tests ran`

**原因**：`pytest.ini` 配置 `testpaths = tests`，但项目无 `tests/` 目录。

**解决**：
- 方案 A：直接运行 `python test_full.py` 或 `python test_all.py`
- 方案 B：创建 `tests/` 目录并按第 8.3 节建议改造测试文件
- 方案 C：临时修改 `pytest.ini` 为 `testpaths = .`，但需注意 `test_all.py` / `test_full.py` 不是 pytest 风格函数，仍不会被收集

#### 问题 2：`ModuleNotFoundError: No module named 'app'`

**原因**：未在 `backend/` 目录下执行，或未将 `backend/` 加入 `sys.path`。

**解决**：
```bash
cd c:\Users\LEgion\Desktop\backend\RAG-PY\backend
python test_full.py
```
`test_full.py` 开头已执行 `sys.path.insert(0, _BASE)`，但需在正确目录运行。

#### 问题 3：`test_all.py` 多个测试点失败

**原因**：`test_all.py` 存在 API 漂移（字段名/函数名与当前代码不一致），见 12.4。

**解决**：优先以 `test_full.py` 为准；或按 12.4 修正 `test_all.py` 中的字段名。

### 13.2 依赖类问题

#### 问题 4：`ImportError: faiss` 或 `ImportError: numpy`

**原因**：可选依赖未安装。

**解决**：
```bash
pip install numpy faiss-cpu
```
不安装也可运行，系统会自动回退到 `PurePythonVectorStore` 与纯 Python 路径，但性能下降。

#### 问题 5：`requests` 未安装导致 LLM 测试失败

**原因**：`HTTPLLMProvider` 需要 `requests`，但 `requirements.txt` 未列出。

**解决**：Mock 模式下不受影响。若要测试 deepseek/openai/custom：
```bash
pip install requests
```

#### 问题 6：`pydantic.v1` / `BaseSettings` 导入错误

**原因**：项目使用 `pydantic>=1.10,<2.0`，若环境装了 pydantic v2 会导致 `BaseSettings` 位置变化。

**解决**：
```bash
pip install "pydantic>=1.10,<2.0"
```

### 13.3 数据库类问题

#### 问题 7：`init_db` 报 relationship 字符串引用无法解析

**原因**：未显式 `import app.models.entities` 触发实体注册。

**解决**：`app/models/database.py` 的 `init_db()` 已包含 `import app.models.entities`，确保调用 `init_db()` 而非直接 `Base.metadata.create_all`。

#### 问题 8：SQLite `database is locked`

**原因**：并发写入或 Session 未关闭。

**解决**：
- 测试每个用例后 `db.close()`
- `test_full.py` Part C 使用内存 DB 避免此问题
- 长事务改为短事务

#### 问题 9：测试后 `./data/rag_system.db` 膨胀

**原因**：`test_all.py` 3.x 与 `test_full.py` L.9 在真实 DB 创建测试数据，部分未清理。

**解决**：
- 定期删除 `data/rag_system.db` 重新初始化
- 或将 DB 测试统一改为内存 DB

### 13.4 向量存储类问题

#### 问题 10：`向量维度非法: 期望 X，实际 Y`

**原因**：`EmbeddingService.dim` 与 `VectorStoreManager.default_dim` 不一致。

**解决**：
- 创建 store 时显式传 `dim=es.dim`：`vm.get_store(kb_id, dim=es.dim)`
- 检查 `.env` 中 `EMBEDDING_DEFAULT_DIM` 与实际 provider 维度是否匹配（RemoteAPI 时首次调用会自动检测）

#### 问题 11：FAISS 索引文件加载失败

**原因**：`.vecstore.faiss` 文件损坏或 FAISS 版本不兼容。

**解决**：`VectorStoreManager.get_store` 已 try/except，加载失败会用新 store 替代；或删除 `data/vector_stores/kb_*.vecstore*` 重建。

#### 问题 12：检索结果为空但知识库有 chunks

**原因**：向量索引未建立（`store.total() == 0`），但 DB 中有 DocumentChunk。

**解决**：`DocumentService.search_in_kb` 与 `RetrievalService.search` 都会调用 `_rebuild_vector_index` 自动重建。若仍为空，检查 `embedding.encode` 是否返回空列表。

### 13.5 LLM / RAG 类问题

#### 问题 13：MockLLM 始终返回"未能检索到"

**原因**：system prompt 中未包含 `【知识库片段】` 标记，或 chunks 格式不符合 `[#N]` 模式。

**解决**：
- 检查 `RAGPipeline.build_context` 输出格式：`[#1] (来源: xxx, 相似度: 0.xxx)\n内容`
- `MockLLMProvider._parse_chunks` 从 `【知识库片段】` 标记后开始解析
- 若 max_score < 0.42，RAGPipeline 会直接拒答，不调用 LLM

#### 问题 14：RAG 流式接口无 token 事件

**原因**：`answer_stream` 在 L1/L2 拒答时只产出 1 个 token 事件（整段拒答文本）。

**解决**：检查 `events[0]["type"] == "retrieval_done"` 与 `events[-1]["type"] == "done"`，拒答场景下 token 事件数为 1 属正常。

#### 问题 15：Agent 无 DB 时工具为空

**原因**：`AgentService._build_tools` 在 `db is None` 时不注册 `search_kb` / `get_doc`。

**解决**：`debug_agent.py` 与 `test_full.py` J.1 验证了这是预期行为（优雅降级）。需工具调用时传入有效 `db`。

### 13.6 鉴权 / Token 类问题

#### 问题 16：JWT 验证返回 None

**原因**：
- token 过期（`exp < now`）
- 签名不匹配（`SECRET_KEY` 变更）
- token 格式不是三段式

**解决**：
- `decode_jwt_token` 内部用 `hmac.compare_digest` 防时序攻击
- 检查 `.env` 的 `SECRET_KEY` 是否在签发与验证之间被修改
- `JWT_EXPIRE_MINUTES=1440`（24 小时），过期后需重新登录

#### 问题 17：Webhook Token `generic_http` 渠道验证失败

**原因**：历史 Bug —— `verify_webhook_token` 曾用 `split("_")` 解析，`generic_http` 含下划线会出错。

**解决**：当前代码已修复，使用 `rsplit("_", 1)` 从右侧拆分。`test_full.py` K.2 专门验证此修复。若仍失败，检查 `IntegrationService.verify_webhook_token` 是否为最新版本。

#### 问题 18：昨日 Webhook Token 突然失效

**原因**：Token 含 `int(time.time() / 86400)` 天级时间戳，跨天失效。

**解决**：当前代码已支持 `today` 与 `today-1` 两天的容灾（见 `verify_webhook_token` 的 `for day_key in (today, today-1)`）。`test_full.py` K.5 验证此容灾。若需更长容忍期，需修改 `IntegrationService.verify_webhook_token`。

### 13.7 接口测试类问题

#### 问题 19：`/api/v1/chat/message` 返回 422

**原因**：请求体不符合 `ChatRequest` schema（必填字段 `knowledge_base_id` 与 `message` 缺失）。

**解决**：
```json
{
  "knowledge_base_id": 1,
  "message": "你的问题"
}
```
注意：字段是 `message` 而非 `query`（`test_all.py` 5.4 用错了字段名）。

#### 问题 20：`/api/v1/agent/run` 返回 400 "knowledge_base_id 不能为空"

**原因**：`agent.py` 的 `agent_run` 显式校验 `payload.knowledge_base_id`，未传或为 None 时 400。

**解决**：请求体必须包含 `knowledge_base_id`（整数）。

#### 问题 21：`TestClient` 初始化失败

**原因**：`app.main` 导入时 `init_db` 未执行，或 `settings.ensure_dirs()` 失败。

**解决**：
- `app.main.create_app()` 在 startup 事件中调用 `init_db()`，`TestClient` 实例化时会触发 startup
- 确保工作目录有写权限（需创建 `./data/uploads` 与 `./data/vector_stores`）

### 13.8 排查通用流程

1. **查看失败详情**：`test_full.py` 失败时会打印 `[FAIL] 测试名 — 详情`，末尾汇总所有 `_errors`
2. **隔离复现**：将失败测试点的 try/except 内代码复制到独立脚本，加 `traceback.print_exc()` 查看完整堆栈
3. **检查依赖**：`python -c "import numpy, faiss, fastapi, sqlalchemy, pydantic; print(pydantic.VERSION)"`
4. **检查配置**：`python -c "from app.core.config import settings; print(settings.LLM_PROVIDER, settings.EMBEDDING_DEFAULT_DIM)"`
5. **检查 DB**：`sqlite3 data/rag_system.db ".tables"` 查看表是否创建
6. **检查向量索引**：`GET /api/v1/retrieval/status` 查看磁盘上的知识库索引列表
7. **启用调试日志**：设置 `APP_DEBUG=true`，SQLAlchemy 会打印 SQL

---

## 附录：测试文件清单

| 文件 | 路径 | 类型 | 说明 |
| --- | --- | --- | --- |
| `test_all.py` | `backend/test_all.py` | 脚本式 | 14 章节测试，部分 API 漂移 |
| `test_full.py` | `backend/test_full.py` | 脚本式 | Parts A-N 完整套件，主推荐 |
| `debug_agent.py` | `backend/debug_agent.py` | 调试脚本 | Agent 单步调试 |
| `pytest.ini` | `backend/pytest.ini` | 配置 | 指向不存在的 `tests/` 目录 |
| `requirements.txt` | `backend/requirements.txt` | 依赖 | 含 pytest、pytest-asyncio |
| `.env.example` | `backend/.env.example` | 配置模板 | 环境变量示例 |
| `test_output.txt` | `backend/test_output.txt` | 测试输出 | 历史运行记录 |
| `test_output2.txt` | `backend/test_output2.txt` | 测试输出 | 历史运行记录 |

---

*本文档基于 `backend/` 目录下源码与测试文件编写，所有引用的代码片段均与项目实际文件一致。建议在代码变更后同步更新本文档。*

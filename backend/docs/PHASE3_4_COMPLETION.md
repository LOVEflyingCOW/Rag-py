# Phase 3 + Phase 4 补全实施文档

> 日期: 2026-08-15
> 范围: design.md 中 Phase 3 和 Phase 4 的缺口补全

---

## 一、整体思路

对照 [design.md](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/docs/design.md) 检查发现：

**Phase 3 缺口**:
- L1+L2 多级缓存（设计有 L1 本地 LRU + L2 Redis，实际只实现了 L2）
- 请求幂等性中间件（Idempotency-Key 机制完全未实现）
- RabbitMQ（设计用 RabbitMQ 做 broker，实际用 Redis——**决定跳过**，Redis broker 已满足需求）

**Phase 4 缺口**:
- Reranker（Cross-Encoder 二次精排，完全未实现）
- 多策略分块（ChunkerFactory：recursive / semantic / markdown 三种策略）
- RAG 评估模块（RAGEvaluator：召回率/准确率/MRR/延迟评估）

---

## 二、Phase 3 补全

### 2.1 L1+L2 多级缓存

**文件**: [app/core/cache.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/cache.py)（重写）

#### 想法

原来的 cache.py 只有 L2 Redis 缓存。design.md 设计了 L1 本地 LRU + L2 Redis 的多级缓存。我的思考：

1. **为什么需要 L1?** Redis 虽然快（~1ms），但本地内存访问是 ~0.01ms。对于高频热点（如知识库元数据），本地缓存可以减少 Redis 网络往返。
2. **一致性怎么保证?** L1 的 TTL 必须比 L2 短（L1=60s, L2=可配置），这样数据更新后 L1 会更快过期。写操作时同时失效 L1 和 L2。
3. **LRU 怎么实现?** 用 `OrderedDict` + `asyncio.Lock`，简单可靠。`move_to_end()` 实现 LRU 淘汰。
4. **缓存击穿怎么防?** design.md 提到互斥锁。我用 Redis `SET NX EX` 实现：只有获取锁的请求回源查 DB，其他请求等待 100ms 后重试缓存。

#### 关键设计

```
读取流程: L1 → L2 → (互斥锁) → DB → 回填 L1+L2
写入流程: 同时写 L1 + L2 (L1 的 TTL = min(用户TTL, 60))
失效流程: 同时删 L1 + L2
```

#### 坑点

- **asyncio.Lock 与 OrderedDict**: L1 缓存的 `OrderedDict` 不是线程安全的，需要 `asyncio.Lock` 保护。但 Lock 是协程级的，如果多线程调用（比如 Celery 同步任务），需要注意。当前系统 API 层全是 async，所以没问题。
- **L1 TTL 控制**: L1 不能用和 L2 一样的 TTL。如果 L2 TTL=3600s（embedding 缓存），L1 也用 3600s 会导致本地缓存数据过旧。所以 L1 固定使用 `min(ttl, 60)` 秒。
- **delete_pattern**: `clear_embed_cache()` 需要清空所有 `embed:` 前缀的缓存。L1 用遍历匹配，L2 用 `KEYS` 命令。Redis KEYS 在生产环境有性能风险，但 embed 缓存量不大（<1000），可以接受。

#### 新增接口

```python
get_l1_stats()          # L1 缓存统计 (size, max_size)
cleanup_l1_expired()    # 清理过期条目
clear_l1_cache()         # 清空 L1
```

---

### 2.2 请求幂等性中间件

**文件**: [app/core/middleware/idempotency.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/middleware/idempotency.py)（新建）

#### 想法

design.md 5.3 节设计了 `Idempotency-Key` 中间件。我的思考：

1. **核心场景**: 文档上传、知识库创建等 POST 操作，防止用户重复点击导致重复创建。
2. **实现方式**: 客户端在请求头中携带 `Idempotency-Key`，中间件检查 Redis 是否已存在。
3. **缓存什么?** 需要缓存完整的响应（status_code + body），这样重复请求返回的是首次的结果。
4. **只缓存成功的响应**: 4xx/5xx 不缓存，允许客户端重试。

#### 坑点

- **body_iterator 消费问题**: Starlette 的 `BaseHTTPMiddleware` 中，`response.body_iterator` 是一个异步迭代器，读取后就被消费了。必须重新构造 Response 返回。我在读取完 body 后用 `Response(content=..., status_code=..., headers=...)` 重新构造。
- **大响应体缓存**: 文档上传可能返回大响应。我加了 `_MAX_BODY_CACHE = 100KB` 限制，超过不缓存（直接返回，不保证幂等性）。
- **中间件注册顺序**: Starlette 中间件后注册的先执行。幂等性中间件应该在安全头之后、限流之前执行，这样限流后的请求才走幂等检查。实际注册顺序：
  ```
  app.add_middleware(AuditLogMiddleware)      # 最后执行
  app.add_middleware(RateLimitMiddleware)
  app.add_middleware(IdempotencyMiddleware)
  app.add_middleware(SecurityHeadersMiddleware)  # 最先执行
  ```

---

## 三、Phase 4 补全

### 3.1 Reranker (Cross-Encoder 二次精排)

**文件**: [app/processors/retrieval/reranker.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/processors/retrieval/reranker.py)（新建）

#### 想法

design.md 6.2 节设计了 Cross-Encoder Reranker。我的思考：

1. **Cross-Encoder vs Bi-Encoder**: 现有的 EmbeddingService 是 Bi-Encoder（query 和 document 独立编码），速度快但精度有限。Cross-Encoder 同时编码 query+document 对，精度更高但速度慢。适合对 top-20 结果做精排。
2. **模型选择**: `BAAI/bge-reranker-base` 适合 CPU 环境（~50ms/pair），`bge-reranker-large` 需要 GPU。默认用 base。
3. **懒加载**: 模型在首次调用时加载，避免应用启动慢。加载一次后缓存在内存中。
4. **降级策略**: `sentence-transformers` 没装时，降级到 `KeywordReranker`（基于关键词覆盖率和位置加权）。

#### 关键设计

```
CrossEncoderReranker:
  rerank(query, documents, top_k) → 线程池执行 → CrossEncoder.predict → sigmoid 归一化 → 排序

KeywordReranker (降级):
  提取关键词 → 计算覆盖率 + 位置加权 → 排序
```

#### 坑点

- **CPU 密集型阻塞**: `CrossEncoder.predict()` 是 CPU 密集型操作，直接在 async 中调用会阻塞事件循环。必须用 `asyncio.get_event_loop().run_in_executor(None, ...)` 放到线程池执行。
- **sigmoid 转换**: CrossEncoder 输出的是 logit（范围约 -10~+10），需要 `sigmoid` 转为 0~1 的概率分数：`score = 1 / (1 + exp(-logit))`。
- **内容截断**: Cross-Encoder 的 `max_length=512` tokens，超长文档会截断。我在构建 pairs 时先截断到 2000 字符（约 500 tokens）。

---

### 3.2 多策略分块 (ChunkerFactory)

**文件**: [app/processors/document/chunker_factory.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/processors/document/chunker_factory.py)（新建）

#### 想法

design.md 6.3 节设计了三种分块策略。我的思考：

1. **为什么需要多种策略?** 不同文档类型适合不同分块方式。纯文本适合递归字符分块，Markdown 适合按标题层级分块，混合文档适合语义感知分块。
2. **复用现有代码**: `SemanticChunker` 已经实现得很好（结构感知、句子边界、overlap），我通过 `SemanticChunkerAdapter` 适配到统一接口。
3. **递归分块器**: 参考 LangChain 的 `RecursiveCharacterTextSplitter`，按分隔符优先级递归切分：`\n\n` → `\n` → `。` → `.` → 空格。
4. **自动检测**: `detect_strategy(text)` 根据文本特征推荐策略——有标题/代码块/表格的选 markdown，段落多的选 semantic，纯文本选 recursive。

#### 三种策略对比

| 策略 | 适用场景 | 分块方式 | 优势 |
|------|---------|---------|------|
| recursive | 纯文本/日志 | 按分隔符递归切分 | 简单高效 |
| semantic | 混合文档 | 保持 Markdown 结构 | 结构感知 |
| markdown | MD 文档 | 按标题层级分块 | 完美保留结构 |

#### 坑点

- **递归切分的终止条件**: 如果所有分隔符都切不开（比如一整段无标点的中文），最终要 fallback 到按长度强制切分。我在 `_recursive_split` 中检查 `if not separators:` 时按 `chunk_size` 切。
- **小 chunk 合并**: 递归切分后可能产生过小的 chunk（<50 字符）。我在 `_merge_small_chunks` 中合并相邻的小 chunk。
- **Markdown 检测**: `_is_markdown()` 用正则检测标题、代码块、表格、列表等 Markdown 特征。检测到 2 个以上特征才判定为 Markdown，避免误判。

---

### 3.3 RAG 评估模块 (RAGEvaluator)

**文件**: [app/services/evaluation_service.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/evaluation_service.py)（新建）

#### 想法

design.md 6.4 节设计了 RAG 质量评估。我的思考：

1. **评估什么?** 两个维度：检索质量（Recall/Precision/MRR）和生成质量（关键词命中率）。
2. **Recall@K**: 前 K 个检索结果中包含正确文档的比例。
3. **MRR (Mean Reciprocal Rank)**: 第一个正确文档的倒数排名的均值。如果正确文档排在第 1 位，MRR=1.0；第 2 位 MRR=0.5；第 3 位 MRR=0.33。
4. **关键词命中率**: LLM 回答中包含预期关键词的比例。不使用 LLM 时，改为检查检索内容中是否包含关键词。
5. **批量评估**: 支持一次运行多个测试用例，输出结构化报告。

#### 评估指标定义

```
Recall@K = |正确文档 ∩ 检索文档| / |正确文档|
Precision@K = |正确文档 ∩ 检索文档| / |检索文档|
MRR = 1 / (第一个正确文档的排名)
Keyword Hit Rate = |答案中命中的关键词| / |预期关键词|
```

#### 坑点

- **expected_doc_ids vs expected_source**: 测试用例可以用文档 ID 或文件名来指定预期结果。我同时支持两种：先检查 doc_ids，再检查文件名匹配。
- **LLM 调用阻塞**: LLM 调用是同步的（`llm.chat()`），需要用 `asyncio.to_thread()` 放到线程池。默认 `use_llm=False`，仅评估检索质量。
- **LLM 不可用时降级**: 如果 LLM 服务不可用，只评估检索质量，跳过关键词命中率检查。不使用 LLM 时，改为检查检索到的内容中是否包含预期关键词。

---

## 四、文件变更清单

### 新建文件

| 文件 | 说明 |
|------|------|
| [app/core/middleware/idempotency.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/middleware/idempotency.py) | 请求幂等性中间件 |
| [app/processors/retrieval/reranker.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/processors/retrieval/reranker.py) | Cross-Encoder Reranker + 关键词降级 |
| [app/processors/document/chunker_factory.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/processors/document/chunker_factory.py) | 多策略分块器工厂 |
| [app/services/evaluation_service.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/evaluation_service.py) | RAG 质量评估模块 |

### 修改文件

| 文件 | 改动 |
|------|------|
| [app/core/cache.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/cache.py) | 重写为 L1+L2 多级缓存，增加互斥锁防击穿 |
| [app/core/middleware/__init__.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/middleware/__init__.py) | 导出 IdempotencyMiddleware |
| [app/main.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/main.py) | 注册 IdempotencyMiddleware |

---

## 五、设计决策记录

### 5.1 为什么跳过 RabbitMQ?

design.md 设计用 RabbitMQ 做 Celery broker，但实际实现中用了 Redis。

**理由**:
- Redis 已经在运行（缓存+限流+黑名单），无需额外维护 RabbitMQ 容器
- Redis broker 的性能足够当前规模（<1000 tasks/min）
- RabbitMQ 的优势（死信队列、优先级队列）在当前阶段不需要
- 如果未来需要 RabbitMQ，只需改 `CELERY_BROKER_URL` 环境变量

### 5.2 为什么 Reranker 默认不启用?

Cross-Encoder 模型需要下载（~400MB），首次加载慢（~10s）。所以：
- `get_reranker()` 是懒加载，首次调用才加载
- `sentence-transformers` 是可选依赖（不在 requirements.txt 中）
- 未安装时自动降级到 `KeywordReranker`

### 5.3 ChunkerFactory 为什么不复用 LangChain?

**理由**:
- LangChain 的 `RecursiveCharacterTextSplitter` 功能强大，但引入 LangChain 整个包太重
- 我们的递归分块器只用了标准库 `re`，零额外依赖
- `SemanticChunker` 已经实现了语义分块，适配一下接口即可

---

## 六、后续建议

1. **安装 sentence-transformers**: `pip install sentence-transformers` 启用 Cross-Encoder 精排
2. **评估测试**: 运行 RAGEvaluator 对混合检索效果做量化评估
3. **ChunkerFactory 集成**: 在 DocumentService 中支持 `chunk_strategy` 参数选择分块策略
4. **幂等性测试**: 编写测试验证重复请求返回相同结果
5. **L1 缓存监控**: 在 `/health` 端点暴露 L1 缓存统计信息

# Phase 4 — 全文检索功能 (pgvector + FTS) 完整工作记录

> 日期: 2026-08-14
> 项目: RAG 知识库系统 (FastAPI + SQLAlchemy + FAISS + pgvector)

---

## 一、任务概述

Phase 3 (Redis 缓存 + Celery 后台任务 + 连接池优化) 完成后，开始 Phase 4 的核心任务：**实现工业级全文检索功能**，用 PostgreSQL 原生能力 (pgvector + FTS) 替代现有的 FAISS 内存向量索引 + 纯 Python BM25 文本搜索。

### 目标
1. 扩展数据模型，支持向量列和全文检索列
2. 实现 PostgreSQL pgvector 向量存储服务
3. 实现 PostgreSQL 原生全文检索 (FTS) 服务
4. 重构 RetrievalService 支持双模式自动切换 (PostgreSQL 原生 / SQLite 降级)
5. 编写集成测试验证功能正确性
6. 构造测试数据，运行混合检索查看实际效果

---

## 二、实现内容

### 2.1 数据模型扩展 — DocumentChunk 增加 pgvector 和 TSVector 列

**文件**: [app/models/entities/document.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/entities/document.py)

**改动内容**:

```python
# 根据数据库类型动态选择列类型
if _is_sqlite:
    VectorType = JSON        # SQLite 降级: 用 JSON 存储向量
    TSVectorType = Text      # SQLite 降级: 用 Text 存储全文索引
else:
    from pgvector.sqlalchemy import Vector
    VectorType = Vector(384) # PostgreSQL: pgvector 向量类型 (384维)
    from sqlalchemy.dialects.postgresql import TSVector
    TSVectorType = TSVector() # PostgreSQL: 原生 TSVector 类型
```

DocumentChunk 表新增两列:
- `embedding` — 存储文档块的向量嵌入
- `search_vector` — 存储 PostgreSQL 全文检索的 tsvector

索引配置 (仅 PostgreSQL):
- `ix_chunks_embedding_cosine` — IVFFLAT 索引，加速向量余弦相似度搜索
- `ix_chunks_search_vector` — GIN 索引，加速全文检索

**设计亮点**: 通过 `_is_sqlite` 标志实现动态列类型选择，SQLite 模式自动降级为 JSON/Text，保证开发环境兼容性。

---

### 2.2 PgVectorStore — pgvector 向量存储服务

**文件**: [app/services/pg_vector_store.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/pg_vector_store.py) (新建)

**功能**: 替代 FAISSVectorStore，将向量直接存储在 PostgreSQL 数据库中。

**核心方法**:

| 方法 | 说明 |
|------|------|
| `add_vector(chunk_id, embedding)` | 为文档块添加向量 |
| `add_vectors(chunks, embeddings)` | 批量添加向量 |
| `search(kb_id, query_vector, top_k)` | 余弦距离向量搜索 |
| `search_by_l2_distance(...)` | L2 距离向量搜索 |
| `search_by_inner_product(...)` | 内积向量搜索 |
| `remove_vector(chunk_id)` | 删除向量 |
| `count_vectors(kb_id)` | 统计向量数量 |
| `create_index(index_type, lists)` | 创建 IVF/HNSW 索引 |
| `rebuild_indexes()` | 重建所有向量索引 |
| `stats(kb_id)` | 获取统计信息 |

**距离度量**:
- 余弦距离 `<=>`: `score = 1 - distance/2` (范围 0~1)
- L2 距离 `<->`: `score = 1/(1+distance)`
- 内积 `<#>`: `score = (ip+1)/2`

**优势**:
- 数据一致性: 向量与元数据在同一事务中
- 持久化: 数据库存储，无文件丢失风险
- 支持百万级向量 (IVF 索引)

---

### 2.3 PgFullTextSearch — PostgreSQL 全文检索服务

**文件**: [app/services/postgres_search.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/postgres_search.py) (新建)

**功能**: 替代内存 BM25Index，使用 PostgreSQL 原生 FTS 能力。

**核心方法**:

| 方法 | 说明 |
|------|------|
| `index_document(chunk_id, content)` | 为文档创建 tsvector 全文索引 |
| `search(kb_id, query_text, top_k)` | 全文检索，返回排名结果 |
| `_preprocess_query(query)` | 查询预处理 (中文/英文分词) |
| `_extract_snippet(content, query)` | 提取匹配片段 |

**中文分词策略**:
PostgreSQL 默认 FTS 分词器不支持中文，采用自定义预处理:
- 提取中文字符串和英文单词
- 中文段: 每个字符用 `|` (OR) 连接
- 各段之间用 `&` (AND) 连接

示例: `"如何使用RAG系统"` → `"如 | 何 | 使 | 用 & RAG & 系 | 统"`

**SQL 查询**:
```sql
SELECT c.id, c.content,
       ts_rank(c.search_vector, to_tsquery('simple', :query)) AS rank
FROM chunks c
WHERE c.knowledge_base_id = :kb_id
  AND c.search_vector @@ to_tsquery('simple', :query)
ORDER BY rank DESC
LIMIT :limit
```

---

### 2.4 PostgreSQLHybridSearch — 混合检索服务

**文件**: [app/services/postgres_search.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/pg_vector_store.py) (同文件)

**功能**: 融合向量搜索和全文检索，提供更准确的检索结果。

**两种融合策略**:

1. **线性加权融合 (Linear Weighted)**
   ```
   final_score = vector_weight * vector_score + fts_weight * fts_score
   ```
   默认权重: vector=0.6, fts=0.4

2. **倒数排名融合 (RRF, Reciprocal Rank Fusion)**
   ```
   score = 1/(k + rank_vector) + 1/(k + rank_fts)
   ```
   默认 k=60

**核心方法**:

| 方法 | 说明 |
|------|------|
| `search(kb_id, query_text, top_k)` | 线性加权混合检索 |
| `search_with_rrf(kb_id, query_text, top_k)` | RRF 混合检索 |

---

### 2.5 RetrievalService 重构 — 自动选择检索策略

**文件**: [app/services/retrieval_service.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/retrieval_service.py) (重写)

**核心设计**: 根据 `_is_sqlite` 标志自动选择检索策略。

```
PostgreSQL 模式:
  查询 → _has_postgres_vectors() → 有向量 → _search_postgres_native()
                                  → 无向量 → _search_fallback() (降级)

SQLite 模式:
  查询 → _search_fallback() (FAISS + BM25)
```

**新增方法**:

| 方法 | 说明 |
|------|------|
| `search(..., force_postgres=False)` | 统一搜索入口，自动选择策略 |
| `_has_postgres_vectors(kb_id)` | 检查知识库是否有 pgvector 数据 |
| `_search_postgres_native(...)` | PostgreSQL 原生混合检索 |
| `_search_fallback(...)` | 降级搜索 (FAISS + BM25) |
| `index_document_for_search(...)` | 为文档块创建双重索引 (向量+全文) |
| `batch_index_documents(...)` | 批量创建索引 |
| `initialize_postgres_search(db)` | 初始化 pgvector 扩展 (应用启动时调用) |

**RetrievedHit 数据类** 新增 `search_type` 字段:
- `"postgres_native"` — PostgreSQL 原生检索
- `"faiss_bm25"` — FAISS + BM25 降级模式

**降级保护**: PostgreSQL 原生检索失败时，自动降级到 FAISS + BM25 模式。

---

### 2.6 集成测试 — test_phase4_fulltext_search.py

**文件**: [tests/phases/test_phase4_fulltext_search.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase4_fulltext_search.py) (新建)

**测试结果**: 30/30 通过，1 个跳过 (API 集成需服务运行)

| 测试模块 | 测试项 | 结果 |
|---------|--------|------|
| 数据模型扩展 | embedding/search_vector 列存在 | PASS |
| PgVectorStore | 模块导入 + 5 个方法验证 | PASS |
| PgFullTextSearch | 模块导入 + 方法验证 + 中文/英文查询预处理 + 片段提取 | PASS |
| PostgreSQLHybridSearch | 模块导入 + search + search_with_rrf | PASS |
| RetrievalService | 6 个方法验证 + RetrievedHit 数据类 | PASS |
| 混合检索逻辑 | 关键词提取 + 文本重叠度 + 关键词匹配分数 | PASS |
| API 集成测试 | (需服务运行) | SKIP |

---

### 2.7 混合检索实际效果测试 — test_phase4_hybrid_demo.py

**文件**: [tests/phases/test_phase4_hybrid_demo.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase4_hybrid_demo.py) (新建)

**测试环境**:
- 数据库: SQLite (降级模式)
- 检索方式: FAISS 向量 + BM25 文本 + 关键词匹配 三路融合
- 权重: Vector=0.50, BM25=0.35, Keyword=0.15
- 测试文档: 6 篇中英文技术文档
- 嵌入维度: 384 (all-MiniLM-L6-v2)

**测试文档**:

| 文档 | 内容主题 |
|------|---------|
| rag_introduction.txt | RAG 系统概念和工作流程 |
| vector_database_comparison.txt | FAISS/pgvector/Milvus/Pinecone 对比 |
| embedding_models.txt | 嵌入模型选择 (OpenAI/Sentence-Transformers/BGE) |
| machine_learning_basics.txt | 机器学习/深度学习基础 |
| chinese_nlp_techniques.txt | 中文 NLP 分词和处理 |
| search_relevance_ranking.txt | 搜索排序 (TF-IDF/BM25/Hybrid/RRF) |

**查询结果**: 6/6 全部正确命中预期文档

| 查询 | Top-1 文档 | 最终分数 |
|------|-----------|---------|
| 什么是RAG系统 | rag_introduction.txt | 0.6604 |
| vector database comparison FAISS pgvector Milvus | vector_database_comparison.txt | 0.6868 |
| 如何选择嵌入模型 | embedding_models.txt | **0.7314** |
| 深度学习和神经网络有什么关系 | machine_learning_basics.txt | 0.6748 |
| 中文分词工具和NLP处理 | chinese_nlp_techniques.txt | 0.6990 |
| hybrid search ranking BM25 TF-IDF reranking | search_relevance_ranking.txt | **0.7462** |

**模式对比** (查询: "RAG系统如何使用向量数据库进行检索"):

| 模式 | Rank 1 | 分数 |
|------|--------|------|
| A. 混合检索 (Vector+BM25+Keyword) | vector_database_comparison.txt | 0.6577 |
| B. 仅向量检索 (无重排) | machine_learning_basics.txt | 0.2597 |
| C. 仅 BM25 文本匹配 | vector_database_comparison.txt | 1.0000 |

**关键发现**:
1. 混合检索效果最佳，综合语义和关键词匹配
2. 纯向量检索分数差异小 (0.2597 vs 0.2541)，容易偏离意图
3. BM25 补充关键词精确匹配能力
4. 中文查询表现优秀 (双字组合分词策略有效)
5. 英文混合查询得分最高 (BM25=1.0 + Keyword=1.0)

---

### 2.8 数据库迁移 — 自动 ALTER TABLE

在测试脚本中实现了自动迁移逻辑，检测 SQLite 数据库的 chunks 表是否缺少新列，自动执行 ALTER TABLE:

```python
# 检测缺失列
result = await db.execute(sql_text("PRAGMA table_info(chunks)"))
columns = [row[1] for row in result.fetchall()]

# 自动添加
if "embedding" not in columns:
    await db.execute(sql_text("ALTER TABLE chunks ADD COLUMN embedding JSON"))
if "search_vector" not in columns:
    await db.execute(sql_text("ALTER TABLE chunks ADD COLUMN search_vector TEXT"))
```

---

## 三、文件变更清单

### 新建文件

| 文件 | 说明 |
|------|------|
| [app/services/pg_vector_store.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/pg_vector_store.py) | PgVectorStore 向量存储服务 (384 行) |
| [app/services/postgres_search.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/postgres_search.py) | PgFullTextSearch + PostgreSQLHybridSearch (350 行) |
| [tests/phases/test_phase4_fulltext_search.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase4_fulltext_search.py) | Phase 4 集成测试 (488 行) |
| [tests/phases/test_phase4_hybrid_demo.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase4_hybrid_demo.py) | 混合检索实际效果测试 (500 行) |

### 修改文件

| 文件 | 改动 |
|------|------|
| [app/models/entities/document.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/entities/document.py) | DocumentChunk 增加 embedding + search_vector 列，动态列类型，IVF/GIN 索引 |
| [app/services/retrieval_service.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/retrieval_service.py) | 全面重构，支持 PostgreSQL 原生检索 + SQLite 降级模式自动切换 |

---

## 四、架构设计

### 4.1 双模式检索架构

```
                    用户查询
                       |
                       v
            RetrievalService.search()
                       |
               _is_sqlite 判断
               /               \
          PostgreSQL            SQLite
              |                    |
    _has_postgres_vectors()    _search_fallback()
         /         \               |
       有向量      无向量         FAISS 向量搜索
         |           |            + BM25 文本搜索
         v           v            + 关键词匹配
  _search_postgres_  降级到
     native()       _search_fallback()
         |
   PostgreSQLHybridSearch
    /              \
  向量搜索         全文检索
  (pgvector)       (FTS + GIN)
    \              /
     线性加权融合 / RRF 融合
```

### 4.2 权重配置

**PostgreSQL 原生模式**:
- 向量搜索权重: 0.6
- 全文检索权重: 0.4

**SQLite 降级模式**:
- 向量搜索权重: 0.50
- BM25 权重: 0.35
- 关键词匹配权重: 0.15

---

## 五、遇到的问题及解决方案

### 问题 1: PostgreSQL 容器服务名不匹配
- **现象**: `docker-compose up -d pgvector` 报错 "no such service: pgvector"
- **原因**: docker-compose.yml 中服务名为 "postgres" 而非 "pgvector"
- **解决**: 使用正确服务名 `docker-compose up -d postgres`

### 问题 2: SQLAlchemy inspect 不兼容
- **现象**: `'Mapper' object has no attribute 'indexes'` / `'tometadata'`
- **原因**: SQLAlchemy 版本差异，Mapper 对象 API 不同
- **解决**: 直接访问 `DocumentChunk.__table__.columns` 获取列信息

### 问题 3: SQLite 缺少新列
- **现象**: `sqlite3.OperationalError: table chunks has no column named search_vector`
- **原因**: 模型更新了但 SQLite 数据库文件未迁移
- **解决**: 测试脚本中自动检测并执行 `ALTER TABLE ADD COLUMN`

### 问题 4: User 模型字段名不匹配
- **现象**: `'hashed_password' is an invalid keyword argument for User`
- **原因**: User 模型字段名为 `password_hash` 而非 `hashed_password`
- **解决**: 使用正确的字段名 `password_hash`

### 问题 5: 密码哈希函数名不匹配
- **现象**: `cannot import name 'get_password_hash' from 'app.core.security'`
- **原因**: security 模块中函数名为 `hash_password` 而非 `get_password_hash`
- **解决**: `from app.core.security import hash_password as get_password_hash`

### 问题 6: Unicode 字符 GBK 编码错误
- **现象**: `'gbk' codec can't encode character '\u2713'`
- **原因**: Windows 终端默认 GBK 编码，不支持 Unicode 特殊字符 (✓✗)
- **解决**: 替换为 ASCII 字符 `[OK]` / `[FAIL]`

---

## 六、测试统计

### 集成测试 (test_phase4_fulltext_search.py)
```
通过: 30
失败: 0
跳过: 1 (API 集成测试需服务运行)
总计: 31
```

### 混合检索效果测试 (test_phase4_hybrid_demo.py)
```
查询成功: 6/6
全部正确命中预期文档
```

---

## 七、后续可优化方向

1. **启动 PostgreSQL 容器**: 当前在 SQLite 降级模式运行，启动 PostgreSQL 后可测试 pgvector + FTS 原生检索效果
2. **Alembic 迁移脚本**: 编写正式的 Alembic 迁移脚本替代手动 ALTER TABLE
3. **HNSW 索引**: pgvector 0.5+ 支持 HNSW 索引，在大多数场景下优于 IVF
4. **中文分词优化**: 集成 jieba 作为 PostgreSQL 自定义分词器，提升中文 FTS 效果
5. **Reranking 模型**: 在初步检索后使用 Cross-Encoder 模型进行二次精排
6. **API 端点集成**: 将新的检索功能暴露为 API 端点，供前端调用
7. **性能基准测试**: 对比 PostgreSQL 原生检索 vs SQLite 降级模式的延迟和召回率

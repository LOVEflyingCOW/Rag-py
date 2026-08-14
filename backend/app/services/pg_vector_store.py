"""PgVectorStore - 基于 PostgreSQL pgvector 扩展的向量存储

替代 FAISS 实现，提供:
1. 向量存储到数据库 (embedding 列)
2. 向量相似度搜索 (pgvector <=> 操作符)
3. 索引管理 (IVF 索引创建和重建)
4. 与 DocumentChunk 模型深度集成

优势:
- 数据一致性: 向量与元数据在同一事务中
- 持久化: 数据库持久化，无文件丢失风险
- 分布式: 支持多实例并发访问
- 扩展性: 支持百万级向量 (IVF 索引)
"""

from __future__ import annotations

import math
import time
from typing import List, Dict, Optional, Any, Tuple

from sqlalchemy import select, update, delete, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.entities.document import DocumentChunk


class PgVectorStore:
    """基于 PostgreSQL pgvector 的向量存储"""

    def __init__(self, db: AsyncSession, dim: int = 384):
        """
        初始化 pgvector 存储

        Args:
            db: 数据库会话
            dim: 向量维度 (默认 384，与 EmbeddingService 一致)
        """
        self.db = db
        self.dim = dim
        self._cache: Dict[int, int] = {}  # kb_id -> vector_count
        self._last_refresh: Dict[int, float] = {}

    # ---------- 添加向量 ----------
    async def add_vectors(
        self,
        chunks: List[DocumentChunk],
        embeddings: List[List[float]],
    ) -> bool:
        """
        为文档块添加向量

        Args:
            chunks: 文档块列表
            embeddings: 对应的嵌入向量列表

        Returns:
            是否成功
        """
        if len(chunks) != len(embeddings):
            raise ValueError(f"chunks 数量 ({len(chunks)}) 与 embeddings 数量 ({len(embeddings)}) 不匹配")

        for chunk, embedding in zip(chunks, embeddings):
            if len(embedding) != self.dim:
                raise ValueError(
                    f"向量维度错误: 期望 {self.dim}, 实际 {len(embedding)}"
                )
            
            vector_str = f"[{','.join(str(v) for v in embedding)}]"
            
            await self.db.execute(
                update(DocumentChunk)
                .where(DocumentChunk.id == chunk.id)
                .values(embedding=vector_str)
            )

        await self.db.commit()
        self._invalidate_cache()
        
        logger.info(f"成功为 {len(chunks)} 个文档块添加向量")
        return True

    async def add_vector(
        self,
        chunk_id: int,
        embedding: List[float],
    ) -> bool:
        """
        为单个文档块添加向量

        Args:
            chunk_id: 文档块 ID
            embedding: 嵌入向量

        Returns:
            是否成功
        """
        if len(embedding) != self.dim:
            raise ValueError(
                f"向量维度错误: 期望 {self.dim}, 实际 {len(embedding)}"
            )

        vector_str = f"[{','.join(str(v) for v in embedding)}]"
        
        result = await self.db.execute(
            update(DocumentChunk)
            .where(DocumentChunk.id == chunk_id)
            .values(embedding=vector_str)
        )
        await self.db.commit()
        
        self._invalidate_cache()
        return result.rowcount > 0

    # ---------- 搜索 ----------
    async def search(
        self,
        kb_id: int,
        query_vector: List[float],
        top_k: int = 5,
        min_score: float = 0.0,
        exclude_chunk_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """
        向量相似度搜索 (余弦距离)

        Args:
            kb_id: 知识库 ID
            query_vector: 查询向量
            top_k: 返回前 K 个结果
            min_score: 最低相似度阈值 (0-1)
            exclude_chunk_ids: 要排除的 chunk ID 列表

        Returns:
            List of dicts: [
                {
                    "chunk_id": int,
                    "document_id": int,
                    "content": str,
                    "metadata": dict,
                    "score": float (0-1, 越大越相似),
                    "distance": float,
                }
            ]
        """
        if len(query_vector) != self.dim:
            raise ValueError(
                f"查询向量维度错误: 期望 {self.dim}, 实际 {len(query_vector)}"
            )

        vector_str = f"[{','.join(str(v) for v in query_vector)}]"
        
        # 构建查询
        distance_expr = DocumentChunk.embedding.cosine_distance(vector_str)
        
        query = select(
            DocumentChunk.id.label("chunk_id"),
            DocumentChunk.document_id,
            DocumentChunk.content,
            DocumentChunk.metadata_,
            distance_expr.label("distance"),
        ).where(
            DocumentChunk.knowledge_base_id == kb_id
        ).where(
            DocumentChunk.embedding.isnot(None)
        )

        # 排除指定 chunk
        if exclude_chunk_ids:
            query = query.where(
                DocumentChunk.id.notin_(exclude_chunk_ids)
            )

        # 排序和限制
        query = query.order_by(distance_expr).limit(top_k * 5)

        result = await self.db.execute(query)
        rows = result.fetchall()

        results = []
        for row in rows:
            distance = float(row.distance) if row.distance else 2.0
            # 余弦距离转换为相似度分数: [0, 2] -> [1, 0]
            score = 1.0 - distance / 2.0
            score = max(0.0, min(1.0, score))
            
            if score < min_score:
                continue

            results.append({
                "chunk_id": row.chunk_id,
                "document_id": row.document_id,
                "content": row.content,
                "metadata": self._parse_metadata(row.metadata_),
                "score": score,
                "distance": distance,
            })

        return results

    async def search_by_l2_distance(
        self,
        kb_id: int,
        query_vector: List[float],
        top_k: int = 5,
        max_distance: float = float("inf"),
    ) -> List[Dict[str, Any]]:
        """
        向量搜索 (L2 距离)

        Args:
            kb_id: 知识库 ID
            query_vector: 查询向量
            top_k: 返回前 K 个结果
            max_distance: 最大 L2 距离

        Returns:
            按 L2 距离排序的结果
        """
        vector_str = f"[{','.join(str(v) for v in query_vector)}]"
        
        distance_expr = DocumentChunk.embedding.l2_distance(vector_str)
        
        query = select(
            DocumentChunk.id.label("chunk_id"),
            DocumentChunk.document_id,
            DocumentChunk.content,
            DocumentChunk.metadata_,
            distance_expr.label("distance"),
        ).where(
            DocumentChunk.knowledge_base_id == kb_id
        ).where(
            DocumentChunk.embedding.isnot(None)
        )

        if max_distance < float("inf"):
            query = query.where(distance_expr <= max_distance)

        query = query.order_by(distance_expr).limit(top_k)

        result = await self.db.execute(query)
        rows = result.fetchall()

        results = []
        for row in rows:
            distance = float(row.distance) if row.distance else float("inf")
            score = 1.0 / (1.0 + distance)
            
            results.append({
                "chunk_id": row.chunk_id,
                "document_id": row.document_id,
                "content": row.content,
                "metadata": self._parse_metadata(row.metadata_),
                "score": score,
                "distance": distance,
            })

        return results

    async def search_by_inner_product(
        self,
        kb_id: int,
        query_vector: List[float],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        向量搜索 (内积)
        适用于归一化后的向量 (与余弦距离等价)

        Args:
            kb_id: 知识库 ID
            query_vector: 查询向量
            top_k: 返回前 K 个结果

        Returns:
            按内积排序的结果
        """
        vector_str = f"[{','.join(str(v) for v in query_vector)}]"
        
        inner_expr = DocumentChunk.embedding.max_inner_product(vector_str)
        
        query = select(
            DocumentChunk.id.label("chunk_id"),
            DocumentChunk.document_id,
            DocumentChunk.content,
            DocumentChunk.metadata_,
            inner_expr.label("inner_product"),
        ).where(
            DocumentChunk.knowledge_base_id == kb_id
        ).where(
            DocumentChunk.embedding.isnot(None)
        ).order_by(inner_expr.desc()).limit(top_k)

        result = await self.db.execute(query)
        rows = result.fetchall()

        results = []
        for row in rows:
            ip = float(row.inner_product) if row.inner_product else 0.0
            # 归一化到 [0, 1]
            score = (ip + 1.0) / 2.0
            
            results.append({
                "chunk_id": row.chunk_id,
                "document_id": row.document_id,
                "content": row.content,
                "metadata": self._parse_metadata(row.metadata_),
                "score": score,
                "inner_product": ip,
            })

        return results

    # ---------- 批量操作 ----------
    async def batch_add_vectors(
        self,
        items: List[Tuple[int, List[float]]],
    ) -> int:
        """
        批量添加向量

        Args:
            items: [(chunk_id, embedding), ...]

        Returns:
            成功添加的数量
        """
        count = 0
        for chunk_id, embedding in items:
            try:
                await self.add_vector(chunk_id, embedding)
                count += 1
            except Exception as e:
                logger.error(f"添加向量失败 chunk_id={chunk_id}: {e}")
                continue

        return count

    async def batch_search(
        self,
        kb_id: int,
        query_vectors: List[List[float]],
        top_k: int = 5,
    ) -> List[List[Dict[str, Any]]]:
        """批量向量搜索"""
        return [
            await self.search(kb_id, qv, top_k)
            for qv in query_vectors
        ]

    # ---------- 删除 ----------
    async def remove_vector(self, chunk_id: int) -> bool:
        """
        删除文档块的向量

        Args:
            chunk_id: 文档块 ID

        Returns:
            是否成功
        """
        result = await self.db.execute(
            update(DocumentChunk)
            .where(DocumentChunk.id == chunk_id)
            .values(embedding=None)
        )
        await self.db.commit()
        self._invalidate_cache()
        return result.rowcount > 0

    async def remove_vectors_by_document(
        self, document_id: int
    ) -> int:
        """
        删除文档的所有向量

        Args:
            document_id: 文档 ID

        Returns:
            删除的数量
        """
        result = await self.db.execute(
            update(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .values(embedding=None)
        )
        await self.db.commit()
        self._invalidate_cache()
        return result.rowcount

    # ---------- 查询 ----------
    async def get_vector(self, chunk_id: int) -> Optional[List[float]]:
        """
        获取单个文档块的向量

        Args:
            chunk_id: 文档块 ID

        Returns:
            向量列表或 None
        """
        result = await self.db.execute(
            select(DocumentChunk.embedding)
            .where(DocumentChunk.id == chunk_id)
            .where(DocumentChunk.embedding.isnot(None))
        )
        row = result.first()
        if row and row[0]:
            return self._parse_vector(row[0])
        return None

    async def get_vectors_by_kb(
        self, kb_id: int
    ) -> List[Dict[str, Any]]:
        """
        获取知识库的所有向量

        Args:
            kb_id: 知识库 ID

        Returns:
            List of dicts with chunk_id and embedding
        """
        result = await self.db.execute(
            select(DocumentChunk.id, DocumentChunk.embedding)
            .where(DocumentChunk.knowledge_base_id == kb_id)
            .where(DocumentChunk.embedding.isnot(None))
        )
        rows = result.fetchall()
        
        return [
            {
                "chunk_id": row[0],
                "embedding": self._parse_vector(row[1]),
            }
            for row in rows
        ]

    async def count_vectors(self, kb_id: int) -> int:
        """
        统计知识库中的向量数量

        Args:
            kb_id: 知识库 ID

        Returns:
            向量数量
        """
        # 检查缓存
        if kb_id in self._cache:
            return self._cache[kb_id]

        result = await self.db.execute(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.knowledge_base_id == kb_id)
            .where(DocumentChunk.embedding.isnot(None))
        )
        count = result.scalar() or 0
        
        self._cache[kb_id] = count
        self._last_refresh[kb_id] = time.time()
        
        return count

    async def has_vectors(self, kb_id: int) -> bool:
        """检查知识库是否有向量"""
        return await self.count_vectors(kb_id) > 0

    # ---------- 索引管理 ----------
    async def create_index(
        self,
        kb_id: Optional[int] = None,
        index_type: str = "ivfflat",
        lists: int = 100,
    ) -> Dict[str, Any]:
        """
        为向量列创建索引

        Args:
            kb_id: 知识库 ID (可选，为整个表创建)
            index_type: 索引类型 ('ivfflat', 'hnsw')
            lists: IVF 索引的列表数 (通常设为 sqrt(总向量数))

        Returns:
            操作结果
        """
        try:
            if index_type == "ivfflat":
                # 创建 IVF 索引
                index_name = f"ix_chunks_embedding_ivfflat_{kb_id or 'all'}"
                
                sql = text(f"""
                    CREATE INDEX IF NOT EXISTS {index_name}
                    ON chunks
                    USING ivfflat (embedding vector_cosine_ops)
                    WITH (lists = :lists)
                """)
                await self.db.execute(sql, {"lists": lists})
                
            elif index_type == "hnsw":
                # 创建 HNSW 索引 (pgvector 0.5+)
                index_name = f"ix_chunks_embedding_hnsw_{kb_id or 'all'}"
                
                sql = text(f"""
                    CREATE INDEX IF NOT EXISTS {index_name}
                    ON chunks
                    USING hnsw (embedding vector_cosine_ops)
                """)
                await self.db.execute(sql)
                
            else:
                raise ValueError(f"不支持的索引类型: {index_type}")

            await self.db.commit()
            
            logger.info(f"成功创建 {index_type} 索引: {index_name}")
            return {
                "success": True,
                "index_name": index_name,
                "index_type": index_type,
            }
            
        except Exception as e:
            logger.error(f"创建索引失败: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    async def rebuild_indexes(self) -> Dict[str, Any]:
        """重建所有向量索引"""
        try:
            # 获取现有索引
            sql = text("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'chunks'
                AND indexname LIKE 'ix_chunks_embedding%'
            """)
            result = await self.db.execute(sql)
            indexes = [row[0] for row in result.fetchall()]
            
            # 重建索引
            for index_name in indexes:
                await self.db.execute(text(f"REINDEX INDEX {index_name}"))
            
            await self.db.commit()
            
            logger.info(f"成功重建 {len(indexes)} 个索引")
            return {
                "success": True,
                "rebuilt_indexes": indexes,
                "count": len(indexes),
            }
            
        except Exception as e:
            logger.error(f"重建索引失败: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    # ---------- 统计信息 ----------
    async def stats(self, kb_id: Optional[int] = None) -> Dict[str, Any]:
        """
        获取向量存储统计信息

        Args:
            kb_id: 知识库 ID (可选)

        Returns:
            统计信息字典
        """
        query = select(
            func.count(DocumentChunk.id),
            func.sum(
                func.case(
                    (DocumentChunk.embedding.isnot(None), 1),
                    else_=0
                )
            ),
        )
        
        if kb_id is not None:
            query = query.where(DocumentChunk.knowledge_base_id == kb_id)

        result = await self.db.execute(query)
        row = result.first()
        
        total_chunks = int(row[0]) if row[0] else 0
        chunks_with_vectors = int(row[1]) if row[1] else 0
        
        return {
            "total_chunks": total_chunks,
            "chunks_with_vectors": chunks_with_vectors,
            "vector_dimension": self.dim,
            "kb_id": kb_id,
        }

    # ---------- 工具方法 ----------
    def _parse_vector(self, raw: Any) -> Optional[List[float]]:
        """解析数据库中的向量表示"""
        if raw is None:
            return None
        
        if isinstance(raw, str):
            # pgvector 返回的格式: "[0.1,0.2,...]"
            clean = raw.strip("[]")
            values = [float(v.strip()) for v in clean.split(",") if v.strip()]
            return values if values else None
        
        if isinstance(raw, list):
            return [float(v) for v in raw]
        
        return None

    def _parse_metadata(self, raw: Any) -> Dict[str, Any]:
        """解析元数据"""
        if raw is None:
            return {}
        
        import json
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"raw": raw}
        
        return dict(raw) if raw else {}

    def _invalidate_cache(self) -> None:
        """使缓存失效"""
        self._cache.clear()
        self._last_refresh.clear()


# 辅助函数
async def init_pgvector_extension(db: AsyncSession) -> None:
    """
    初始化 pgvector 扩展

    必须在使用 pgvector 之前调用一次。
    通常在应用启动时调用。
    """
    try:
        await db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await db.commit()
        logger.info("pgvector 扩展已初始化")
    except Exception as e:
        logger.error(f"初始化 pgvector 扩展失败: {e}")
        raise


def create_pg_vector_store(db: AsyncSession, dim: int = 384) -> PgVectorStore:
    """创建 PgVectorStore 实例"""
    return PgVectorStore(db, dim)

"""🟨 RAG-PY 项目实体工厂（适配层）

级联关系图（默认 SubFactory 自动建，也可手动传覆盖）：
  Tenant (1)
    └── User (N)   [tenant_id nullable，兼容老用户]
          ├── KnowledgeBase (N)
          │     └── Document (N)
          │           └── DocumentChunk (N)
          └── Conversation (N)
                └── ChatMessageRecord (N)
  RefreshToken (N) → User (1)
  Role (N) ↔ Permission (N)  (多对多，role_permissions 关联表)
  User (N) ↔ Role (N)        (多对多，user_roles 关联表)
  AuditLog (独立，不级联，通常直接造)

### 为什么 UserFactory 要自动 hash 密码？
  实体类 User.password_hash 存的是 Argon2id hash，如果测试里直接写：
      await UserFactory.acreate(db_session, password_hash="123456")
  那 security.verify_password("123456", user.password_hash) 永远返回 False，
  所有需要登录的测试（API 层、鉴权层）都会莫名其妙挂。

  所以 UserFactory 增加一个「虚拟字段」 password：
    await UserFactory.acreate(db_session, password="Test@123")
  → 内部自动调用 hash_password("Test@123") 填到 password_hash。
  不传 password 时默认用 `FactoryDefaultPassword123!`（写死一个强密码）。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

import factory
from faker import Faker

from .base_factory import BaseAsyncFactory


# ============================================================
#  Faker 实例（全模块共享，用户可以通过 pytest-randomly seed 固定复现）
# ============================================================
fake = Faker("zh_CN")  # 中文假数据更贴合真实场景（用户名、地址、标题等）


# ============================================================
#  1. Tenant 工厂（最顶层，无依赖）
# ============================================================
class TenantFactory(BaseAsyncFactory):
    class Meta:
        from app.models.entities.tenant import Tenant as _Tenant
        model = _Tenant

    # name 要 unique，用 uuid4 后缀避免冲突（Faker.company() 可能重复）
    name = factory.LazyAttribute(lambda _: f"{fake.company()}_{fake.uuid4()[:8]}")
    plan = factory.LazyAttribute(lambda _: random.choice(["free", "pro", "enterprise"]))
    is_active = True

    # 配额：随机给点值，也可能是 NULL（0/NULL = 不限）
    max_knowledge_bases = factory.LazyAttribute(
        lambda _: random.choice([None, 5, 50, 500])
    )
    max_documents_per_kb = factory.LazyAttribute(
        lambda _: random.choice([None, 100, 1000])
    )
    max_storage_mb = factory.LazyAttribute(
        lambda _: random.choice([None, 1024, 10240])
    )
    max_requests_per_minute = factory.LazyAttribute(
        lambda _: random.choice([None, 60, 600, 6000])
    )


# ============================================================
#  2. User 工厂（依赖 Tenant，nullable）
# ============================================================
class UserFactory(BaseAsyncFactory):
    class Meta:
        from app.models.entities.user import User as _User
        model = _User

    username = factory.LazyAttribute(lambda _: f"{fake.user_name()}_{fake.uuid4()[:6]}")
    email = factory.LazyAttribute(lambda _: f"{fake.uuid4()[:8]}@{fake.free_email_domain()}")
    is_active = True
    is_admin = False

    # 默认不自动建 Tenant（保持和历史兼容：nullable=True 老用户可以没有 tenant）。
    # 需要 tenant 时手动：await UserFactory.acreate(db_session, tenant=tenant_obj)
    # 或传 SubFactory：UserFactory(tenant=factory.SubFactory(TenantFactory))
    tenant = None
    tenant_id = None

    # ---- 虚拟字段：password（不直接存，取出来 hash 后填 password_hash）----
    class Params:
        # 声明一个「工厂参数」，不会直接映射到数据库字段
        password = "FactoryDefaultPassword123!"

    password_hash = factory.LazyAttribute(
        lambda o: _hash_password(o.password)
    )

    # ---- post_generation：多对多 Role 分配 ----
    @factory.post_generation
    def roles(self, create: bool, extracted: Any, **kwargs: Any):
        """测试里这样用：
            user = await UserFactory.acreate(db_session, roles=[role_admin, role_editor])
        不传 roles 默认不分配任何角色（RBAC opt-in，老用户没有角色绕过权限检查）。
        """
        if not create:
            return  # build 模式不用处理
        if extracted:
            # extracted 就是传进来的 roles 列表
            for role in extracted:
                self.roles.append(role)


def _hash_password(raw: str) -> str:
    """密码 hash 辅助函数（懒加载 import，避免工厂模块 import app.core 出错）。"""
    try:
        from app.core.security import hash_password
        return hash_password(raw)
    except Exception:
        # 极端情况（security 模块暂时坏了），返回一个占位 hash，保证工厂至少能建对象
        return f"$argon2id$fallback${raw}"


# ============================================================
#  3. KnowledgeBase 工厂（依赖 User 作为 owner）
# ============================================================
class KnowledgeBaseFactory(BaseAsyncFactory):
    class Meta:
        from app.models.entities.knowledge_base import KnowledgeBase as _KB
        model = _KB

    name = factory.LazyAttribute(
        lambda _: f"{fake.bs()} 知识库 {fake.random_int(100, 9999)}"
    )
    description = factory.LazyAttribute(lambda _: fake.text(max_nb_chars=200))
    embedding_model = factory.LazyAttribute(
        lambda _: random.choice(["default", "text-embedding-3-small", "bge-small-zh"])
    )
    chunk_size = factory.LazyAttribute(lambda _: random.choice([256, 500, 1024]))
    chunk_overlap = factory.LazyAttribute(
        lambda o: max(0, o.chunk_size // 10)  # 默认 overlap = chunk_size 的 10%
    )
    is_public = factory.LazyAttribute(lambda _: random.choice([True, False]))
    status = "active"
    total_documents = 0
    total_chunks = 0

    # 默认自动建 owner：测试里很少「KB 没有 owner」，除非手动传 owner=None
    owner = factory.SubFactory(UserFactory)
    user_id = factory.LazyAttribute(lambda o: o.owner.id if o.owner is not None else None)


# ============================================================
#  4. Document 工厂（依赖 KnowledgeBase）
# ============================================================
class DocumentFactory(BaseAsyncFactory):
    class Meta:
        from app.models.entities.document import Document as _Doc
        model = _Doc

    filename = factory.LazyAttribute(
        lambda _: random.choice([
            f"{fake.word()}.md",
            f"{fake.word()}.txt",
            f"{fake.word()}.pdf",
            f"{fake.word()}.docx",
        ])
    )
    file_type = factory.LazyAttribute(
        lambda o: o.filename.rsplit(".", 1)[-1].lower() if "." in o.filename else "txt"
    )
    mime_type = factory.LazyAttribute(
        lambda o: {
            "md": "text/markdown", "txt": "text/plain",
            "pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }.get(o.file_type, "application/octet-stream")
    )
    file_size = factory.LazyAttribute(lambda _: random.randint(1_000, 5_000_000))
    size_bytes = factory.LazyAttribute(lambda o: o.file_size)
    description = factory.LazyAttribute(lambda _: fake.sentence(nb_words=10))
    content_text = factory.LazyAttribute(lambda _: fake.text(max_nb_chars=3000))
    status = factory.LazyAttribute(
        lambda _: random.choice(["pending", "processing", "ready", "failed"])
    )
    total_chunks = 0

    # 级联：默认自动建所属 KB（含所属 User + 所属 Tenant 可选）
    knowledge_base = factory.SubFactory(KnowledgeBaseFactory)
    knowledge_base_id = factory.LazyAttribute(lambda o: o.knowledge_base.id)


# ============================================================
#  5. DocumentChunk 工厂（依赖 Document + KnowledgeBase）
# ============================================================
class DocumentChunkFactory(BaseAsyncFactory):
    class Meta:
        from app.models.entities.document import DocumentChunk as _Chunk
        model = _Chunk

    content = factory.LazyAttribute(lambda _: fake.paragraph(nb_sentences=5))
    chunk_index = factory.Sequence(lambda n: n)  # 自增序号，每创建 1 个 +1
    metadata_ = None  # 可以传 JSON str，比如 '{"page": 1}'
    vector_index = factory.LazyAttribute(lambda o: o.chunk_index)
    embedding = None  # 向量列默认空，测试向量检索时手动塞假数据
    search_vector = None  # FTS 列默认空

    # 级联：默认自动建 Document（连带建 KB + User）
    document = factory.SubFactory(DocumentFactory)
    document_id = factory.LazyAttribute(lambda o: o.document.id)
    knowledge_base_id = factory.LazyAttribute(lambda o: o.document.knowledge_base_id)


# ============================================================
#  6. Conversation + ChatMessageRecord 工厂
# ============================================================
class ConversationFactory(BaseAsyncFactory):
    class Meta:
        from app.models.entities.conversation import Conversation as _Conv
        model = _Conv

    title = factory.LazyAttribute(
        lambda _: random.choice([
            fake.sentence(nb_words=6),
            "New Conversation",
            f"关于 {fake.word()} 的讨论",
        ])
    )
    is_active = True

    # 默认同时建 owner 和关联 KB（典型对话场景：用户在某个 KB 里提问）
    owner = factory.SubFactory(UserFactory)
    user_id = factory.LazyAttribute(lambda o: o.owner.id if o.owner else None)
    knowledge_base = factory.SubFactory(KnowledgeBaseFactory)
    knowledge_base_id = factory.LazyAttribute(
        lambda o: o.knowledge_base.id if o.knowledge_base else None
    )


class ChatMessageRecordFactory(BaseAsyncFactory):
    class Meta:
        from app.models.entities.conversation import ChatMessageRecord as _Msg
        model = _Msg

    role = factory.LazyAttribute(lambda _: random.choice(["user", "assistant", "system"]))
    content = factory.LazyAttribute(
        lambda _: fake.text(max_nb_chars=500) if random.random() > 0.3 else fake.word()
    )
    retrieved_contexts = None

    conversation = factory.SubFactory(ConversationFactory)
    conversation_id = factory.LazyAttribute(lambda o: o.conversation.id)


# ============================================================
#  7. RefreshToken + Role + Permission + AuditLog 工厂
# ============================================================
class RefreshTokenFactory(BaseAsyncFactory):
    class Meta:
        from app.models.entities.auth import RefreshToken as _RT
        model = _RT

    # 懒加载：hash 函数要 token 生成完才调用
    class Params:
        raw_token = factory.LazyAttribute(lambda _: fake.sha256())

    token_hash = factory.LazyAttribute(
        lambda o: _sha256_hash(o.raw_token)
    )
    device_info = factory.LazyAttribute(
        lambda _: f"{fake.user_agent()} / {fake.platform()}"
    )
    ip_address = factory.LazyAttribute(lambda _: fake.ipv4_public())
    expires_at = factory.LazyAttribute(
        lambda _: datetime.utcnow() + timedelta(days=random.randint(1, 30))
    )
    revoked_at = None  # 默认没过期，测试撤销场景手动传

    # ⚠️ 语义约束：RefreshToken 必须绑定一个「已存在」的真实用户，
    #    不使用 SubFactory 自动级联（RefreshToken 没有 user relationship，SubFactory 级联保存失效）。
    #    调用方必须显式传 user_id=xxx，例如：
    #      user = await user_factory.acreate(db_session)
    #      rt = await refresh_token_factory.acreate(db_session, user_id=user.id)
    user_id = None


class RoleFactory(BaseAsyncFactory):
    class Meta:
        from app.models.entities.auth import Role as _Role
        model = _Role

    name = factory.LazyAttribute(
        lambda _: random.choice(["admin", "editor", "viewer", f"custom_{fake.word()}"])
    )
    description = factory.LazyAttribute(lambda _: fake.sentence(nb_words=8))

    @factory.post_generation
    def permissions(self, create: bool, extracted: Any, **kwargs: Any):
        if not create or not extracted:
            return
        for p in extracted:
            self.permissions.append(p)


class PermissionFactory(BaseAsyncFactory):
    class Meta:
        from app.models.entities.auth import Permission as _Perm
        model = _Perm

    resource = factory.LazyAttribute(
        lambda _: random.choice(["kb", "document", "conversation", "user", "tenant"])
    )
    action = factory.LazyAttribute(
        lambda _: random.choice(["create", "read", "update", "delete", "list"])
    )
    description = factory.LazyAttribute(
        lambda o: f"允许对 {o.resource} 执行「{o.action}」操作"
    )


class AuditLogFactory(BaseAsyncFactory):
    class Meta:
        from app.models.entities.auth import AuditLog as _Audit
        model = _Audit

    method = factory.LazyAttribute(
        lambda _: random.choice(["GET", "POST", "PUT", "DELETE", "PATCH"])
    )
    path = factory.LazyAttribute(
        lambda _: random.choice([
            "/api/v1/knowledge-bases",
            f"/api/v1/knowledge-bases/{fake.random_int(1, 999)}",
            "/api/v1/auth/login",
            "/api/v1/documents",
            f"/api/v1/conversations/{fake.random_int(1, 999)}",
            "/health",
        ])
    )
    status_code = factory.LazyAttribute(
        lambda _: random.choices(
            [200, 201, 204, 400, 401, 403, 404, 500],
            weights=[50, 10, 5, 8, 7, 5, 10, 5],  # 2xx 多，模拟真实分布
            k=1,
        )[0]
    )
    ip_address = factory.LazyAttribute(lambda _: fake.ipv4_public())
    user_agent = factory.LazyAttribute(lambda _: fake.user_agent())
    request_body = factory.LazyAttribute(
        # Faker 18.x 的 json() API 不稳定，手动构造一个脱敏的请求体摘要 JSON 字符串
        lambda _: (
            '{"query": "' + fake.word() + '", '
            '"page": ' + str(fake.random_int(min=1, max=100)) + ', '
            '"page_size": ' + str(fake.random_int(min=5, max=50)) + '}'
        )
    )
    response_time_ms = factory.LazyAttribute(lambda _: random.randint(5, 5000))

    # user_id / username 可能空（匿名请求），20% 概率匿名
    user_id = factory.LazyAttribute(
        lambda _: None if random.random() < 0.2 else fake.random_int(1, 1000)
    )
    username = factory.LazyAttribute(
        lambda o: None if o.user_id is None else fake.user_name()
    )


# ============================================================
#  工具函数
# ============================================================
def _sha256_hash(raw: str) -> str:
    """SHA-256 辅助（RefreshToken.token_hash 用）。"""
    import hashlib
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

"""phase3 ext: tenant tables + RBAC seed data

Revision ID: f1a2c3d4e5f6
Revises: d83ae09cb032
Create Date: 2026-08-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a2c3d4e5f6'
down_revision: Union[str, None] = 'd83ae09cb032'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 默认角色与权限配置 —— 首次迁移时写入
DEFAULT_ROLES = ["admin", "editor", "viewer"]

# 权限 = (resource, action, description)
DEFAULT_PERMISSIONS = [
    # knowledge base
    ("kb", "create", "创建知识库"),
    ("kb", "read", "查看知识库"),
    ("kb", "update", "更新知识库"),
    ("kb", "delete", "删除知识库"),
    # document
    ("document", "create", "上传文档"),
    ("document", "read", "查看文档"),
    ("document", "update", "更新文档"),
    ("document", "delete", "删除文档"),
    # conversation
    ("conversation", "create", "创建会话"),
    ("conversation", "read", "查看会话"),
    ("conversation", "delete", "删除会话"),
    # chat / retrieval
    ("chat", "use", "使用聊天问答"),
    ("retrieval", "use", "使用检索接口"),
    # user / admin
    ("user", "manage", "管理用户"),
    ("system", "admin", "系统管理员权限"),
]

# 角色 -> 权限映射
ROLE_PERMISSIONS = {
    "admin": [  # 管理员 — 全部 15 个权限
        ("kb", "create"), ("kb", "read"), ("kb", "update"), ("kb", "delete"),
        ("document", "create"), ("document", "read"), ("document", "update"), ("document", "delete"),
        ("conversation", "create"), ("conversation", "read"), ("conversation", "delete"),
        ("chat", "use"), ("retrieval", "use"),
        ("user", "manage"), ("system", "admin"),
    ],
    "editor": [  # 编辑者 — 业务全权限，不含系统管理
        ("kb", "create"), ("kb", "read"), ("kb", "update"), ("kb", "delete"),
        ("document", "create"), ("document", "read"), ("document", "update"), ("document", "delete"),
        ("conversation", "create"), ("conversation", "read"), ("conversation", "delete"),
        ("chat", "use"), ("retrieval", "use"),
    ],
    "viewer": [  # 观察者 — 只读 + 问答
        ("kb", "read"),
        ("document", "read"),
        ("conversation", "read"),
        ("chat", "use"), ("retrieval", "use"),
    ],
}


def upgrade() -> None:
    # ---- 1. tenants 表 ----
    op.create_table(
        'tenants',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('plan', sa.String(length=50), server_default='free', nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('1'), nullable=True),
        sa.Column('max_knowledge_bases', sa.Integer(), nullable=True),
        sa.Column('max_documents_per_kb', sa.Integer(), nullable=True),
        sa.Column('max_storage_mb', sa.Integer(), nullable=True),
        sa.Column('max_requests_per_minute', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tenants_created_at'), 'tenants', ['created_at'], unique=False)
    op.create_index(op.f('ix_tenants_is_active'), 'tenants', ['is_active'], unique=False)
    op.create_index(op.f('ix_tenants_name'), 'tenants', ['name'], unique=True)
    op.create_index(op.f('ix_tenants_plan'), 'tenants', ['plan'], unique=False)

    # ---- 2. users.tenant_id 外键 ----
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_users_tenant_id_tenants',
            'tenants', ['tenant_id'], ['id'],
            ondelete='SET NULL'
        )
        batch_op.create_index(op.f('ix_users_tenant_id'), ['tenant_id'], unique=False)

    # ---- 3. 种子数据 — 角色 ----
    conn = op.get_bind()
    for role_name in DEFAULT_ROLES:
        conn.execute(
            sa.text("INSERT OR IGNORE INTO roles (name, description) VALUES (:n, :d)"),
            {"n": role_name, "d": _role_desc(role_name)}
        )

    # ---- 4. 种子数据 — 权限 ----
    for (res, act, desc) in DEFAULT_PERMISSIONS:
        conn.execute(
            sa.text("INSERT OR IGNORE INTO permissions (resource, action, description) VALUES (:r, :a, :d)"),
            {"r": res, "a": act, "d": desc}
        )

    # ---- 5. 种子数据 — 角色 <-> 权限 关联 ----
    for role_name, perms in ROLE_PERMISSIONS.items():
        role_row = conn.execute(
            sa.text("SELECT id FROM roles WHERE name = :n"), {"n": role_name}
        ).fetchone()
        if role_row is None:
            continue
        role_id = role_row[0] if isinstance(role_row, (list, tuple)) else role_row.id
        for (res, act) in perms:
            perm_row = conn.execute(
                sa.text("SELECT id FROM permissions WHERE resource = :r AND action = :a"),
                {"r": res, "a": act}
            ).fetchone()
            if perm_row is None:
                continue
            perm_id = perm_row[0] if isinstance(perm_row, (list, tuple)) else perm_row.id
            conn.execute(
                sa.text("INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (:rid, :pid)"),
                {"rid": role_id, "pid": perm_id}
            )


def downgrade() -> None:
    # 1. users.tenant_id
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(op.f('ix_users_tenant_id'))
        batch_op.drop_constraint('fk_users_tenant_id_tenants', type_='foreignkey')
        batch_op.drop_column('tenant_id')

    # 2. tenants
    op.drop_index(op.f('ix_tenants_plan'), table_name='tenants')
    op.drop_index(op.f('ix_tenants_name'), table_name='tenants')
    op.drop_index(op.f('ix_tenants_is_active'), table_name='tenants')
    op.drop_index(op.f('ix_tenants_created_at'), table_name='tenants')
    op.drop_table('tenants')

    # 3. 种子数据不回滚（数据层面）


def _role_desc(name: str) -> str:
    return {
        "admin": "系统管理员 — 拥有全部权限",
        "editor": "编辑者 — 可创建/修改知识库和文档",
        "viewer": "观察者 — 只读权限，可提问",
    }.get(name, "")

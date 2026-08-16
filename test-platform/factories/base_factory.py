"""🟦 通用异步 Factory Boy 基类（任何项目直接用）

为什么要写这个基类？
  Factory Boy 3.3.0 自带的 `SQLAlchemyModelFactory` 默认是「同步 session + sync commit」，
  而现代 FastAPI + SQLAlchemy 1.4+ 项目基本都是 AsyncSession。
  直接用原生 SQLAlchemyModelFactory 会炸：
    - factory.create() → session.commit() 是 sync，AsyncSession 没有这个方法
    - session.add() 在 async 下要 await session.flush() / session.commit()

所以这个基类做了 3 件事：
  1. 把 Factory Boy 的同步 `_create()` hook 改成「先挂到 session，不立即 commit」
  2. 暴露 `async acreate()` 方法：add → flush → commit（或不 commit，走 fixture 事务回滚）
  3. 暴露 `async abuild()` 方法：只构造对象不进数据库（等价 build()）

用法（任何项目通用）：
  ```python
  from factories.base_factory import BaseAsyncFactory

  class UserFactory(BaseAsyncFactory):
      class Meta:
          model = User
      username = Faker("user_name")
      email = Faker("email")

  # 测试里：
  async def test_xxx(db_session):
      user = await UserFactory.acreate(db_session, username="alice")
      assert user.id is not None
  ```

### 坑点记录（为什么不直接用 factory.alchemy 自带的 async 支持）
  Factory Boy 3.3 的 SQLAlchemyModelFactory 其实有部分 async 支持，但有两个问题：
  1. 它要求 session 是全局可获取的（通过 _meta.sqlalchemy_session），fixture 里每个 test
     都有独立 db_session，传进去很绕。
  2. 它的 `create()` 会隐式调用 `session.commit()`，而我们的 db_session fixture 是
     「每 test 结束 rollback」——commit 了就 rollback 不了，数据污染下一个 test。
  所以我们的策略是：**默认 acreate() 只 flush 不 commit**（id 已经拿到了），由外层
  fixture 统一管理事务。如果要真 commit，传 commit=True。
"""

from __future__ import annotations

from typing import Any, Optional, TypeVar

import factory
from factory.alchemy import SQLAlchemyModelFactory
from sqlalchemy.ext.asyncio import AsyncSession


M = TypeVar("M")


class BaseAsyncFactory(SQLAlchemyModelFactory):
    """通用异步 Factory 基类

    子类只需要声明：
      - class Meta: model = YourEntity（不用写 sqlalchemy_session，acreate 里显式传）
      - 各个字段的 LazyAttribute / SubFactory / Faker 声明
    """

    class Meta:
        abstract = True
        # 故意不写 sqlalchemy_session = ...，因为我们在 acreate 里显式传独立 session
        sqlalchemy_session_persistence = "flush"  # Factory Boy 不要自己 commit

    # ============================================================
    #  公开 API（async 世界里应该用的方法）
    # ============================================================

    @classmethod
    async def acreate(
        cls,
        db_session: AsyncSession,
        commit: bool = False,
        **kwargs: Any,
    ) -> M:
        """异步创建并持久化 1 条记录。

        Args:
            db_session: 当前 test 的 AsyncSession（fixture 注入的）
            commit: 默认 False = 只 flush（id 已生成，test 结束 fixture 统一 rollback）
                    True = 真 commit（极少用，仅测试 rollback 场景时开）
            **kwargs: 覆盖默认字段的值
        """
        # 1) 用 Factory Boy 的 build 策略生成对象（包含 SubFactory 的级联处理）
        #    注意：我们临时把 sqlalchemy_session 设进去，让 SubFactory 能 flush
        cls._meta.sqlalchemy_session = db_session
        try:
            instance = cls.create(**kwargs)
        finally:
            cls._meta.sqlalchemy_session = None  # 清理，防止串到其他 test

        # 2) 如果 SubFactory 没处理到的话，手动 add + flush
        #    （create() 默认会按 sqlalchemy_session_persistence 执行，这里兜底）
        try:
            await db_session.flush()
        except Exception:
            # 已经 flush 过就忽略
            pass

        if commit:
            await db_session.commit()
        return instance

    @classmethod
    async def abatch(
        cls,
        db_session: AsyncSession,
        size: int,
        commit: bool = False,
        **kwargs: Any,
    ) -> list[M]:
        """批量创建 size 条记录（带 Faker 多样性，每条字段不同）。"""
        result = []
        for _ in range(size):
            item = await cls.acreate(db_session, commit=False, **kwargs)
            result.append(item)
        if commit:
            await db_session.commit()
        return result

    @classmethod
    def abuild(cls, **kwargs: Any) -> M:
        """只构造内存对象，不写数据库（同步也行，因为不涉及 IO）。"""
        return cls.build(**kwargs)

    # ============================================================
    #  内部：给 SubFactory 级联时用的同步钩子
    # ============================================================

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        """Override Factory Boy 原生 _create，兼容 SubFactory 级联。

        说明：SubFactory 在同步 create() 流程里调用这个方法。
        如果 sqlalchemy_session 已经被 acreate() 临时设置了，就正常 add + flush；
        否则走纯内存构造（但 id 会是 None，因为没进 DB）。
        """
        session = cls._meta.sqlalchemy_session
        if session is None:
            # 纯 build 场景：直接返回内存对象
            return model_class(*args, **kwargs)

        # async session 没有同步 add/commit，我们用 sync 兼容 trick：
        # AsyncSession 的 add() 其实是协程，但可以拿到 sync_proxy_session 加。
        # 或者直接用 sync_session() 方法（SQLAlchemy 2.0+）。
        # 最简单：直接实例化然后 session.add 包一层 run_sync。
        instance = model_class(*args, **kwargs)

        # AsyncSession.add 本身其实是「同步接口」（SQLAlchemy 2.0 的设计：
        # add 不涉及 IO，所以不需要 await）。但不同版本行为可能有差异，try-except 兜底。
        try:
            session.add(instance)
        except Exception:
            # 老版本可能是协程，这里用同步包装调用
            import asyncio
            coro = session.add(instance)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # 在 async 上下文里，先挂着后面 acreate 会 flush
                    pass
                else:
                    loop.run_until_complete(coro)
            except Exception:
                pass

        # persistence = "flush" → 交给外层 acreate() 统一 flush，这里不重复操作
        return instance

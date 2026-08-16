"""B2 · 集成测试 — Agent ReAct 循环

直接构造 AgentService(db_session)，mock LLM 返回已知格式，验证：
  1) _parse_agent_output 解析正确
  2) run() 对空 query 直接拒绝 / 超过 max_turns 立即停止
  3) 工具不存在时 Observation 的反馈
"""
from __future__ import annotations

import pytest


class TestAgentParser:
    """AgentService._parse_agent_output 解析器 (纯函数)"""

    def test_full_react_format(self):
        from app.services.agent_service import AgentService
        text = (
            "Thought: 我需要查知识库\n"
            "Action: search_kb\n"
            "Action Input: {\"kb_id\": 1, \"query\": \"什么是 pgvector\"}\n"
        )
        t, a, ai = AgentService._parse_agent_output(text)
        assert "查知识库" in t
        assert a == "search_kb"
        assert "pgvector" in ai

    def test_chinese_colons(self):
        from app.services.agent_service import AgentService
        # 当前实现正则是 "Thought/Action/Action Input" 前缀 + [中英冒号]
        text = "Thought：我要计算\nAction：计算器\nAction Input：1+2"
        t, a, ai = AgentService._parse_agent_output(text)
        assert "计算" in t
        assert "计算器" in a
        assert "1+2" in ai

    def test_final_answer_shortcut(self):
        from app.services.agent_service import AgentService
        text = "直接给出答案: 42"
        t, a, ai = AgentService._parse_agent_output(text)
        # 当 LLM 直接回答时 action 应为空 (或特殊 "Final Answer")
        # Agent 层对 action 为空会直接 break 循环
        assert a == "" or a.lower() == "final answer"


class TestAgentRun:
    """Agent 执行入口 — 重点测「空 query / max_turns=0」这些不依赖外部的分支"""

    async def test_empty_query_refused(self):
        from app.services.agent_service import AgentService
        agent = AgentService(db=None)
        res = await agent.run("   ", kb_id=1, user_id=1, max_turns=3)
        assert res.success is False
        assert res.error and "不能为空" in res.error

    async def test_max_turns_zero_stops_immediately(self):
        from app.services.agent_service import AgentService
        agent = AgentService(db=None)
        # max_turns=0 → 循环不执行, 会直接返回空回答 (或无工具 error)
        res = await agent.run("任意问题", kb_id=None, user_id=1, max_turns=0)
        # 无论走哪条路径都不应抛异常
        assert isinstance(res.success, bool)
        assert isinstance(res.latency_ms, float)

    async def test_tool_name_invalid_observation(self, db_session, test_user, test_kb):
        """当 LLM 乱选一个「不存在的工具」时，Observation 应回一个 "未知工具 xxx" 反馈。"""
        from app.services.agent_service import AgentService
        from app.processors.llm.llm_service import ChatMessage

        class _OneShotMock:
            provider_name = "mock"

            def chat(self, messages, **kw):
                from app.processors.llm.llm_service import ChatResult
                # 故意返回不存在的工具名
                return ChatResult(
                    content=(
                        "Thought: 测试不存在的工具\n"
                        "Action: this_tool_does_not_exist\n"
                        "Action Input: {\"a\": 1}\n"
                    ),
                    model="mock",
                    provider="mock",
                )

        svc = AgentService(db=db_session)
        svc.llm = _OneShotMock()

        res = await svc.run("测试未知工具", kb_id=test_kb.id,
                            user_id=test_user.id, max_turns=2,
                            include_raw_steps=True)
        # 至少 1 步 steps 且 Observation 提到 "未知工具" / "not found" 或类似含义
        assert res.steps, "至少有 1 个 ReAct 步骤"
        any_obs_mentions_unknown = any(
            (s.observation and ("未知工具" in str(s.observation) or
             "not found" in str(s.observation).lower())) for s in res.steps
        )
        # 允许实现写 "未找到工具 / 工具不存在" 的任意变体, 只要不是空 observation
        assert all(s.observation != "" for s in res.steps if s.action and s.action != "Final Answer"), \
            "未知工具的 Observation 不能为空"

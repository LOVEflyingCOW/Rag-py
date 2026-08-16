from __future__ import annotations

"""Agent 服务 —— 基于 ReAct 推理 + 工具调用的智能助手

核心能力:
  1. 知识库搜索工具    (search_kb)           —— 搜索指定知识库中的相关内容
  2. 文档内容查询工具   (get_doc)              —— 根据 document_id 获取全文
  3. 网页搜索工具       (web_search, 可选)      —— 当知识库不足时联网查询
  4. 对话历史管理       (conversation_history) —— 多轮对话记忆
  5. ReAct 风格推理     (Thought / Action / Observation) —— 让 Agent 决定下一步动作

执行流程:
  user_query ──▶ system_prompt + 对话历史 + 工具描述
                └─▶ LLM 产生 Thought + Action(tool_name, args)
                      └─▶ 调用工具, 得到 Observation
                            └─▶ 循环 1~3 次
                                  └─▶ 最终回答（基于所有检索到的上下文）
"""

import json
import re
import time
from typing import List, Dict, Optional, Any, Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger as _logger
from app.models.entities.document import Document, DocumentChunk
from app.models.entities.knowledge_base import KnowledgeBase
from app.processors.llm.llm_service import ChatMessage
from app.services.retrieval_service import RetrievalService, RetrievedHit
from app.services.chat_service import RAGPipeline
from app.processors import get_llm_service


# ============ 工具定义 ============

@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    content: str
    error: Optional[str] = None
    raw: Optional[Any] = None


class BaseTool:
    """Agent 工具基类"""
    name: str = "base_tool"
    description: str = "基类，请勿直接使用"

    async def run(self, args: Dict[str, Any], context: Dict[str, Any]) -> ToolResult:
        raise NotImplementedError


class SearchKBTool(BaseTool):
    """知识库搜索工具"""
    name = "search_kb"
    description = (
        "在指定知识库中进行语义搜索。"
        "参数: {\"kb_id\": int, \"query\": str, \"top_k\": int (可选，默认5)}"
        "当你需要查找商家内部文档、FAQ、产品描述、政策条款等信息时，请使用此工具。"
    )

    def __init__(self, db: AsyncSession):
        self.db = db
        self.retrieval = RetrievalService(db)

    async def run(self, args: Dict[str, Any], context: Dict[str, Any]) -> ToolResult:
        kb_id = args.get("kb_id") or context.get("kb_id")
        query = args.get("query", "").strip()
        top_k = int(args.get("top_k", 5))
        user_id = context.get("user_id")

        if not kb_id or not query:
            return ToolResult(False, "", "需要 kb_id 和 query 参数")

        try:
            hits = await self.retrieval.search(
                kb_id=int(kb_id),
                query_text=query,
                user_id=user_id,
                top_k=top_k,
                min_score=0.1,
                enable_rerank=True,
                enable_merge=True,
            )
        except Exception as exc:
            return ToolResult(False, "", f"检索异常: {exc}")

        if not hits:
            return ToolResult(True, "(知识库中未检索到相关内容)")

        lines = [f"—— 知识库 #{kb_id} 检索到 {len(hits)} 条："]
        for i, h in enumerate(hits, start=1):
            lines.append(
                f"[{i}] (文档: {h.document_filename}, 综合分={h.final_score:.3f}, "
                f"vec={h.vector_score:.3f}, bm25={h.bm25_score:.3f})\n"
                f"    {h.content[:400]}"
            )
        return ToolResult(True, "\n\n".join(lines), raw=hits)


class GetDocTool(BaseTool):
    """文档全文查询工具"""
    name = "get_doc"
    description = (
        "获取指定文档的完整内容。"
        "参数: {\"document_id\": int}"
        "当你已经通过 search_kb 找到一个相关文档，但需要看全文以获得更完整信息时，使用此工具。"
    )

    def __init__(self, db: AsyncSession):
        self.db = db

    async def run(self, args: Dict[str, Any], context: Dict[str, Any]) -> ToolResult:
        doc_id = args.get("document_id")
        if not doc_id:
            return ToolResult(False, "", "需要 document_id 参数")
        result = await self.db.execute(select(Document).where(Document.id == int(doc_id)))
        doc = result.scalars().first()
        if doc is None:
            return ToolResult(False, "", f"找不到 document_id={doc_id}")
        result = await self.db.execute(
            select(DocumentChunk).where(DocumentChunk.document_id == int(doc_id)).order_by(DocumentChunk.id)
        )
        chunks = result.scalars().all()
        content = "\n\n".join([c.content for c in chunks if c.content]) or doc.description or "(文档无正文)"
        return ToolResult(
            True,
            f"—— 文档 #{doc.id} 「{doc.filename}」 ——\n{content[:2000]}{'...（超长已截断）' if len(content) > 2000 else ''}",
            raw={"doc": doc, "chunks": chunks},
        )


# ============ Agent 主流程 ============

@dataclass
class AgentStep:
    """Agent 的一步推理"""
    thought: str = ""
    action: str = ""            # 工具名 或 "Final Answer"
    action_input: str = ""      # 工具参数 JSON 字符串
    observation: str = ""       # 工具输出
    latency_ms: float = 0.0


@dataclass
class AgentResult:
    """Agent 执行结果"""
    success: bool = True
    answer: str = ""
    steps: List[AgentStep] = field(default_factory=list)
    latency_ms: float = 0.0
    error: Optional[str] = None


class AgentService:
    """ReAct Agent —— 思考-行动-观察 循环"""

    # ReAct 风格系统 prompt
    SYSTEM_PROMPT = """你是一个严谨的客服助手（Agent），只能根据提供的上下文回答用户问题。

你通过"思考→行动→观察"的循环来回答问题：
  Thought  : 用一句话分析用户问题，确定需要调用什么工具来获取信息
  Action   : 选择一个工具并传入参数（JSON 格式）
  Observation: 工具返回的信息（由系统填充，你不要自己编造）

你可以调用的工具：
$TOOL_LIST

【使用规则】
1. 永远先调用工具获取信息，再根据工具返回的内容给出回答。
2. 绝对不要编造 Observation 内容。
3. 你的 Action 必须写成：
   Action: <工具名>
   Action Input: {"key": "value"}
4. 当你已有足够信息回答问题时，写：
   Action: Final Answer
   Action Input: <你的完整回答>
5. 最多循环 $MAX_TURNS 次。
6. 回答要用中文，简洁明了，标注出引用来源。

现在请与用户对话。
"""

    USER_TURN_PROMPT = """【历史上下文】
{history_text}

【当前用户问题】
{query}

【请开始你的推理循环】
"""

    def __init__(self, db: Optional[AsyncSession] = None):
        self.db = db
        self.llm = get_llm_service()
        self._build_tools()

    def _build_tools(self):
        """构造可用工具列表。DB 不可用时仅能使用纯文本工具。"""
        self.tools: Dict[str, BaseTool] = {}
        if self.db is not None:
            self.tools[SearchKBTool.name] = SearchKBTool(self.db)
            self.tools[GetDocTool.name] = GetDocTool(self.db)

    # ---------- 主入口 ----------
    async def run(
        self,
        query: str,
        *,
        kb_id: Optional[int] = None,
        user_id: Optional[int] = None,
        max_turns: int = 3,
        history: Optional[List[Dict[str, str]]] = None,
        include_raw_steps: bool = False,
    ) -> AgentResult:
        """执行一次 Agent 会话 —— 返回思考步骤 + 最终回答"""
        start = time.perf_counter()
        result = AgentResult()

        if not query or not query.strip():
            result.success = False
            result.error = "查询不能为空"
            return result

        # 工具描述
        tool_lines = []
        for t in self.tools.values():
            tool_lines.append(f"- {t.name}: {t.description}")
        if not tool_lines:
            tool_lines.append("- (无可调用工具，请先配置数据库)")
        tool_list_text = "\n".join(tool_lines)

        # 系统 prompt（用 replace 避免 JSON 花括号与 format 冲突）
        sys_prompt_text = self.SYSTEM_PROMPT.replace("$TOOL_LIST", tool_list_text).replace("$MAX_TURNS", str(max_turns))
        messages: List[ChatMessage] = [
            ChatMessage(role="system", content=sys_prompt_text),
        ]

        # 对话历史（最多保留最近 5 条 user/assistant 对）
        if history:
            for h in history[-10:]:
                role = h.get("role", "user")
                content = str(h.get("content", ""))
                if role in {"user", "assistant"}:
                    messages.append(ChatMessage(role=role, content=content))

        # 首轮 user prompt
        current_user_message = self.USER_TURN_PROMPT.format(
            history_text="（无历史对话）" if not history else f"（共 {len(history)} 条历史）",
            query=query,
        )
        messages.append(ChatMessage(role="user", content=current_user_message))

        # ReAct 循环
        for turn in range(max_turns):
            turn_start = time.perf_counter()

            # 1) LLM 产生动作
            llm_result = self.llm.chat(messages, temperature=0.2, max_tokens=800)
            if not llm_result.success:
                result.success = False
                result.error = f"LLM 调用失败: {llm_result.error}"
                break

            llm_text = llm_result.content or ""
            messages.append(ChatMessage(role="assistant", content=llm_text))

            # 2) 解析 Thought / Action / Action Input
            thought, action, action_input = self._parse_agent_output(llm_text)

            step = AgentStep(
                thought=thought,
                action=action,
                action_input=action_input,
                latency_ms=(time.perf_counter() - turn_start) * 1000,
            )
            result.steps.append(step)

            # 3) Final Answer → 结束
            if action.lower() == "final answer" or action == "":
                # 若 action 为空（LLM 没按格式走），但 content 看起来像回答，则直接返回
                answer_text = action_input if action_input else llm_text.strip()
                result.answer = answer_text
                result.success = True
                break

            # 4) 否则：调用工具
            if action not in self.tools:
                # 工具不存在 → 反馈让 LLM 重新选
                observation_text = f"错误：不存在工具「{action}」。可用工具：{', '.join(self.tools.keys())}"
                step.observation = observation_text[:500]
                messages.append(ChatMessage(role="user", content=f"Observation: {observation_text}"))
                continue

            # 解析 JSON 参数
            tool_args: Dict[str, Any] = {}
            try:
                tool_args = json.loads(action_input) if action_input.strip() else {}
            except Exception:
                # 不是 JSON，就作为字符串
                tool_args = {"query": action_input, "kb_id": kb_id}

            # 执行工具
            context = {"kb_id": kb_id, "user_id": user_id, "query": query, "turn": turn}
            tool_output = await self.tools[action].run(tool_args, context)
            step.observation = tool_output.content[:1500] if tool_output.success else f"(工具失败) {tool_output.error}"

            # 把 observation 作为 user message 追加 → 让 LLM 下一步
            obs_text = f"Observation [{action}]:\n{tool_output.content[:1500] if tool_output.success else tool_output.error}"
            messages.append(ChatMessage(role="user", content=obs_text))

        # 如果循环结束但还没产出 final answer → 让 LLM 用最后 observation 总结
        if not result.answer:
            summary_msg = (
                f"请根据以上所有 Observation 信息，用「Final Answer: <回答>」格式给出最终回答。\n"
                f"用户问题: {query}"
            )
            messages.append(ChatMessage(role="user", content=summary_msg))
            final_llm = self.llm.chat(messages, temperature=0.2, max_tokens=800)
            if final_llm.success:
                result.answer = final_llm.content.strip()
            else:
                result.success = False
                result.error = "LLM 最终总结失败"

        result.latency_ms = (time.perf_counter() - start) * 1000
        return result

    # ---------- 辅助: 解析 Agent 输出 ----------
    @staticmethod
    def _parse_agent_output(text: str) -> tuple[str, str, str]:
        """从 LLM 输出中提取 Thought / Action / Action Input"""
        thought = ""
        action = ""
        action_input = ""

        # 匹配双语前缀: Thought/思考 · Action/动作 · Action Input/动作输入 · Observation/观察 · Final Answer/最终回答
        # 兼容 ASCII 冒号 : 和中文全角冒号 ：，兼容大小写 (?i)
        # ⚠️ 关键词分组用 (?:...) 非捕获：保证 group(1) 依然是「内容本身」，不破坏下方 m.group(1) 代码
        patterns = [
            (
                r"(?i)(?:Thought|思考)\s*[:：]\s*(.*?)(?=(?:Action\s*[:：]|动作\s*[:：]|Observation\s*[:：]|观察\s*[:：]|Final Answer|最终回答|$))",
                "thought",
            ),
            (
                r"(?i)(?:Action|动作)\s*[:：]\s*(.*?)(?=(?:Action Input\s*[:：]|动作输入\s*[:：]|Observation\s*[:：]|观察\s*[:：]|Thought\s*[:：]|思考\s*[:：]|$))",
                "action",
            ),
            (
                r"(?i)(?:Action Input|动作输入)\s*[:：]\s*(.*?)(?=(?:Observation\s*[:：]|观察\s*[:：]|Thought\s*[:：]|思考\s*[:：]|Action\s*[:：]|动作\s*[:：]|$))",
                "input",
            ),
        ]

        for pat, key in patterns:
            m = re.search(pat, text, re.DOTALL)
            if m:
                val = m.group(1).strip().strip("`").strip()
                if key == "thought":
                    thought = val
                elif key == "action":
                    # 去除括号后缀（如 "Final Answer [summary]" → "Final Answer"）
                    action = re.split(r"[\[\(]", val, 1)[0].strip() if val else val
                elif key == "input":
                    action_input = val

        # 如果完全没匹配到 Action，但文本很短 → 视为直接 answer
        if not action and len(text) < 500:
            contains_react_keyword = any(k in text for k in ("Final", "Action", "最终回答", "动作"))
            if not contains_react_keyword:
                action = "Final Answer"
                action_input = text.strip()

        return thought, action, action_input


__all__ = ["AgentService", "AgentResult", "AgentStep", "SearchKBTool", "GetDocTool"]

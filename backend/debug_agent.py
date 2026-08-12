"""Agent 服务调试脚本 —— 单步追踪 Agent.run 的每一步"""
import sys, os, traceback
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.services.agent_service import AgentService
from app.processors import get_llm_service, ChatMessage

print("--- 1. 先看 mock LLM 对 Agent system prompt 的反应 ---")
llm = get_llm_service()
messages = [
    ChatMessage(role="system", content="你是一个严谨的客服助手，只能使用提供的上下文回答。"),
    ChatMessage(role="user", content="请介绍一下你自己"),
]
res = llm.chat(messages, temperature=0.2, max_tokens=800)
print("LLM 返回内容预览：")
print(res.content[:300])
print("---")

print("\n--- 2. 运行 AgentService.run() ---")
try:
    agent = AgentService(db=None)
    result = agent.run("请介绍一下你自己", kb_id=1, max_turns=1)
    print("成功!")
    print("answer:", result.answer[:200])
    print("steps:", len(result.steps))
    print("success:", result.success, "error:", result.error)
except Exception as e:
    print("崩溃了!")
    traceback.print_exc()

print("\n--- 3. 测试 _parse_agent_output ---")
for test_text in [
    "Thought: 用户想了解政策\nAction: Final Answer\nAction Input: 这是测试回答",
    "根据知识库信息，RAG 是检索增强生成技术。",
    "Thought: 需要搜索\nAction: search_kb\nAction Input: {\"query\": \"RAG\"}",
]:
    thought, action, inp = AgentService._parse_agent_output(test_text)
    print(f"输入: {test_text[:50]}... → thought={thought[:20]}, action={action}, inp={inp[:30]}")

print("\n--- 4. 结束 ---")
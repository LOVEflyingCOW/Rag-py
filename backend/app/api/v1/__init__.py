from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, knowledge_base, document, chat, agent, embedding

api_router = APIRouter(prefix="/v1")

api_router.include_router(auth.router)
api_router.include_router(knowledge_base.router)
api_router.include_router(document.router)
api_router.include_router(embedding.router)
api_router.include_router(chat.router)
api_router.include_router(agent.router)
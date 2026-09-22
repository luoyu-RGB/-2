"""AI 与知识库路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..ai.agent import FinanceAssistant
from ..ai.knowledge import KnowledgeBase
from ..deps import current_user_id, get_assistant, get_knowledge_base
from ..models import AffordabilityRequest, CalculatorRequest, ChatRequest, KnowledgeIngestRequest
from ..services.affordability import AffordabilityInput, AffordabilityService
from ..services.calculator import CalculatorError, calculate
from ..deps import get_affordability_service

router = APIRouter(prefix="/api", tags=["AI 助手"])


@router.post("/chat", summary="对话：记账 / 可负担性分析 / 知识问答 / 财务咨询")
def chat(
    payload: ChatRequest,
    user_id: int = Depends(current_user_id),
    assistant: FinanceAssistant = Depends(get_assistant),
):
    target_user = payload.user_id or user_id
    return assistant.chat(target_user, payload.message, confirm=payload.confirm).as_dict()


@router.post("/calculator", summary="安全四则运算（AST 白名单 + Decimal）")
def calculator(payload: CalculatorRequest):
    try:
        return calculate(payload.expression).as_dict()
    except CalculatorError as exc:
        return {"error": str(exc)}


@router.post("/affordability", summary="消费可负担性分析（确定性计算，不需要模型）")
def affordability(
    payload: AffordabilityRequest,
    user_id: int = Depends(current_user_id),
    service: AffordabilityService = Depends(get_affordability_service),
):
    result = service.analyze(
        user_id,
        AffordabilityInput(
            item_name=payload.item_name,
            price=payload.price,
            tax=payload.tax,
            shipping=payload.shipping,
            accessories=payload.accessories,
            subscription=payload.subscription,
            installment_fee=payload.installment_fee,
            discount=payload.discount,
            emergency_months_target=payload.emergency_months_target,
            months=payload.months,
        ),
    )
    return result.as_dict()


@router.get("/ai/actions", summary="AI 操作审计日志")
def ai_actions(
    limit: int = Query(default=20, ge=1, le=200),
    user_id: int = Depends(current_user_id),
    assistant: FinanceAssistant = Depends(get_assistant),
):
    return assistant.repository.list_ai_actions(user_id, limit)


@router.post("/ai/actions/{log_id}/reject", summary="取消待确认的 AI 写操作")
def reject_ai_action(
    log_id: int,
    user_id: int = Depends(current_user_id),
    assistant: FinanceAssistant = Depends(get_assistant),
):
    assistant.repository.reject_ai_action(user_id, log_id)
    return {"success": True, "log_id": log_id, "status": "rejected"}


# --------------------------------------------------------------------------- #
# 知识库
# --------------------------------------------------------------------------- #
@router.post("/knowledge", status_code=201, summary="上传理财资料（切分入库）")
def ingest_knowledge(
    payload: KnowledgeIngestRequest,
    user_id: int = Depends(current_user_id),
    knowledge: KnowledgeBase = Depends(get_knowledge_base),
):
    return knowledge.ingest(user_id, title=payload.title, content=payload.content)


@router.get("/knowledge", summary="资料列表")
def list_knowledge(
    user_id: int = Depends(current_user_id),
    knowledge: KnowledgeBase = Depends(get_knowledge_base),
):
    return knowledge.list_documents(user_id)


@router.delete("/knowledge/{document_id}", summary="删除资料及其切片")
def delete_knowledge(
    document_id: int,
    user_id: int = Depends(current_user_id),
    knowledge: KnowledgeBase = Depends(get_knowledge_base),
):
    return {"success": True, "deleted": knowledge.delete_document(user_id, document_id)}


@router.post("/knowledge/search", summary="知识库检索（有向量走向量，否则 BM25）")
def search_knowledge(
    question: str = Query(min_length=1),
    top_k: int = Query(default=5, ge=1, le=20),
    user_id: int = Depends(current_user_id),
    knowledge: KnowledgeBase = Depends(get_knowledge_base),
):
    chunks = knowledge.search(user_id, question, top_k=top_k)
    return {
        "mode": chunks[0].mode if chunks else "none",
        "count": len(chunks),
        "items": [chunk.as_dict() for chunk in chunks],
    }

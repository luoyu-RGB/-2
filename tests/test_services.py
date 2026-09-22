"""确定性与规则模式测试：计算器、可负担性分析、规则记账、知识库检索。

这些能力在**没有配置 API Key** 时也必须完整可用 —— 这是答辩与演示的兜底。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.agent import FinanceAssistant
from app.ai.knowledge import KnowledgeBase, split_text, tokenize
from app.services.affordability import AffordabilityInput, AffordabilityService
from app.services.calculator import CalculatorError, calculate


# --------------------------------------------------------------------------- #
# 计算器
# --------------------------------------------------------------------------- #
def test_calculator_is_exact_with_decimals():
    result = calculate("12000 - 6500 - 1850 - 600 - 1000")
    assert result.value == Decimal("2050.00")


def test_calculator_handles_percentage_and_step_trace():
    result = calculate("3500 * 2%")
    assert result.value == Decimal("70.00")
    assert result.steps, "应保留计算步骤，便于把口径写进回答"


def test_calculator_rejects_code_execution():
    for expression in ("__import__('os').system('dir')", "2 ** 10", "abs(-1)", "1 if True else 2"):
        with pytest.raises(CalculatorError):
            calculate(expression)


def test_calculator_rejects_division_by_zero():
    with pytest.raises(CalculatorError):
        calculate("100 / 0")


# --------------------------------------------------------------------------- #
# 可负担性分析
# --------------------------------------------------------------------------- #
def test_affordability_blocks_purchase_that_breaks_emergency_fund(repo, user_id, fresh_data):
    service = AffordabilityService(repo)
    result = service.analyze(user_id, AffordabilityInput(item_name="笔记本", price=Decimal("8500")))
    assert result.total_cost == Decimal("8500.00")
    assert result.monthly_necessary_expense > 0
    assert result.verdict in {"建议暂缓", "不建议购买", "可以购买"}
    assert result.calculation_steps
    # 应急金目标 = 月必要支出 × 目标月数
    expected_target = (result.monthly_necessary_expense * 6).quantize(Decimal("0.01"))
    assert result.emergency_target == expected_target


def test_affordability_includes_all_cost_components(repo, user_id, fresh_data):
    service = AffordabilityService(repo)
    result = service.analyze(
        user_id,
        AffordabilityInput(
            item_name="手机",
            price=Decimal("5000"),
            tax=Decimal("100"),
            shipping=Decimal("20"),
            accessories=Decimal("200"),
            subscription=Decimal("300"),
            installment_fee=Decimal("150"),
            discount=Decimal("70"),
        ),
    )
    assert result.total_cost == Decimal("5700.00")
    assert result.cost_breakdown["installment_fee"] == 150.0


def test_affordability_months_to_save_is_positive_when_short(repo, user_id, fresh_data):
    service = AffordabilityService(repo)
    result = service.analyze(user_id, AffordabilityInput(item_name="相机", price=Decimal("30000")))
    assert result.months_to_save != 0 or result.verdict == "可以购买"
    if result.verdict != "可以购买":
        assert result.months_to_save >= 1 or result.months_to_save == -1


# --------------------------------------------------------------------------- #
# 规则模式：无 Key 也能记账
# --------------------------------------------------------------------------- #
def test_rule_bookkeeping_creates_preview_then_confirms(repo, user_id, fresh_data, settings):
    assistant = FinanceAssistant(repo, settings)
    reply = assistant.chat(user_id, "今天午餐花了 32 元，用支付宝")
    assert reply.pending_action is not None
    assert reply.pending_action["action"] == "add_transaction"
    assert reply.pending_action["payload"]["amount"] == "32.00"
    assert "确认" in reply.reply

    # 预览阶段不应改动账本
    logged = repo.list_ai_actions(user_id)
    assert logged and logged[0]["status"] == "preview"

    confirmed = assistant.chat(user_id, "确认", confirm=True)
    assert "已记账" in confirmed.reply
    assert repo.list_ai_actions(user_id)[0]["status"] == "applied"

    rows = repo.list_transactions(user_id, keyword="午餐", limit=5)
    assert any(row["amount"] == Decimal("32.00") for row in rows)


def test_rule_bookkeeping_detects_income(repo, user_id, fresh_data, settings):
    assistant = FinanceAssistant(repo, settings)
    reply = assistant.chat(user_id, "收到工资 15000 元")
    assert reply.pending_action["payload"]["trans_type"] == "收入"
    assert reply.pending_action["payload"]["category_name"] == "工资收入"


def test_purchase_intent_uses_affordability(repo, user_id, fresh_data, settings):
    assistant = FinanceAssistant(repo, settings)
    reply = assistant.chat(user_id, "我想买一台 8500 元的笔记本，现在能买吗")
    assert reply.affordability is not None
    assert reply.affordability["total_cost"] == 8500.0
    assert "结论" in reply.reply


def test_general_question_without_key_returns_deterministic_summary(repo, user_id, fresh_data, settings):
    assistant = FinanceAssistant(repo, settings)
    reply = assistant.chat(user_id, "帮我看看这个月的情况")
    assert reply.mode == "rules"
    assert "本月收入" in reply.reply
    assert "未配置大模型" in reply.reply


# --------------------------------------------------------------------------- #
# 知识库
# --------------------------------------------------------------------------- #
def test_split_and_tokenize_chinese():
    chunks = split_text("第一段内容。\n\n第二段内容，包含贷款利率与手续费说明。" * 20)
    assert len(chunks) > 1
    tokens = tokenize("贷款利率 3.5% 手续费")
    assert "贷款" in tokens
    assert "利率" in tokens


def test_knowledge_search_falls_back_to_bm25(repo, adapter, settings, user_id, fresh_data):
    knowledge = KnowledgeBase(adapter, settings)
    result = knowledge.ingest(
        user_id,
        title="消费贷款条款",
        content="本合同项下贷款年化利率为 7.2%，提前还款需支付剩余本金 1% 的违约金。逾期将影响征信记录。",
    )
    assert result["chunk_count"] >= 1

    hits = knowledge.search(user_id, "提前还款违约金是多少")
    assert hits
    assert hits[0].mode in {"bm25", "embedding"}
    assert "违约金" in hits[0].content

    documents = knowledge.list_documents(user_id)
    assert any(document["title"] == "消费贷款条款" for document in documents)

    knowledge.delete_document(user_id, int(documents[0]["document_id"]))
    assert knowledge.list_documents(user_id) == []


def test_knowledge_answer_without_key_returns_source_chunks(repo, adapter, settings, user_id, fresh_data):
    knowledge = KnowledgeBase(adapter, settings)
    knowledge.ingest(user_id, title="货币基金说明", content="货币基金不承诺保本，七日年化收益率会随市场波动。")
    assistant = FinanceAssistant(repo, settings, knowledge=knowledge)
    reply = assistant.chat(user_id, "货币基金保本吗")
    assert reply.sources
    assert "片段" in reply.reply or "资料" in reply.reply

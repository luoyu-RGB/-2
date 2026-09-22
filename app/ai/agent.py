"""对话编排：意图路由 → 工具调用 → 写前确认 → 审计。

三种运行模式（对上层完全透明）：
* ``llm``      ：配置了 API Key，走大模型函数调用循环；
* ``rules``    ：没有 Key 或模型不可用，用正则 + 确定性计算兜底；
* ``knowledge``：知识库问答（有 Key 时由模型组织答案，无 Key 时直接给检索原文）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from ..config import Settings
from ..db.base import DatabaseError, current_month, month_bounds, quantize_money
from ..db.repository import LedgerRepository
from ..services.affordability import AffordabilityInput, AffordabilityService
from ..services.calculator import format_money
from .knowledge import KnowledgeBase
from .llm import ChatClient, ChatUnavailable
from .prompts import KNOWLEDGE_ANSWER_PROMPT, NO_KNOWLEDGE_HIT, SYSTEM_PROMPT, render_bookkeeping_hint
from .tools import (
    TOOL_SCHEMAS,
    ToolContext,
    WRITE_TOOLS,
    apply_write_action,
    execute_tool,
    payload_to_json,
)

MAX_TOOL_ROUNDS = 5

AMOUNT_PATTERN = re.compile(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块钱|块|rmb|RMB|¥)?")
PURCHASE_HINTS = ("买", "购", "分期", "值不值", "划算", "负担", "入手", "换新", "升级")
BOOKKEEPING_HINTS = (
    "花了",
    "消费",
    "支出",
    "付了",
    "支付",
    "买了",
    "收到",
    "收入",
    "报销",
    "退款",
    "记一笔",
    "记账",
    "发工资",
)
INCOME_HINTS = ("工资", "收入", "报销", "退款", "收到", "分红", "利息", "奖金")
KNOWLEDGE_HINTS = (
    "资料",
    "条款",
    "政策",
    "规定",
    "利率",
    "手续费",
    "违约金",
    "免息",
    "什么是",
    "怎么算",
    "保险",
    "合同",
    "文件",
    "文档",
    "保本",
    "收益",
    "年化",
    "风险等级",
    "抵押",
    "征信",
    "逾期",
    "基金",
    "理财",
    "定投",
    "退保",
    "费率",
    "额度",
    "账单日",
    "还款日",
)

CATEGORY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("早餐", "早餐"),
    ("午饭", "午餐"),
    ("午餐", "午餐"),
    ("晚饭", "晚餐"),
    ("晚餐", "晚餐"),
    ("外卖", "午餐"),
    ("咖啡", "零食饮料"),
    ("奶茶", "零食饮料"),
    ("零食", "零食饮料"),
    ("地铁", "公共交通"),
    ("公交", "公共交通"),
    ("打车", "公共交通"),
    ("出租", "公共交通"),
    ("加油", "加油充电"),
    ("房租", "房租"),
    ("水电", "水电燃气"),
    ("燃气", "水电燃气"),
    ("话费", "通讯网络"),
    ("流量", "通讯网络"),
    ("宽带", "通讯网络"),
    ("药", "医疗健康"),
    ("看病", "医疗健康"),
    ("门诊", "医疗健康"),
    ("课程", "教育培训"),
    ("书", "教育培训"),
    ("衣服", "购物消费"),
    ("购物", "购物消费"),
    ("超市", "购物消费"),
    ("电影", "休闲娱乐"),
    ("游戏", "休闲娱乐"),
    ("会员", "休闲娱乐"),
    ("工资", "工资收入"),
    ("分红", "投资收益"),
    ("利息", "投资收益"),
)

ACCOUNT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("现金", "现金"),
    ("支付宝", "支付宝"),
    ("微信", "微信钱包"),
    ("银行卡", "工商银行储蓄卡"),
    ("储蓄卡", "工商银行储蓄卡"),
    ("股票", "股票账户"),
)


@dataclass
class AssistantReply:
    reply: str
    mode: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    pending_action: dict[str, Any] | None = None
    affordability: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "mode": self.mode,
            "tool_calls": self.tool_calls,
            "pending_action": self.pending_action,
            "affordability": self.affordability,
            "sources": self.sources,
            "usage": self.usage,
        }


class FinanceAssistant:
    def __init__(
        self,
        repository: LedgerRepository,
        settings: Settings,
        *,
        knowledge: KnowledgeBase | None = None,
        client: ChatClient | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.knowledge = knowledge or KnowledgeBase(repository.adapter, settings)
        self.client = client or ChatClient(settings)
        self.affordability = AffordabilityService(repository)

    # ------------------------------------------------------------------ #
    # 对外入口
    # ------------------------------------------------------------------ #
    def chat(self, user_id: int, message: str, *, confirm: bool = False) -> AssistantReply:
        context = ToolContext(
            user_id=user_id,
            repository=self.repository,
            affordability=self.affordability,
            knowledge=self.knowledge,
            settings=self.settings,
        )
        if confirm:
            return self._apply_pending(context)

        text = (message or "").strip()
        if not text:
            return AssistantReply(reply="请描述你的记账、消费或理财问题。", mode="rules")

        intent = self._detect_intent(text)
        if intent == "bookkeeping":
            return self._handle_bookkeeping(context, text)
        if intent == "purchase":
            return self._handle_purchase(context, text)
        if intent == "knowledge":
            return self._handle_knowledge(context, text)
        return self._handle_general(context, text)

    # ------------------------------------------------------------------ #
    # 意图识别
    # ------------------------------------------------------------------ #
    @staticmethod
    def _detect_intent(text: str) -> str:
        has_amount = bool(AMOUNT_PATTERN.search(text))
        if any(hint in text for hint in BOOKKEEPING_HINTS) and has_amount:
            return "bookkeeping"
        if any(hint in text for hint in PURCHASE_HINTS) and has_amount:
            return "purchase"
        if any(hint in text for hint in KNOWLEDGE_HINTS):
            return "knowledge"
        if any(hint in text for hint in PURCHASE_HINTS):
            return "purchase"
        if any(hint in text for hint in BOOKKEEPING_HINTS):
            return "bookkeeping"
        return "general"

    # ------------------------------------------------------------------ #
    # 对话式记账
    # ------------------------------------------------------------------ #
    def _handle_bookkeeping(self, context: ToolContext, text: str) -> AssistantReply:
        parsed = self._parse_bookkeeping(context, text)
        if not parsed or not parsed.get("amount"):
            return AssistantReply(
                reply=(
                    "我没能确定金额或类别。可以这样说：\n"
                    "「今天午餐花了 32 元，用支付宝」或「收到工资 15000 元」。\n"
                    "也可以直接在记账页手动录入。"
                ),
                mode="rules",
            )

        result = execute_tool("add_transaction", parsed, context)
        if result.get("error"):
            self.repository.log_ai_action(
                context.user_id, action="add_transaction", status="failed", user_input=text, message=str(result["error"])
            )
            return AssistantReply(reply=f"记账失败：{result['error']}", mode="rules")

        log_id = self.repository.log_ai_action(
            context.user_id,
            action="add_transaction",
            status="preview",
            payload=payload_to_json(result["payload"]),
            user_input=text,
            message=result["summary"],
        )
        reply = (
            f"待确认的记账：{result['summary']}\n"
            "回复「确认」后才会真正写入账本；本次操作已记入审计日志"
            f"（#{log_id}）。"
        )
        return AssistantReply(
            reply=reply,
            mode="llm" if self.client.available else "rules",
            tool_calls=[{"name": "add_transaction", "arguments": parsed, "result": result}],
            pending_action={"log_id": log_id, **result},
        )

    def _parse_bookkeeping(self, context: ToolContext, text: str) -> dict[str, Any] | None:
        if self.client.available:
            accounts = [account["account_name"] for account in context.repository.list_accounts(context.user_id)]
            expense = [item["category_name"] for item in context.repository.list_categories(context.user_id, "支出")]
            income = [item["category_name"] for item in context.repository.list_categories(context.user_id, "收入")]
            extracted = self.client.complete_json(
                render_bookkeeping_hint(text, accounts, expense, income)
            )
            if extracted.get("amount"):
                return {
                    "trans_type": extracted.get("trans_type") or "支出",
                    "amount": extracted.get("amount"),
                    "category": extracted.get("category"),
                    "account": extracted.get("account"),
                    "trans_date": extracted.get("trans_date") or date.today().isoformat(),
                    "remark": extracted.get("remark") or text[:60],
                }
        return self._rule_based_bookkeeping(text)

    def _rule_based_bookkeeping(self, text: str) -> dict[str, Any] | None:
        match = AMOUNT_PATTERN.search(text)
        if not match:
            return None
        amount = Decimal(match.group(1))
        if amount <= 0:
            return None
        trans_type = "收入" if any(hint in text for hint in INCOME_HINTS) else "支出"
        category = next((name for keyword, name in CATEGORY_KEYWORDS if keyword in text), None)
        account = next((name for keyword, name in ACCOUNT_KEYWORDS if keyword in text), None)
        if trans_type == "收入" and not category:
            category = "其他收入"
        if trans_type == "支出" and not category:
            category = "其他支出"
        return {
            "trans_type": trans_type,
            "amount": str(amount),
            "category": category,
            "account": account,
            "trans_date": date.today().isoformat(),
            "remark": text[:60],
        }

    # ------------------------------------------------------------------ #
    # 可负担性分析
    # ------------------------------------------------------------------ #
    def _handle_purchase(self, context: ToolContext, text: str) -> AssistantReply:
        arguments = self._parse_purchase(context, text)
        if not arguments.get("price"):
            return AssistantReply(
                reply=(
                    "要判断这笔消费是否合适，我需要知道大概价格。\n"
                    "可以这样说：「我想买一台 8500 元的笔记本，现在能买吗？」"
                ),
                mode="rules",
                tool_calls=[{"name": "analyze_affordability", "arguments": arguments}],
            )

        result = execute_tool("analyze_affordability", arguments, context)
        if result.get("error"):
            return AssistantReply(reply=f"分析失败：{result['error']}", mode="rules")

        self.repository.log_ai_action(
            context.user_id,
            action="analyze_affordability",
            status="applied",
            payload=payload_to_json(result),
            user_input=text,
            message=result.get("verdict", ""),
        )

        reply = self._compose_affordability_reply(result, text)
        return AssistantReply(
            reply=reply,
            mode="llm" if self.client.available else "rules",
            tool_calls=[{"name": "analyze_affordability", "arguments": arguments, "result": result}],
            affordability=result,
        )

    def _parse_purchase(self, context: ToolContext, text: str) -> dict[str, Any]:
        item_name = "目标商品"
        if self.client.available:
            from .prompts import AFFORDABILITY_HINT

            extracted = self.client.complete_json(AFFORDABILITY_HINT.format(message=text))
            if extracted.get("price"):
                extracted.setdefault("item_name", item_name)
                return {key: value for key, value in extracted.items() if value is not None}

        match = AMOUNT_PATTERN.search(text)
        if not match:
            return {"item_name": item_name, "price": None}
        # 取句子里最大的金额作为价格（"分期 12 期每期 700" 这类描述里价格通常是最大值）
        amounts = [Decimal(item) for item in AMOUNT_PATTERN.findall(text)]
        price = max(amounts) if amounts else Decimal(match.group(1))
        return {"item_name": _guess_item_name(text), "price": str(price)}

    def _compose_affordability_reply(self, result: dict[str, Any], text: str) -> str:
        if self.client.available:
            facts = result.get("calculation_steps", [])
            prompt = (
                f"用户说：{text}\n\n"
                "以下是系统基于用户真实账本计算出的结果，请据此回答，禁止改动任何数字：\n"
                f"商品：{result.get('item_name')}\n"
                f"到手总成本：{result.get('total_cost_text')} 元\n"
                f"可动用现金：{format_money(Decimal(str(result.get('liquid_balance', 0))))} 元\n"
                f"月必要支出：{format_money(Decimal(str(result.get('monthly_necessary_expense', 0))))} 元\n"
                f"月结余：{format_money(Decimal(str(result.get('monthly_surplus', 0))))} 元\n"
                f"应急金目标：{format_money(Decimal(str(result.get('emergency_target', 0))))} 元\n"
                f"购买后应急金：{result.get('emergency_months_after')} 个月\n"
                f"需攒钱：{result.get('months_to_save')} 个月\n"
                f"系统结论：{result.get('verdict')}\n"
                f"判断依据：{'；'.join(result.get('reasons', []))}\n"
                f"计算过程：{'；'.join(facts)}\n"
            )
            try:
                reply = self.client.complete(
                    [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ]
                )
                if reply.content:
                    return reply.content
            except ChatUnavailable:
                pass

        lines = [
            f"结论：{result.get('verdict')}",
            "",
            "财务依据：",
            f"- 到手总成本：{result.get('total_cost_text')} 元",
            f"- 可动用现金：{format_money(Decimal(str(result.get('liquid_balance', 0))))} 元",
            f"- 月必要支出：{format_money(Decimal(str(result.get('monthly_necessary_expense', 0))))} 元"
            f"（近 {result.get('data_window', {}).get('months', 0)} 个月均值）",
            f"- 月结余：{format_money(Decimal(str(result.get('monthly_surplus', 0))))} 元",
            "",
            "计算与影响：",
        ]
        lines.extend(f"- {step}" for step in result.get("calculation_steps", []))
        lines.append("")
        lines.append(
            f"购买后应急金：{result.get('emergency_months_after')} 个月"
            f"（应急金目标金额 {format_money(Decimal(str(result.get('emergency_target', 0))))} 元）"
        )
        if result.get("reasons"):
            lines.append("")
            lines.append("判断依据：" + "；".join(result["reasons"]))
        months = result.get("months_to_save", 0)
        if months and months > 0:
            lines.append(f"下一步：先按每月结余储蓄，预计 {months} 个月后可覆盖这笔支出。")
        elif months == -1:
            lines.append("下一步：当前月结余不足，建议先压缩可选支出或提高收入后再考虑。")
        else:
            lines.append("下一步：应急金以上现金已可覆盖，可择机购买，但建议保留不少于目标月数的应急金。")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 知识库问答
    # ------------------------------------------------------------------ #
    def _handle_knowledge(self, context: ToolContext, text: str) -> AssistantReply:
        chunks = self.knowledge.search(context.user_id, text, top_k=self.settings.retrieval_limit)
        if not chunks:
            return AssistantReply(reply=NO_KNOWLEDGE_HIT, mode="knowledge", sources=[])

        sources = [chunk.as_dict() for chunk in chunks]
        if self.client.available:
            prompt = KNOWLEDGE_ANSWER_PROMPT.format(context=self.knowledge.build_context(chunks), question=text)
            try:
                reply = self.client.complete(
                    [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ]
                )
                if reply.content:
                    return AssistantReply(reply=reply.content, mode="knowledge-llm", sources=sources)
            except ChatUnavailable:
                pass

        lines = ["未配置大模型，下面是知识库检索到的原文片段（按相关度排序）：", ""]
        for index, chunk in enumerate(chunks, start=1):
            lines.append(f"[片段{index}] 《{chunk.title}》（相关度 {chunk.score:.3f}）")
            lines.append(chunk.content)
            lines.append("")
        return AssistantReply(reply="\n".join(lines).strip(), mode="knowledge", sources=sources)

    # ------------------------------------------------------------------ #
    # 通用对话（有 Key 时走完整工具循环）
    # ------------------------------------------------------------------ #
    def _handle_general(self, context: ToolContext, text: str) -> AssistantReply:
        if self.client.available:
            return self._llm_tool_loop(context, text)
        return AssistantReply(reply=self._deterministic_summary(context, text), mode="rules")

    def _llm_tool_loop(self, context: ToolContext, text: str) -> AssistantReply:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        tool_calls: list[dict[str, Any]] = []
        pending: dict[str, Any] | None = None
        usage: dict[str, Any] = {}

        for _ in range(MAX_TOOL_ROUNDS):
            try:
                reply = self.client.complete(messages, tools=TOOL_SCHEMAS)
            except ChatUnavailable as exc:
                return AssistantReply(reply=f"{exc}\n\n{self._deterministic_summary(context, text)}", mode="rules")

            if reply.usage:
                usage = reply.usage
            if not reply.tool_calls:
                return AssistantReply(reply=reply.content or "（模型没有返回内容）", mode="llm", tool_calls=tool_calls, pending_action=pending, usage=usage)

            messages.append(
                {
                    "role": "assistant",
                    "content": reply.content,
                    "tool_calls": [
                        {
                            "id": call.call_id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
                        }
                        for call in reply.tool_calls
                    ],
                }
            )

            for call in reply.tool_calls:
                result = execute_tool(call.name, call.arguments, context)
                tool_calls.append({"name": call.name, "arguments": call.arguments, "result": result})
                if call.name in WRITE_TOOLS and result.get("status") == "preview":
                    log_id = self.repository.log_ai_action(
                        context.user_id,
                        action=call.name,
                        status="preview",
                        payload=payload_to_json(result.get("payload", {})),
                        user_input=text,
                        message=str(result.get("summary", "")),
                    )
                    pending = {"log_id": log_id, **result}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )

        return AssistantReply(
            reply="这次问题需要的步骤较多，我先给出当前可确定的结论，你可以把问题再拆小一点。",
            mode="llm",
            tool_calls=tool_calls,
            pending_action=pending,
            usage=usage,
        )

    # ------------------------------------------------------------------ #
    # 无模型时的确定性回答
    # ------------------------------------------------------------------ #
    def _deterministic_summary(self, context: ToolContext, text: str) -> str:
        overview = self.repository.overview(context.user_id)
        month = overview["month"]
        surplus = quantize_money(overview["month_income"]) - quantize_money(overview["month_expense"])
        lines = [
            f"未配置大模型（DEEPSEEK_API_KEY），以下是基于你账本的确定性汇总（{month}）：",
            f"- 本月收入 {format_money(overview['month_income'])} 元，支出 {format_money(overview['month_expense'])} 元，"
            f"结余 {format_money(surplus)} 元",
            f"- 账户总余额 {format_money(overview['net_worth'])} 元（净资产，含资产与负债）",
        ]
        try:
            status = self.repository.budget_status(context.user_id, current_month())
        except DatabaseError:
            status = []
        if status:
            lines.append("- 本月预算执行：")
            for row in status[:5]:
                lines.append(
                    f"    · {row['category_name']}：{format_money(row['budget_amount'])} 元预算，"
                    f"已用 {format_money(row['actual_expense'])} 元（{row['completion_rate']}%，{row['status']}）"
                )
        try:
            ratios = self.repository.category_ratio(context.user_id, current_month())
        except DatabaseError:
            ratios = []
        if ratios:
            top = ratios[:3]
            lines.append("- 本月支出前三：" + "、".join(f"{row['category_name']} {format_money(row['expense_amount'])} 元" for row in top))
        lines.append("")
        lines.append("配置 API Key 后我可以用自然语言帮你记账、判断某笔消费是否负担得起、并解读你上传的理财资料。")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 确认落库
    # ------------------------------------------------------------------ #
    def _apply_pending(self, context: ToolContext) -> AssistantReply:
        rows = self.repository.adapter.query(
            f"SELECT log_id, action, payload, message FROM ai_action_log "
            f"WHERE user_id = {self.repository.ph} AND status = 'preview' "
            f"ORDER BY log_id DESC LIMIT 1",
            (context.user_id,),
        )
        if not rows:
            return AssistantReply(reply="当前没有待确认的操作。", mode="rules")

        row = rows[0]
        action = row["action"]
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            payload = {}

        try:
            result = apply_write_action(context, action, payload)
        except (DatabaseError, ValueError) as exc:
            self.repository.adapter.execute(
                f"UPDATE ai_action_log SET status = 'failed', message = {self.repository.ph} WHERE log_id = {self.repository.ph}",
                (str(exc)[:300], row["log_id"]),
            )
            return AssistantReply(reply=f"确认失败：{exc}", mode="rules")

        self.repository.adapter.execute(
            f"UPDATE ai_action_log SET status = 'applied' WHERE log_id = {self.repository.ph}",
            (row["log_id"],),
        )
        detail = json.dumps(result, ensure_ascii=False, default=str)
        return AssistantReply(
            reply=f"已记账并生效：{row['message']}\n账户余额由数据库触发器自动更新。\n（审计记录 #{row['log_id']}，结果：{detail}）",
            mode="llm" if self.client.available else "rules",
            tool_calls=[{"name": action, "arguments": payload, "result": result}],
        )


def _guess_item_name(text: str) -> str:
    """从"我想买一台 8500 元的笔记本"里粗略取出商品名。"""
    cleaned = AMOUNT_PATTERN.sub(" ", text)
    cleaned = re.sub(r"(我想|想要|准备|打算|现在|能不能|可以|该不该|要不要|买|购|入|一台|一个|一部|元|块钱|，|。|\?|？)", " ", cleaned)
    candidate = " ".join(part for part in cleaned.split() if part)
    return candidate[:30] or "目标商品"


__all__ = ["AssistantReply", "FinanceAssistant", "MAX_TOOL_ROUNDS"]

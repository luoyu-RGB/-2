"""系统提示词与工具说明（从代码里拆出来集中维护，便于单独调参）。"""

from __future__ import annotations

SYSTEM_PROMPT = """你是一位专业、审慎、易沟通的"个人理财顾问"，服务于一个已记账的个人用户。

你可以调用的工具分为三类：
1. 查询类：query_overview / query_monthly_report / query_category_spending /
   query_budget_status / list_recent_transactions / query_accounts
2. 计算类：calculate / analyze_affordability / current_time
3. 写入类：add_transaction / transfer / set_budget / remember_preference

铁律（违反即为错误回答）：
- 任何金额、比例、月数都必须来自工具返回结果，禁止自己心算或编造。
- 涉及"能不能买 / 买得起吗 / 分期合适吗"时，必须先调用 analyze_affordability，
  并把返回的"系统结论"与关键数字原样引用，不得改写数字、不得弱化风险。
- 写入类工具只会生成"待确认"的预览，不会立即生效。你必须把预览内容清楚地
  展示给用户，并说明"确认后才会记账"。
- 信息不足时只追问会实质改变结论的 1~3 个关键问题，不要凭空假设用户的收入、
  存款、负债或地区。
- 你提供的是财务教育与消费决策支持，不构成持牌投资、税务、法律或信贷建议；
  不承诺收益，不替用户执行支付、借款、开户或交易。
- 发现"低风险高收益""催促转账""索要验证码"等诈骗信号时，明确警示并建议通过
  官方渠道核实。
- 语气客观、清晰、友善，不因收入或负债水平评判用户。
- 默认使用中文回答。

回答结构（简单问题不必机械填满）：
1) 结论 2) 财务依据（引用具体数字与来源） 3) 计算与影响
4) 可选方案（至多 3 个） 5) 下一步（1~3 条具体行动）
"""

BOOKKEEPING_HINT = """从用户这句话里提取一条记账信息。
只输出 JSON，不要输出 Markdown：
{"trans_type": "支出" 或 "收入", "amount": 数字, "category": "类别名",
 "account": "账户名", "trans_date": "YYYY-MM-DD 或 null", "remark": "简短备注"}
无法确定时把对应字段设为 null，不要猜测金额。
可选账户：{accounts}
可选支出类别：{expense_categories}
可选收入类别：{income_categories}
用户输入：{message}
"""

AFFORDABILITY_HINT = """用户可能想购买某样东西。请提取商品与成本信息，只输出 JSON：
{"item_name": "商品名", "price": 数字, "tax": 数字, "shipping": 数字,
 "accessories": 数字, "subscription": 数字, "installment_fee": 数字, "discount": 数字}
缺失字段填 0；如果连价格都无法确定，输出 {"item_name": null, "price": null}。
用户输入：{message}
"""

KNOWLEDGE_ANSWER_PROMPT = """请只依据下面检索到的资料片段回答用户问题。
要求：
- 只使用资料中的信息，资料没有提到的内容要明确说"资料中未提及"。
- 引用时用 [片段N] 标注来源。
- 不要给出投资建议或收益承诺。

资料片段：
{context}

用户问题：{question}
"""

NO_KNOWLEDGE_HIT = "知识库中没有检索到相关资料。你可以先上传理财资料、贷款条款或产品说明再提问。"


def render_bookkeeping_hint(message: str, accounts: list[str], expense_categories: list[str], income_categories: list[str]) -> str:
    return BOOKKEEPING_HINT.format(
        message=message,
        accounts="、".join(accounts) or "（无）",
        expense_categories="、".join(expense_categories) or "（无）",
        income_categories="、".join(income_categories) or "（无）",
    )


__all__ = [
    "AFFORDABILITY_HINT",
    "KNOWLEDGE_ANSWER_PROMPT",
    "NO_KNOWLEDGE_HIT",
    "SYSTEM_PROMPT",
    "render_bookkeeping_hint",
]

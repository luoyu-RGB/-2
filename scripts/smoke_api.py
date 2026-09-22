"""端到端冒烟脚本：对**真实运行中的服务**发 HTTP 请求，验证关键链路。

与 pytest 的区别：pytest 走 ASGI 测试客户端（进程内），本脚本走真实网络栈，
可以用来验证「服务真的起得来、前端真的能调到」这件事。

用法：
    # 终端 1
    .venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8000
    # 终端 2
    .venv\\Scripts\\python.exe scripts\\smoke_api.py --base http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
from datetime import date

import httpx

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [OK]   {name}{(' — ' + detail) if detail else ''}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}{(' — ' + detail) if detail else ''}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    client = httpx.Client(base_url=base, timeout=60.0)

    print(f"冒烟目标：{base}\n")

    print("1) 健康检查与结构自检")
    health = client.get("/api/health").json()
    check("服务存活", health.get("status") == "ok", f"版本 {health.get('version')}")
    check("结构自检通过", bool(health["database"].get("schema_ok")),
          f"{len(health['database']['tables'])} 表 / {len(health['database']['views'])} 视图 / {len(health['database']['triggers'])} 触发器")
    # 触发器命名随方言不同：PostgreSQL 是单个行级触发器，SQLite 因无 plpgsql 拆成三个
    check("触发器就位", any("transaction_balance" in name for name in health["database"]["triggers"]),
          "、".join(health["database"]["triggers"]))
    check("演示数据存在", health["data"]["transactions"] > 0, f"{health['data']['transactions']} 笔交易")

    print("\n2) 账本与报表")
    accounts = client.get("/api/accounts").json()
    check("账户列表", len(accounts) >= 1, f"{len(accounts)} 个账户")
    overview = client.get("/api/overview").json()
    check("总览接口", overview["month"] == date.today().isoformat()[:7], f"本月支出 {overview['month_expense']}")
    check("净资产接口", "net_worth" in client.get("/api/net-worth").json())
    check("预算执行视图", len(client.get("/api/budget-status").json()) >= 1)
    check("类别占比视图", isinstance(client.get("/api/category-ratio").json(), list))
    check("月度趋势视图", isinstance(client.get("/api/monthly-trend").json(), list))

    print("\n3) 安全计算与可负担性（不需要任何 API Key）")
    calc = client.post("/api/calculator", json={"expression": "12000 - 6500 - 1850 - 600 - 1000"}).json()
    check("计算器精确", calc.get("value") == 2050.0, f"结果 {calc.get('value')}")
    blocked = client.post("/api/calculator", json={"expression": "__import__('os')"}).json()
    check("计算器拒绝代码执行", "error" in blocked)

    affordable = client.post(
        "/api/affordability",
        json={"item_name": "笔记本电脑", "price": 8500, "installment_fee": 300},
    ).json()
    necessary = affordable.get("monthly_necessary_expense", 0)
    check("可负担性给出结论", bool(affordable.get("verdict")), affordable.get("verdict", ""))
    check("月必要支出已归并父类别（应远大于 100 元）", necessary > 100, f"{necessary} 元/月")
    check("到手总成本含分期费用", affordable.get("total_cost") == 8800.0, f"{affordable.get('total_cost')} 元")
    check("计算步骤可复算", bool(affordable.get("calculation_steps")), f"{len(affordable.get('calculation_steps', []))} 步")

    print("\n4) 对话式记账（预览 → 确认 → 审计）")
    preview = client.post("/api/chat", json={"message": "今天午餐花了 32 元，用支付宝"}).json()
    check("识别为记账意图", preview.get("mode") in {"rules", "llm"}, f"mode={preview.get('mode')}")
    pending = preview.get("pending_action")
    check("生成待确认预览", bool(pending), (pending or {}).get("summary", ""))
    if pending:
        before = len(client.get("/api/transactions", params={"limit": 300}).json())
        confirmed = client.post("/api/chat", json={"message": "确认", "confirm": True}).json()
        after = len(client.get("/api/transactions", params={"limit": 300}).json())
        check("确认后落库", "已记账" in confirmed.get("reply", ""), confirmed.get("reply", "")[:40])
        check("交易数量增加", after == before + 1, f"{before} → {after}")
        actions = client.get("/api/ai/actions").json()
        check("审计日志已记录", bool(actions) and actions[0]["status"] == "applied")

    print("\n5) 可负担性对话（自然语言 → 系统计算）")
    purchase = client.post("/api/chat", json={"message": "我想买一台 8500 元的笔记本，现在能买吗"}).json()
    check("走可负担性分析而非泛泛而谈", purchase.get("affordability") is not None,
          (purchase.get("affordability") or {}).get("verdict", ""))

    print("\n6) 知识库（BM25 兜底检索）")
    document = client.post(
        "/api/knowledge",
        json={"title": "消费贷款条款", "content": "本合同项下贷款年化利率 7.2%，提前还款需支付剩余本金 1% 的违约金，逾期将影响征信。"},
    ).json()
    check("资料入库", document.get("chunk_count", 0) >= 1, f"{document.get('chunk_count')} 个片段")
    hits = client.post("/api/knowledge/search", params={"question": "提前还款违约金是多少"}).json()
    check("检索命中", hits.get("count", 0) >= 1, f"mode={hits.get('mode')}")
    if document.get("document_id"):
        client.delete(f"/api/knowledge/{document['document_id']}")

    print("\n7) 取消待确认操作（审计闭环）")
    second = client.post("/api/chat", json={"message": "今天打车花了 45 元"}).json()
    pending2 = second.get("pending_action")
    check("生成第二条待确认预览", bool(pending2), (pending2 or {}).get("summary", ""))
    if pending2:
        before = len(client.get("/api/transactions", params={"limit": 300}).json())
        rejected = client.post(f"/api/ai/actions/{pending2['log_id']}/reject").json()
        check("取消接口返回 rejected", rejected.get("status") == "rejected")
        after_confirm = client.post("/api/chat", json={"message": "确认", "confirm": True}).json()
        after = len(client.get("/api/transactions", params={"limit": 300}).json())
        check("取消后「确认」不会落库", after == before, f"{before} → {after}")
        check("取消后没有待确认操作", "没有待确认" in after_confirm.get("reply", ""), after_confirm.get("reply", ""))
        actions = client.get("/api/ai/actions").json()
        check("审计状态为 rejected", bool(actions) and actions[0]["status"] == "rejected",
              actions[0]["status"] if actions else "")

    print("\n8) 前端资源（同源托管，页面里的相对链接要能取到）")
    index = client.get("/")
    check("首页返回 HTML", index.status_code == 200 and "text/html" in index.headers.get("content-type", ""))
    for view in ("overview", "records", "budget", "assets", "assistant"):
        check(f"视图 {view} 存在", f'id="view-{view}"' in index.text)
    for asset in ("/app.js", "/style.css"):
        response = client.get(asset)
        check(f"{asset} 可访问", response.status_code == 200, f"{len(response.text)} 字节")

    print("\n" + "=" * 60)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("失败项：" + "、".join(FAILED))
        return 1
    print("全部冒烟通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

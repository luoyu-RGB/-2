"""接口冒烟测试：确认迁移后的 24 个接口与原 Flask 版行为对齐。"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

EXPECTED_DB_MODE = (
    "postgres" if os.environ.get("TEST_DB_MODE", "sqlite").lower().startswith(("postgres", "pg")) else "sqlite"
)


def test_health_reports_database_and_ai_status(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["mode"] == EXPECTED_DB_MODE
    assert body["config"]["chat_ready"] is False  # 测试环境故意不配 Key
    assert body["data"]["transactions"] > 0


def test_schema_endpoint_exposes_triggers(client):
    body = client.get("/api/schema").json()
    assert body["schema_ok"] is True
    # 触发器命名随方言不同：PostgreSQL 为 trg_transaction_balance，SQLite 为三个 *_insert/update/delete
    assert any("transaction_balance" in name for name in body["triggers"]), body["triggers"]


def test_accounts_and_categories(client):
    accounts = client.get("/api/accounts").json()
    assert len(accounts) >= 5
    assert {"account_id", "account_name", "account_type", "balance"} <= set(accounts[0])

    categories = client.get("/api/categories", params={"type": "支出"}).json()
    assert any(item["category_name"] == "餐饮" for item in categories)
    assert all(item["category_type"] == "支出" for item in categories)


def test_account_crud_and_balance_is_read_only(client):
    created = client.post(
        "/api/accounts",
        json={"account_name": "测试账户", "account_type": "电子钱包", "balance": 100},
    )
    assert created.status_code == 201
    account_id = created.json()["account_id"]

    updated = client.put(
        f"/api/accounts/{account_id}",
        json={"account_name": "测试账户改名", "account_type": "现金"},
    )
    assert updated.status_code == 200

    # 结构上不接受 balance 字段：账户余额只能由交易驱动
    assert client.put(
        f"/api/accounts/{account_id}",
        json={"account_name": "x", "account_type": "现金", "balance": 999999},
    ).status_code == 200  # 多余字段被忽略
    balance = next(item for item in client.get("/api/accounts").json() if item["account_id"] == account_id)["balance"]
    assert Decimal(str(balance)) == Decimal("100")

    assert client.delete(f"/api/accounts/{account_id}").status_code == 200
    assert all(item["account_id"] != account_id for item in client.get("/api/accounts").json())


def test_transaction_flow_and_month_filter(client, adapter):
    from app.db.seed import seed_database

    seed_database(adapter, force=True)
    accounts = client.get("/api/accounts").json()
    categories = client.get("/api/categories", params={"type": "支出"}).json()
    account_id = accounts[0]["account_id"]
    category_id = categories[0]["category_id"]
    today = date.today().isoformat()

    created = client.post(
        "/api/transactions",
        json={
            "account_id": account_id,
            "category_id": category_id,
            "amount": 12.5,
            "trans_type": "支出",
            "trans_date": today,
            "remark": "接口冒烟",
        },
    )
    assert created.status_code == 201
    transaction_id = created.json()["transaction_id"]

    listed = client.get("/api/transactions", params={"month": today[:7], "limit": 50}).json()
    assert any(item["transaction_id"] == transaction_id for item in listed)

    assert client.put(
        f"/api/transactions/{transaction_id}",
        json={
            "account_id": account_id,
            "category_id": category_id,
            "amount": 20,
            "trans_type": "支出",
            "trans_date": today,
            "remark": "改一下",
        },
    ).status_code == 200

    assert client.delete(f"/api/transactions/{transaction_id}").status_code == 200
    assert client.delete(f"/api/transactions/{transaction_id}").status_code == 404


def test_overdraft_returns_400_with_chinese_message(client, adapter):
    from app.db.seed import seed_database

    seed_database(adapter, force=True)
    account = next(item for item in client.get("/api/accounts").json() if item["account_name"] == "现金")
    category_id = client.get("/api/categories", params={"type": "支出"}).json()[0]["category_id"]
    response = client.post(
        "/api/transactions",
        json={
            "account_id": account["account_id"],
            "category_id": category_id,
            "amount": float(account["balance"]) + 1000,
            "trans_type": "支出",
        },
    )
    assert response.status_code == 400
    assert "余额不足" in response.json()["error"]


def test_transfer_endpoint(client, adapter):
    from app.db.seed import seed_database

    seed_database(adapter, force=True)
    accounts = {item["account_name"]: item for item in client.get("/api/accounts").json()}
    response = client.post(
        "/api/transfers",
        json={
            "from_account": accounts["工商银行储蓄卡"]["account_id"],
            "to_account": accounts["支付宝"]["account_id"],
            "amount": 500,
            "remark": "接口转账",
        },
    )
    assert response.status_code == 201
    assert response.json()["transfer_group_id"]

    same = client.post(
        "/api/transfers",
        json={
            "from_account": accounts["支付宝"]["account_id"],
            "to_account": accounts["支付宝"]["account_id"],
            "amount": 10,
        },
    )
    assert same.status_code == 400


def test_report_endpoints(client):
    month = date.today().isoformat()[:7]
    for path in (
        "/api/overview",
        "/api/net-worth",
        "/api/monthly-trend",
        "/api/daily-cashflow",
        "/api/stats",
    ):
        response = client.get(path)
        assert response.status_code == 200, path

    overview = client.get("/api/overview").json()
    assert overview["month"] == month
    assert Decimal(str(overview["month_income"])) >= 0

    report = client.get("/api/monthly-report", params={"month": month}).json()
    assert report["year_month"] == month

    ratio = client.get("/api/category-ratio", params={"month": month}).json()
    assert isinstance(ratio, list)

    status = client.get("/api/budget-status", params={"month": month}).json()
    assert status and {"budget_amount", "actual_expense", "completion_rate", "status"} <= set(status[0])


def test_budget_upsert_and_copy(client, adapter):
    from app.db.seed import seed_database

    seed_database(adapter, force=True)
    categories = client.get("/api/categories", params={"type": "支出"}).json()
    target = next(item for item in categories if item["category_name"] == "通讯网络")
    month = date.today().isoformat()[:7]

    assert client.post(
        "/api/budgets",
        json={"category_id": target["category_id"], "year_month": month, "amount": 120},
    ).status_code == 200
    budgets = client.get("/api/budgets", params={"month": month}).json()
    assert any(item["category_name"] == "通讯网络" for item in budgets)

    # 幂等：再设一次走 upsert 分支
    assert client.post(
        "/api/budgets",
        json={"category_id": target["category_id"], "year_month": month, "amount": 150},
    ).status_code == 200
    budgets = client.get("/api/budgets", params={"month": month}).json()
    updated = next(item for item in budgets if item["category_name"] == "通讯网络")
    assert Decimal(str(updated["budget_amount"])) == Decimal("150")


def test_asset_liability_crud(client):
    created = client.post(
        "/api/asset-liability",
        json={
            "item_name": "测试负债",
            "item_type": "负债",
            "amount": 1000,
            "acquire_date": "2026-01-01",
            "remark": "冒烟",
        },
    )
    assert created.status_code == 201
    item_id = created.json()["item_id"]

    assert any(item["item_id"] == item_id for item in client.get("/api/asset-liability").json())
    assert client.put(
        f"/api/asset-liability/{item_id}",
        json={
            "item_name": "测试负债改",
            "item_type": "负债",
            "amount": 1200,
            "acquire_date": "2026-01-02",
        },
    ).status_code == 200
    assert client.delete(f"/api/asset-liability/{item_id}").status_code == 200
    assert client.delete(f"/api/asset-liability/{item_id}").status_code == 404


def test_calculator_endpoint(client):
    ok = client.post("/api/calculator", json={"expression": "12000 - 6500 - 1850"}).json()
    assert ok["value"] == 3650.0
    bad = client.post("/api/calculator", json={"expression": "__import__('os')"}).json()
    assert "error" in bad


def test_affordability_endpoint_without_ai_key(client, adapter):
    from app.db.seed import seed_database

    seed_database(adapter, force=True)
    body = client.post(
        "/api/affordability",
        json={"item_name": "笔记本", "price": 8500, "installment_fee": 300},
    ).json()
    assert body["total_cost"] == 8800.0
    assert body["verdict"]
    assert body["calculation_steps"]


def test_chat_endpoint_rules_mode_and_confirmation(client, adapter):
    from app.db.seed import seed_database

    seed_database(adapter, force=True)
    preview = client.post("/api/chat", json={"message": "今天打车花了 45 元"}).json()
    assert preview["mode"] == "rules"
    assert preview["pending_action"]["action"] == "add_transaction"

    confirmed = client.post("/api/chat", json={"message": "确认", "confirm": True}).json()
    assert "已记账" in confirmed["reply"]

    actions = client.get("/api/ai/actions").json()
    assert actions[0]["status"] == "applied"


def test_knowledge_endpoints(client, adapter):
    from app.db.seed import seed_database

    seed_database(adapter, force=True)
    created = client.post(
        "/api/knowledge",
        json={"title": "信用卡分期条款", "content": "分期手续费按月收取，提前结清仍需支付剩余期数手续费。"},
    )
    assert created.status_code == 201
    document_id = created.json()["document_id"]

    assert any(item["document_id"] == document_id for item in client.get("/api/knowledge").json())
    hits = client.post("/api/knowledge/search", params={"question": "提前结清还要手续费吗"}).json()
    assert hits["count"] >= 1
    assert client.delete(f"/api/knowledge/{document_id}").status_code == 200


def test_clear_data_endpoint(client, adapter):
    from app.db.seed import seed_database

    seed_database(adapter, force=True)
    response = client.delete("/api/clear-data")
    assert response.status_code == 200
    assert response.json()["cleared"]["transactions"] > 0
    assert client.get("/api/transactions").json() == []
    seed_database(adapter, force=True)

"""PostgreSQL / GaussDB 真机验收脚本。

做四件事：

1. 用管理账号创建独立的**应用角色**与**数据库**（不使用超级用户跑业务）；
2. 按正确顺序执行 docs/sql 下的脚本（02 → 04 → 05 → 06 → 07 → 10），
   并用应用适配层写入演示数据（真机验证触发器维护余额）；
3. 验证 PostgreSQL 专属对象：fn_monthly_report / fn_category_spending /
   sp_add_transfer / sp_copy_budget / 带 user_id 的视图 / 余额预检触发器；
4. 用 EXPLAIN 证明索引友好改造有效：范围谓词走索引，TO_CHAR 包裹列不走。

用法：
    .venv\\Scripts\\python.exe scripts\\verify_postgres.py
    # 从 .env 读取 PG_*；首次运行时 PG_USER 需为超级用户（默认 postgres），
    # 脚本会创建 finance_app 角色与 finance_db 数据库，并把应用凭据写回 .env。

参数：
    --keep-scratch   保留 EXPLAIN 用的临时 schema（默认用后即删）
    --skip-env       不修改 .env（只做验证）
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SQL_DIR = PROJECT_ROOT / "docs" / "sql"
ENV_FILE = PROJECT_ROOT / ".env"

APP_ROLE = "finance_app"
APP_DB = "finance_db"
SCRIPTS_IN_ORDER = (
    "02_create_tables.sql",
    "04_create_indexes.sql",
    "05_create_views.sql",
    "06_create_triggers.sql",
    "07_create_functions.sql",
    "10_upgrade_ai.sql",
)

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASSED if ok else FAILED).append(name)
    print(f"  [{'OK' if ok else 'FAIL'}]   {name}{(' — ' + detail) if detail else ''}")
    return ok


# --------------------------------------------------------------------------- #
# psql 调用
# --------------------------------------------------------------------------- #
def find_psql() -> str | None:
    found = shutil.which("psql")
    if found:
        return found
    for base in (Path(r"D:\PostgreSQL"), Path(r"C:\Program Files\PostgreSQL")):
        if base.exists():
            for candidate in sorted(base.glob("*/bin/psql.exe"), reverse=True):
                return str(candidate)
            direct = base / "bin" / "psql.exe"
            if direct.exists():
                return str(direct)
    return None


def run_psql(psql: str, *, user: str, password: str, database: str, host: str, port: int,
             script: Path | None = None, command: str | None = None) -> subprocess.CompletedProcess:
    args = [psql, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-U", user, "-h", host, "-p", str(port), "-d", database]
    if script is not None:
        args += ["-f", str(script)]
    if command is not None:
        args += ["-c", command]
    env = {**os.environ, "PGPASSWORD": password, "PGCLIENTENCODING": "UTF8"}
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


# --------------------------------------------------------------------------- #
# .env 读写（只用 UTF-8，避免编码往返破坏中文注释）
# --------------------------------------------------------------------------- #
def load_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def update_env(updates: dict[str, str]) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                output.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(output) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-scratch", action="store_true")
    parser.add_argument("--skip-env", action="store_true")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="先清空 public schema 再执行建表脚本（可重复运行验收；会删除该库里的既有数据）",
    )
    args = parser.parse_args()

    env = load_env_values()
    host = env.get("PG_HOST") or "127.0.0.1"
    port = int(env.get("PG_PORT") or 5432)
    admin_user = env.get("PG_USER") or "postgres"
    admin_password = env.get("PG_PASSWORD") or os.environ.get("PGPASSWORD", "")
    database = env.get("PG_DATABASE") or APP_DB
    schema = env.get("PG_SCHEMA") or "public"

    psql = find_psql()
    print(f"psql：{psql}")
    if not psql:
        print("找不到 psql，请把 PostgreSQL 的 bin 目录加入 PATH 或修改脚本里的候选路径。")
        return 2
    if not admin_password:
        print("!! .env 里的 PG_PASSWORD 为空：请填入 PostgreSQL 管理账号密码后重试。")
        return 2

    version = run_psql(psql, user=admin_user, password=admin_password, database="postgres",
                       host=host, port=port, command="SELECT version();")
    print(f"服务端：{version.stdout.strip().splitlines()[0] if version.stdout.strip() else version.stderr.strip()}")
    if version.returncode != 0:
        print("!! 无法连接 PostgreSQL，请检查 .env 中的 PG_HOST / PG_PORT / PG_USER / PG_PASSWORD。")
        print(version.stderr.strip()[:400])
        return 2

    # ------------------------------------------------------------------ #
    print("\n1) 创建应用角色与数据库（业务不使用超级用户）")
    app_password = env.get("PG_APP_PASSWORD") or secrets.token_urlsafe(15)
    if admin_user != APP_ROLE:
        role_sql = (
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
            f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{app_password}'; ELSE "
            f"ALTER ROLE {APP_ROLE} WITH LOGIN PASSWORD '{app_password}'; END IF; END $$;"
        )
        result = run_psql(psql, user=admin_user, password=admin_password, database="postgres",
                          host=host, port=port, command=role_sql)
        check("应用角色就绪", result.returncode == 0, APP_ROLE if result.returncode == 0 else result.stderr.strip()[:200])

        exists = run_psql(psql, user=admin_user, password=admin_password, database="postgres", host=host, port=port,
                          command=f"SELECT 1 FROM pg_database WHERE datname = '{database}';")
        if "1" not in exists.stdout:
            created = run_psql(psql, user=admin_user, password=admin_password, database="postgres", host=host, port=port,
                               command=f'CREATE DATABASE "{database}" OWNER {APP_ROLE};')
            check("数据库已创建", created.returncode == 0, database if created.returncode == 0 else created.stderr.strip()[:200])
        else:
            check("数据库已存在", True, database)
        run_psql(psql, user=admin_user, password=admin_password, database=database, host=host, port=port,
                 command=f"ALTER SCHEMA public OWNER TO {APP_ROLE};")
        app_user = APP_ROLE
    else:
        check("使用 .env 指定账号（跳过角色创建）", True, admin_user)
        app_user = admin_user

    if not args.skip_env and admin_user != APP_ROLE:
        update_env({"DB_MODE": "postgres", "PG_HOST": host, "PG_PORT": str(port),
                    "PG_DATABASE": database, "PG_USER": app_user, "PG_PASSWORD": app_password,
                    "PG_SCHEMA": schema, "PG_APP_PASSWORD": app_password})
        print("     已把应用凭据写回 .env（PG_USER=finance_app；密码未打印，请查看 .env）")

    # ------------------------------------------------------------------ #
    print("\n2) 按顺序执行 docs/sql 脚本（应用角色）")
    if args.reset:
        reset_command = (
            "DROP SCHEMA IF EXISTS public CASCADE; "
            "CREATE SCHEMA public; "
            f"ALTER SCHEMA public OWNER TO {app_user}; "
            f"GRANT ALL ON SCHEMA public TO {app_user};"
        )
        result = run_psql(psql, user=app_user, password=app_password, database=database,
                          host=host, port=port, command=reset_command)
        check("重置 public schema（--reset）", result.returncode == 0,
              "" if result.returncode == 0 else result.stderr.strip()[:200])
    for name in SCRIPTS_IN_ORDER:
        script = SQL_DIR / name
        if not script.exists():
            check(f"{name} 存在", False, "文件缺失")
            continue
        result = run_psql(psql, user=app_user, password=app_password, database=database,
                          host=host, port=port, script=script)
        detail = "" if result.returncode == 0 else (result.stderr.strip().splitlines() or [""])[-1][:200]
        check(f"{name} 执行成功", result.returncode == 0, detail)

    # ------------------------------------------------------------------ #
    print("\n3) 通过应用适配层连接并播种（真机验证触发器维护余额）")
    os.environ.update({
        "DB_MODE": "postgres", "PG_HOST": host, "PG_PORT": str(port), "PG_DATABASE": database,
        "PG_USER": app_user, "PG_PASSWORD": app_password, "PG_SCHEMA": schema,
    })
    from app.config import get_settings  # noqa: E402

    get_settings.cache_clear()
    from app.db import build_adapter  # noqa: E402
    from app.db.repository import LedgerRepository  # noqa: E402
    from app.db.seed import seed_database  # noqa: E402

    settings = get_settings()
    adapter = build_adapter(settings)
    info = adapter.init_schema()
    check("结构自检通过", bool(info.get("schema_ok")), f"{len(info.get('tables', []))} 表 / {len(info.get('views', []))} 视图")
    check("触发器已就位", any("transaction_balance" in name for name in info.get("triggers", [])),
          "、".join(info.get("triggers", [])) or "无")

    seeded = seed_database(adapter, force=True)
    check("演示数据写入成功", bool(seeded.get("seeded")), str(seeded.get("transactions", 0)) + " 笔交易")

    repo = LedgerRepository(adapter)
    user_id = settings.default_user_id
    accounts = repo.list_accounts(user_id)
    check("账户余额非负（触发器维护）", all(Decimal(str(a["balance"])) >= 0 for a in accounts),
          "、".join(f"{a['account_name']}={a['balance']}" for a in accounts[:3]))

    # ------------------------------------------------------------------ #
    print("\n4) PostgreSQL 专属对象验证")
    month = repo.overview(user_id)["month"]

    report = repo.monthly_report(user_id, month)
    check("fn_monthly_report 可调用", report["total_income"] > 0,
          f"{month} 收入 {report['total_income']} / 支出 {report['total_expense']}")

    from app.db.base import month_bounds

    start, end = month_bounds(month)
    spending = repo.category_spending(user_id, start, end)
    check("fn_category_spending 可调用", bool(spending),
          "、".join(f"{row['category_name']}={row['expense_amount']}" for row in spending[:3]))

    accounts_map = {a["account_name"]: a for a in accounts}
    before_from = Decimal(str(accounts_map["工商银行储蓄卡"]["balance"]))
    before_to = Decimal(str(accounts_map["支付宝"]["balance"]))
    transfer = repo.transfer(user_id, from_account=int(accounts_map["工商银行储蓄卡"]["account_id"]),
                             to_account=int(accounts_map["支付宝"]["account_id"]),
                             amount=Decimal("123.45"), remark="真机验收转账")
    after = {a["account_name"]: Decimal(str(a["balance"])) for a in repo.list_accounts(user_id)}
    check("sp_add_transfer 存储过程生效",
          after["工商银行储蓄卡"] == before_from - Decimal("123.45") and after["支付宝"] == before_to + Decimal("123.45"),
          f"转出 {before_from}→{after['工商银行储蓄卡']}，转入 {before_to}→{after['支付宝']}")
    rows = repo.list_transactions(user_id, trans_type="转账", limit=5)
    check("transfer_group_id 已写入", any(row["transfer_group_id"] == transfer["transfer_group_id"] for row in rows),
          transfer["transfer_group_id"])

    copied = repo.copy_budget(user_id, month, "2099-01")
    check("sp_copy_budget 存储过程生效", copied >= 1, f"复制 {copied} 条预算")
    adapter.execute("DELETE FROM budget WHERE year_month = %s", ("2099-01",))

    check("视图带 user_id 且按用户隔离", repo.list_accounts(user_id + 9999) == [])

    # 触发器余额预检（中文业务错误）
    from app.db.base import InsufficientBalanceError

    cash = next(a for a in accounts if a["account_name"] == "现金")
    category = next(c for c in repo.list_categories(user_id, "支出") if c["category_name"] == "其他支出")
    raised = None
    try:
        repo.create_transaction(user_id, account_id=int(cash["account_id"]), category_id=int(category["category_id"]),
                                amount=Decimal(str(cash["balance"])) + Decimal("1000"), trans_type="支出")
    except InsufficientBalanceError as exc:
        raised = str(exc)
    except Exception as exc:  # noqa: BLE001
        raised = f"{type(exc).__name__}: {exc}"
    check("透支被触发器拦下并返回中文提示", bool(raised) and "余额不足" in (raised or ""), (raised or "")[:60])

    # ------------------------------------------------------------------ #
    print("\n5) EXPLAIN：证明索引友好改造有效（20000 行临时表）")
    # 注意：verify_scratch 是顶层 schema，不能写成 public.verify_scratch（PostgreSQL 会当成跨库引用）
    adapter.execute("DROP SCHEMA IF EXISTS verify_scratch CASCADE")
    adapter.execute("CREATE SCHEMA verify_scratch")
    adapter.execute(
        "CREATE TABLE verify_scratch.tx AS "
        "SELECT transaction_id, user_id, amount, trans_type, "
        "       (DATE '2026-01-01' + mod(g, 400)) AS trans_date "
        "FROM generate_series(1, 20000) AS g, "
        "     (SELECT transaction_id, user_id, amount, trans_type FROM transaction_record LIMIT 1) AS seed"
    )
    adapter.execute("CREATE INDEX idx_scratch_user_date ON verify_scratch.tx(user_id, trans_date)")
    adapter.execute("ANALYZE verify_scratch.tx")

    plan_range = adapter.query(
        "EXPLAIN SELECT SUM(amount) FROM verify_scratch.tx "
        "WHERE user_id = %s AND trans_date >= %s AND trans_date <= %s",
        (user_id, "2026-06-01", "2026-06-30"),
    )
    plan_tochar = adapter.query(
        "EXPLAIN SELECT SUM(amount) FROM verify_scratch.tx "
        "WHERE user_id = %s AND TO_CHAR(trans_date, 'YYYY-MM') = %s",
        (user_id, "2026-06"),
    )
    range_text = " ".join(str(row.get("QUERY PLAN", "")) for row in plan_range)
    tochar_text = " ".join(str(row.get("QUERY PLAN", "")) for row in plan_tochar)
    uses_index = "Index" in range_text
    check("范围谓词使用索引", uses_index, range_text.splitlines()[0][:70] if range_text else "无计划")
    check("TO_CHAR 包裹列无法用该索引（对照）", "Seq Scan" in tochar_text,
          tochar_text.splitlines()[0][:70] if tochar_text else "无计划")

    if not args.keep_scratch:
        adapter.execute("DROP SCHEMA verify_scratch CASCADE")
        print("     临时 schema 已清理（--keep-scratch 可保留）")

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 64)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("失败项：" + "、".join(FAILED))
        return 1
    print("PostgreSQL 真机验收全部通过 ✅")
    print("\n下一步：让同一套 63 个用例也跑在 PostgreSQL 上")
    print('  $env:TEST_DB_MODE="postgres"; .venv\\Scripts\\python.exe -m pytest -q')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

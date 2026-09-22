"""一键初始化数据库并写入演示数据。

用法（在项目根目录执行）：

    .venv\\Scripts\\python.exe scripts\\init_db.py                 # 建结构 + 首次播种
    .venv\\Scripts\\python.exe scripts\\init_db.py --force         # 清空后重新播种
    .venv\\Scripts\\python.exe scripts\\init_db.py --mode postgres # 对 GaussDB 做结构自检
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.db import build_adapter  # noqa: E402
from app.db.repository import LedgerRepository  # noqa: E402
from app.db.seed import seed_database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化个人理财助手数据库")
    parser.add_argument("--mode", choices=["sqlite", "postgres"], help="覆盖 DB_MODE")
    parser.add_argument("--force", action="store_true", help="清空业务数据后重新播种")
    args = parser.parse_args()

    settings = get_settings()
    if args.mode:
        settings = settings.model_copy(update={"db_mode": args.mode})

    adapter = build_adapter(settings)
    print(f"[1/3] 数据库模式：{adapter.mode}")

    schema = adapter.init_schema()
    print(f"[2/3] 结构自检：{json.dumps(schema, ensure_ascii=False, indent=2)}")
    if not schema.get("schema_ok", False):
        print("!! 结构不完整，请先按 docs/sql 顺序执行建表/升级脚本。")
        return 1

    result = seed_database(adapter, force=args.force)
    print(f"[3/3] 演示数据：{json.dumps(result, ensure_ascii=False)}")

    repo = LedgerRepository(adapter)
    user_id = settings.default_user_id
    print("\n—— 账户余额（由触发器维护）——")
    for account in repo.list_accounts(user_id):
        print(f"  {account['account_name']:<16} {account['account_type']:<6} {account['balance']:>12}")
    print("\n—— 本月概览 ——")
    print(json.dumps({k: str(v) for k, v in repo.overview(user_id).items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

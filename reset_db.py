"""
Reset all application data in the PostgreSQL database.
Keeps the schema (tables/columns) intact — only wipes rows.

Usage (from backend/ directory):
    python reset_db.py
"""
import asyncio
import os
import sys
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


SKIP_TABLES = {"alembic_version"}


def _load_env():
    for name in (".env", ".env.local"):
        path = Path(__file__).resolve().parent / name
        if path.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(path, override=False)
                print(f"[env] loaded {name}")
                return
            except ImportError:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        os.environ.setdefault(k.strip(), v.strip())
                print(f"[env] loaded {name} (manual)")
                return


def _make_engine(url: str):
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return create_async_engine(url, echo=False)


async def reset(db_url: str):
    engine = _make_engine(db_url)
    async with engine.begin() as conn:
        result = await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ))
        all_tables = [row[0] for row in result if row[0] not in SKIP_TABLES]

    if not all_tables:
        print("No tables found.")
        await engine.dispose()
        return

    print(f"\nFound {len(all_tables)} tables:\n  " + "\n  ".join(sorted(all_tables)))
    print("\n⚠️  This will DELETE ALL ROWS from every table (schema kept intact).")
    confirm = input("\nType 'yes' to continue: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        await engine.dispose()
        return

    async with engine.begin() as conn:
        tables_sql = ", ".join(f'"{t}"' for t in all_tables)
        await conn.execute(text(f"TRUNCATE TABLE {tables_sql} RESTART IDENTITY CASCADE"))

    print(f"\n✅  All {len(all_tables)} tables truncated. Database is clean.")
    await engine.dispose()


if __name__ == "__main__":
    _load_env()
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url or "PASTE" in db_url:
        print("❌  DATABASE_URL not set in .env")
        sys.exit(1)
    print(f"[db] connecting to: {db_url[:40]}...")
    asyncio.run(reset(db_url))

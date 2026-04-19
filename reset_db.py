"""
Reset all application data in the PostgreSQL database.
Keeps the schema (tables/columns) intact — only wipes rows.

Usage (from backend/ directory):
    python reset_db.py
"""
import asyncio
import sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


SKIP_TABLES = {"alembic_version"}


def _make_engine(url: str):
    url = url.replace("postgres://", "postgresql+asyncpg://", 1)
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

    print(f"Found {len(all_tables)} tables: {', '.join(sorted(all_tables))}\n")
    print("⚠️  This will DELETE ALL ROWS from every table.")
    confirm = input("Type 'yes' to continue: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        await engine.dispose()
        return

    async with engine.begin() as conn:
        tables = ", ".join(all_tables)
        await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        print(f"✅  Truncated {len(all_tables)} tables. Database is clean.")

    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python reset_db.py <DATABASE_URL>")
        print("Get the URL from: Railway dashboard → PostgreSQL → Connect tab")
        sys.exit(1)
    asyncio.run(reset(sys.argv[1]))

"""DB health check — compares SQLAlchemy model tables against actual DB tables."""
import asyncio
from sqlalchemy import text
from database import engine, Base

# Import ALL models so Base.metadata is populated
import models  # noqa: F401

async def main():
    async with engine.connect() as conn:
        # Tables in DB
        r = await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        ))
        db_tables = {row[0] for row in r.fetchall()}

        # Tables in models
        model_tables = set(Base.metadata.tables.keys())

        missing_in_db     = sorted(model_tables - db_tables - {"alembic_version"})
        extra_in_db       = sorted(db_tables - model_tables - {"alembic_version"})

        print("\n── TABLE COMPARISON ─────────────────────────────────")
        all_tables = sorted(model_tables | db_tables)
        for t in all_tables:
            if t == "alembic_version":
                continue
            in_model = "model" if t in model_tables else "     "
            in_db    = "DB   " if t in db_tables    else "     "
            flag     = "✗ MISSING IN DB" if t in model_tables and t not in db_tables else \
                       "⚠ extra in DB"   if t in db_tables    and t not in model_tables else "✓"
            print(f"  {flag:<18s} {t}")

        # Row counts for key tables
        print("\n── ROW COUNTS ───────────────────────────────────────")
        for t in ["users", "wallets", "transactions", "virtual_cards",
                  "platform_accounts", "pending_registrations"]:
            if t in db_tables:
                r2 = await conn.execute(text(f"SELECT COUNT(*) FROM {t}"))
                print(f"  {t:<35s} {r2.scalar()} rows")

        # Alembic version
        r3 = await conn.execute(text("SELECT version_num FROM alembic_version"))
        print(f"\n── MIGRATION HEAD ───────────────────────────────────")
        for row in r3.fetchall():
            print(f"  {row[0]}")

        print(f"\n── SUMMARY ──────────────────────────────────────────")
        if missing_in_db:
            print(f"  ⚠  {len(missing_in_db)} model table(s) missing from DB:")
            for t in missing_in_db:
                print(f"       - {t}")
        else:
            print("  ✅  All model tables exist in DB.")

        if extra_in_db:
            print(f"  ℹ  {len(extra_in_db)} extra table(s) in DB (not in models):")
            for t in extra_in_db:
                print(f"       - {t}")

asyncio.run(main())

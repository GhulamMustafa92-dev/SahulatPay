"""Metal rate scheduler — fetches live gold/silver prices every hour
and saves to metal_rate_cache. This is the ONLY place that calls the external
metals API. The Zakat endpoints read from this cache instead."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from database import AsyncSessionLocal
from models.zakat import MetalRateCache

scheduler = AsyncIOScheduler(timezone="UTC")

NISAB_GOLD_GRAMS   = Decimal("87.48")
NISAB_SILVER_GRAMS = Decimal("612.36")
TROY_OZ_GRAMS      = Decimal("31.1035")

_METALS_URL = "https://api.metals.live/v1/spot/gold,silver"
_FX_URL     = "https://open.er-api.com/v6/latest/USD"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _fetch_and_cache_rates() -> None:
    """
    Fetch gold/silver USD/oz from metals.live and USD→PKR from er-api.com.
    Compute PKR/gram and nisab thresholds, then INSERT a new MetalRateCache row.
    On ANY API failure: log error, do not save — previous cache row remains.
    """
    print(f"[metal_rate_scheduler] fetching rates @ {_utcnow().isoformat()}")

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(_METALS_URL) as metals_resp:
                if metals_resp.status != 200:
                    raise ValueError(f"metals.live returned HTTP {metals_resp.status}")
                metals_data = await metals_resp.json()

            async with session.get(_FX_URL) as fx_resp:
                if fx_resp.status != 200:
                    raise ValueError(f"er-api.com returned HTTP {fx_resp.status}")
                fx_data = await fx_resp.json()

    except Exception as e:
        print(f"[metal_rate_scheduler] ERROR fetching rates: {e}")
        await _check_cache_age()
        return

    try:
        gold_usd_oz   = Decimal("0")
        silver_usd_oz = Decimal("0")
        for item in metals_data:
            if item.get("metal") == "gold":
                gold_usd_oz   = Decimal(str(item.get("price", 0)))
            elif item.get("metal") == "silver":
                silver_usd_oz = Decimal(str(item.get("price", 0)))

        if gold_usd_oz <= 0 or silver_usd_oz <= 0:
            raise ValueError(f"Unexpected metal prices: gold={gold_usd_oz}, silver={silver_usd_oz}")

        usd_to_pkr = Decimal(str(fx_data.get("rates", {}).get("PKR", 0)))
        if usd_to_pkr <= 0:
            raise ValueError(f"Unexpected USD/PKR rate: {usd_to_pkr}")

        gold_pkr_gram    = (gold_usd_oz   * usd_to_pkr) / TROY_OZ_GRAMS
        silver_pkr_gram  = (silver_usd_oz * usd_to_pkr) / TROY_OZ_GRAMS
        nisab_gold_pkr   = gold_pkr_gram   * NISAB_GOLD_GRAMS
        nisab_silver_pkr = silver_pkr_gram * NISAB_SILVER_GRAMS

        async with AsyncSessionLocal() as db:
            row = MetalRateCache(
                gold_usd_oz      = gold_usd_oz.quantize(Decimal("0.0001")),
                silver_usd_oz    = silver_usd_oz.quantize(Decimal("0.0001")),
                usd_to_pkr       = usd_to_pkr.quantize(Decimal("0.0001")),
                gold_pkr_gram    = gold_pkr_gram.quantize(Decimal("0.0001")),
                silver_pkr_gram  = silver_pkr_gram.quantize(Decimal("0.0001")),
                nisab_gold_pkr   = nisab_gold_pkr.quantize(Decimal("0.01")),
                nisab_silver_pkr = nisab_silver_pkr.quantize(Decimal("0.01")),
                source           = "metals.live + er-api.com",
                fetched_at       = _utcnow(),
            )
            db.add(row)
            await db.commit()

        print(
            f"[metal_rate_scheduler] saved — "
            f"gold={gold_pkr_gram:.2f} PKR/g, silver={silver_pkr_gram:.4f} PKR/g, "
            f"nisab_gold={nisab_gold_pkr:.2f}, nisab_silver={nisab_silver_pkr:.2f}"
        )

    except Exception as e:
        print(f"[metal_rate_scheduler] ERROR processing/saving rates: {e}")


async def _check_cache_age() -> None:
    """Log a critical alert if the most recent cache entry is older than 7 days."""
    try:
        from sqlalchemy import select
        async with AsyncSessionLocal() as db:
            from models.zakat import MetalRateCache as _MC
            latest = (await db.execute(
                select(_MC).order_by(_MC.fetched_at.desc()).limit(1)
            )).scalar_one_or_none()

            if latest is None:
                print("[metal_rate_scheduler] CRITICAL — metal_rate_cache is EMPTY. No fallback available.")
            else:
                age_days = (_utcnow() - latest.fetched_at).days
                if age_days >= 7:
                    print(
                        f"[metal_rate_scheduler] CRITICAL — cache is {age_days} days old "
                        f"(last fetched: {latest.fetched_at.isoformat()}). "
                        f"Rates may be significantly inaccurate."
                    )
    except Exception as e:
        print(f"[metal_rate_scheduler] ERROR checking cache age: {e}")


def start_metal_rate_scheduler() -> AsyncIOScheduler:
    scheduler.add_job(
        _fetch_and_cache_rates,
        trigger=IntervalTrigger(hours=1),
        id="metal_rate_hourly_fetch",
        replace_existing=True,
        max_instances=1,
        next_run_time=datetime.now(timezone.utc),  # fetch immediately on startup
    )
    if not scheduler.running:
        scheduler.start()
    print("[metal_rate_scheduler] started — immediate first fetch + every 1 hour")
    return scheduler


def stop_metal_rate_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
    print("[metal_rate_scheduler] stopped")

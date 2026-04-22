"""Metal rate scheduler — fetches live gold/silver prices every hour
and saves to metal_rate_cache. This is the ONLY place that calls the external
metals API. The Zakat endpoints read from this cache instead.

Source: goldpricez.com (PKR per gram, native — no manual conversion needed)
        open.er-api.com  (USD→PKR, used only to back-calculate USD/oz for storage)
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import asyncio
import json
import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import settings
from database import AsyncSessionLocal
from models.zakat import MetalRateCache

scheduler = AsyncIOScheduler(timezone="UTC")

NISAB_GOLD_GRAMS   = Decimal("87.48")
NISAB_SILVER_GRAMS = Decimal("612.36")
TROY_OZ_GRAMS      = Decimal("31.1035")

# goldpricez.com — gold USD/oz (free tier; double-encoded JSON response)
_GOLDPRICEZ_URL = "https://goldpricez.com/api/rates/currency/usd/measure/ounce"
# er-api.com — USD→PKR rate + XAG (silver troy oz per USD) in one call
_FX_URL         = "https://open.er-api.com/v6/latest/USD"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _fetch_and_cache_rates() -> None:
    """
    Fetch gold USD/oz from goldpricez.com, silver USD/oz from er-api.com XAG rate,
    USD→PKR from er-api.com, then compute PKR/gram and nisab thresholds.
    INSERT a new MetalRateCache row on success; on failure keep previous cache row.
    """
    print(f"[metal_rate_scheduler] fetching rates @ {_utcnow().isoformat()}")

    try:
        headers = {"X-API-KEY": settings.GOLDPRICEZ_API_KEY}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(_GOLDPRICEZ_URL, headers=headers) as metals_resp:
                raw_text = await metals_resp.text()
                if metals_resp.status != 200:
                    raise ValueError(
                        f"goldpricez.com returned HTTP {metals_resp.status}: {raw_text[:300]}"
                    )
                try:
                    parsed = json.loads(raw_text)
                    # goldpricez free tier double-encodes: outer JSON is a string containing JSON
                    gold_data = json.loads(parsed) if isinstance(parsed, str) else parsed
                except (json.JSONDecodeError, TypeError) as e:
                    raise ValueError(f"goldpricez.com non-JSON response: {raw_text[:400]}") from e
                print(f"[metal_rate_scheduler] goldpricez decoded: type={type(gold_data).__name__}, keys={list(gold_data.keys()) if isinstance(gold_data, dict) else repr(gold_data)[:300]}")

            async with session.get(_FX_URL) as fx_resp:
                if fx_resp.status != 200:
                    raise ValueError(f"er-api.com returned HTTP {fx_resp.status}")
                fx_data = await fx_resp.json(content_type=None)

    except asyncio.CancelledError:
        return  # graceful shutdown — do nothing
    except Exception as e:
        print(f"[metal_rate_scheduler] ERROR fetching rates: {e}")
        await _check_cache_age()
        return

    try:
        # goldpricez: flat dict with ounce_price_usd = gold USD/troy oz
        if not isinstance(gold_data, dict):
            raise ValueError(f"goldpricez decoded to unexpected type: {type(gold_data).__name__}: {repr(gold_data)[:200]}")
        gold_usd_oz = Decimal(str(
            gold_data.get("ounce_price_usd")
            or gold_data.get("price")
            or gold_data.get("rate")
            or 0
        ))
        if gold_usd_oz <= 0:
            raise ValueError(f"goldpricez: no valid gold USD/oz price. Keys: {list(gold_data.keys())}")

        # er-api.com includes XAG (troy oz of silver per USD) in the same response
        # silver_usd_oz = 1 / XAG_per_USD  (e.g. XAG=0.031 → silver=$32.26/oz)
        xag_per_usd = Decimal(str(fx_data.get("rates", {}).get("XAG", 0)))
        if xag_per_usd > 0:
            silver_usd_oz = Decimal("1") / xag_per_usd
        else:
            # Fallback: gold:silver ratio ~80:1 (approximate)
            silver_usd_oz = gold_usd_oz / Decimal("80")
            print("[metal_rate_scheduler] XAG not in er-api response — using gold/80 ratio for silver")

        usd_to_pkr = Decimal(str(fx_data.get("rates", {}).get("PKR", 0)))
        if usd_to_pkr <= 0:
            raise ValueError(f"Unexpected USD/PKR rate: {usd_to_pkr}")

        gold_pkr_gram   = (gold_usd_oz   * usd_to_pkr) / TROY_OZ_GRAMS
        silver_pkr_gram = (silver_usd_oz * usd_to_pkr) / TROY_OZ_GRAMS

        if gold_pkr_gram <= 0 or silver_pkr_gram <= 0:
            raise ValueError(
                f"Unexpected PKR/gram values: gold={gold_pkr_gram}, silver={silver_pkr_gram}"
            )

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
                source           = "goldpricez.com (gold) + er-api.com (XAG silver + PKR)",
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


# Hardcoded fallback rates (Apr 2026 approximate values)
_FALLBACK_GOLD_USD_OZ   = Decimal("3250.00")
_FALLBACK_SILVER_USD_OZ = Decimal("32.50")
_FALLBACK_USD_TO_PKR    = Decimal("278.50")


async def _seed_fallback_rates() -> None:
    """Insert hardcoded fallback rates so the app is functional when the external API is unreachable."""
    try:
        gold_pkr_gram    = (_FALLBACK_GOLD_USD_OZ   * _FALLBACK_USD_TO_PKR) / TROY_OZ_GRAMS
        silver_pkr_gram  = (_FALLBACK_SILVER_USD_OZ * _FALLBACK_USD_TO_PKR) / TROY_OZ_GRAMS
        nisab_gold_pkr   = gold_pkr_gram   * NISAB_GOLD_GRAMS
        nisab_silver_pkr = silver_pkr_gram * NISAB_SILVER_GRAMS
        async with AsyncSessionLocal() as db:
            db.add(MetalRateCache(
                gold_usd_oz      = _FALLBACK_GOLD_USD_OZ.quantize(Decimal("0.0001")),
                silver_usd_oz    = _FALLBACK_SILVER_USD_OZ.quantize(Decimal("0.0001")),
                usd_to_pkr       = _FALLBACK_USD_TO_PKR.quantize(Decimal("0.0001")),
                gold_pkr_gram    = gold_pkr_gram.quantize(Decimal("0.0001")),
                silver_pkr_gram  = silver_pkr_gram.quantize(Decimal("0.0001")),
                nisab_gold_pkr   = nisab_gold_pkr.quantize(Decimal("0.01")),
                nisab_silver_pkr = nisab_silver_pkr.quantize(Decimal("0.01")),
                source           = "hardcoded-fallback",
                fetched_at       = _utcnow(),
            ))
            await db.commit()
        print("[metal_rate_scheduler] seeded fallback rates — gold=~3250 USD/oz, USD/PKR=~278.5")
    except Exception as e:
        print(f"[metal_rate_scheduler] ERROR seeding fallback rates: {e}")


async def _check_cache_age() -> None:
    """Log a critical alert if the most recent cache entry is older than 7 days; seed fallback if empty."""
    try:
        from sqlalchemy import select
        async with AsyncSessionLocal() as db:
            from models.zakat import MetalRateCache as _MC
            latest = (await db.execute(
                select(_MC).order_by(_MC.fetched_at.desc()).limit(1)
            )).scalar_one_or_none()

            if latest is None:
                print("[metal_rate_scheduler] cache is EMPTY — seeding hardcoded fallback rates.")
                await _seed_fallback_rates()
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

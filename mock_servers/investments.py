"""Mock investment server: PSX stocks + Mutual Funds."""
import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from mock_servers.db import get_db
from mock_servers.models import MockStock, MockMutualFund, MockPortfolio

router = APIRouter()


class StockOrderRequest(BaseModel):
    user_phone: str
    symbol: str
    units: float
    order_type: str = "buy"   # buy | sell


class MutualFundOrderRequest(BaseModel):
    user_phone: str
    fund_code: str
    amount_pkr: float
    order_type: str = "buy"   # buy | sell (amount in PKR for MF)


# ── GET /mock/investments/stocks ─────────────────────────────────────────────
@router.get("/stocks")
def list_stocks(sector: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(MockStock)
    if sector:
        q = q.filter(MockStock.sector == sector)
    stocks = q.all()
    return {
        "count": len(stocks),
        "stocks": [
            {
                "symbol":         s.symbol,
                "company":        s.company_name,
                "sector":         s.sector,
                "price":          s.price,
                "change":         s.change,
                "change_percent": s.change_percent,
                "volume":         s.volume,
            }
            for s in stocks
        ],
    }


# ── GET /mock/investments/stocks/{symbol} ─────────────────────────────────────
@router.get("/stocks/{symbol}")
def get_stock(symbol: str, db: Session = Depends(get_db)):
    stock = db.query(MockStock).filter_by(symbol=symbol.upper()).first()
    if not stock:
        raise HTTPException(404, f"Stock {symbol} not found")
    return {
        "symbol":         stock.symbol,
        "company":        stock.company_name,
        "sector":         stock.sector,
        "price":          stock.price,
        "change":         stock.change,
        "change_percent": stock.change_percent,
        "volume":         stock.volume,
        "market_cap":     stock.market_cap,
    }


# ── POST /mock/investments/stocks/order ───────────────────────────────────────
@router.post("/stocks/order")
def stock_order(body: StockOrderRequest, db: Session = Depends(get_db)):
    stock = db.query(MockStock).filter_by(symbol=body.symbol.upper()).first()
    if not stock:
        raise HTTPException(404, f"Stock {body.symbol} not found on PSX")
    total_pkr = round(body.units * stock.price, 2)
    brokerage  = round(total_pkr * 0.002, 2)  # 0.2% brokerage fee
    portfolio  = db.query(MockPortfolio).filter_by(
        user_phone=body.user_phone, asset_type="stock", symbol=body.symbol.upper()
    ).first()
    if body.order_type == "buy":
        if not portfolio:
            portfolio = MockPortfolio(
                user_phone=body.user_phone, asset_type="stock",
                symbol=body.symbol.upper(), units=body.units, avg_price=stock.price,
            )
            db.add(portfolio)
        else:
            total_cost = portfolio.units * portfolio.avg_price + total_pkr
            portfolio.units     += body.units
            portfolio.avg_price  = total_cost / portfolio.units
        db.commit()
    elif body.order_type == "sell":
        if not portfolio or portfolio.units < body.units:
            raise HTTPException(400, "Insufficient shares to sell")
        portfolio.units -= body.units
        if portfolio.units == 0:
            db.delete(portfolio)
        db.commit()
    return {
        "success":       True,
        "order_id":      "PSX" + secrets.token_hex(5).upper(),
        "type":          body.order_type,
        "symbol":        body.symbol.upper(),
        "units":         body.units,
        "price":         stock.price,
        "total_pkr":     total_pkr,
        "brokerage_fee": brokerage,
        "net_pkr":       total_pkr + brokerage if body.order_type == "buy" else total_pkr - brokerage,
        "status":        "executed",
        "message":       f"{body.order_type.capitalize()} order executed: {body.units} units of {body.symbol} @ PKR {stock.price}",
    }


# ── GET /mock/investments/funds ───────────────────────────────────────────────
@router.get("/funds")
def list_funds(category: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(MockMutualFund)
    if category:
        q = q.filter(MockMutualFund.category == category)
    funds = q.all()
    return {
        "count": len(funds),
        "funds": [
            {
                "fund_code":   f.fund_code,
                "name":        f.fund_name,
                "provider":    f.provider,
                "category":    f.category,
                "nav":         f.nav,
                "ytd_return":  f.ytd_return,
                "risk_level":  f.risk_level,
            }
            for f in funds
        ],
    }


# ── POST /mock/investments/funds/order ────────────────────────────────────────
@router.post("/funds/order")
def fund_order(body: MutualFundOrderRequest, db: Session = Depends(get_db)):
    fund = db.query(MockMutualFund).filter_by(fund_code=body.fund_code.upper()).first()
    if not fund:
        raise HTTPException(404, f"Fund {body.fund_code} not found")
    units = round(body.amount_pkr / fund.nav, 4)
    portfolio = db.query(MockPortfolio).filter_by(
        user_phone=body.user_phone, asset_type="mutual_fund", symbol=body.fund_code.upper()
    ).first()
    if body.order_type == "buy":
        if not portfolio:
            portfolio = MockPortfolio(
                user_phone=body.user_phone, asset_type="mutual_fund",
                symbol=body.fund_code.upper(), units=units, avg_price=fund.nav,
            )
            db.add(portfolio)
        else:
            portfolio.units += units
        db.commit()
    elif body.order_type == "sell":
        if not portfolio or portfolio.units < units:
            raise HTTPException(400, "Insufficient units to redeem")
        portfolio.units -= units
        if portfolio.units <= 0:
            db.delete(portfolio)
        db.commit()
    return {
        "success":    True,
        "order_id":   "MF" + secrets.token_hex(5).upper(),
        "type":       body.order_type,
        "fund":       fund.fund_name,
        "provider":   fund.provider,
        "amount_pkr": body.amount_pkr,
        "nav":        fund.nav,
        "units":      units,
        "status":     "confirmed",
        "message":    f"{body.order_type.capitalize()} {units} units of {fund.fund_name} @ NAV {fund.nav}",
    }


# ── GET /mock/investments/portfolio ───────────────────────────────────────────
@router.get("/portfolio")
def get_portfolio(user_phone: str, db: Session = Depends(get_db)):
    items = db.query(MockPortfolio).filter_by(user_phone=user_phone).all()
    result = []
    for item in items:
        current_price = None
        if item.asset_type == "stock":
            stock = db.query(MockStock).filter_by(symbol=item.symbol).first()
            current_price = stock.price if stock else item.avg_price
        elif item.asset_type == "mutual_fund":
            fund = db.query(MockMutualFund).filter_by(fund_code=item.symbol).first()
            current_price = fund.nav if fund else item.avg_price
        current_value = round(item.units * current_price, 2)
        invested      = round(item.units * item.avg_price, 2)
        result.append({
            "type":          item.asset_type,
            "symbol":        item.symbol,
            "units":         item.units,
            "avg_price":     item.avg_price,
            "current_price": current_price,
            "current_value": current_value,
            "invested":      invested,
            "gain_loss":     round(current_value - invested, 2),
        })
    return {"user_phone": user_phone, "portfolio": result}

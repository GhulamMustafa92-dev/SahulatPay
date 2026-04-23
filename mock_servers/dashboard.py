"""Public read-only endpoint that exposes all mock SQLite data for the demo dashboard."""
from fastapi import APIRouter
from mock_servers.db import SessionLocal
from mock_servers.models import (
    MockWalletAccount, MockBankAccount, MockBill, MockChallan,
    MockInsurancePolicy, MockStock, MockMutualFund, MockInternationalTransfer,
)
from mock_servers.seeds import seed_all

router = APIRouter()


@router.get("/all")
def dashboard_all():
    """
    Returns all mock SQLite data in one call.
    Public — no auth required (demo data only).
    """
    db = SessionLocal()
    try:
        wallets = db.query(MockWalletAccount).order_by(MockWalletAccount.provider).all()
        banks   = db.query(MockBankAccount).order_by(MockBankAccount.bank_code).all()
        bills   = db.query(MockBill).order_by(MockBill.company).all()
        challans= db.query(MockChallan).order_by(MockChallan.department).all()
        insurance=db.query(MockInsurancePolicy).all()
        stocks  = db.query(MockStock).order_by(MockStock.symbol).all()
        funds   = db.query(MockMutualFund).order_by(MockMutualFund.fund_code).all()
        transfers=db.query(MockInternationalTransfer).order_by(MockInternationalTransfer.id.desc()).limit(20).all()

        return {
            "wallets": [
                {
                    "id": w.id, "provider": w.provider, "phone": w.phone,
                    "name": w.name, "balance": w.balance, "is_active": w.is_active,
                }
                for w in wallets
            ],
            "banks": [
                {
                    "id": b.id, "bank_code": b.bank_code, "account_number": b.account_number,
                    "iban": b.iban, "account_title": b.account_title,
                    "balance": b.balance, "is_active": b.is_active,
                }
                for b in banks
            ],
            "bills": [
                {
                    "id": b.id, "company": b.company, "consumer_id": b.consumer_id,
                    "customer_name": b.customer_name, "amount_due": b.amount_due,
                    "due_date": b.due_date, "bill_month": b.bill_month, "is_paid": b.is_paid,
                }
                for b in bills
            ],
            "challans": [
                {
                    "id": c.id, "department": c.department, "psid": c.psid,
                    "description": c.description, "amount": c.amount,
                    "due_date": c.due_date, "is_paid": c.is_paid,
                }
                for c in challans
            ],
            "insurance": [
                {
                    "policy_number": p.policy_number, "policy_type": p.policy_type,
                    "provider": p.provider, "customer_name": p.customer_name,
                    "premium_amount": p.premium_amount, "coverage_amount": p.coverage_amount,
                    "next_due_date": p.next_due_date, "is_active": p.is_active,
                }
                for p in insurance
            ],
            "stocks": [
                {
                    "symbol": s.symbol, "company_name": s.company_name, "sector": s.sector,
                    "price": s.price, "change": s.change, "change_percent": s.change_percent,
                    "volume": s.volume, "market_cap": s.market_cap,
                }
                for s in stocks
            ],
            "mutual_funds": [
                {
                    "fund_code": f.fund_code, "fund_name": f.fund_name, "provider": f.provider,
                    "category": f.category, "nav": f.nav, "ytd_return": f.ytd_return,
                    "risk_level": f.risk_level,
                }
                for f in funds
            ],
            "international_transfers": [
                {
                    "id": t.id, "provider": t.provider, "reference": t.reference,
                    "receiver_name": t.receiver_name, "country": t.country,
                    "amount_pkr": t.amount_pkr, "amount_fx": t.amount_fx,
                    "currency": t.currency, "status": t.status,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in transfers
            ],
            "summary": {
                "total_wallets":    len(wallets),
                "total_banks":      len(banks),
                "total_bills":      len(bills),
                "bills_paid":       sum(1 for b in bills if b.is_paid),
                "bills_pending":    sum(1 for b in bills if not b.is_paid),
                "total_challans":   len(challans),
                "challans_paid":    sum(1 for c in challans if c.is_paid),
                "total_stocks":     len(stocks),
                "total_funds":      len(funds),
                "wallet_balance_total": round(sum(w.balance for w in wallets), 2),
                "bank_balance_total":   round(sum(b.balance for b in banks), 2),
            },
        }
    finally:
        db.close()


@router.post("/reseed")
def reseed_mock_db():
    """
    Wipe all mock SQLite tables and reseed with the latest data.
    Useful after code updates to Railway without a fresh deployment.
    """
    db = SessionLocal()
    try:
        db.query(MockWalletAccount).delete()
        db.query(MockBankAccount).delete()
        db.query(MockBill).delete()
        db.query(MockChallan).delete()
        db.query(MockInsurancePolicy).delete()
        db.query(MockStock).delete()
        db.query(MockMutualFund).delete()
        db.query(MockInternationalTransfer).delete()
        db.commit()
        seed_all(db)
        wallets = db.query(MockWalletAccount).count()
        banks   = db.query(MockBankAccount).count()
        bills   = db.query(MockBill).count()
        return {
            "success": True,
            "message": "Mock DB wiped and reseeded",
            "counts":  {"wallets": wallets, "banks": banks, "bills": bills},
        }
    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e)}
    finally:
        db.close()

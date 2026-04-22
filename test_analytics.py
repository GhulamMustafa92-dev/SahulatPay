import asyncio
from sqlalchemy import select, func, desc, extract, cast, Date
from datetime import datetime, timezone, timedelta
from database import AsyncSessionLocal
from models.transaction import Transaction
from models.user import User

async def main():
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        seven_days_ago = now - timedelta(days=7)

        # 1. Transaction Volume (last 7 days grouped by date)
        q_vol = select(
            cast(Transaction.created_at, Date).label("date"),
            func.sum(Transaction.amount).label("total")
        ).where(
            Transaction.created_at >= seven_days_ago,
            Transaction.status == "completed"
        ).group_by(cast(Transaction.created_at, Date)).order_by(cast(Transaction.created_at, Date))
        
        res = await db.execute(q_vol)
        vol_data = res.all()
        print("VOL:", vol_data)

        # 2. Buyer Category (Users by account_type)
        q_cat = select(
            User.account_type,
            func.count(User.id)
        ).group_by(User.account_type)
        res = await db.execute(q_cat)
        print("CAT:", res.all())

        # 3. Purpose Breakdown
        q_pur = select(
            Transaction.purpose,
            func.count(Transaction.id)
        ).where(Transaction.purpose != None).group_by(Transaction.purpose).order_by(desc(func.count(Transaction.id))).limit(6)
        res = await db.execute(q_pur)
        print("PUR:", res.all())

        # 4. Status Breakdown (Health)
        q_sts = select(
            Transaction.status,
            func.count(Transaction.id)
        ).group_by(Transaction.status)
        res = await db.execute(q_sts)
        print("STS:", res.all())

asyncio.run(main())

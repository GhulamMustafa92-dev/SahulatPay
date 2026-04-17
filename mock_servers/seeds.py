"""Seed all mock SQLite databases with realistic Pakistani data."""
from datetime import date, timedelta
from mock_servers.models import (
    MockWalletAccount, MockBankAccount, MockBill, MockChallan,
    MockCNIC, MockInsurancePolicy, MockStock, MockMutualFund,
)


def seed_all(db):
    """Run all seeders. Safe to call multiple times — skips if already seeded."""
    seed_wallet_accounts(db)
    seed_bank_accounts(db)
    seed_bills(db)
    seed_challans(db)
    seed_cnics(db)
    seed_insurance(db)
    seed_stocks(db)
    seed_mutual_funds(db)
    db.commit()
    print("[mock_seeds] all mock data seeded")


def seed_wallet_accounts(db):
    if db.query(MockWalletAccount).count() > 0:
        return
    accounts = [
        ("jazzcash",  "+923001234567", "Ali Hassan",         8500),
        ("jazzcash",  "+923009876543", "Sara Khan",          12000),
        ("jazzcash",  "+923001112233", "Muhammad Asif",      3200),
        ("jazzcash",  "+923004445566", "Fatima Malik",       25000),
        ("easypaisa", "+923101234567", "Usman Iqbal",        7800),
        ("easypaisa", "+923109876543", "Ayesha Raza",        15500),
        ("easypaisa", "+923101112233", "Bilal Ahmed",        4100),
        ("easypaisa", "+923104445566", "Nadia Siddiqui",     9000),
        ("sadapay",   "+923201234567", "Hamza Tariq",        31000),
        ("sadapay",   "+923209876543", "Zainab Ali",         5500),
        ("sadapay",   "+923201112233", "Omer Farooq",        18000),
        ("nayapay",   "+923301234567", "Rabia Noor",         22000),
        ("nayapay",   "+923309876543", "Kamran Sheikh",      11000),
        ("nayapay",   "+923301112233", "Sana Butt",          6700),
        ("upaisa",    "+923321234567", "Waqas Hussain",      4500),
        ("upaisa",    "+923329876543", "Hina Javed",         13500),
        ("upaisa",    "+923321112233", "Tariq Mehmood",      28000),
    ]
    for provider, phone, name, balance in accounts:
        db.add(MockWalletAccount(provider=provider, phone=phone, name=name, balance=balance))


def seed_bank_accounts(db):
    if db.query(MockBankAccount).count() > 0:
        return
    accounts = [
        ("hbl",        "01234567890123", "PK36HABB0000001234567890", "Muhammad Ghulam Mustafa", 125000),
        ("hbl",        "01234567891234", "PK36HABB0000001234567891", "Amna Tariq",               75000),
        ("mcb",        "1234567890",     "PK24MUCB0002460078000034", "Rashid Karim",             250000),
        ("mcb",        "9876543210",     "PK24MUCB0002460078000035", "Shazia Rehman",            88000),
        ("ubl",        "0011223344556",  "PK60UNIL0109000000123456", "Faisal Naeem",             180000),
        ("ubl",        "6655443322110",  "PK60UNIL0109000000123457", "Mariam Zahid",             45000),
        ("meezan",     "02012345678901", "PK07MEZN0001090109876543", "Abdul Rehman",             320000),
        ("meezan",     "02012345678902", "PK07MEZN0001090109876544", "Khadija Hussain",          95000),
        ("allied",     "10020012345678", "PK55ABPA0010020012345678", "Imran Butt",               160000),
        ("alfalah",    "0110123456789",  "PK29ALFH0010001000684560", "Sobia Khalid",             210000),
        ("faysal",     "0001012345678",  "PK45FAYS3756220600000001", "Junaid Shah",              55000),
        ("habibmetro", "0101234567890",  "PK38MPBL0000001234000001", "Rabia Anwar",              140000),
        ("js",         "1001234567890",  "PK07JSBL9999888000000001", "Salman Akhtar",            77000),
        ("scb",        "01234567-8",     "PK05SCBL0000001123456702", "Natasha Mirza",            410000),
    ]
    for bank, acct, iban, title, bal in accounts:
        db.add(MockBankAccount(bank_code=bank, account_number=acct, iban=iban, account_title=title, balance=bal))


def seed_bills(db):
    if db.query(MockBill).count() > 0:
        return
    today = date.today()
    bills = [
        ("ssgc",       "1234567890", "Muhammad Tariq",    3250.50,  str(today + timedelta(days=5)),  "March 2026"),
        ("ssgc",       "0987654321", "Fatima Baig",       1890.00,  str(today + timedelta(days=8)),  "March 2026"),
        ("sngpl",      "1122334455", "Khalid Mahmood",    4100.75,  str(today + timedelta(days=3)),  "March 2026"),
        ("sngpl",      "5544332211", "Samina Akhtar",     2750.00,  str(today + timedelta(days=12)), "March 2026"),
        ("kelectric",  "KE-001-001", "Rizwan Ahmed",      6500.25,  str(today + timedelta(days=6)),  "March 2026"),
        ("kelectric",  "KE-002-002", "Huma Farooq",       9200.00,  str(today + timedelta(days=9)),  "March 2026"),
        ("lesco",      "LE-0001234", "Naveed Anwar",      7800.50,  str(today + timedelta(days=4)),  "March 2026"),
        ("lesco",      "LE-0005678", "Asma Raza",         5100.00,  str(today + timedelta(days=7)),  "March 2026"),
        ("iesco",      "IE-1234567", "Waseem Haider",     4300.25,  str(today + timedelta(days=10)), "March 2026"),
        ("fesco",      "FE-7654321", "Amjad Ali",         3600.75,  str(today + timedelta(days=5)),  "March 2026"),
        ("mepco",      "ME-1234567", "Rukhsana Bibi",     2900.00,  str(today + timedelta(days=15)), "March 2026"),
        ("ptcl",       "92511234567","Arshad Hussain",    1500.00,  str(today + timedelta(days=8)),  "March 2026"),
        ("ptcl",       "92519876543","Shaista Naz",       2200.00,  str(today + timedelta(days=11)), "March 2026"),
        ("stormfiber", "SF-000111",  "Hamid Sheikh",      3000.00,  str(today + timedelta(days=3)),  "April 2026"),
        ("stormfiber", "SF-000222",  "Lubna Qureshi",     4500.00,  str(today + timedelta(days=6)),  "April 2026"),
        ("nayatel",    "NT-00123",   "Pervez Iqbal",      2800.00,  str(today + timedelta(days=7)),  "April 2026"),
        ("wapda",      "WA-0012345", "Tahir Bhatti",      8900.50,  str(today + timedelta(days=5)),  "March 2026"),
        ("wapda",      "WA-0054321", "Nargis Begum",      6700.00,  str(today + timedelta(days=9)),  "March 2026"),
    ]
    for company, cid, name, amount, due, month in bills:
        db.add(MockBill(company=company, consumer_id=cid, customer_name=name,
                        amount_due=amount, due_date=due, bill_month=month))


def seed_challans(db):
    if db.query(MockChallan).count() > 0:
        return
    today = date.today()
    challans = [
        ("FBR",      "FBR-2026-001234", "FBR-TX-001", "Income Tax Payment Q3 2026",    25000, str(today + timedelta(days=30))),
        ("FBR",      "FBR-2026-005678", "FBR-TX-002", "Sales Tax Filing March 2026",   12500, str(today + timedelta(days=15))),
        ("Traffic",  "TRF-0012345",     "TRF-001",    "Traffic Violation - Speeding",   2000,  str(today + timedelta(days=10))),
        ("Traffic",  "TRF-0067890",     "TRF-002",    "Wrong Parking Fine",              500,  str(today + timedelta(days=7))),
        ("PSID",     "PSID-9876543",    "PSI-001",    "Property Tax Payment",           18000, str(today + timedelta(days=20))),
        ("PSID",     "PSID-1234567",    "PSI-002",    "Stamp Duty Fee",                 5500,  str(today + timedelta(days=25))),
        ("Passport", "PASS-00112233",   "PP-001",     "Passport Renewal Fee",           5200,  str(today + timedelta(days=60))),
        ("NADRA",    "NADRA-001122",    "ND-001",     "CNIC Renewal Fee",                350,  str(today + timedelta(days=45))),
        ("Municipal","MC-001234567",    "MC-001",     "Water Tax Quarterly",            3200,  str(today + timedelta(days=12))),
        ("BISP",     "BISP-9990001",    "BP-001",     "BISP Registration Fee",             0,  str(today + timedelta(days=30))),
    ]
    for dept, psid, ref, desc, amount, due in challans:
        db.add(MockChallan(department=dept, psid=psid, reference=ref,
                           description=desc, amount=amount, due_date=due))


def seed_cnics(db):
    if db.query(MockCNIC).count() > 0:
        return
    cnics = [
        ("35202-1234567-1", "Muhammad Ghulam Mustafa", "Abdul Rashid",    "1990-05-15", "House 12, Street 4, Gulberg III, Lahore",    "valid"),
        ("42201-9876543-2", "Fatima Noor Hussain",     "Noor Muhammad",   "1995-08-22", "Flat 5B, Block 7, Gulshan-e-Iqbal, Karachi", "valid"),
        ("37405-1122334-3", "Muhammad Bilal Khan",     "Wazir Khan",      "1988-03-10", "Village Chak 12, Tehsil Jhang, Faisalabad",  "valid"),
        ("61101-5544332-4", "Zainab Ali Raza",         "Ali Raza",        "2000-12-01", "House 45, F-8/3, Islamabad",                "valid"),
        ("35202-7890123-5", "Imran Sheikh",            "Liaquat Sheikh",  "1985-07-19", "Street 6, Model Town, Lahore",              "valid"),
        ("42301-3456789-6", "Sana Malik",              "Tariq Malik",     "1993-11-28", "Plot 22, DHA Phase 6, Karachi",             "expired"),
        ("35104-9870123-7", "Ahmed Raza",              "Raza Khan",       "1975-02-14", "House 1, Canal Road, Faisalabad",           "valid"),
    ]
    for cnic, name, father, dob, address, status in cnics:
        db.add(MockCNIC(cnic=cnic, full_name=name, father_name=father,
                        dob=dob, address=address, status=status))


def seed_insurance(db):
    if db.query(MockInsurancePolicy).count() > 0:
        return
    today = date.today()
    policies = [
        ("JL-2026-001234", "life",    "Jubilee Life",    "Muhammad Tariq",  2500, 2500000, str(today + timedelta(days=30))),
        ("SL-2025-009876", "life",    "State Life",      "Fatima Hussain",  1800, 1500000, str(today + timedelta(days=15))),
        ("EF-2026-001122", "health",  "EFU Health",      "Bilal Khan",      3200, 1000000, str(today + timedelta(days=45))),
        ("AJ-2026-005566", "vehicle", "Adamjee",         "Usman Qureshi",   4500,  800000, str(today + timedelta(days=20))),
        ("TP-2026-007788", "travel",  "TPL Insurance",   "Sara Ahmed",       900,  500000, str(today + timedelta(days=10))),
        ("JL-2026-003344", "home",    "Jubilee General", "Rizwan Shah",     1200,  750000, str(today + timedelta(days=60))),
    ]
    for pno, ptype, provider, cname, premium, coverage, due in policies:
        db.add(MockInsurancePolicy(policy_number=pno, policy_type=ptype, provider=provider,
                                   customer_name=cname, premium_amount=premium,
                                   coverage_amount=coverage, next_due_date=due))


def seed_stocks(db):
    if db.query(MockStock).count() > 0:
        return
    stocks = [
        ("ENGRO",  "Engro Corporation",          "Fertilizer",  338.50,  +5.20,  +1.56, 1250000, 480000000000),
        ("HBL",    "Habib Bank Limited",          "Banking",     145.30,  -2.10,  -1.42,  890000, 215000000000),
        ("LUCK",   "Lucky Cement",                "Cement",      698.75, +12.40,  +1.81,  560000, 530000000000),
        ("FFC",    "Fauji Fertilizer Company",    "Fertilizer",  118.25,  -0.75,  -0.63, 1800000, 190000000000),
        ("PSO",    "Pakistan State Oil",          "Energy",      389.60,  +8.90,  +2.34, 2100000, 420000000000),
        ("MCB",    "MCB Bank",                    "Banking",     198.45,  +1.35,  +0.68,  720000, 250000000000),
        ("OGDC",   "Oil & Gas Dev. Company",      "Energy",      154.20,  -3.50,  -2.22, 3400000, 660000000000),
        ("PPL",    "Pakistan Petroleum Limited",  "Energy",      118.90,  +0.60,  +0.51, 1100000, 195000000000),
        ("EFERT",  "Engro Fertilizers",           "Fertilizer",   96.75,  +1.20,  +1.25, 2200000, 160000000000),
        ("UBL",    "United Bank Limited",         "Banking",     185.10,  +2.80,  +1.54,  950000, 230000000000),
        ("MLCF",   "Maple Leaf Cement",           "Cement",       68.30,  -0.90,  -1.30, 4500000,  78000000000),
        ("MEBL",   "Meezan Bank",                 "Banking",     157.60,  +3.10,  +2.01,  780000, 200000000000),
        ("KOHC",   "Kohat Cement",                "Cement",      151.20,  +4.50,  +3.07,  320000, 125000000000),
        ("COLG",   "Colgate-Palmolive Pakistan",  "FMCG",        2450.00, +45.0,  +1.87,   85000, 290000000000),
        ("NESTLE", "Nestlé Pakistan",             "FMCG",        6980.00, -120.0, -1.69,   42000, 350000000000),
    ]
    for sym, name, sector, price, chg, chg_pct, vol, mcap in stocks:
        db.add(MockStock(symbol=sym, company_name=name, sector=sector, price=price,
                         change=chg, change_percent=chg_pct, volume=vol, market_cap=mcap))


def seed_mutual_funds(db):
    if db.query(MockMutualFund).count() > 0:
        return
    funds = [
        ("NBP-EF",  "NBP Fullerton Equity Fund",       "NBP",    "equity",        125.60, +18.5, "high"),
        ("UBL-SF",  "UBL Stock Advantage Fund",         "UBL",    "equity",         98.40, +15.2, "high"),
        ("HBL-MF",  "HBL Multi Asset Fund",             "HBL",    "balanced",      112.30, +12.8, "medium"),
        ("MEZ-IF",  "Meezan Islamic Income Fund",       "Meezan", "islamic",        50.25,  +9.1, "low"),
        ("MEZ-EF",  "Meezan Islamic Equity Fund",       "Meezan", "islamic",       145.80, +22.4, "high"),
        ("MCB-CF",  "MCB Cash Management Optimizer",    "MCB",    "money_market",   10.15,  +5.8, "low"),
        ("UBL-CF",  "UBL Liquidity Plus Fund",          "UBL",    "money_market",   10.08,  +5.5, "low"),
        ("NBP-IF",  "NBP Islamic Saver Fund",           "NBP",    "islamic",        10.32,  +6.2, "low"),
        ("HBL-EF",  "HBL Islamic Equity Fund",          "HBL",    "islamic",       189.50, +19.7, "high"),
        ("ALFL-BF", "Alfalah Income Multiplier Fund",   "Alfalah","income",         58.75, +10.3, "medium"),
    ]
    for code, name, provider, category, nav, ytd, risk in funds:
        db.add(MockMutualFund(fund_code=code, fund_name=name, provider=provider,
                              category=category, nav=nav, ytd_return=ytd, risk_level=risk))

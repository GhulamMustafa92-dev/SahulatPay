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
    try:
        db.commit()
        print("[mock_seeds] all mock data seeded")
    except Exception:
        db.rollback()
        print("[mock_seeds] seed skipped (already seeded by another worker)")


def seed_wallet_accounts(db):
    if db.query(MockWalletAccount).count() > 0:
        return
    accounts = [
        # JazzCash (12 accounts)
        ("jazzcash",  "+923001234567", "Ali Hassan",            8500),
        ("jazzcash",  "+923009876543", "Sara Khan",             12000),
        ("jazzcash",  "+923001112233", "Muhammad Asif",         3200),
        ("jazzcash",  "+923004445566", "Fatima Malik",          25000),
        ("jazzcash",  "+923007788990", "Shahid Afridi",         9800),
        ("jazzcash",  "+923003344556", "Iqra Aziz",             6400),
        ("jazzcash",  "+923006677889", "Babar Azam",            19500),
        ("jazzcash",  "+923002233445", "Nimra Khan",            4700),
        ("jazzcash",  "+923005511223", "Asim Azhar",            14200),
        ("jazzcash",  "+923008833445", "Minal Khan",            7300),
        ("jazzcash",  "+923001998877", "Ali Zafar",             31000),
        ("jazzcash",  "+923004321987", "Hania Aamir",           5600),
        # EasyPaisa (12 accounts)
        ("easypaisa", "+923101234567", "Usman Iqbal",           7800),
        ("easypaisa", "+923109876543", "Ayesha Raza",           15500),
        ("easypaisa", "+923101112233", "Bilal Ahmed",           4100),
        ("easypaisa", "+923104445566", "Nadia Siddiqui",        9000),
        ("easypaisa", "+923108899001", "Asad Ali",              11200),
        ("easypaisa", "+923105566778", "Hira Baig",             6300),
        ("easypaisa", "+923102233445", "Zohaib Hassan",         22000),
        ("easypaisa", "+923106677889", "Sadaf Kanwal",          3900),
        ("easypaisa", "+923103344556", "Humayun Saeed",         18700),
        ("easypaisa", "+923107788990", "Yumna Zaidi",           8400),
        ("easypaisa", "+923101998877", "Waseem Akram",          41000),
        ("easypaisa", "+923104321987", "Mehreen Raheel",        5200),
        # SadaPay (10 accounts)
        ("sadapay",   "+923201234567", "Hamza Tariq",           31000),
        ("sadapay",   "+923209876543", "Zainab Ali",            5500),
        ("sadapay",   "+923201112233", "Omer Farooq",           18000),
        ("sadapay",   "+923205566778", "Arslan Naseer",         14000),
        ("sadapay",   "+923208899001", "Maryam Nawaz",          27500),
        ("sadapay",   "+923202233445", "Fawad Khan",            8900),
        ("sadapay",   "+923203344556", "Aiza Baig",             12600),
        ("sadapay",   "+923207788990", "Hassan Ali",            6100),
        ("sadapay",   "+923201998877", "Sadia Islam",           23000),
        ("sadapay",   "+923204321987", "Azfar Rehman",          9800),
        # NayaPay (10 accounts)
        ("nayapay",   "+923301234567", "Rabia Noor",            22000),
        ("nayapay",   "+923309876543", "Kamran Sheikh",         11000),
        ("nayapay",   "+923301112233", "Sana Butt",             6700),
        ("nayapay",   "+923305566778", "Danish Taimoor",        16500),
        ("nayapay",   "+923308899001", "Aiman Zaman",           9300),
        ("nayapay",   "+923302233445", "Junaid Khan",           4200),
        ("nayapay",   "+923303344556", "Urwa Hocane",           18900),
        ("nayapay",   "+923307788990", "Fahad Mustafa",         35000),
        ("nayapay",   "+923301998877", "Mehwish Hayat",         7800),
        ("nayapay",   "+923304321987", "Ahad Raza Mir",         11500),
        # UPaisa (10 accounts)
        ("upaisa",    "+923321234567", "Waqas Hussain",         4500),
        ("upaisa",    "+923329876543", "Hina Javed",            13500),
        ("upaisa",    "+923321112233", "Tariq Mehmood",         28000),
        ("upaisa",    "+923325566778", "Shehzad Roy",           7600),
        ("upaisa",    "+923328899001", "Adnan Siddiqui",        19000),
        ("upaisa",    "+923322233445", "Saba Qamar",            5100),
        ("upaisa",    "+923323344556", "Noman Ijaz",            14300),
        ("upaisa",    "+923327788990", "Ushna Shah",            8900),
        ("upaisa",    "+923321998877", "Zahid Ahmed",           22500),
        ("upaisa",    "+923324321987", "Sana Javed",            6700),
    ]
    for provider, phone, name, balance in accounts:
        db.add(MockWalletAccount(provider=provider, phone=phone, name=name, balance=balance))


def seed_bank_accounts(db):
    if db.query(MockBankAccount).count() > 0:
        return
    accounts = [
        # HBL
        ("hbl",        "01234567890123", "PK36HABB0000001234567890", "Muhammad Ghulam Mustafa", 125000),
        ("hbl",        "01234567891234", "PK36HABB0000001234567891", "Amna Tariq",               75000),
        ("hbl",        "01234567892345", "PK36HABB0000001234567892", "Kamran Akbar",             190000),
        # MCB
        ("mcb",        "1234567890",     "PK24MUCB0002460078000034", "Rashid Karim",             250000),
        ("mcb",        "9876543210",     "PK24MUCB0002460078000035", "Shazia Rehman",             88000),
        ("mcb",        "5555666677",     "PK24MUCB0002460078000036", "Tariq Hussain",            135000),
        # UBL
        ("ubl",        "0011223344556",  "PK60UNIL0109000000123456", "Faisal Naeem",             180000),
        ("ubl",        "6655443322110",  "PK60UNIL0109000000123457", "Mariam Zahid",              45000),
        ("ubl",        "1122334455667",  "PK60UNIL0109000000123458", "Sajid Mehmood",            220000),
        # Meezan
        ("meezan",     "02012345678901", "PK07MEZN0001090109876543", "Abdul Rehman",             320000),
        ("meezan",     "02012345678902", "PK07MEZN0001090109876544", "Khadija Hussain",           95000),
        ("meezan",     "02012345678903", "PK07MEZN0001090109876545", "Aamir Liaqat",             175000),
        # Allied
        ("allied",     "10020012345678", "PK55ABPA0010020012345678", "Imran Butt",               160000),
        ("allied",     "10020087654321", "PK55ABPA0010020087654321", "Saima Khalid",              68000),
        # Alfalah
        ("alfalah",    "0110123456789",  "PK29ALFH0010001000684560", "Sobia Khalid",             210000),
        ("alfalah",    "0110987654321",  "PK29ALFH0010001000684561", "Nauman Ijaz",              115000),
        # Faysal
        ("faysal",     "0001012345678",  "PK45FAYS3756220600000001", "Junaid Shah",               55000),
        ("faysal",     "0001087654321",  "PK45FAYS3756220600000002", "Farah Naz",                 92000),
        # Habib Metro
        ("habibmetro", "0101234567890",  "PK38MPBL0000001234000001", "Rabia Anwar",              140000),
        ("habibmetro", "0109876543210",  "PK38MPBL0000001234000002", "Asif Zardari Jr",           83000),
        # JS Bank
        ("js",         "1001234567890",  "PK07JSBL9999888000000001", "Salman Akhtar",             77000),
        ("js",         "1009876543210",  "PK07JSBL9999888000000002", "Nadia Hussain",             49000),
        # SCB
        ("scb",        "01234567-8",     "PK05SCBL0000001123456702", "Natasha Mirza",            410000),
        ("scb",        "09876543-2",     "PK05SCBL0000001123456703", "Bilal Chaudhry",           265000),
        # Silk Bank (new)
        ("silk",       "SLK-0001234",    "PK71SILK0001000001234567", "Arshad Waheed",             58000),
        ("silk",       "SLK-0005678",    "PK71SILK0001000005678901", "Uzma Gillani",              34000),
        # Askari Bank (new)
        ("askari",     "ASK-1122334",    "PK07ASCM0000001122334455", "Capt Waqas Retd",           99000),
        ("askari",     "ASK-5566778",    "PK07ASCM0000005566778899", "Rubab Iqbal",               47000),
        # Soneri Bank (new)
        ("soneri",     "SON-2233445",    "PK56SONE0002233445566677", "Ghulam Abbas",              72000),
        ("soneri",     "SON-6677889",    "PK56SONE0006677889900112", "Kishwar Naheed",             31000),
        # Bank Al-Habib (new)
        ("bahl",       "BAHL-001234",    "PK61BAHL0001000001234567", "Syed Omer Farooq",         185000),
        ("bahl",       "BAHL-005678",    "PK61BAHL0001000005678901", "Amna Babar",                63000),
    ]
    for bank, acct, iban, title, bal in accounts:
        db.add(MockBankAccount(bank_code=bank, account_number=acct, iban=iban, account_title=title, balance=bal))


def seed_bills(db):
    if db.query(MockBill).count() > 0:
        return
    today = date.today()
    bills = [
        # SSGC
        ("ssgc",       "1234567890",  "Muhammad Tariq",    3250.50,  str(today + timedelta(days=5)),  "March 2026"),
        ("ssgc",       "0987654321",  "Fatima Baig",       1890.00,  str(today + timedelta(days=8)),  "March 2026"),
        ("ssgc",       "1122334455",  "Irfan Siddiqui",    2640.00,  str(today + timedelta(days=12)), "April 2026"),
        ("ssgc",       "9988776655",  "Sadia Rasheed",     4100.00,  str(today + timedelta(days=6)),  "April 2026"),
        # SNGPL
        ("sngpl",      "1122334455",  "Khalid Mahmood",    4100.75,  str(today + timedelta(days=3)),  "March 2026"),
        ("sngpl",      "5544332211",  "Samina Akhtar",     2750.00,  str(today + timedelta(days=12)), "March 2026"),
        ("sngpl",      "2233445566",  "Aslam Niazi",       3300.00,  str(today + timedelta(days=9)),  "April 2026"),
        ("sngpl",      "6677889900",  "Razia Sultana",     1980.00,  str(today + timedelta(days=14)), "April 2026"),
        # K-Electric
        ("kelectric",  "KE-001-001",  "Rizwan Ahmed",      6500.25,  str(today + timedelta(days=6)),  "March 2026"),
        ("kelectric",  "KE-002-002",  "Huma Farooq",       9200.00,  str(today + timedelta(days=9)),  "March 2026"),
        ("kelectric",  "KE-003-003",  "Shoaib Malik",      5700.00,  str(today + timedelta(days=7)),  "April 2026"),
        ("kelectric",  "KE-004-004",  "Zubaida Tariq",     8100.50,  str(today + timedelta(days=11)), "April 2026"),
        # LESCO
        ("lesco",      "LE-0001234",  "Naveed Anwar",      7800.50,  str(today + timedelta(days=4)),  "March 2026"),
        ("lesco",      "LE-0005678",  "Asma Raza",         5100.00,  str(today + timedelta(days=7)),  "March 2026"),
        ("lesco",      "LE-0009012",  "Babar Rana",        6200.00,  str(today + timedelta(days=10)), "April 2026"),
        ("lesco",      "LE-0003456",  "Shagufta Perveen",  4350.00,  str(today + timedelta(days=5)),  "April 2026"),
        # IESCO
        ("iesco",      "IE-1234567",  "Waseem Haider",     4300.25,  str(today + timedelta(days=10)), "March 2026"),
        ("iesco",      "IE-9876543",  "Misbah Ul Haq",     3800.00,  str(today + timedelta(days=8)),  "April 2026"),
        # FESCO
        ("fesco",      "FE-7654321",  "Amjad Ali",         3600.75,  str(today + timedelta(days=5)),  "March 2026"),
        ("fesco",      "FE-1234567",  "Rukhsana Parveen",  2850.00,  str(today + timedelta(days=13)), "April 2026"),
        # MEPCO
        ("mepco",      "ME-1234567",  "Rukhsana Bibi",     2900.00,  str(today + timedelta(days=15)), "March 2026"),
        ("mepco",      "ME-9876543",  "Ghulam Rasool",     3450.00,  str(today + timedelta(days=18)), "April 2026"),
        # PESCO
        ("pesco",      "PE-0011223",  "Shah Faisal",       4700.00,  str(today + timedelta(days=7)),  "March 2026"),
        ("pesco",      "PE-0044556",  "Nusrat Begum",      3100.00,  str(today + timedelta(days=10)), "April 2026"),
        # HESCO
        ("hesco",      "HE-1122334",  "Ali Gohar",         5200.00,  str(today + timedelta(days=8)),  "March 2026"),
        ("hesco",      "HE-5566778",  "Marvi Memon",       4600.00,  str(today + timedelta(days=12)), "April 2026"),
        # PTCL
        ("ptcl",       "92511234567", "Arshad Hussain",    1500.00,  str(today + timedelta(days=8)),  "March 2026"),
        ("ptcl",       "92519876543", "Shaista Naz",       2200.00,  str(today + timedelta(days=11)), "March 2026"),
        ("ptcl",       "92513344556", "Zahid Hamid",       1800.00,  str(today + timedelta(days=9)),  "April 2026"),
        # StormFiber
        ("stormfiber", "SF-000111",   "Hamid Sheikh",      3000.00,  str(today + timedelta(days=3)),  "April 2026"),
        ("stormfiber", "SF-000222",   "Lubna Qureshi",     4500.00,  str(today + timedelta(days=6)),  "April 2026"),
        ("stormfiber", "SF-000333",   "Rashid Minhas",     3500.00,  str(today + timedelta(days=8)),  "May 2026"),
        # Nayatel
        ("nayatel",    "NT-00123",    "Pervez Iqbal",      2800.00,  str(today + timedelta(days=7)),  "April 2026"),
        ("nayatel",    "NT-00456",    "Saira Bano",        3200.00,  str(today + timedelta(days=10)), "May 2026"),
        # WAPDA
        ("wapda",      "WA-0012345",  "Tahir Bhatti",      8900.50,  str(today + timedelta(days=5)),  "March 2026"),
        ("wapda",      "WA-0054321",  "Nargis Begum",      6700.00,  str(today + timedelta(days=9)),  "March 2026"),
        ("wapda",      "WA-0098765",  "Imtiaz Ahmad",      7400.00,  str(today + timedelta(days=6)),  "April 2026"),
        # Sui Southern (SSGCL broadband)
        ("telenor_bb", "TNR-001122",  "Asim Bajwa",        1200.00,  str(today + timedelta(days=5)),  "April 2026"),
        ("jazz_bb",    "JZZ-334455",  "Ayesha Gulzar",      950.00,  str(today + timedelta(days=7)),  "April 2026"),
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

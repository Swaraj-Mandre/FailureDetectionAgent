"""How well the team answers on uploaded shops, with the Monitor's any-shop rules off and on.

    python ta2/evaluate_uploads.py known      the five shops the first rules were built on
    python ta2/evaluate_uploads.py blind1     three shops first used as a blind test of those rules
    python ta2/evaluate_uploads.py blind2     three more, written before the second round of work and run once after it
    add --quiet to print only the counts

Every question has a query written by us that gives the right answer. An answer counts as
    right   the correct name and number are in it
    wrong   anything else that was presented as an answer
    safe    the team said it could not get a reliable answer
and each answer is also marked when the Monitor asked the person to verify it.
Saved to traces/eval/uploads_<set>.json.
"""
import io
import json
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

import openpyxl
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError

import graph
import shops
from config import REPO_DIR
from meaning import DATE_TEXT, MONTH_NAMES

SAMPLES = REPO_DIR / "data" / "sample_uploads"
OUT_DIR = REPO_DIR / "traces" / "eval"


def csv_file(name, rows):
    return name, "\n".join(",".join(str(v) for v in r) for r in rows).encode()


def xlsx_file(name, sheets):
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    data = io.BytesIO()
    book.save(data)
    return name, data.getvalue()


def sample(*names):
    return [(n, (SAMPLES / n).read_bytes()) for n in names]


# ------------------------------------------------------------------ the five known shops

def hardware():
    r = random.Random(21)
    rows = ["Bill Number,Bill Date,Customer Name,Mobile,Product,Category,Qty,Unit Price,Discount,Net Amount,Paid,Pending".split(",")]
    stock = [("Cement bag", "Building", 390), ("Asian paint 1L", "Paint", 420), ("PVC pipe", "Plumbing", 210),
             ("Tap", "Plumbing", 350), ("Nails 1kg", "Building", 95)]
    for b in range(1, 81):
        who, day = r.choice(["Mahesh Kale", "Anita Shah", "Walk in", "Vijay Builders"]), f"{r.randint(1, 28):02}/{r.choice([8, 9, 10]):02}/2026"
        for _ in range(r.randint(1, 3)):
            n, c, p = r.choice(stock)
            qty, disc = r.randint(1, 9), r.choice([0, 0, 20])
            net = qty * p - disc
            paid = r.choice([net, net, 0])
            rows.append([f"HW/{b:03}", day, who, 9800000000 + b, n, c, qty, p, disc, net, paid, net - paid])
    return [csv_file("hardware.csv", rows)]


def mobile():
    r = random.Random(99)
    sales = [["SAI MOBILE SHOPEE"], ["Sales register"], [],
             ["Invoice No.", "Invoice Date", "Customer", "Contact No", "Model", "Brand", "Units", "Rate (₹)", "Total (₹)", "Mode", "Salesman"]]
    models = [("Redmi 13", "Xiaomi", 11999), ("Galaxy A16", "Samsung", 17499), ("Vivo Y29", "Vivo", 14999),
              ("Back cover", "Local", 199), ("Charger 33W", "Xiaomi", 999)]
    for i in range(1, 91):
        day, who = f"{r.randint(1, 28):02}-{r.choice([6, 7, 8]):02}-2026", r.choice(["Amol", "Priya", "Walk-in", "Farhan"])
        man, mode = r.choice(["Ravi", "Sneha"]), r.choice(["Cash", "UPI", "Card"])
        for m, b, p in r.sample(models, r.randint(1, 2)):
            u = r.randint(1, 3)
            sales.append([f"SM-{i:04}", day, who, 9800000000 + i, m, b, u, p, u * p, mode, man])
    bought = [["Purchase Date", "Vendor", "Model", "Quantity", "Cost"]]
    for _ in range(40):
        m, b, p = r.choice(models)
        bought.append([f"{r.randint(1, 28):02}-{r.choice([6, 7, 8]):02}-2026", r.choice(["Metro Dist", "Om Traders"]), m, r.randint(2, 10), round(p * .85)])
    return [xlsx_file("sai.xlsx", {"Orders": sales, "Purchases": bought})]


K, M, P = "kirana_sale_report", "medical_bills", "medical_stock"
SALE = "tbl_sale s JOIN tbl_item i USING(itm_id)"
KNOWN = [
    ("hardware", hardware, [
        ("What was my sale in August 2026?", "SELECT SUM(net_amount) FROM hardware WHERE bill_date LIKE '2026-08%'"),
        ("Which category brought in the most money?", "SELECT category, SUM(net_amount) v FROM hardware GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How many bills did I make?", "SELECT COUNT(DISTINCT bill_number) FROM hardware"),
        ("How much money is pending in total?", "SELECT SUM(pending) FROM hardware"),
        ("Which customer bought the most?", "SELECT customer_name, SUM(net_amount) v FROM hardware GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How many cement bags did I sell in September 2026?", "SELECT SUM(qty) FROM hardware WHERE product='Cement bag' AND bill_date LIKE '2026-09%'"),
        ("How much discount did I give in total?", "SELECT SUM(discount) FROM hardware"),
        ("What is the average amount of one bill?", "SELECT AVG(t) FROM (SELECT SUM(net_amount) t FROM hardware GROUP BY bill_number)"),
        ("How much does Vijay Builders still have to pay me?", "SELECT SUM(pending) FROM hardware WHERE customer_name='Vijay Builders'"),
        ("Which day had the highest sale?", "SELECT bill_date, SUM(net_amount) v FROM hardware GROUP BY 1 ORDER BY v DESC LIMIT 1")]),
    ("kirana", lambda: sample("kirana_sale_report.xlsx"), [
        ("What was the total sale amount in August 2026?", f"SELECT SUM(amount) FROM {K} WHERE date LIKE '2026-08%'"),
        ("Which item sold the most quantity?", f"SELECT item_name, SUM(qty) v FROM {K} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("What was the sale to Hotel Annapurna?", f"SELECT SUM(amount) FROM {K} WHERE party_name='Hotel Annapurna'"),
        ("On which day was the sale the highest?", f"SELECT date, SUM(amount) v FROM {K} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How many invoices were made in September 2026?", f"SELECT COUNT(DISTINCT invoice_no) FROM {K} WHERE date LIKE '2026-09%'"),
        ("Who owes me the most money?", f"SELECT party_name, SUM(balance_due) v FROM {K} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How much total sale came through UPI?", f"SELECT SUM(amount) FROM {K} WHERE payment_type='UPI'"),
        ("What was the average bill value per invoice?", f"SELECT AVG(t) FROM (SELECT SUM(amount) t FROM {K} GROUP BY invoice_no)")]),
    ("medical", lambda: sample("medical_stock.csv", "medical_bills.csv"), [
        ("What is the total of all bills in October 2026?", f"SELECT SUM(total) FROM {M} WHERE bill_date LIKE '2026-10%'"),
        ("Which medicine has the lowest stock?", f"SELECT medicine, stock_qty FROM {P} ORDER BY 2 LIMIT 1"),
        ("How many medicines expire before 1 January 2027?", f"SELECT COUNT(*) FROM {P} WHERE expiry_date < '2027-01-01'"),
        ("Which company's medicines earned the most?", f"SELECT s.company, SUM(b.total) v FROM {M} b JOIN {P} s USING(medicine) GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("What is the value of my current stock at MRP?", f"SELECT SUM(stock_qty*mrp) FROM {P}"),
        ("How many units of Dolo 650 were sold?", f"SELECT SUM(qty) FROM {M} WHERE medicine='Dolo 650'")]),
    ("garments", lambda: sample("garments.sql"), [
        ("Which item sold the most pieces?", f"SELECT i.itm_desc, SUM(pcs) v FROM {SALE} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("What was the total sale value in September 2026?", f"SELECT SUM(pcs*sell_price) FROM {SALE} WHERE sale_dt LIKE '2026-09%'"),
        ("Which supplier's items gave the most sale value?", f"SELECT p.sup_name, SUM(pcs*sell_price) v FROM {SALE} JOIN tbl_supplier p USING(sup_id) GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How many sales were returned?", "SELECT SUM(returned) FROM tbl_sale"),
        ("What is the total profit (selling price minus cost price) on all sales?", f"SELECT SUM(pcs*(sell_price-cost_price)) FROM {SALE}"),
        ("How many suppliers are there?", "SELECT COUNT(*) FROM tbl_supplier"),
        ("How many Drop Shoulder T-shirt pieces were sold?", f"SELECT SUM(pcs) FROM {SALE} WHERE itm_desc='Drop Shoulder T-shirt'")]),
    ("mobile", mobile, [
        ("How much did I sell in July 2026?", "SELECT SUM(total) FROM orders WHERE invoice_date LIKE '2026-07%'"),
        ("Which brand sold the most units?", "SELECT brand, SUM(units) v FROM orders GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("Which salesman made the most sales in rupees?", "SELECT salesman, SUM(total) v FROM orders GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How many invoices are there?", "SELECT COUNT(DISTINCT invoice_no) FROM orders"),
        ("How much did customers pay by card?", "SELECT SUM(total) FROM orders WHERE mode='Card'"),
        ("What is the average bill amount?", "SELECT AVG(x) FROM (SELECT SUM(total) x FROM orders GROUP BY invoice_no)"),
        ("How many Galaxy A16 phones were sold?", "SELECT SUM(units) FROM orders WHERE model='Galaxy A16'"),
        ("How much did I spend on purchases in total?", "SELECT SUM(quantity*cost) FROM purchases"),
        ("Which vendor did I buy the most from in rupees?", "SELECT vendor, SUM(quantity*cost) v FROM purchases GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("Which is the least sold model by units?", "SELECT model, SUM(units) v FROM orders GROUP BY 1 ORDER BY v LIMIT 1"),
        ("What was the sale of Xiaomi in August 2026?", "SELECT SUM(total) FROM orders WHERE brand='Xiaomi' AND invoice_date LIKE '2026-08%'"),
        ("Which customer spent the most?", "SELECT customer, SUM(total) v FROM orders GROUP BY 1 ORDER BY v DESC LIMIT 1")]),
]


# ------------------------------------------------------------------ first blind test

def restaurant():
    r = random.Random(501)
    menu = [("Misal Pav", "Snacks", 90), ("Paneer Tikka", "Starter", 240), ("Chicken Thali", "Main", 280), ("Veg Thali", "Main", 190),
            ("Masala Chai", "Drinks", 25), ("Lassi", "Drinks", 70)]
    rows = [["Bill No", "Date", "Table", "Item", "Category", "Qty", "Price", "Amount", "Waiter", "Payment"]]
    for b in range(1, 131):
        day, table = f"{r.randint(1, 28):02}/{r.choice([9, 10]):02}/2026", f"T{r.randint(1, 8)}"
        waiter, pay = r.choice(["Ganesh", "Suraj", "Imran"]), r.choice(["Cash", "UPI", "UPI", "Card"])
        for n, c, p in r.sample(menu, r.randint(1, 4)):
            q = r.randint(1, 5)
            rows.append([b, day, table, n, c, q, p, q * p, waiter, pay])
    return [csv_file("shivneri_bills.csv", rows)]


def stationery():
    r = random.Random(502)
    q = lambda v: "'" + v.replace("'", "''") + "'" if isinstance(v, str) else str(v)
    out = ["-- MySQL dump", "CREATE TABLE `customers` (`cust_id` int NOT NULL, `cust_name` varchar(50), `city` varchar(30), `phone` varchar(15), PRIMARY KEY (`cust_id`));",
           "CREATE TABLE `products` (`prod_id` int NOT NULL, `prod_name` varchar(50), `brand` varchar(30), `purchase_price` decimal(8,2), `sale_price` decimal(8,2), `stock` int, PRIMARY KEY (`prod_id`));",
           "CREATE TABLE `invoices` (`inv_no` int NOT NULL, `inv_date` date, `cust_id` int, `grand_total` decimal(10,2), `amount_paid` decimal(10,2), PRIMARY KEY (`inv_no`), FOREIGN KEY (`cust_id`) REFERENCES `customers` (`cust_id`));",
           "CREATE TABLE `invoice_items` (`line_id` int NOT NULL, `inv_no` int, `prod_id` int, `qty` int, `price` decimal(8,2), PRIMARY KEY (`line_id`), FOREIGN KEY (`inv_no`) REFERENCES `invoices` (`inv_no`), FOREIGN KEY (`prod_id`) REFERENCES `products` (`prod_id`));"]
    put = lambda table, rows: out.append(f"INSERT INTO `{table}` VALUES " + ",".join("(" + ",".join(map(q, x)) + ")" for x in rows) + ";")
    people = [("Modern School", "Pune"), ("Rekha Book Depot", "Satara"), ("City College", "Pune"), ("Anand Patil", "Kolhapur"), ("Sunrise Academy", "Satara")]
    put("customers", [(i, n, c, f"98000001{i:02}") for i, (n, c) in enumerate(people, 1)])
    goods = [("Long Notebook", "Classmate", 42, 60, 310), ("Gel Pen", "Cello", 6, 10, 900), ("Geometry Box", "Camlin", 95, 140, 60),
             ("A4 Paper Ream", "JK", 210, 270, 85), ("Sketch Pens", "Camlin", 33, 50, 12)]
    put("products", [(i, *g) for i, g in enumerate(goods, 1)])
    invoices, lines, n = [], [], 0
    for inv in range(501, 591):
        total = 0
        for pid in r.sample(range(1, 6), r.randint(1, 4)):
            n += 1
            qty, price = r.randint(1, 20), goods[pid - 1][3]
            total += qty * price
            lines.append((n, inv, pid, qty, price))
        invoices.append((inv, f"2026-{r.choice([7, 8, 9]):02}-{r.randint(1, 28):02}", r.randint(1, 5), total, r.choice([total, total, total // 2])))
    put("invoices", invoices)
    put("invoice_items", lines)
    return [("stationery.sql", "\n".join(out).encode())]


def agro():
    r = random.Random(503)
    goods = [("Urea 45kg", "IFFCO", 240, 267), ("DAP 50kg", "Coromandel", 1200, 1350), ("Cotton Seed", "Mahyco", 700, 853), ("Neem Oil 1L", "Local", 310, 400)]
    ledger = [["SHETKARI KRUSHI SEVA KENDRA"], ["Date", "Farmer", "Village", "Mobile No", "Item", "Bags", "Rate", "Bill Amount", "Jama", "Baki"]]
    villages = {"Dattu Pawar": "Wai", "Laxman Jadhav": "Karad", "Vithal More": "Wai", "Shanta Bhosale": "Phaltan", "Balu Shinde": "Karad"}
    for i in range(140):
        f = r.choice(list(villages))
        n, co, buy, sell = r.choice(goods)
        b = r.randint(1, 8)
        amount = b * sell
        jama = r.choice([amount, amount // 2, 0])
        ledger.append([f"{r.randint(1, 28):02}-{r.choice([5, 6, 7]):02}-2026", f, villages[f], 9800000200 + list(villages).index(f), n, b, sell, amount, jama, amount - jama])
    stock = [["Item", "Company", "Bags in stock", "Purchase Rate", "Sale Rate"]] + [[n, c, r.randint(5, 90), a, b] for n, c, a, b in goods]
    return [xlsx_file("krushi.xlsx", {"Udhari": ledger, "Stock": stock})]


R, S, LINES = "shivneri_bills", "udhari", "invoice_items l JOIN products p USING(prod_id)"
BLIND1 = [
    ("restaurant", restaurant, [
        ("What was my total collection in October 2026?", f"SELECT SUM(amount) FROM {R} WHERE date LIKE '2026-10%'"),
        ("Which dish sold the most plates?", f"SELECT item, SUM(qty) v FROM {R} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How many bills did we make in September 2026?", f"SELECT COUNT(DISTINCT bill_no) FROM {R} WHERE date LIKE '2026-09%'"),
        ("On average how much does one bill come to?", f"SELECT AVG(t) FROM (SELECT SUM(amount) t FROM {R} GROUP BY bill_no)"),
        ("Which waiter served the highest amount?", f"SELECT waiter, SUM(amount) v FROM {R} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How much did we earn from drinks?", f"SELECT SUM(amount) FROM {R} WHERE category='Drinks'"),
        ("How much payment came in cash?", f"SELECT SUM(amount) FROM {R} WHERE payment='Cash'"),
        ("How many cups of Masala Chai were sold?", f"SELECT SUM(qty) FROM {R} WHERE item='Masala Chai'"),
        ("Which table gave the most business?", f"SELECT table_info, SUM(amount) v FROM {R} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("What was the biggest single bill?", f"SELECT SUM(amount) v FROM {R} GROUP BY bill_no ORDER BY v DESC LIMIT 1"),
        ("Which item earned the least?", f"SELECT item, SUM(amount) v FROM {R} GROUP BY 1 ORDER BY v LIMIT 1"),
        ("What did Ganesh sell in October 2026?", f"SELECT SUM(amount) FROM {R} WHERE waiter='Ganesh' AND date LIKE '2026-10%'")]),
    ("stationery", stationery, [
        ("What was the total billing in August 2026?", "SELECT SUM(grand_total) FROM invoices WHERE inv_date LIKE '2026-08%'"),
        ("Which customer has billed the most?", "SELECT cust_name, SUM(grand_total) v FROM invoices JOIN customers USING(cust_id) GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How much money is still to be received?", "SELECT SUM(grand_total-amount_paid) FROM invoices"),
        ("What is my total profit?", f"SELECT SUM(l.qty*(l.price-p.purchase_price)) FROM {LINES}"),
        ("How many Gel Pens did I sell?", f"SELECT SUM(l.qty) FROM {LINES} WHERE prod_name='Gel Pen'"),
        ("Which brand sold the most in rupees?", f"SELECT brand, SUM(l.qty*l.price) v FROM {LINES} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("What is the average invoice value?", "SELECT AVG(grand_total) FROM invoices"),
        ("What is the total value of my stock at purchase price?", "SELECT SUM(stock*purchase_price) FROM products"),
        ("Which city gives me the most sale?", "SELECT city, SUM(grand_total) v FROM invoices JOIN customers USING(cust_id) GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How many invoices were made for Modern School?", "SELECT COUNT(*) FROM invoices JOIN customers USING(cust_id) WHERE cust_name='Modern School'"),
        ("Which product is lowest in stock?", "SELECT prod_name, stock FROM products ORDER BY stock LIMIT 1"),
        ("How much does Rekha Book Depot owe me?", "SELECT SUM(grand_total-amount_paid) FROM invoices JOIN customers USING(cust_id) WHERE cust_name='Rekha Book Depot'")]),
    ("agro", agro, [
        ("What is the total baki?", f"SELECT SUM(baki) FROM {S}"),
        ("Which farmer has the most baki?", f"SELECT farmer, SUM(baki) v FROM {S} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How many bags of Urea 45kg were sold in June 2026?", f"SELECT SUM(bags) FROM {S} WHERE item='Urea 45kg' AND date LIKE '2026-06%'"),
        ("What was the total bill amount in July 2026?", f"SELECT SUM(bill_amount) FROM {S} WHERE date LIKE '2026-07%'"),
        ("Which village bought the most in rupees?", f"SELECT village, SUM(bill_amount) v FROM {S} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How much has Dattu Pawar paid so far?", f"SELECT SUM(jama) FROM {S} WHERE farmer='Dattu Pawar'"),
        ("What is the value of the stock at sale rate?", "SELECT SUM(bags_in_stock*sale_rate) FROM stock"),
        ("Which company's items sold the most in rupees?", f"SELECT company, SUM(bill_amount) v FROM {S} JOIN stock USING(item) GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How much profit did I make on all sales?", f"SELECT SUM(bags*(rate-purchase_rate)) FROM {S} JOIN stock USING(item)"),
        ("Which item has the fewest bags left?", "SELECT item, bags_in_stock FROM stock ORDER BY 2 LIMIT 1"),
        ("How much was sold in Karad in May 2026?", f"SELECT SUM(bill_amount) FROM {S} WHERE village='Karad' AND date LIKE '2026-05%'"),
        ("In which month was the sale highest?", f"SELECT strftime('%m', date), SUM(bill_amount) v FROM {S} GROUP BY 1 ORDER BY v DESC LIMIT 1")]),
]


# ------------------------------------------------------------------ second blind test

def dairy():
    r = random.Random(601)
    farmers = ["Suresh Kadam", "Mangal Shinde", "Ashok Gaikwad", "Rohini Dairy Farm", "Nana Kale"]
    milk = [["JAI KISAN DUDH SANKALAN"], [], ["Date", "Shift", "Supplier", "Phone", "Litres", "Fat", "Rate per litre", "Amount"]]
    paid = [["Date", "Supplier", "Paid Amount", "Mode"]]
    for d in range(1, 29):
        for month in (8, 9):
            for who in r.sample(farmers, 3):
                litres, rate = r.randint(4, 30), r.choice([34, 36, 38])
                milk.append([f"{d:02}/{month:02}/2026", r.choice(["Morning", "Evening"]), who, 9800000300 + farmers.index(who), litres, r.choice([3.5, 4.0, 4.5, 6.1]), rate, litres * rate])
    for _ in range(30):
        paid.append([f"{r.randint(1, 28):02}/{r.choice([8, 9]):02}/2026", r.choice(farmers), r.randint(5, 60) * 100, r.choice(["Cash", "Bank"])])
    return [xlsx_file("dudh.xlsx", {"Milk": milk, "Payments": paid})]


def distributor():
    r = random.Random(602)
    q = lambda v: "'" + v.replace("'", "''") + "'" if isinstance(v, str) else str(v)
    out = ["CREATE TABLE tblCustomer (CustID INTEGER PRIMARY KEY, CustName TEXT, Area TEXT, MobileNo TEXT);",
           "CREATE TABLE tblItem (ItemID INTEGER PRIMARY KEY, ItemName TEXT, GroupName TEXT, MRP REAL, CostPrice REAL, StockQty INTEGER);",
           "CREATE TABLE tblBillHead (BillNo INTEGER PRIMARY KEY, BillDate TEXT, CustID INTEGER REFERENCES tblCustomer(CustID), Discount REAL, NetTotal REAL, BalanceDue REAL);",
           "CREATE TABLE tblBillDetail (DetailID INTEGER PRIMARY KEY, BillNo INTEGER REFERENCES tblBillHead(BillNo), ItemID INTEGER REFERENCES tblItem(ItemID), Qty INTEGER, Rate REAL, LineAmount REAL);"]
    put = lambda t, rows: out.extend(f"INSERT INTO {t} VALUES (" + ", ".join(map(q, x)) + ");" for x in rows)
    put("tblCustomer", [(1, "Shree Kirana", "Kothrud", "9800000401"), (2, "Balaji Stores", "Hadapsar", "9800000402"), (3, "Om Provision", "Kothrud", "9800000403"), (4, "New Mart", "Baner", "9800000404")])
    goods = [(1, "Tea 1kg", "Beverages", 520, 430, 140), (2, "Biscuit Carton", "Snacks", 960, 850, 75), (3, "Soap Box", "Home care", 1150, 1010, 40), (4, "Coffee Jar", "Beverages", 340, 275, 18), (5, "Namkeen Pack", "Snacks", 210, 160, 260)]
    put("tblItem", goods)
    heads, lines, n = [], [], 0
    for b in range(2001, 2081):
        total = 0
        for item in r.sample(goods, r.randint(1, 4)):
            n += 1
            qty = r.randint(1, 12)
            lines.append((n, b, item[0], qty, item[3], qty * item[3]))
            total += qty * item[3]
        disc = r.choice([0, 0, 50, 100])
        heads.append((b, f"2026-{r.choice([8, 9, 10]):02}-{r.randint(1, 28):02}", r.randint(1, 4), disc, total - disc, r.choice([0, 0, total - disc])))
    put("tblBillHead", heads)
    put("tblBillDetail", lines)
    return [("distributor.sql", "\n".join(out).encode())]


def salon():
    r = random.Random(603)
    services = [("Haircut", 150), ("Facial", 600), ("Hair colour", 1200), ("Threading", 40), ("Head massage", 200)]
    rows = [["Visit No", "Visit Date", "Client", "Contact", "Service", "Stylist", "Charge", "Tip", "Payment Mode", "Rating"]]
    for v in range(1, 121):
        day, who = f"{r.randint(1, 28):02}/{r.choice([7, 8, 9]):02}/2026", r.choice(["Neha", "Pooja", "Arjun", "Kavita", "Sameer"])
        mode = r.choice(["Cash", "GPay", "Card"])
        for n, c in r.sample(services, r.randint(1, 3)):
            rows.append([f"V{v:03}", day, who, 9800000500 + v, n, r.choice(["Meena", "Rakesh"]), c, r.choice([0, 0, 10, 20, 50]), mode, r.randint(2, 5)])
    return [csv_file("salon_visits.csv", rows)]


LINE = "tbl_bill_detail d JOIN tbl_item i ON i.item_id=d.item_id"
HEAD = "tbl_bill_head h JOIN tbl_customer c ON c.cust_id=h.cust_id"
V = "salon_visits"
BLIND2 = [
    ("dairy", dairy, [
        ("How many litres of milk came in August 2026?", "SELECT SUM(litres) FROM milk WHERE date LIKE '2026-08%'"),
        ("Which supplier gave the most milk?", "SELECT supplier, SUM(litres) v FROM milk GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("What is the total milk amount in September 2026?", "SELECT SUM(amount) FROM milk WHERE date LIKE '2026-09%'"),
        ("How much have I paid to Suresh Kadam?", "SELECT SUM(paid_amount) FROM payments WHERE supplier='Suresh Kadam'"),
        ("How much money was paid by bank?", "SELECT SUM(paid_amount) FROM payments WHERE mode='Bank'"),
        ("Which shift collects more milk?", "SELECT shift, SUM(litres) v FROM milk GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("What is the average fat?", "SELECT AVG(fat) FROM milk"),
        ("On which date did we collect the most milk?", "SELECT date, SUM(litres) v FROM milk GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("What is the milk bill of Nana Kale for August 2026?", "SELECT SUM(amount) FROM milk WHERE supplier='Nana Kale' AND date LIKE '2026-08%'"),
        ("Who was paid the least?", "SELECT supplier, SUM(paid_amount) v FROM payments GROUP BY 1 ORDER BY v LIMIT 1"),
        ("How many suppliers do I have?", "SELECT COUNT(DISTINCT supplier) FROM milk"),
        ("How many litres came in the evening?", "SELECT SUM(litres) FROM milk WHERE shift='Evening'")]),
    ("distributor", distributor, [
        ("What was the sale in October 2026?", "SELECT SUM(net_total) FROM tbl_bill_head WHERE bill_date LIKE '2026-10%'"),
        ("Which item brought the most money?", f"SELECT item_name, SUM(line_amount) v FROM {LINE} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("Which area has the highest sale?", f"SELECT area, SUM(net_total) v FROM {HEAD} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How much does Balaji Stores have to pay?", f"SELECT SUM(balance_due) FROM {HEAD} WHERE cust_name='Balaji Stores'"),
        ("How much total profit did I make?", f"SELECT SUM(qty*(rate-cost_price)) FROM {LINE}"),
        ("How many Coffee Jars were sold?", f"SELECT SUM(qty) FROM {LINE} WHERE item_name='Coffee Jar'"),
        ("How much discount was given in August 2026?", "SELECT SUM(discount) FROM tbl_bill_head WHERE bill_date LIKE '2026-08%'"),
        ("What is the cost value of the stock in hand?", "SELECT SUM(stock_qty*cost_price) FROM tbl_item"),
        ("Which group sold the most quantity?", f"SELECT group_name, SUM(qty) v FROM {LINE} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How many bills were made for New Mart?", f"SELECT COUNT(*) FROM {HEAD} WHERE cust_name='New Mart'"),
        ("What is the average bill?", "SELECT AVG(net_total) FROM tbl_bill_head"),
        ("Which month had the highest sale?", "SELECT strftime('%m', bill_date), SUM(net_total) v FROM tbl_bill_head GROUP BY 1 ORDER BY v DESC LIMIT 1")]),
    ("salon", salon, [
        ("How much did we earn in August 2026?", f"SELECT SUM(charge) FROM {V} WHERE visit_date LIKE '2026-08%'"),
        ("Which service earned the most?", f"SELECT service, SUM(charge) v FROM {V} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("Which stylist got the most tips?", f"SELECT stylist, SUM(tip) v FROM {V} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("How many visits were there?", f"SELECT COUNT(DISTINCT visit_no) FROM {V}"),
        ("How many haircuts did we do in September 2026?", f"SELECT COUNT(*) FROM {V} WHERE service='Haircut' AND visit_date LIKE '2026-09%'"),
        ("What is the average rating of Meena?", f"SELECT AVG(rating) FROM {V} WHERE stylist='Meena'"),
        ("How much came through GPay?", f"SELECT SUM(charge) FROM {V} WHERE payment_mode='GPay'"),
        ("Which client spent the most?", f"SELECT client, SUM(charge) v FROM {V} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
        ("What is the average amount per visit?", f"SELECT AVG(t) FROM (SELECT SUM(charge) t FROM {V} GROUP BY visit_no)"),
        ("Which service has the lowest rating on average?", f"SELECT service, AVG(rating) v FROM {V} GROUP BY 1 ORDER BY v LIMIT 1"),
        ("How much did Rakesh earn from Facial?", f"SELECT SUM(charge) FROM {V} WHERE stylist='Rakesh' AND service='Facial'"),
        ("In which month did we earn the least?", f"SELECT strftime('%m', visit_date), SUM(charge) v FROM {V} GROUP BY 1 ORDER BY v LIMIT 1")]),
]
SETS = {"known": KNOWN, "blind1": BLIND1, "blind2": BLIND2}
MONTHS = dict(zip("01 02 03 04 05 06 07 08 09 10 11 12".split(), MONTH_NAMES))


# ------------------------------------------------------------------ running and judging

def numbers(text):
    return [float(n.replace(",", "")) for n in re.findall(r"(?<!\w)(?<!\d\.)\d[\d,]*(?:\.\d+)?", text)]


def close(a, b):
    return abs(a - b) <= max(0.011, abs(b) * 0.001)


def judge(answer, row):
    """right or wrong, against the row our own query returned: (value,) or (name, value)."""
    value = row[-1]
    text = re.sub(r"\d+ retries", "", answer)
    if len(row) == 2:
        name = str(row[0])
        names = [name] + ([MONTHS[name]] if name in MONTHS else [])
        if DATE_TEXT.fullmatch(name):   # 2026-08-10 may be written as 10 August 2026
            day, month_word = int(name[8:]), MONTHS[name[5:7]]
            names += [f"{month_word} {day}", f"{day} {month_word}"] + [f"{day}{s} {o}{month_word}" for s in ("st", "nd", "rd", "th") for o in ("", "of ")]
        hit = next((n for n in names if n.lower() in text.lower()), None)
        if hit is None:
            return "wrong"
        left = re.sub(re.escape(hit), " ", text, flags=re.IGNORECASE)
        left = re.sub(r"\b202[56]\b|\btop \d\b", " ", left)
        seen = numbers(left)
        return "right" if not seen or any(close(n, value) for n in seen) else "wrong"
    return "right" if any(close(n, value) for n in numbers(text)) else "wrong"


def ask(team, question, thread):
    started = time.time()
    config = {"configurable": {"thread_id": thread}, "recursion_limit": 60}
    try:
        state = team.invoke(graph.team_turn(question), config)
    except GraphRecursionError:
        return {"answer": "(too many steps)", "events": [], "sql": [], "seconds": round(time.time() - started, 1), "stopped": True}
    sql = [c["args"].get("query") for m in state["messages"] for c in getattr(m, "tool_calls", None) or [] if c["name"] == "run_sql"]
    return {"answer": state["messages"][-1].content, "events": state["events"][1:], "sql": sql,
            "seconds": round(time.time() - started, 1), "stopped": any(e.startswith("stop:") for e in state["events"])}


def run(which, quiet):
    saved = []
    for name, make, questions in SETS[which]:
        token, about = shops.prepare(make())
        about["name"] = name.title()
        db = shops.WAITING_DIR / token / "shop.db"
        conn = sqlite3.connect(db)
        try:
            for rules in (False, True):
                shop = shops.Shop(f"eval-{name}", about["name"], db, {**about, "rules": rules})
                team = graph.build_team(checkpointer=InMemorySaver(), shop=shop, failure_log=NoLog())
                for i, (question, truth_sql) in enumerate(questions):
                    truth = conn.execute(truth_sql).fetchone()
                    result = ask(team, question, f"{rules}{i}")
                    outcome = "safe" if result["stopped"] else judge(result["answer"], truth)
                    verify = any("please verify" in e for e in result["events"])
                    saved.append({"shop": name, "rules": rules, "question": question, "outcome": outcome, "verify": verify,
                                  "truth": truth, **result})
                    if not quiet and outcome != "right":
                        print(f"{'rules ON ' if rules else 'rules off'} {outcome.upper():5} {'verify' if verify else '':6} {name}: {question}\n"
                              f"      {result['answer'][:170]!r} truth={truth}")
                        for q in result["sql"]:
                            print("      sql:", " ".join((q or "").split())[:260])
                        if rules:
                            for e in result["events"]:
                                print("        ", e[:170])
        finally:
            conn.close()
            shops.discard(token)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"uploads_{which}.json").write_text(json.dumps(saved, indent=1, ensure_ascii=False), encoding="utf-8")
    for rules in (False, True):
        mine = [r for r in saved if r["rules"] == rules]
        count = lambda o, v=None: sum(r["outcome"] == o and (v is None or r["verify"] == v) for r in mine)
        print(f"{which}, rules {'ON ' if rules else 'off'}: {len(mine)} questions, right {count('right')} (of these asked to verify {count('right', True)}), "
              f"safe {count('safe')}, wrong {count('wrong')} (of these asked to verify {count('wrong', True)}), "
              f"average {sum(r['seconds'] for r in mine) / len(mine):.1f} s")


class NoLog:
    """The evaluation must not fill the app's own failure log."""

    def add(self, *args):
        pass


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "known", "--quiet" in sys.argv)

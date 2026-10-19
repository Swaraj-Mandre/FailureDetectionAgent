"""Builds shop.db, the sample database QueryMend talks to.

Sahyadri Stores is a made-up retail shop with branches across Maharashtra.
All names, emails and phone numbers are invented. The random seed is fixed,
so every run (and every teammate's laptop) gets exactly the same data.

Usage:
    python data/make_shop_db.py
    python data/make_shop_db.py --path some/other.db
"""
import argparse
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SEED = 17
DEFAULT_PATH = Path(__file__).with_name("shop.db")

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Arjun", "Sai", "Reyansh", "Kabir", "Ishaan",
    "Rohan", "Omkar", "Pranav", "Yash", "Tanmay", "Nikhil", "Harsh", "Kunal",
    "Ananya", "Diya", "Isha", "Saanvi", "Pooja", "Sneha", "Riya", "Mrunal",
    "Gauri", "Shruti", "Neha", "Tanvi", "Aditi", "Prachi", "Kavya", "Sayali",
]
LAST_NAMES = [
    "Patil", "Kulkarni", "Deshmukh", "Joshi", "Pawar", "Shinde", "Jadhav",
    "Gokhale", "Bhosale", "Kale", "More", "Chavan", "Sawant", "Apte", "Naik",
]
CITIES = ["Pune", "Mumbai", "Nashik", "Nagpur", "Kolhapur", "Aurangabad"]

# (name, category, unit_price in rupees)
PRODUCTS = [
    ("Wireless Mouse", "Electronics", 699),
    ("USB-C Charger 25W", "Electronics", 1199),
    ("Bluetooth Earbuds", "Electronics", 2499),
    ("Power Bank 10000mAh", "Electronics", 1499),
    ("LED Desk Lamp", "Electronics", 899),
    ("Steel Water Bottle", "Home & Kitchen", 449),
    ("Non-stick Tawa", "Home & Kitchen", 1299),
    ("Pressure Cooker 3L", "Home & Kitchen", 2199),
    ("Glass Storage Jars (Set of 3)", "Home & Kitchen", 799),
    ("Cotton Bedsheet", "Home & Kitchen", 1099),
    ("A5 Notebook (Pack of 4)", "Stationery", 240),
    ("Gel Pens (Pack of 10)", "Stationery", 150),
    ("Sketch Book", "Stationery", 320),
    ("Desk Organizer", "Stationery", 549),
    ("Scientific Calculator", "Stationery", 1150),
    ("Herbal Shampoo 340ml", "Personal Care", 310),
    ("Neem Face Wash", "Personal Care", 185),
    ("Sunscreen SPF 50", "Personal Care", 499),
    ("Electric Trimmer", "Personal Care", 1799),
    ("Coconut Hair Oil 500ml", "Personal Care", 260),
    ("Masala Chai Tea 500g", "Snacks & Beverages", 290),
    ("Roasted Makhana 200g", "Snacks & Beverages", 230),
    ("Filter Coffee 250g", "Snacks & Beverages", 340),
    ("Dry Fruit Mix 500g", "Snacks & Beverages", 699),
    ("Jaggery Cookies", "Snacks & Beverages", 120),
]

ORDER_START = date(2026, 4, 1)
ORDER_END = date(2026, 9, 30)
NUM_CUSTOMERS = 40
NUM_ORDERS = 300

SCHEMA = """
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    city        TEXT NOT NULL,
    email       TEXT NOT NULL,
    phone       TEXT NOT NULL,
    joined_on   TEXT NOT NULL              -- YYYY-MM-DD
);

CREATE TABLE products (
    product_id  INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    unit_price  REAL NOT NULL CHECK (unit_price > 0)
);

CREATE TABLE orders (
    order_id       INTEGER PRIMARY KEY,
    customer_id    INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date     TEXT NOT NULL,          -- YYYY-MM-DD
    status         TEXT NOT NULL CHECK (status IN ('delivered', 'cancelled', 'pending')),
    payment_method TEXT NOT NULL CHECK (payment_method IN ('UPI', 'Card', 'Cash'))
);

CREATE TABLE order_items (
    order_id     INTEGER NOT NULL REFERENCES orders(order_id),
    product_id   INTEGER NOT NULL REFERENCES products(product_id),
    quantity     INTEGER NOT NULL CHECK (quantity > 0),
    discount_pct INTEGER NOT NULL DEFAULT 0 CHECK (discount_pct BETWEEN 0 AND 50),
    PRIMARY KEY (order_id, product_id)
);
"""


def random_date(rng, start, end):
    return start + timedelta(days=rng.randint(0, (end - start).days))


def make_customers(rng):
    rows = []
    used_names = set()
    for customer_id in range(1, NUM_CUSTOMERS + 1):
        while True:
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            if (first, last) not in used_names:
                used_names.add((first, last))
                break
        email = f"{first.lower()}.{last.lower()}@example.in"
        phone = "9" + "".join(str(rng.randint(0, 9)) for _ in range(9))
        joined = random_date(rng, date(2025, 1, 1), date(2026, 3, 31))
        rows.append((customer_id, f"{first} {last}", rng.choice(CITIES), email, phone, joined.isoformat()))
    return rows


def pick_order_date(rng):
    # Aug and Sep are festive months, so they get a bit more traffic.
    weights = {4: 1.0, 5: 0.9, 6: 0.8, 7: 1.0, 8: 1.3, 9: 1.5}
    month = rng.choices(list(weights), weights=list(weights.values()))[0]
    last_day = 30 if month in (4, 6, 9) else 31
    return date(2026, month, rng.randint(1, last_day))


def make_orders_and_items(rng):
    orders, items = [], []
    for order_id in range(1, NUM_ORDERS + 1):
        order_date = pick_order_date(rng)
        if order_date > ORDER_END - timedelta(days=5):
            status = rng.choice(["pending", "delivered"])
        else:
            status = "cancelled" if rng.random() < 0.08 else "delivered"
        payment = rng.choices(["UPI", "Card", "Cash"], weights=[6, 3, 1])[0]
        customer_id = rng.randint(1, NUM_CUSTOMERS)
        orders.append((order_id, customer_id, order_date.isoformat(), status, payment))

        for product_id in rng.sample(range(1, len(PRODUCTS) + 1), k=rng.randint(1, 4)):
            category = PRODUCTS[product_id - 1][1]
            max_qty = 6 if category == "Snacks & Beverages" else 3
            discount = rng.choice([0, 0, 0, 0, 0, 0, 5, 10, 15])
            items.append((order_id, product_id, rng.randint(1, max_qty), discount))
    return renumber_by_date(orders, items)


def renumber_by_date(orders, items):
    """Real shops hand out order numbers in time order, so a higher order_id means a newer order."""
    by_date = sorted(orders, key=lambda o: (o[2], o[0]))
    new_id = {old[0]: i for i, old in enumerate(by_date, start=1)}
    orders = [(new_id[o[0]],) + o[1:] for o in by_date]
    items = sorted(((new_id[i[0]],) + i[1:] for i in items), key=lambda i: (i[0], i[1]))
    return orders, items


def build(path):
    path = Path(path)
    if path.exists():
        path.unlink()

    rng = random.Random(SEED)
    customers = make_customers(rng)
    products = [(i, name, cat, price) for i, (name, cat, price) in enumerate(PRODUCTS, start=1)]
    orders, items = make_orders_and_items(rng)

    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)", customers)
        conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", products)
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", orders)
        conn.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?)", items)

    print(f"Built {path}")
    print(f"  customers   {len(customers)}")
    print(f"  products    {len(products)}")
    print(f"  orders      {len(orders)}")
    print(f"  order_items {len(items)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the Sahyadri Stores sample database.")
    parser.add_argument("--path", default=DEFAULT_PATH, help="where to write the .db file")
    build(parser.parse_args().path)

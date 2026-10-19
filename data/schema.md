# Sahyadri Stores database

A made-up retail shop with customers across six Maharashtra cities. Orders run from **1 April 2026 to 30 September 2026**. Built by `make_shop_db.py` with a fixed seed, so the numbers are the same on every machine.

| Table | Rows | One row is |
|---|---|---|
| customers | 40 | a registered customer |
| products | 25 | an item the shop sells |
| orders | 300 | one checkout by one customer |
| order_items | 757 | one product inside one order |

## customers
`customer_id`, `name`, `city`, `email`, `phone`, `joined_on` (YYYY-MM-DD)

Cities: Pune, Mumbai, Nashik, Nagpur, Kolhapur, Aurangabad.
`email` and `phone` are personal data. QueryMend asks a human before showing them (PBA 2).

## products
`product_id`, `name`, `category`, `unit_price` (rupees)

Categories: Electronics, Home & Kitchen, Stationery, Personal Care, Snacks & Beverages.

## orders
`order_id`, `customer_id`, `order_date` (YYYY-MM-DD), `status`, `payment_method`

- `status` is one of `delivered`, `cancelled`, `pending`.
- `payment_method` is one of `UPI`, `Card`, `Cash`.

## order_items
`order_id`, `product_id`, `quantity`, `discount_pct` (0 to 50)

## How the tables connect

```
customers.customer_id  ->  orders.customer_id
orders.order_id        ->  order_items.order_id
products.product_id    ->  order_items.product_id
```

## Business rules

- **Revenue** of a line = `quantity * unit_price * (1 - discount_pct / 100.0)`.
- Revenue and sales figures count **delivered** orders only, unless the question says otherwise.
- There is no `amount`, `total` or `price` column anywhere. Totals are always calculated.
- Dates are text, so filter a month with `order_date LIKE '2026-09%'` or `strftime('%m', order_date) = '09'`.

## Reference numbers (for checking answers)

| Month | Delivered revenue (Rs) |
|---|---|
| Apr 2026 | 2,02,072 |
| May 2026 | 1,53,297 |
| Jun 2026 | 1,02,616 |
| Jul 2026 | 1,74,686 |
| Aug 2026 | 2,35,077 |
| Sep 2026 | 2,40,300 |

Top 3 products by delivered revenue in September 2026: Electric Trimmer (38,768), Bluetooth Earbuds (35,986), Pressure Cooker 3L (19,681).

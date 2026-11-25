"""What counts as personal data, for the sample shop and for any uploaded one.

Two checks, because a query can rename a column (SELECT mobile AS m):
the column name, and whether the values themselves look like phone numbers or emails.
Names and cities are not treated as personal in this project.
"""
import re

ANYWHERE = ("email", "e_mail", "phone", "mobile", "whatsapp", "aadhaar", "aadhar", "gstin", "address")
WHOLE_WORD = {"mob", "pan", "dob", "gst_no", "contact", "contact_no", "contact_number", "account_no", "upi_id",
              "मोबाईल", "मोबाइल", "फोन", "पत्ता", "पता", "ईमेल"}

# found anywhere in a value, because a query can join columns (name || ' ' || mobile)
PHONE = re.compile(r"(?<![\d.])(\+?91[-\s]?|0)?[6-9]\d{4}[-\s]?\d{5}(?!\.?\d)")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def personal_name(column):
    name = column.lower().strip()
    return any(w in name for w in ANYWHERE) or name in WHOLE_WORD or bool({"mob", "pan", "dob"} & set(name.split("_")))


def personal_value(value):
    text = str(value).strip()
    return bool(PHONE.search(text) or EMAIL.search(text))


def personal_columns(columns, rows=()):
    """Columns that are personal by name, or because most of their filled values look personal."""
    found = []
    for i, column in enumerate(columns):
        values = [r[i] for r in rows if i < len(r) and str(r[i]) not in ("", "None", "(hidden)")][:50]
        if personal_name(column) or (values and sum(map(personal_value, values)) > len(values) / 2):
            found.append(column)
    return found

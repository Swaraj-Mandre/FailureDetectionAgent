"""Seeds the long-term memory store (state/store.db) with starting glossaries.

Two shop owners, two private glossaries. The same word can mean different
things to different people, and the namespace (user_id, "glossary") keeps
them apart.

    python ta2/seed_store.py
"""
from memory import load_terms, make_store, save_term

SEED = {
    "owner_pune": {
        "revenue": "delivered orders only, after discount",
        "festive season": "August and September 2026",
    },
    "owner_mumbai": {
        "revenue": "delivered and pending orders, after discount",
    },
}


def seed():
    store = make_store()
    for user_id, terms in SEED.items():
        for term, meaning in terms.items():
            save_term(store, user_id, term, meaning)
    for user_id in SEED:
        print(f"{user_id}: {load_terms(store, user_id)}")


if __name__ == "__main__":
    seed()

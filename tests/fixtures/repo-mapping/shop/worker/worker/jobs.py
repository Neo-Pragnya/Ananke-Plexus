"""Nightly jobs, outside the backend package."""
import sqlite3


def record_audit(action):
    conn = sqlite3.connect("shop.db")
    conn.execute("INSERT INTO audit_log (action) VALUES (?)", (action,))
    conn.commit()


def archive_orders():
    conn = sqlite3.connect("shop.db")
    rows = conn.execute("SELECT id FROM orders").fetchall()
    record_audit("archived")
    return rows

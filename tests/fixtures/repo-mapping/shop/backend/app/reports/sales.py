from sqlalchemy import text

from app.db import engine

TOTALS = """
SELECT c.email, SUM(o.total) AS total
FROM orders o
JOIN customers c ON c.id = o.customer_id
GROUP BY c.email
"""


def sales_by_customer():
    with engine.connect() as conn:
        return conn.execute(text(TOTALS)).fetchall()

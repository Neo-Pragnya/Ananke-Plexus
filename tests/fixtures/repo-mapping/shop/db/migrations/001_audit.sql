CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    action VARCHAR(64) NOT NULL,
    order_id INTEGER REFERENCES orders(id)
);

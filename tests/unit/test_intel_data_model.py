"""The data model: tables, relationships and table use, read from small repositories written here."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ananke.plexus.intel import data_model
from ananke.plexus.intel.context import RepoContext


def quickest(work: Callable[[], object]) -> float:
    """The best of three runs: backtracking is slow every time, a busy machine only sometimes."""
    times = []
    for _ in range(3):
        start = time.perf_counter()
        work()
        times.append(time.perf_counter() - start)
    return min(times)


def analyze(root: Path, files: dict[str, str]) -> dict[str, Any]:
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return data_model.analyze(RepoContext.open(root))


def table(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(t for t in report["tables"] if t["name"] == name)


def column(report: dict[str, Any], name: str, col: str) -> dict[str, Any]:
    return next(c for c in table(report, name)["columns"] if c["name"] == col)


def links(report: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    return {
        (r["from_table"], r["from_column"], r["to_table"], r["to_column"])
        for r in report["relationships"]
    }


def uses(report: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {(u["symbol"], u["table"], u["access"]) for u in report["usage"]}


MODELS = """
from sqlalchemy import Column, ForeignKey, Integer, String, Table, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class Stamped:
    created = Column(String)

class Customer(Stamped, Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    email = Column("email_address", String(255), nullable=False, unique=True)
    orders = relationship("Order")

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    note: Mapped[str | None]
    title: Mapped[str] = mapped_column(String(10))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    customer: Mapped["Customer"] = relationship()
    tags: Mapped[list["Tag"]] = relationship(secondary="order_tags")

class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = {"schema": "shop"}
    id = Column(Integer, primary_key=True)

metadata = MetaData()
order_tags = Table(
    "order_tags",
    metadata,
    Column("order_id", ForeignKey("orders.id"), primary_key=True),
    Column("tag_id", ForeignKey("shop.tags.id"), primary_key=True),
)
"""


def test_sqlalchemy_declarative_and_core(tmp_path: Path) -> None:
    report = analyze(tmp_path, {"models.py": MODELS})
    assert report["status"] == "found"
    assert {t["name"] for t in report["tables"]} == {"customers", "orders", "tags", "order_tags"}
    assert table(report, "customers")["source"] == "sqlalchemy"
    assert table(report, "customers")["id"] == "table:customers"
    assert {c["name"] for c in table(report, "customers")["columns"]} == {
        "created",
        "id",
        "email_address",
    }
    pk = column(report, "customers", "id")
    assert (pk["primary_key"], pk["nullable"], pk["type"]) == (True, False, "Integer")
    assert column(report, "customers", "email_address")["type"] == "String(255)"
    assert column(report, "customers", "email_address")["nullable"] is False
    assert column(report, "orders", "note")["nullable"] is True
    assert column(report, "orders", "title")["nullable"] is False
    assert column(report, "orders", "customer_id")["foreign_key"] == "customers.id"
    assert {c["name"] for c in table(report, "orders")["columns"]} == {
        "id",
        "note",
        "title",
        "customer_id",
    }
    assert table(report, "tags")["schema"] == "shop"
    assert ("orders", "customer_id", "customers", "id") in links(report)
    assert ("order_tags", "tag_id", "tags", "id") in links(report)
    many = [r for r in report["relationships"] if r["cardinality"] == "many-to-many"]
    assert [(r["from_table"], r["to_table"]) for r in many] == [("orders", "tags")]
    for t in report["tables"]:
        assert re.fullmatch(r"models\.py:\d+", t["evidence"])
        assert all(re.fullmatch(r"models\.py:\d+", c["evidence"]) for c in t["columns"])


def test_sqlmodel(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "m.py": """
from sqlmodel import Field, Relationship, SQLModel

class Team(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    heroes: list["Hero"] = Relationship(back_populates="team")

class Hero(SQLModel, table=True):
    __tablename__ = "heroes"
    id: int | None = Field(default=None, primary_key=True)
    nick: str | None = None
    team_id: int | None = Field(default=None, foreign_key="team.id")

class HeroRead(SQLModel):
    id: int
"""
        },
    )
    assert {t["name"] for t in report["tables"]} == {"team", "heroes"}
    assert table(report, "team")["source"] == "sqlmodel"
    assert {c["name"] for c in table(report, "team")["columns"]} == {"id", "name"}
    assert column(report, "heroes", "nick")["nullable"] is True
    assert column(report, "team", "name")["nullable"] is False
    assert links(report) == {("heroes", "team_id", "team", "id")}


def test_django(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "shop/models.py": """
from django.conf import settings
from django.db import models

class Stamped(models.Model):
    created = models.DateTimeField()
    class Meta:
        abstract = True

class Tag(models.Model):
    label = models.CharField(max_length=9, unique=True)

class Order(Stamped):
    code = models.CharField(primary_key=True, max_length=9)
    tag = models.ForeignKey(Tag, null=True, on_delete=models.CASCADE)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    parent = models.ForeignKey("self", db_column="parent_code", on_delete=models.CASCADE)
    receipt = models.OneToOneField("shop.Receipt", on_delete=models.CASCADE)
    tags = models.ManyToManyField(Tag)

class Receipt(models.Model):
    class Meta:
        db_table = "receipts"
"""
        },
    )
    names = {t["name"] for t in report["tables"]}
    assert names == {"shop_tag", "shop_order", "receipts", "auth_user"}
    assert "Stamped" not in names and "shop_stamped" not in names
    assert column(report, "shop_tag", "id")["primary_key"] is True
    assert column(report, "shop_tag", "id")["type"] == "AutoField"
    order = {c["name"] for c in table(report, "shop_order")["columns"]}
    assert order == {"created", "code", "tag_id", "owner_id", "parent_code", "receipt_id"}
    assert column(report, "shop_order", "tag_id")["nullable"] is True
    by_link = {
        (r["from_table"], r["from_column"], r["to_table"]): r["cardinality"]
        for r in report["relationships"]
    }
    assert by_link[("shop_order", "tag_id", "shop_tag")] == "many-to-one"
    assert by_link[("shop_order", "receipt_id", "receipts")] == "one-to-one"
    assert by_link[("shop_order", "parent_code", "shop_order")] == "many-to-one"
    assert by_link[("shop_order", "code", "shop_tag")] == "many-to-many"
    assert table(report, "auth_user")["source"] == "inferred-from-reference"
    assert any("auth_user" in line for line in report["limits"])


DDL = """
-- schema; with a comment
CREATE TABLE IF NOT EXISTS public.Customers (
    id INTEGER PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    key TEXT,
    note TEXT DEFAULT 'a, b (c);'
);
CREATE TABLE items (
    order_id INT NOT NULL,
    line INT NOT NULL,
    customer_id INT REFERENCES customers(id),
    other INT,
    CONSTRAINT pk_items PRIMARY KEY (order_id, line),
    FOREIGN KEY (other) REFERENCES public.customers (id) ON DELETE CASCADE,
    CHECK (line > 0)
);
ALTER TABLE items ADD COLUMN sku TEXT NOT NULL;
ALTER TABLE ONLY items ADD CONSTRAINT fk_sku FOREIGN KEY (sku) REFERENCES products (code);
ALTER TABLE items DROP COLUMN other;
CREATE VIEW v AS SELECT id FROM customers;
"""


def test_sql_ddl(tmp_path: Path) -> None:
    report = analyze(tmp_path, {"db/001.sql": DDL})
    assert {t["name"] for t in report["tables"]} == {"Customers", "items", "products"}
    customers = table(report, "Customers")
    assert (customers["schema"], customers["source"]) == ("public", "sql")
    assert customers["evidence"] == "db/001.sql:3"
    assert {c["name"] for c in customers["columns"]} == {"id", "email", "key", "note"}
    assert column(report, "Customers", "email")["nullable"] is False
    assert column(report, "Customers", "email")["type"] == "VARCHAR(255)"
    assert column(report, "Customers", "note")["nullable"] is True
    assert column(report, "items", "line")["primary_key"] is True
    assert column(report, "items", "sku")["evidence"] == "db/001.sql:18"
    assert links(report) == {
        ("items", "customer_id", "Customers", "id"),
        ("items", "other", "Customers", "id"),
        ("items", "sku", "products", "code"),
    }
    assert any("DROP, RENAME" in line for line in report["limits"])
    assert any("views" in line for line in report["limits"])


def test_tsql_batches_and_brackets(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "t.sql": """
CREATE TABLE [Person].[Address](
    [AddressID] [int] IDENTITY (1, 1) NOT NULL,
    [City] [nvarchar](30) NOT NULL
) ON [PRIMARY]
GO
CREATE TABLE [Person].[Home](
    [HomeID] [int] NOT NULL,
    [AddressID] [int] NOT NULL,
    CONSTRAINT [PK_Home] PRIMARY KEY CLUSTERED ([HomeID] ASC)
)
GO
ALTER TABLE [Person].[Home] WITH CHECK ADD CONSTRAINT [FK] FOREIGN KEY([AddressID])
REFERENCES [Person].[Address] ([AddressID])
GO
"""
        },
    )
    assert {(t["name"], t["schema"]) for t in report["tables"]} == {
        ("Address", "Person"),
        ("Home", "Person"),
    }
    assert column(report, "Home", "HomeID")["primary_key"] is True
    assert links(report) == {("Home", "AddressID", "Address", "AddressID")}


def test_alembic_reads_the_upgrade_only(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "versions/1.py": """
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
    )
    op.add_column("orders", sa.Column("note", sa.String()))
    op.create_foreign_key("fk", "orders", "people", ["note"], ["name"])
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("extra", sa.String()))

def downgrade():
    op.create_table("gone", sa.Column("id", sa.Integer()))
    op.execute("DELETE FROM orders")
"""
        },
    )
    assert "gone" not in {t["name"] for t in report["tables"]}
    assert table(report, "orders")["source"] == "alembic"
    assert {c["name"] for c in table(report, "orders")["columns"]} == {
        "id",
        "customer_id",
        "note",
        "extra",
    }
    assert column(report, "orders", "id")["primary_key"] is True
    assert ("orders", "customer_id", "customers", "id") in links(report)
    assert ("orders", "note", "people", "name") in links(report)
    assert not report["usage"]


def test_prisma(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "prisma/schema.prisma": """
datasource db { provider = "postgresql" }
enum Role { ADMIN }
model User {
  id    Int     @id @default(autoincrement())
  email String  @unique @map("email_address") // a comment
  role  Role
  bio   String?
  posts Post[]
  groups Group[]
  @@map("users")
}
model Post {
  id       Int  @id
  authorId Int  @map("author_id") @unique
  author   User @relation(fields: [authorId], references: [id])
}
model Group {
  id    Int    @id
  users User[]
}
"""
        },
    )
    assert {t["name"] for t in report["tables"]} == {"users", "Post", "Group"}
    assert table(report, "users")["source"] == "prisma"
    assert {c["name"] for c in table(report, "users")["columns"]} == {
        "id",
        "email_address",
        "role",
        "bio",
    }
    assert column(report, "users", "bio")["nullable"] is True
    assert ("Post", "author_id", "users", "id") in links(report)
    cards = {(r["from_table"], r["to_table"]): r["cardinality"] for r in report["relationships"]}
    assert cards[("Post", "users")] == "one-to-one"
    assert cards[("Group", "users")] == "many-to-many"


def test_typeorm(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "src/user.entity.ts": """
import { Entity, Column, PrimaryGeneratedColumn, OneToMany, ManyToOne, JoinColumn } from "typeorm";

@Entity("users")
export class User {
  @PrimaryGeneratedColumn()
  id: number;

  @Column({ type: "varchar", nullable: true })
  name: string;

  @OneToMany(() => OrderItem, (i) => i.owner)
  items: OrderItem[];
}

@Entity()
export class OrderItem {
  @PrimaryGeneratedColumn()
  id: number;

  @ManyToOne(() => User, (u) => u.items)
  @JoinColumn({ name: "owner_id" })
  owner: User;
}
"""
        },
    )
    assert {t["name"] for t in report["tables"]} == {"users", "order_item"}
    assert table(report, "users")["source"] == "typeorm"
    assert {c["name"] for c in table(report, "users")["columns"]} == {"id", "name"}
    assert column(report, "users", "name")["nullable"] is True
    assert ("order_item", "owner_id", "users", "id") in links(report)


SERVICE = """
from sqlalchemy import select, text, update
from .models import Customer, Order, order_tags
from . import models

class Repo:
    SQL = "SELECT id FROM customers"

    def load(self, session):
        return session.execute(select(Order)).all()

    def raw(self, conn):
        return conn.execute(text(self.SQL))

def add(session):
    session.add(Order(title="x"))

def build():
    return Customer(email="a")

def add_var(session, order: Order):
    session.add(order)

def wipe(session):
    session.query(Order).filter(Order.id == 1).delete()

def rename(session):
    session.execute(update(Customer).values(email="b"))

def via_module(session):
    return session.query(models.Customer.email).all()

def core(conn):
    conn.execute(order_tags.insert())

def seen(items, order: Order):
    items.add(order)

def nested(session):
    def inner():
        return session.get(Customer, 1)
    return inner()

STARTUP = Order(title="boot")
"""


def test_orm_use_is_attributed_to_its_function_or_method(tmp_path: Path) -> None:
    report = analyze(
        tmp_path, {"app/__init__.py": "", "app/models.py": MODELS, "app/service.py": SERVICE}
    )
    found = uses(report)
    path = "app/service.py"
    assert (f"method:{path}:Repo.load", "orders", "read") in found
    assert (f"method:{path}:Repo.raw", "customers", "read") in found
    assert (f"function:{path}:add", "orders", "write") in found
    assert (f"function:{path}:build", "customers", "write") in found
    assert (f"function:{path}:add_var", "orders", "write") in found
    assert (f"function:{path}:wipe", "orders", "write") in found
    assert (f"function:{path}:wipe", "orders", "read") not in found
    assert (f"function:{path}:rename", "customers", "write") in found
    assert (f"function:{path}:via_module", "customers", "read") in found
    assert (f"function:{path}:core", "order_tags", "write") in found
    assert (f"function:{path}:nested", "customers", "read") in found
    assert (f"file:{path}", "orders", "write") in found
    assert not [u for u in found if u[0].endswith(":seen")]
    assert all(u["origin"] == "inferred" and 0 < u["confidence"] <= 1 for u in report["usage"])
    assert all(u["evidence"].startswith(f"{path}:") for u in report["usage"])


def test_symbols_are_the_graphs_ids(tmp_path: Path) -> None:
    analyze(tmp_path, {"app/__init__.py": "", "app/models.py": MODELS, "app/service.py": SERVICE})
    ctx = RepoContext.open(tmp_path)
    nodes = {node.node_id for node in ctx.graph.nodes}
    report = data_model.analyze(ctx)
    assert report["usage"]
    assert {u["symbol"] for u in report["usage"]} <= nodes


def test_django_managers(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "shop/models.py": "from django.db import models\nclass Order(models.Model):\n    code = models.CharField()\n",
            "shop/views.py": """
from shop.models import Order

def read():
    return Order.objects.filter(code="a").first()

def make():
    return Order.objects.create(code="a")

def change():
    Order.objects.filter(code="a").update(code="b")

def persist():
    order = Order.objects.get(code="a")
    order.save()
""",
        },
    )
    found = uses(report)
    path = "shop/views.py"
    assert (f"function:{path}:read", "shop_order", "read") in found
    assert (f"function:{path}:read", "shop_order", "write") not in found
    assert (f"function:{path}:make", "shop_order", "write") in found
    assert (f"function:{path}:change", "shop_order", "write") in found
    assert (f"function:{path}:change", "shop_order", "read") not in found
    assert (f"function:{path}:persist", "shop_order", "write") in found


def test_a_class_with_a_models_name_elsewhere_isnt_the_model(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "models.py": 'from sqlalchemy import Column, Integer\nfrom db import Base\nclass Order(Base):\n    __tablename__ = "orders"\n    id = Column(Integer, primary_key=True)\n',
            "schemas.py": "class Order:\n    pass\n",
            "api.py": "from schemas import Order\n\ndef handler():\n    return Order()\n",
            "local.py": "class Order:\n    pass\n\ndef make():\n    return Order()\n",
            "third.py": "from stripe import Order\n\ndef charge():\n    return Order()\n",
        },
    )
    assert not report["usage"]


def test_sql_strings(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "queries.py": 'SHARED = "SELECT a.id FROM accounts a"\n',
            "app.py": """
import sqlite3
from sqlalchemy import text
from queries import SHARED

TOTALS = '''
SELECT c.email, SUM(o.total)
FROM orders o
JOIN customers c ON c.id = o.customer_id
'''
UNUSED = "SELECT id FROM ledgers"

def report(conn):
    return conn.execute(text(TOTALS))

def shared(conn):
    return conn.execute(SHARED)

def add(conn):
    conn.execute("INSERT INTO audit (action, who) VALUES (?, ?)", (1, 2))

def change(conn):
    conn.execute("UPDATE public.audit SET who = 1, action = 2 WHERE id = 3")

def remove(conn):
    conn.execute("DELETE FROM audit WHERE id = 1")

def local_lowercase(conn):
    q = "select name from people"
    return conn.execute(q)

def built(conn, day):
    sql = f"SELECT id FROM events WHERE day = {day}"
    return conn.execute(sql)

def unknown_table(conn, name):
    return conn.execute(f"SELECT * FROM {name}")

def unknown_quoted(conn, name):
    return conn.execute(f'SELECT * FROM "{name}"')

def unknown_part(conn, name):
    return conn.execute(f"SELECT * FROM logs_{name}")

conn = sqlite3.connect("x")
conn.execute("DELETE FROM startup WHERE id = 1")
""",
        },
    )
    found = uses(report)
    assert ("function:app.py:report", "orders", "read") in found
    assert ("function:app.py:report", "customers", "read") in found
    assert ("function:app.py:shared", "accounts", "read") in found
    assert ("function:app.py:add", "audit", "write") in found
    assert ("function:app.py:change", "audit", "write") in found
    assert ("function:app.py:remove", "audit", "write") in found
    assert ("function:app.py:local_lowercase", "people", "read") in found
    assert ("function:app.py:built", "events", "read") in found
    assert ("file:app.py", "startup", "write") in found
    assert ("file:app.py", "ledgers", "read") in found
    # A constant used in a function counts for that function, at the line that uses it, not for its file.
    assert ("file:app.py", "orders", "read") not in found
    use = next(
        u
        for u in report["usage"]
        if u["symbol"] == "function:app.py:report" and u["table"] == "orders"
    )
    assert use["evidence"] == "app.py:14"
    # Only what the SQL states, and a limit saying the definition wasn't found.
    audit = table(report, "audit")
    assert audit["source"] == "inferred-from-sql"
    assert audit["schema"] == "public"
    assert {c["name"] for c in audit["columns"]} == {"action", "who"}
    assert {c["name"] for c in table(report, "orders")["columns"]} == {"total", "customer_id"}
    assert {c["name"] for c in table(report, "customers")["columns"]} == {"email", "id"}
    assert {c["name"] for c in table(report, "people")["columns"]} == {"name"}
    assert any("audit" in line and "definition wasn't found" in line for line in report["limits"])
    # A table only known at run time is a limit, never a table.
    assert not [t for t in report["tables"] if "dyn" in t["name"] or t["name"].startswith("logs")]
    assert any("run time" in line for line in report["limits"])


def test_ddl_in_python_strings(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "store.py": '''
SCHEMA = """
CREATE TABLE alpha (id INTEGER PRIMARY KEY);
CREATE TABLE b (id INTEGER PRIMARY KEY, a_id INTEGER REFERENCES alpha(id));
"""

def setup(conn):
    conn.executescript(SCHEMA)
    conn.execute("CREATE TABLE IF NOT EXISTS c (id INTEGER)")
'''
        },
    )
    assert {t["name"] for t in report["tables"]} == {"alpha", "b", "c"}
    assert table(report, "b")["evidence"] == "store.py:4"
    assert column(report, "b", "a_id")["evidence"] == "store.py:4"
    assert links(report) == {("b", "a_id", "alpha", "id")}
    assert not report["usage"]


def test_the_same_table_from_several_sources_is_one_table(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "models.py": 'from sqlalchemy import Column, Integer\nfrom db import Base\nclass Order(Base):\n    __tablename__ = "orders"\n    id = Column(Integer, primary_key=True)\n',
            "db/1.sql": "CREATE TABLE ORDERS (id INT, extra TEXT);\nCREATE TABLE Orders (more TEXT);\n",
            "q.py": 'def f(c):\n    c.execute("SELECT Id, nothing FROM Orders")\n',
        },
    )
    assert [t["name"] for t in report["tables"]] == ["orders"]
    assert table(report, "orders")["source"] == "sqlalchemy"
    assert table(report, "orders")["evidence"] == "models.py:3"
    assert [c["name"] for c in table(report, "orders")["columns"]] == ["id", "extra", "more"]
    assert ("function:q.py:f", "orders", "read") in uses(report)


def test_ctes_subqueries_and_system_tables_arent_tables(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "q.py": """
def f(c):
    c.execute('''
        WITH recent AS (SELECT id FROM orders), top(id) AS (SELECT id FROM recent)
        SELECT t.id, u.name
        FROM top t JOIN (SELECT id, name FROM users) u ON u.id = t.id
    ''')
    c.execute("SELECT name FROM sqlite_master")
    c.execute("SELECT * FROM information_schema.tables")
    c.execute("SELECT * FROM pg_catalog.pg_class")
    c.execute("SELECT EXTRACT(year FROM created) FROM events")
    c.execute("SELECT * FROM generate_series(1, 3)")
    c.execute("SELECT a IS DISTINCT FROM b FROM pairs")
    c.execute("INSERT INTO log SELECT * FROM archive")
    c.execute("UPDATE stock SET n = 1 FROM deliveries d WHERE d.id = 1")
    c.execute("CREATE TEMP TABLE scratch (id INT)")
    c.execute("SELECT id FROM scratch")
"""
        },
    )
    assert {t["name"] for t in report["tables"]} == {
        "orders", "users", "events", "pairs", "log", "archive", "stock", "deliveries",
    }  # fmt: skip
    found = uses(report)
    assert ("function:q.py:f", "log", "write") in found
    assert ("function:q.py:f", "archive", "read") in found
    assert ("function:q.py:f", "stock", "write") in found
    assert ("function:q.py:f", "deliveries", "read") in found


@pytest.mark.parametrize(
    "prose",
    [
        "Select a file from the list",
        "select one from each group",
        "Select items from menu",
        "SELECT THE BEST FROM THE LIST",
        "Update the table with new values",
        "update settings to use the new value",
        "Delete from the cache when done",
        "delete from cache",
        "Insert into the database",
        "insert into logs (see the docs)",
        "Create table of contents",
        "Create a table from this data",
        "With the new feature, select a row",
        "Replace into words",
        "SELECT * FROM table_name",
    ],
)
def test_sql_looking_prose_is_ignored(tmp_path: Path, prose: str) -> None:
    report = analyze(
        tmp_path,
        {"p.py": f'MESSAGE = "{prose}"\n\ndef f():\n    print("{prose}")\n    return MESSAGE\n'},
    )
    assert report["status"] == "none", report["tables"]


def test_docstrings_and_comments_arent_sql(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "p.py": '''
"""SELECT * FROM module_doc"""

def f():
    """SELECT * FROM function_doc"""
    # SELECT * FROM a_comment
    return 1

"SELECT * FROM a_bare_string"
EXAMPLES = ["SELECT * FROM examples_only", "DROP TABLE x"]
'''
        },
    )
    assert report["status"] == "none", report["tables"]


def test_join_relationships_are_inferred_only_from_an_explicit_equality(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "db.sql": """
CREATE TABLE alpha (id INT PRIMARY KEY, b_ref INT, x INT);
CREATE TABLE beta (id INT PRIMARY KEY, y INT);
CREATE TABLE gamma (id INT PRIMARY KEY);
""",
            "q.py": """
def f(k):
    k.execute("SELECT 1 FROM alpha JOIN beta ON alpha.b_ref = beta.id")
    k.execute("SELECT 1 FROM alpha JOIN gamma USING (id)")
    k.execute("SELECT 1 FROM alpha, gamma WHERE alpha.x = gamma.id")
    k.execute("SELECT 1 FROM alpha JOIN beta b2 ON alpha.x > b2.id")
    k.execute("SELECT 1 FROM alpha JOIN beta b3 ON alpha.x = b3.y")
""",
        },
    )
    assert [
        (r["from_table"], r["from_column"], r["to_table"], r["to_column"], r["origin"])
        for r in report["relationships"]
    ] == [("alpha", "b_ref", "beta", "id", "inferred")]


def test_a_join_on_a_column_the_table_lacks_says_nothing(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "db.sql": "CREATE TABLE alpha (x INT);\nCREATE TABLE beta (y INT);\n",
            "q.py": 'def f(c):\n    c.execute("SELECT 1 FROM alpha JOIN beta ON alpha.x = beta.id")\n',
        },
    )
    assert report["relationships"] == []


def test_an_extracted_relationship_wins_over_the_same_join(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "db.sql": "CREATE TABLE p (id INT PRIMARY KEY);\nCREATE TABLE k (p_id INT REFERENCES p(id));\n",
            "q.py": 'def f(c):\n    c.execute("SELECT 1 FROM k JOIN p ON p.id = k.p_id")\n',
        },
    )
    assert [r["origin"] for r in report["relationships"]] == ["extracted"]


ERD_LINE = re.compile(
    r'    \w+ (\}o|\|\|)(--|\.\.)(\|\||o\{) \w+ : "\w+"'
    r"|    \w+ \{|        \w+ \w+( PK, FK| PK| FK)?|    \}"
)


def check_erd(text: str) -> None:
    lines = text.splitlines()
    assert lines[0] == "erDiagram"
    for line in lines[1:]:
        assert ERD_LINE.fullmatch(line), line


def test_erd_syntax(tmp_path: Path) -> None:
    report = analyze(tmp_path, {"db.sql": DDL, "models.py": MODELS})
    check_erd(report["erd_mermaid"])
    assert '    items }o--|| customers : "customer_id"' in report["erd_mermaid"]
    assert "order_id PK" in report["erd_mermaid"]
    assert "customer_id FK" in report["erd_mermaid"]


def test_erd_draws_inferred_links_dashed_and_one_to_one(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "db.sql": "CREATE TABLE alpha (id INT PRIMARY KEY, b_ref INT);\nCREATE TABLE beta (id INT PRIMARY KEY, a_id INT UNIQUE REFERENCES alpha(id));\n",
            "q.py": 'def f(c):\n    c.execute("SELECT 1 FROM alpha JOIN beta ON alpha.b_ref = beta.id")\n',
        },
    )
    check_erd(report["erd_mermaid"])
    assert '    alpha }o..|| beta : "b_ref"' in report["erd_mermaid"]
    assert '    beta ||--|| alpha : "a_id"' in report["erd_mermaid"]


def test_hostile_identifiers_cant_break_the_erd(tmp_path: Path) -> None:
    hostile = repr('evil" {\n}\nerDiagram\n%%{init: {}}%%')
    models = """
from sqlalchemy import Column, ForeignKey, Integer, String
from db import Base

class A(Base):
    __tablename__ = HOSTILE
    id = Column(HOSTILE, String("x } y"), primary_key=True)

class B(Base):
    __tablename__ = "erDiagram"
    style = Column(Integer)
    PK = Column(Integer)
    second = Column("1st col", Integer)
    link = Column(Integer, ForeignKey(HOSTILE ".id"))

class C(Base):
    __tablename__ = "a b"
    id = Column(Integer, primary_key=True)

class D(Base):
    __tablename__ = "a-b"
    id = Column(Integer, primary_key=True)
    up = Column(Integer, ForeignKey("a b.id"))
""".replace("HOSTILE", hostile)
    report = analyze(
        tmp_path,
        {
            "h.py": models,
            "h.sql": 'CREATE TABLE "x; DROP TABLE y" ("co}l" INT, [ok] INT, `tick``ed` TEXT);\n',
        },
    )
    assert report["relationships"]
    check_erd(report["erd_mermaid"])
    assert report["erd_mermaid"].splitlines().count("erDiagram") == 1
    assert "a_b_2" in report["erd_mermaid"]
    assert {"a b", "a-b", "x; DROP TABLE y"} <= {t["name"] for t in report["tables"]}
    # Each table keeps its own entity, so two names that sanitise alike don't merge.
    assert re.search(
        r'a_b_2 \}o--\|\| a_b : "up"|a_b \}o--\|\| a_b_2 : "up"', report["erd_mermaid"]
    )


def test_nothing_found_says_so(tmp_path: Path) -> None:
    report = analyze(tmp_path, {"a.py": "print('hello')\n", "readme.md": "SELECT * FROM docs\n"})
    assert report["status"] == "none"
    assert report["limits"]
    assert (report["tables"], report["relationships"], report["usage"]) == ([], [], [])
    assert report["erd_mermaid"] == ""


def test_malformed_files_become_limits(tmp_path: Path) -> None:
    deep = "x = " + "(" * 5000 + ")" * 5000 + "\n"
    report = analyze(
        tmp_path,
        {
            "broken.py": "def f(:\n",
            "deep.py": deep,
            "nul.py": "x = 1\0\n",
            "open.sql": "CREATE TABLE half (id INT, name TEXT\n",
            "quote.sql": "CREATE TABLE q (id INT DEFAULT 'never closed\n",
            "comment.sql": "/* never closed\nCREATE TABLE hidden (id INT);\n",
            "junk.sql": "\x01\x02 ((( ;;; ))) FROM FROM FROM JOIN , , INTO\nALTER TABLE\nCREATE TABLE\nINSERT INTO\nUPDATE\nDELETE FROM\nWITH\n",
            "bad.prisma": "model {\n  id\nmodel X {\n   @@id([\n",
            "bad.ts": "@Entity(\nclass\n@Column(\n",
            "models.py": 'from sqlalchemy import Column\nclass A(Base):\n    __tablename__ = 1 + 1\n    b = Column()\nclass C(Base):\n    __tablename__ = "c"\n    d = Column(1, 2, primary_key=f())\n    e = Column(foo=1)\n',
            "good.sql": "CREATE TABLE fine (id INT);\n",
        },
    )
    assert report["status"] == "found"
    assert "fine" in {t["name"] for t in report["tables"]}
    assert "hidden" not in {t["name"] for t in report["tables"]}
    assert "half" not in {t["name"] for t in report["tables"]}
    text = " ".join(report["limits"])
    assert "broken.py" in text
    assert "open.sql" in text
    assert "computes its table name" in text
    json.dumps(report)


def test_binary_and_oversized_files(tmp_path: Path) -> None:
    (tmp_path / "bin.sql").write_bytes(bytes(range(256)) * 50)
    (tmp_path / "big.sql").write_text("-- x\n" * 500_000)
    (tmp_path / "ok.sql").write_text("CREATE TABLE ok (id INT);\n")
    report = data_model.analyze(RepoContext.open(tmp_path))
    assert "ok" in {t["name"] for t in report["tables"]}
    assert any("big.sql" in line for line in report["limits"])


def test_tests_and_fixtures_arent_the_data_model(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        {
            "tests/test_x.py": 'def test(c):\n    c.execute("CREATE TABLE in_test (id INT)")\n',
            "tests/fixtures/db.sql": "CREATE TABLE in_fixture (id INT);\n",
            "app/test_y.py": 'def test(c):\n    c.execute("SELECT * FROM named_test")\n',
            "db.sql": "CREATE TABLE real_one (id INT);\n",
        },
    )
    assert {t["name"] for t in report["tables"]} == {"real_one"}
    assert any("test and fixture files" in line for line in report["limits"])


@pytest.mark.parametrize(
    "hostile",
    [
        "SELECT " + "a," * 50_000 + " FROM t",
        "SELECT * FROM " + "a," * 50_000 + "b",
        "SELECT * FROM " + "(" * 100_000,
        "SELECT * FROM t WHERE " + "(" * 50_000 + ")" * 50_000,
        "SELECT " + "(SELECT " * 12_000,
        "WITH " + "a AS (SELECT 1), " * 6_000 + "b AS (SELECT 1) SELECT 1",
        "CREATE TABLE t (" + "a INT," * 17_000 + "z INT)",
        "CREATE TABLE t (" * 6_000,
        "INSERT INTO a (" * 7_000,
        "ALTER TABLE t " + "ADD COLUMN c INT, " * 6_000,
        "/*" * 50_000,
        "'" * 100_000,
        '"' * 100_000,
        "`a" * 50_000,
        "[" * 100_000,
        "{" * 100_000,
        "$" * 100_000,
        "$a$" * 33_000,
        "-" * 100_000,
        "a." * 50_000 + "b",
        "SELECT * FROM " + "a." * 50_000 + "b",
        "FROM " * 20_000,
        "SELECT a.b = c.d ON " * 5_000,
        "GO\n" * 33_000,
        "UPDATE t SET " + "a = 1, " * 14_000,
        "x" * 100_000,
        " " * 100_000 + "SELECT",
        "(" * 100_000 + "SELECT * FROM t",
        "\n" * 100_000,
    ],
    ids=lambda text: text[:16],
)
def test_adversarial_sql_finishes_quickly(hostile: str) -> None:
    assert quickest(lambda: data_model._read_sql(hostile, trusted=True)) < 1.0
    assert quickest(lambda: data_model._read_sql(hostile, trusted=False)) < 1.0


@pytest.mark.parametrize(
    ("name", "hostile"),
    [
        ("a.ts", "@Entity()\nclass A {\n" + "@" * 100_000 + "\n" + " " * 100_000 + ":\n}\n"),
        ("b.ts", "@Entity(" + '"' * 100_000 + ")\nclass " + " " * 100_000 + "\n"),
        (
            "c.ts",
            "@Entity()\nclass A {\n@Column({ name"
            + " " * 100_000
            + ":"
            + " " * 100_000
            + "})\nx: y\n}\n",
        ),
        (
            "d.ts",
            "@Entity()\nclass A {\n@ManyToOne(" + "=" * 50_000 + ">" * 50_000 + ")\nx: y\n}\n",
        ),
        ("e.prisma", "model A {\n" + "a " * 50_000 + "\n" + "@relation(fields:[" * 5_000 + "\n}\n"),
        ("f.py", 'S = "' + "SELECT " * 14_000 + '"\n'),
        ("g.py", "x = " + " + ".join(['"a"'] * 2_000) + "\n"),
    ],
    ids=["a", "b", "c", "d", "e", "f", "g"],
)
def test_adversarial_files_finish_quickly(tmp_path: Path, name: str, hostile: str) -> None:
    (tmp_path / name).write_text(hostile)
    ctx = RepoContext(tmp_path, [name], RepoContext.open(tmp_path).graph)
    assert quickest(lambda: data_model.analyze(ctx)) < 1.0


def test_output_is_deterministic_and_sorted(tmp_path: Path) -> None:
    files = {
        "z.sql": "CREATE TABLE zed (id INT PRIMARY KEY);\nCREATE TABLE abc (zed_id INT REFERENCES zed(id));\n",
        "models.py": MODELS,
        "q.py": 'def b(c):\n    c.execute("SELECT 1 FROM zed")\n\ndef a(c):\n    c.execute("SELECT 1 FROM abc")\n',
    }
    first = analyze(tmp_path, files)
    assert json.dumps(first) == json.dumps(analyze(tmp_path, {}))
    names = [t["name"].lower() for t in first["tables"]]
    assert names == sorted(names)
    symbols = [(u["symbol"], u["table"], u["access"]) for u in first["usage"]]
    assert symbols == sorted(symbols)
    assert first["limits"] == sorted(first["limits"])


def test_shape_matches_the_contract(tmp_path: Path) -> None:
    report = analyze(
        tmp_path, {"models.py": MODELS, "q.py": 'def f(c):\n    c.execute("SELECT 1 FROM tags")\n'}
    )
    assert set(report) == {"status", "limits", "tables", "relationships", "usage", "erd_mermaid"}
    assert set(report["tables"][0]) == {"id", "name", "schema", "source", "evidence", "columns"}
    assert set(report["tables"][0]["columns"][0]) == {
        "name", "type", "nullable", "primary_key", "foreign_key", "evidence",
    }  # fmt: skip
    assert set(report["relationships"][0]) == {
        "from_table", "from_column", "to_table", "to_column", "cardinality", "origin", "evidence",
    }  # fmt: skip
    assert set(report["usage"][0]) == {
        "symbol",
        "table",
        "access",
        "origin",
        "confidence",
        "evidence",
    }


def test_nothing_is_run_or_written(tmp_path: Path) -> None:
    (tmp_path / "models.py").write_text(
        "open('ran.txt', 'w').write('x')\nclass A(Base):\n    __tablename__ = 'a'\n"
    )
    before = sorted(p.name for p in tmp_path.iterdir())
    data_model.analyze(RepoContext.open(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == before

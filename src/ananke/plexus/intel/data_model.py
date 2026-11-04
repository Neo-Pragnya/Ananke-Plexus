"""The data model: which tables and fields a repository has, how they relate, and which code uses them.

Nothing is imported or executed. Python is read with ``ast``, SQL with a small linear tokenizer, and Prisma and TypeORM line by
line. Regular expressions here match single tokens only, so hostile input can't make them backtrack. A file that fails to parse
becomes a limit. See docs/reference/repo-intelligence.md for the shape this answers in and for what isn't supported.
"""

from __future__ import annotations

import ast
import bisect
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ananke.plexus.intel._common import is_fixture, is_test_file, read_text
from ananke.plexus.intel.context import RepoContext

MAX_NAME = 128
MAX_LISTED = 20

# How strongly each kind of definition is believed when several describe one table.
PRIORITY = {
    "sqlalchemy": 0,
    "sqlmodel": 0,
    "django": 0,
    "prisma": 0,
    "typeorm": 0,
    "alembic": 1,
    "sql": 2,
}
UNSUPPORTED_PY = {
    "peewee",
    "tortoise",
    "pony",
    "mongoengine",
    "beanie",
    "odmantic",
    "ormar",
    "piccolo",
}
UNSUPPORTED_SOURCE = {".java": "Java", ".kt": "Kotlin", ".go": "Go", ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".rs": "Rust"}  # fmt: skip
SQLALCHEMY_MODULES = {"sqlalchemy", "flask_sqlalchemy", "sqlmodel"}
NOT_A_COLUMN_TYPE = {"Sequence", "Identity", "Computed"}
DJANGO_BASES = {"Model", "AbstractUser", "AbstractBaseUser", "PermissionsMixin"}
TYPEORM_COLUMNS = {
    "Column", "PrimaryColumn", "PrimaryGeneratedColumn", "CreateDateColumn", "UpdateDateColumn", "DeleteDateColumn", "VersionColumn",
}  # fmt: skip
SQL_HEADS = {
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "WITH",
    "CREATE",
    "ALTER",
    "REPLACE",
    "MERGE",
    "TRUNCATE",
}
SQL_PREAMBLE = {"BEGIN", "PRAGMA", "DROP"}
NOT_AN_ALIAS = {
    "WHERE", "ON", "USING", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "NATURAL", "GROUP", "ORDER", "HAVING",
    "LIMIT", "OFFSET", "UNION", "INTERSECT", "EXCEPT", "SET", "VALUES", "RETURNING", "FOR", "WINDOW", "FETCH", "TABLESAMPLE",
    "SELECT", "FROM", "AS", "STRAIGHT_JOIN", "LATERAL", "DEFAULT", "INTO", "WHEN", "THEN", "AND", "OR", "NOT",
}  # fmt: skip
NOT_A_TABLE = NOT_AN_ALIAS | {
    "INSERT",
    "UPDATE",
    "DELETE",
    "NULL",
    "CASE",
    "UNNEST",
    "ALL",
    "ANY",
    "EACH",
    "IF",
}
PROSE_WORDS = {
    "the", "a", "an", "this", "that", "these", "those", "your", "my", "our", "their", "it", "them", "here", "there", "one", "some",
    "every", "which", "what", "where", "his", "her", "its", "of", "to", "in", "and", "or", "is", "are", "be",
}  # fmt: skip
EXAMPLE_NAMES = {
    "table",
    "tables",
    "table_name",
    "tablename",
    "my_table",
    "mytable",
    "your_table",
    "some_table",
    "example_table",
}
SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "sys", "mysql", "performance_schema"}
SYSTEM_TABLES = {"sqlite_master", "sqlite_schema", "sqlite_sequence", "sqlite_temp_master", "dual"}
FROM_ARGUMENT_FUNCTIONS = {"EXTRACT", "SUBSTRING", "SUBSTR", "TRIM", "OVERLAY", "POSITION"}
COLUMN_ENDS = {
    "NOT", "NULL", "DEFAULT", "PRIMARY", "UNIQUE", "REFERENCES", "CHECK", "CONSTRAINT", "COLLATE", "GENERATED", "AUTO_INCREMENT",
    "AUTOINCREMENT", "ON", "COMMENT", "IDENTITY", "AS", "KEY",
}  # fmt: skip
SQL_TYPES = {
    "INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "MEDIUMINT", "SERIAL", "BIGSERIAL", "TEXT", "VARCHAR", "CHAR", "CHARACTER",
    "NVARCHAR", "NCHAR", "CLOB", "BLOB", "BYTEA", "BOOLEAN", "BOOL", "REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL", "DATE", "TIME",
    "TIMESTAMP", "TIMESTAMPTZ", "DATETIME", "JSON", "JSONB", "UUID", "BINARY", "VARBINARY", "ENUM",
}  # fmt: skip

_WORD = re.compile(r"[^\W\d][\w$]*")
_NUMBER = re.compile(r"\d[\w.]*")
_DOLLAR_TAG = re.compile(r"\$\w{0,64}\$")
_NEWLINE = re.compile(r"\n")
_TS_DECORATOR = re.compile(r"\s{0,200}@(\w{1,64})")
_TS_PROPERTY = re.compile(
    r"\s{0,200}(?:(?:public|private|protected|readonly)\s{1,8}){0,4}(\w{1,128})[!?]?\s{0,8}:"
)
_TS_CLASS = re.compile(r"\bclass\s{1,8}(\w{1,128})")
_TS_NAME = re.compile(r"\bname\s{0,8}:\s{0,8}[\"']([^\"'\n]{1,128})[\"']")
_TS_QUOTED = re.compile(r"[\"']([^\"'\n]{1,128})[\"']")
_TS_TARGET = re.compile(r"=>\s{0,8}(\w{1,128})")

_LineOf = Callable[[int], int]


def _offset(base: int) -> _LineOf:
    return lambda relative: relative + base


# ---------------------------------------------------------------- what is collected


@dataclass
class _Column:
    name: str
    type: str | None
    nullable: bool | None
    primary_key: bool
    path: str
    line: int
    unique: bool = False


@dataclass
class _Fk:
    column: str
    to_table: str
    to_column: str | None
    path: str
    line: int
    one_to_one: bool = False


@dataclass
class _Table:
    name: str
    schema: str | None
    source: str
    path: str
    line: int
    columns: dict[str, _Column] = field(
        default_factory=dict
    )  # by lower-case name, in declared order
    fks: list[_Fk] = field(default_factory=list)
    primary: list[str] = field(default_factory=list)
    unique: list[str] = field(default_factory=list)
    partial: bool = (
        False  # an ALTER, or a subclass's extra columns: never the table's headline evidence
    )

    def add(self, column: _Column) -> None:
        self.columns[column.name.lower()] = column


def _head_of(text: str) -> str:
    """The first word of the text, past whitespace, brackets and comments, without tokenizing the whole of it."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace() or c == "(":
            i += 1
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            break
    word = _WORD.match(text, i)
    return word.group().upper() if word else ""


# ---------------------------------------------------------------- SQL


@dataclass
class _Ref:
    name: str
    schema: str | None
    access: str
    line: int


@dataclass
class _Sql:
    """What one piece of SQL says. Lines count from zero within the text."""

    tables: list[_Table] = field(default_factory=list)
    refs: list[_Ref] = field(default_factory=list)
    columns: list[tuple[str, str, int]] = field(default_factory=list)
    joins: list[tuple[str, str, str, str, int]] = field(default_factory=list)
    computed: int = 0
    not_replayed: int = 0
    views: int = 0
    malformed: list[int] = field(default_factory=list)


@dataclass(slots=True)
class _Tok:
    kind: str  # word, quoted, string, number, punct
    text: str
    upper: str
    pos: int


def _tokenize(text: str) -> list[_Tok]:
    """One pass over the text. Each branch moves past what it read, so the work is linear in the input."""
    toks: list[_Tok] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif c in "'\"`":
            end = i + 1
            while True:
                end = text.find(c, end)
                if end < 0:
                    end = n
                    break
                if text.startswith(c, end + 1):
                    end += 2
                    continue
                break
            toks.append(_Tok("string" if c == "'" else "quoted", text[i + 1 : end], "", i))
            i = end + 1
        elif (
            c == "["
            and (end := text.find("]", i, i + MAX_NAME + 2)) > i + 1
            and _WORD.fullmatch(text, i + 1, end)
        ):
            toks.append(_Tok("quoted", text[i + 1 : end], "", i))
            i = end + 1
        elif c == "{" and (end := text.find("}", i, i + 256)) > 0:
            toks.append(_Tok("word", "__dyn__", "__DYN__", i))
            i = end + 1
        elif c == "$" and (tag := _DOLLAR_TAG.match(text, i)):
            end = text.find(tag.group(), tag.end())
            toks.append(_Tok("string", "", "", i))
            i = n if end < 0 else end + len(tag.group())
        elif word := _WORD.match(text, i):
            toks.append(_Tok("word", word.group(), word.group().upper(), i))
            i = word.end()
        elif number := _NUMBER.match(text, i):
            toks.append(_Tok("number", number.group(), "", i))
            i = number.end()
        else:
            toks.append(_Tok("punct", c, c, i))
            i += 1
    return toks


def _is(tok: _Tok | None, *uppers: str) -> bool:
    return tok is not None and tok.kind in ("word", "punct") and tok.upper in uppers


def _at(toks: list[_Tok], i: int) -> _Tok | None:
    return toks[i] if 0 <= i < len(toks) else None


def _is_name(tok: _Tok | None) -> bool:
    return tok is not None and tok.kind in ("word", "quoted") and 0 < len(tok.text) <= MAX_NAME


def _close(toks: list[_Tok], i: int) -> int:
    """The index of the bracket matching the one at ``i``, or -1."""
    depth = 0
    for j in range(i, len(toks)):
        if toks[j].kind == "punct":
            if toks[j].text == "(":
                depth += 1
            elif toks[j].text == ")":
                depth -= 1
                if depth == 0:
                    return j
    return -1


def _name_at(toks: list[_Tok], i: int) -> tuple[str, str | None, int] | None:
    """``[catalog.][schema.]table`` at ``i``: the table, its schema, and the index after it."""
    if not _is_name(_at(toks, i)):
        return None
    parts = [toks[i].text]
    i += 1
    while _is(_at(toks, i), ".") and _is_name(_at(toks, i + 1)):
        parts.append(toks[i + 1].text)
        i += 2
    schema = parts[-2] if len(parts) > 1 and parts[-2] != "__dyn__" else None
    return parts[-1], schema, i


def _split(toks: list[_Tok]) -> Iterator[list[_Tok]]:
    """Split on commas that aren't inside brackets."""
    depth, start = 0, 0
    for i, tok in enumerate(toks):
        if tok.kind != "punct":
            continue
        if tok.text == "(":
            depth += 1
        elif tok.text == ")":
            depth -= 1
        elif tok.text == "," and depth == 0:
            yield toks[start:i]
            start = i + 1
    if start < len(toks):
        yield toks[start:]


def _bracketed_names(toks: list[_Tok], i: int) -> tuple[list[str], int]:
    """The simple names in the brackets at ``i``, and the index after them."""
    end = _close(toks, i)
    if end < 0:
        return [], i
    return [t.text for t in toks[i + 1 : end] if t.kind in ("word", "quoted")], end + 1


def _is_system_table(name: str, schema: str | None) -> bool:
    low = name.lower()
    return (
        low in SYSTEM_TABLES
        or low.startswith("pg_")
        or (schema is not None and schema.lower() in SYSTEM_SCHEMAS)
    )


class _Dml:
    """One statement scanned once, left to right, without recursion, so deep nesting can't overflow the stack."""

    def __init__(self, toks: list[_Tok], line_of: _LineOf, ctes: set[str], head_at: int) -> None:
        self.toks, self.line_of, self.ctes, self.head_at = toks, line_of, ctes, head_at
        self.refs: list[_Ref] = []
        self.aliases: dict[str, set[str]] = {}
        self.columns: list[tuple[str, str, int]] = []
        self.joins: list[tuple[str, str, str, str, int]] = []
        self.computed = 0
        self.rejected = False
        self.from_brackets: set[int] = set()

    def _ref(self, i: int, access: str) -> tuple[int, str | None]:
        """A table reference at ``i``. Answers the index after it and the table, if it's a real one."""
        toks = self.toks
        while _is(_at(toks, i), "LATERAL", "ONLY"):
            i += 1
        tok = _at(toks, i)
        if _is(tok, "("):
            self.from_brackets.add(i)
            return i, None
        parsed = _name_at(toks, i)
        if parsed is None or tok is None:
            self.computed += 1
            return i, None
        name, schema, after = parsed
        if _is(_at(toks, after), "("):
            return after, None  # a table function
        if name == "__dyn__":
            self.computed += 1
            return after, None
        low = name.lower()
        if tok.kind == "word" and after == i + 1 and tok.upper in NOT_A_TABLE:
            return after, None
        if low in PROSE_WORDS or low in EXAMPLE_NAMES:
            self.rejected = True
            return after, None
        real = low not in self.ctes and not _is_system_table(name, schema)
        if real:
            self.refs.append(_Ref(name, schema, access, self.line_of(tok.pos)))
            self.aliases.setdefault(low, set()).add(low)
        alias_at = after + 1 if _is(_at(toks, after), "AS") else after
        alias = _at(toks, alias_at)
        if alias is not None and _is_name(alias) and alias.upper not in NOT_AN_ALIAS:
            self.aliases.setdefault(alias.text.lower(), set()).add(low if real else "")
            after = alias_at + 1
        return after, low if real else None

    def _refs(self, i: int, access: str) -> int:
        """Comma-separated table references."""
        while True:
            i, _ = self._ref(i, access)
            if _is(_at(self.toks, i), ",") and i not in self.from_brackets:
                i += 1
                continue
            return i

    def _insert(self, i: int) -> int:
        toks = self.toks
        j = i + 1
        for _ in range(3):
            if _is(_at(toks, j), "INTO"):
                break
            j += 1
        else:
            return i + 1
        after, table = self._ref(j + 1, "write")
        if _is(_at(toks, after), "(") and not _is(_at(toks, after + 1), "SELECT", "WITH"):
            names, end = _bracketed_names(toks, after)
            if end == after:
                self.rejected = True
                return after
            if table:
                line = self.line_of(toks[after].pos)
                self.columns += [(table, name, line) for name in names if name != "__dyn__"]
            after = end
        if table and not _is(
            _at(toks, after), "VALUES", "VALUE", "SELECT", "DEFAULT", "WITH", "SET", "__DYN__", "("
        ):
            self.rejected = True
        return after

    def _update(self, i: int) -> int:
        toks = self.toks
        j = i + 1
        if _is(_at(toks, j), "OR"):
            j += 2
        after, table = self._ref(j, "write")
        if not _is(_at(toks, after), "SET"):
            self.rejected = True
            return after
        after += 1
        first, second = _at(toks, after), _at(toks, after + 1)
        if not (_is(first, "(", "__DYN__") or (_is_name(first) and _is(second, "=", "."))):
            self.rejected = True
            return after
        depth, expect, k = 0, True, after
        while k < len(toks):
            tok = toks[k]
            if tok.kind == "punct" and tok.text == "(":
                depth += 1
            elif tok.kind == "punct" and tok.text == ")":
                depth -= 1
                if depth < 0:
                    break
            elif depth == 0 and tok.kind == "punct" and tok.text == ",":
                expect = True
                k += 1
                continue
            elif depth == 0 and _is(tok, "WHERE", "FROM", "RETURNING", "ORDER", "LIMIT"):
                break
            elif (
                expect
                and depth == 0
                and _is_name(tok)
                and _is(_at(toks, k + 1), "=")
                and table
                and tok.text != "__dyn__"
            ):
                self.columns.append((table, tok.text, self.line_of(tok.pos)))
            expect = False
            k += 1
        return after

    def _select_columns(self) -> None:
        """``SELECT a, b FROM t`` states its columns when one table can be the only owner."""
        toks, head = self.toks, self.head_at
        if len(self.refs) != 1 or not _is(_at(toks, head), "SELECT"):
            return
        low = self.refs[0].name.lower()
        end = head + 1
        while end < len(toks) and not _is(toks[end], "FROM"):
            if toks[end].kind == "punct" and toks[end].text == "(":
                return
            end += 1
        for item in _split(toks[head + 1 : end]):
            named = (
                len(item) in (1, 3)
                and _is_name(item[0])
                and item[0].upper not in {"DISTINCT", "ALL", "__DYN__"}
            )
            if named and (len(item) == 1 or _is(item[1], "AS")):
                self.columns.append((low, item[0].text, self.line_of(item[0].pos)))

    def run(self, sql: _Sql) -> None:
        toks = self.toks
        frames: list[str] = []
        qualified: list[tuple[str, str, int]] = []
        in_on = False
        subquery = False
        i = 0
        while i < len(toks) and not self.rejected:
            tok = toks[i]
            prev = _at(toks, i - 1)
            if tok.kind == "punct":
                if tok.text == "(":
                    if i in self.from_brackets:
                        frames.append("<from>")
                        subquery = True
                    else:
                        frames.append(
                            prev.upper if prev is not None and prev.kind == "word" else ""
                        )
                elif tok.text == ")" and frames:
                    in_on = False
                    if frames.pop() == "<from>":
                        j = i + 1
                        if _is(_at(toks, j), "AS"):
                            j += 1
                        alias = _at(toks, j)
                        if (
                            alias is not None
                            and _is_name(alias)
                            and alias.upper not in NOT_AN_ALIAS
                        ):
                            self.aliases.setdefault(alias.text.lower(), set()).add("")
                            j += 1
                        if _is(_at(toks, j), ","):
                            i = self._refs(j + 1, "read")
                            continue
                i += 1
                continue
            if tok.kind not in ("word", "quoted"):
                i += 1
                continue
            # alias.column, and alias.a = other.b inside an ON clause
            if (
                _is(_at(toks, i + 1), ".")
                and _is_name(_at(toks, i + 2))
                and not _is(_at(toks, i + 3), "(")
            ):
                column = toks[i + 2]
                qualified.append((tok.text.lower(), column.text, self.line_of(tok.pos)))
                if (
                    in_on
                    and _is(_at(toks, i + 3), "=")
                    and _is_name(_at(toks, i + 4))
                    and _is(_at(toks, i + 5), ".")
                    and _is_name(_at(toks, i + 6))
                ):
                    self.joins.append(
                        (
                            tok.text.lower(),
                            column.text,
                            toks[i + 4].text.lower(),
                            toks[i + 6].text,
                            self.line_of(tok.pos),
                        )
                    )
                i += 3
                continue
            word = tok.upper if tok.kind == "word" else ""
            if word == "FROM":
                ignored = (_is(prev, "DISTINCT") and _is(_at(toks, i - 2), "IS", "NOT")) or (
                    bool(frames) and frames[-1] in FROM_ARGUMENT_FUNCTIONS
                )
                if not ignored:
                    i = self._refs(i + 1, "write" if _is(prev, "DELETE") else "read")
                    continue
            elif word in ("JOIN", "STRAIGHT_JOIN"):
                i, _ = self._ref(i + 1, "read")
                in_on = False
                continue
            elif word == "ON":
                in_on = not _is(_at(toks, i + 1), "CONFLICT", "DUPLICATE")
            elif word in ("WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "UNION"):
                in_on = False
            elif word in ("INSERT", "REPLACE") and not _is(_at(toks, i + 1), "("):
                i = self._insert(i)
                continue
            elif word == "UPDATE" and (i == self.head_at or _is(prev, "(", "AS")):
                i = self._update(i)
                continue
            elif word == "MERGE" and _is(_at(toks, i + 1), "INTO"):
                i, _ = self._ref(i + 2, "write")
                continue
            elif word == "USING" and _is(_at(toks, self.head_at), "MERGE", "DELETE") and not frames:
                i = self._refs(i + 1, "read")
                continue
            elif word == "TRUNCATE" and i == self.head_at:
                i, _ = self._ref(i + 2 if _is(_at(toks, i + 1), "TABLE") else i + 1, "write")
                continue
            i += 1
        if self.rejected:
            return
        if not subquery:
            self._select_columns()
        self._merge(sql, qualified)

    def _merge(self, sql: _Sql, qualified: list[tuple[str, str, int]]) -> None:
        def owner(alias: str) -> str | None:
            found = self.aliases.get(alias, set())
            return (next(iter(found)) or None) if len(found) == 1 else None

        sql.refs += self.refs
        sql.computed += self.computed
        sql.columns += self.columns
        sql.columns += [
            (table, column, line)
            for alias, column, line in qualified
            if (table := owner(alias)) and column != "__dyn__"
        ]
        for left, left_col, right, right_col, line in self.joins:
            a, b = owner(left), owner(right)
            if a and b and a != b:
                sql.joins.append((a, left_col, b, right_col, line))


def _cte_names(toks: list[_Tok], i: int) -> tuple[set[str], int] | None:
    i += 1
    if _is(_at(toks, i), "RECURSIVE"):
        i += 1
    names: set[str] = set()
    while True:
        tok = _at(toks, i)
        if tok is None or not _is_name(tok):
            return None
        names.add(tok.text.lower())
        i += 1
        if _is(_at(toks, i), "("):
            end = _close(toks, i)
            if end < 0:
                return None
            i = end + 1
        if not _is(_at(toks, i), "AS"):
            return None
        i += 1
        if _is(_at(toks, i), "NOT"):
            i += 1
        if _is(_at(toks, i), "MATERIALIZED"):
            i += 1
        if not _is(_at(toks, i), "("):
            return None
        end = _close(toks, i)
        if end < 0:
            return None
        i = end + 1
        if _is(_at(toks, i), ","):
            i += 1
            continue
        return names, i


def _strong_select(toks: list[_Tok], head: int) -> bool:
    """Whether a SELECT outside any database call reads as SQL rather than as an English sentence."""
    depth, from_at, commas = 0, -1, 0
    for j in range(head + 1, len(toks)):
        tok = toks[j]
        if tok.kind == "punct" and tok.text == "(":
            depth += 1
        elif tok.kind == "punct" and tok.text == ")":
            depth -= 1
        elif depth == 0 and tok.kind == "punct" and tok.text == ",":
            commas += 1
        elif depth == 0 and _is(tok, "FROM"):
            from_at = j
            break
    if from_at < 0:
        return False
    listed = toks[head + 1 : from_at]
    if any(t.kind == "word" and t.text.lower() in PROSE_WORDS for t in listed):
        return False
    if toks[head].text.isupper() and toks[from_at].text.isupper():
        return True
    if _is(_at(toks, head + 1), "*") or commas or any(_is(t, ".") for t in listed):
        return True
    for j in range(from_at + 1, len(toks)):
        if _is(toks[j], "JOIN"):
            return True
        if _is(toks[j], "WHERE") and _is(
            _at(toks, j + 2), "=", "<", ">", "!", "IN", "LIKE", "IS", "."
        ):
            return True
        if (
            _is(toks[j], "GROUP", "ORDER")
            and _is(_at(toks, j + 1), "BY")
            and _is_name(_at(toks, j + 2))
        ):
            return True
    return False


def _sql_type(toks: list[_Tok]) -> str | None:
    out = ""
    for tok in toks:
        if tok.kind == "punct" and tok.text in "(),":
            out += tok.text
        else:
            out += (" " if out and not out.endswith(("(", ",")) else "") + tok.text
        if len(out) > 64:
            break
    return out[:64] or None


def _column_def(item: list[_Tok], line_of: _LineOf, table: _Table) -> None:
    name_tok = item[0]
    k = 1
    while k < len(item) and not (item[k].kind == "word" and item[k].upper in COLUMN_ENDS):
        k += 1
    column = _Column(
        name_tok.text, _sql_type(item[1:k]), True, False, table.path, line_of(name_tok.pos)
    )
    while k < len(item):
        tok = item[k]
        if _is(tok, "NOT") and _is(_at(item, k + 1), "NULL"):
            column.nullable = False
            k += 2
        elif _is(tok, "PRIMARY") and _is(_at(item, k + 1), "KEY"):
            column.primary_key = True
            column.nullable = False
            k += 2
        elif _is(tok, "UNIQUE"):
            column.unique = True
            k += 1
        elif _is(tok, "REFERENCES"):
            parsed = _name_at(item, k + 1)
            if parsed is None:
                k += 1
                continue
            target, _, after = parsed
            to_column = None
            if _is(_at(item, after), "("):
                names, end = _bracketed_names(item, after)
                to_column = names[0] if names else None
                after = end if end > after else after + 1
            table.fks.append(_Fk(column.name, target, to_column, table.path, line_of(tok.pos)))
            k = after
        else:
            k += 1
    table.add(column)


def _bracket_after(item: list[_Tok], j: int) -> tuple[list[str], int]:
    while j < len(item) and not _is(item[j], "("):
        j += 1
    return _bracketed_names(item, j) if j < len(item) else ([], j)


def _constraint(item: list[_Tok], line_of: _LineOf, table: _Table) -> bool:
    """A table-level constraint. False when the item is really a column, such as one called ``key``."""
    first = item[0]
    if first.kind != "word":
        return False
    head = first.upper
    second = _at(item, 1)
    k = 0
    if head == "CONSTRAINT":
        k = 2
        head = item[2].upper if len(item) > 2 else ""
    elif head in ("KEY", "INDEX", "FULLTEXT", "SPATIAL"):
        return second is None or second.upper not in SQL_TYPES
    elif head == "CHECK":
        return _is(second, "(")
    elif head in ("EXCLUDE", "LIKE", "PERIOD"):
        return True
    if head == "PRIMARY" and _is(_at(item, k + 1), "KEY"):
        table.primary += _bracket_after(item, k + 2)[0]
        return True
    if head == "UNIQUE" and (k or _is(second, "(", "KEY", "INDEX")):
        names, _ = _bracket_after(item, k + 1)
        if len(names) == 1:
            table.unique += names
        return True
    if head == "FOREIGN" and _is(_at(item, k + 1), "KEY"):
        local, after = _bracket_after(item, k + 2)
        parsed = _name_at(item, after + 1) if _is(_at(item, after), "REFERENCES") else None
        if parsed is None:
            return True
        target, _, end = parsed
        remote = _bracketed_names(item, end)[0] if _is(_at(item, end), "(") else []
        for index, column in enumerate(local):
            to_column = remote[index] if index < len(remote) else None
            table.fks.append(_Fk(column, target, to_column, table.path, line_of(first.pos)))
        return True
    return k > 0


def _create(toks: list[_Tok], head: int, line_of: _LineOf, sql: _Sql) -> None:
    i = head + 1
    while _is(
        _at(toks, i),
        "TEMP",
        "TEMPORARY",
        "UNLOGGED",
        "GLOBAL",
        "LOCAL",
        "VIRTUAL",
        "OR",
        "REPLACE",
        "EXTERNAL",
    ):
        i += 1
    if _is(_at(toks, i), "VIEW", "MATERIALIZED"):
        sql.views += 1
        return
    if not _is(_at(toks, i), "TABLE"):
        return
    i += 1
    if _is(_at(toks, i), "IF") and _is(_at(toks, i + 1), "NOT") and _is(_at(toks, i + 2), "EXISTS"):
        i += 3
    parsed = _name_at(toks, i)
    if parsed is None:
        return
    name, schema, after = parsed
    if name == "__dyn__":
        sql.computed += 1
        return
    if name.lower() in PROSE_WORDS or name.lower() in EXAMPLE_NAMES:
        return
    table = _Table(name, schema, "sql", "", line_of(toks[i].pos))
    next_tok = _at(toks, after)
    if _is(next_tok, "AS"):  # CREATE TABLE ... AS SELECT
        sql.tables.append(table)
        sql.refs.append(_Ref(name, schema, "write", line_of(toks[i].pos)))
        _Dml(toks[after + 1 :], line_of, set(), 0).run(sql)
        return
    if _is(next_tok, "USING", "LIKE"):
        sql.tables.append(table)
        return
    if not _is(next_tok, "("):
        return
    end = _close(toks, after)
    if end < 0:
        sql.malformed.append(line_of(toks[i].pos))
        return
    for item in _split(toks[after + 1 : end]):
        if item and _is_name(item[0]) and not _constraint(item, line_of, table):
            _column_def(item, line_of, table)
    sql.tables.append(table)


def _alter(toks: list[_Tok], head: int, line_of: _LineOf, sql: _Sql) -> None:
    i = head + 1
    if not _is(_at(toks, i), "TABLE"):
        return
    i += 1
    if _is(_at(toks, i), "IF") and _is(_at(toks, i + 1), "EXISTS"):
        i += 2
    if _is(_at(toks, i), "ONLY"):
        i += 1
    parsed = _name_at(toks, i)
    if parsed is None:
        return
    name, schema, after = parsed
    table = _Table(name, schema, "sql", "", line_of(toks[i].pos), partial=True)
    for action in _split(toks[after:]):
        if not _is(_at(action, 0), "ADD"):
            sql.not_replayed += 1
            continue
        item = action[1:]
        if not item:
            continue
        if _is(item[0], "COLUMN"):
            item = item[1:]
            if _is(_at(item, 0), "IF") and _is(_at(item, 2), "EXISTS"):
                item = item[3:]
            if item and _is_name(item[0]):
                _column_def(item, line_of, table)
        elif not _constraint(item, line_of, table) and _is_name(item[0]):
            _column_def(item, line_of, table)
    sql.tables.append(table)


def _read_sql(text: str, *, trusted: bool) -> _Sql:
    """Everything a piece of SQL says. ``trusted`` is a .sql file or a string given to a database call, where a sentence-like
    statement is still accepted. Either way a statement has to parse as keyword plus table clause."""
    sql = _Sql()
    head_word = _head_of(text)
    if not trusted and head_word not in SQL_HEADS and head_word not in SQL_PREAMBLE:
        return sql
    newlines = [m.start() for m in _NEWLINE.finditer(text)]

    def line_of(pos: int) -> int:
        return bisect.bisect_left(newlines, pos)

    toks = _tokenize(text)
    start = 0
    for i in range(len(toks) + 1):
        if i == len(toks) or (toks[i].kind == "punct" and toks[i].text == ";"):
            if i > start:
                _statement(toks[start:i], line_of, sql, trusted)
            start = i + 1
    return sql


def _statement(toks: list[_Tok], line_of: _LineOf, sql: _Sql, trusted: bool) -> None:
    head = 0
    while _is(_at(toks, head), "("):
        head += 1
    ctes: set[str] = set()
    if _is(_at(toks, head), "WITH"):
        parsed = _cte_names(toks, head)
        if parsed is None:
            return
        ctes, head = parsed
    tok = _at(toks, head)
    if tok is None or tok.kind != "word":
        return
    word = tok.upper
    if word == "CREATE":
        _create(toks, head, line_of, sql)
    elif word == "ALTER":
        _alter(toks, head, line_of, sql)
    elif word == "DROP":
        sql.not_replayed += 1
    elif word in ("SELECT", "INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE", "TRUNCATE"):
        if word == "SELECT" and not trusted and not _strong_select(toks, head):
            return
        if word == "DELETE" and not _delete_shape(toks, head, trusted):
            return
        if word == "TRUNCATE" and not trusted and not tok.text.isupper():
            return
        _Dml(toks, line_of, ctes, head).run(sql)


def _delete_shape(toks: list[_Tok], head: int, trusted: bool) -> bool:
    if not _is(_at(toks, head + 1), "FROM"):
        return False
    parsed = _name_at(toks, head + 2)
    if parsed is None:
        return True  # a computed table: counted, not read
    i = parsed[2]
    if _is(_at(toks, i), "AS"):
        i += 1
    alias = _at(toks, i)
    if alias is not None and _is_name(alias) and alias.upper not in NOT_AN_ALIAS:
        i += 1
    follow = _at(toks, i)
    if follow is not None and not _is(follow, "WHERE", "USING", "RETURNING", "ORDER", "LIMIT"):
        return False
    return trusted or toks[head].text.isupper() or _is(follow, "WHERE")


# ---------------------------------------------------------------- Python helpers


def _tail(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _dotted(node: ast.expr) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return parts[::-1]
    return None


def _literal(node: ast.expr | None) -> Any:
    return node.value if isinstance(node, ast.Constant) else None


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _strings(node: ast.expr | None) -> list[str]:
    if isinstance(node, (ast.List, ast.Tuple)):
        return [
            e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
    return []


def _short_type(node: ast.expr) -> str:
    """``sa.String(255)`` as ``String(255)``."""
    if isinstance(node, ast.Call):
        args = ",".join(ast.unparse(a) for a in node.args)
        name = _short_type(node.func)
        return f"{name}({args})"[:64] if args else name
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _tail(node)
    return ast.unparse(node)[:64]


def _annotation(node: ast.expr | None) -> tuple[str | None, bool]:
    """A Python annotation as a type and whether it allows None. ``Mapped[...]`` is looked through."""
    if node is None:
        return None, True
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _annotation(ast.parse(node.value, mode="eval").body)
        except (SyntaxError, ValueError):
            return node.value[:64], False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        if isinstance(node.right, ast.Constant) and node.right.value is None:
            return _annotation(node.left)[0], True
        if isinstance(node.left, ast.Constant) and node.left.value is None:
            return _annotation(node.right)[0], True
    if isinstance(node, ast.Subscript):
        outer = _tail(node.value)
        if outer == "Mapped":
            return _annotation(node.slice)
        if outer == "Optional":
            return _annotation(node.slice)[0], True
        if outer == "Annotated" and isinstance(node.slice, ast.Tuple) and node.slice.elts:
            return _annotation(node.slice.elts[0])
    return _short_type(node), False


def _is_list_annotation(node: ast.expr | None) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.lower().startswith(("list[", "set["))
    if isinstance(node, ast.Subscript):
        outer = _tail(node.value)
        if outer in ("Mapped", "Optional"):
            return _is_list_annotation(node.slice)
        return outer.lower() in ("list", "set")
    return False


def _string_value(node: ast.expr, depth: int = 0) -> str | None:
    """The text of a string expression, with what's only known at run time as a placeholder."""
    if depth > 50:
        return None
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            literal = _string_value(value, depth + 1) if isinstance(value, ast.Constant) else None
            parts.append(literal if literal is not None else " __dyn__ ")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _string_value(node.left, depth + 1)
        right = _string_value(node.right, depth + 1)
        if left is None and right is None:
            return None
        return (left if left is not None else " __dyn__ ") + (
            right if right is not None else " __dyn__ "
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _string_value(node.left, depth + 1)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    ):
        return _string_value(node.func.value, depth + 1)
    return None


def _snake(name: str) -> str:
    out = ""
    for i, c in enumerate(name):
        out += ("_" if c.isupper() and i and not name[i - 1].isupper() else "") + c.lower()
    return out


@dataclass
class _PyFile:
    path: str
    tree: ast.Module
    imports: dict[str, tuple[str, str | None, int]] = field(
        default_factory=dict
    )  # local -> module, name, level
    stars: list[tuple[str, int]] = field(default_factory=list)
    names: set[str] = field(default_factory=set)
    classes: dict[str, ast.ClassDef] = field(default_factory=dict)
    strings: dict[str, tuple[str, int]] = field(
        default_factory=dict
    )  # NAME or Class.NAME -> text, line
    top_modules: set[str] = field(default_factory=set)
    alembic: bool = False


@dataclass
class _Usage:
    confidence: float
    line: int
    path: str


# ---------------------------------------------------------------- the analysis


class _Analysis:
    def __init__(self, ctx: RepoContext) -> None:
        self.ctx = ctx
        self.limits: set[str] = set()
        self.raw: list[_Table] = []
        self.many: list[
            tuple[str, str, str, int]
        ] = []  # from table, to table or class:Name, path, line
        self.usage: dict[tuple[str, str, str], _Usage] = {}
        self.sql_refs: dict[str, tuple[str, str | None, str, int]] = {}
        self.sql_columns: dict[str, dict[str, tuple[str, str, int]]] = {}
        self.joins: list[tuple[str, str, str, str, str, int]] = []
        self.computed = 0
        self.not_replayed = 0
        self.views = 0
        self.unsupported: set[str] = set()
        self.files: dict[str, _PyFile] = {}
        self.by_tail: dict[str, list[str]] = {}
        self.models: dict[tuple[str, str], str] = {}  # (path, class or variable) -> table
        self.classes_of: set[tuple[str, str]] = (
            set()
        )  # which of models are classes, so calling one builds a row
        self.kinds: dict[tuple[str, str], str] = {}
        self.model_names: set[str] = set()
        self.string_names: set[str] = set()
        self.django_user: str | None = None
        self.seen_ddl: set[tuple[str, int, str]] = set()
        self.referenced: set[tuple[str, str]] = set()
        self.parsed: dict[tuple[str, bool], _Sql] = {}

    # ------------------------------------------------------------ driving

    def run(self) -> dict[str, Any]:
        paths = [p for p in self.ctx.files if not (is_test_file(p) or is_fixture(p))]
        for path in paths:
            if path.endswith(".py"):
                self._load_python(path)
            elif path.endswith(".sql"):
                self._read_sql_file(path)
        self._index_python()
        self._read_classes()
        for path in sorted(self.files):
            self._read_core_tables(self.files[path])
            self._read_alembic(self.files[path])
        self._read_prisma([p for p in paths if p.endswith(".prisma")])
        self._read_typeorm(
            [p for p in paths if p.endswith((".ts", ".tsx")) and not p.endswith(".d.ts")]
        )
        self._note_gaps(paths, len(self.ctx.files) - len(paths))
        return self._answer()

    def _limit(self, text: str) -> None:
        self.limits.add(text)

    def _text(self, path: str) -> str | None:
        text = read_text(self.ctx, path)
        if text is None:
            self._limit(f"{path} is too big or unreadable, so it wasn't read")
        return text

    # ------------------------------------------------------------ Python: files and names

    def _load_python(self, path: str) -> None:
        text = self._text(path)
        if text is None:
            return
        try:
            self.files[path] = self._index_file(path, ast.parse(text))
        except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
            self._limit(f"{path} couldn't be parsed ({type(exc).__name__}), so it wasn't read")

    def _index_file(self, path: str, tree: ast.Module) -> _PyFile:
        file = _PyFile(path, tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                file.top_modules.add(module.split(".")[0])
                for alias in node.names:
                    if alias.name == "*":
                        file.stars.append((module, node.level))
                    else:
                        file.imports[alias.asname or alias.name] = (module, alias.name, node.level)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    file.top_modules.add(top)
                    file.imports[alias.asname or top] = (
                        alias.name if alias.asname else top,
                        None,
                        0,
                    )
        file.alembic = "alembic" in file.top_modules
        self.unsupported |= file.top_modules & UNSUPPORTED_PY
        for stmt in self._module_statements(tree.body):
            if isinstance(stmt, ast.ClassDef):
                file.names.add(stmt.name)
                file.classes[stmt.name] = stmt
                for member in stmt.body:
                    self._index_string(file, member, f"{stmt.name}.")
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                file.names.add(stmt.name)
            else:
                self._index_string(file, stmt, "")
                for target in stmt.targets if isinstance(stmt, ast.Assign) else []:
                    if isinstance(target, ast.Name):
                        file.names.add(target.id)
        if path.rsplit("/", 1)[-1].startswith("settings") and self.django_user is None:
            for stmt in tree.body:
                if isinstance(stmt, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "AUTH_USER_MODEL" for t in stmt.targets
                ):
                    value = _literal(stmt.value)
                    if isinstance(value, str):
                        self.django_user = value
        return file

    def _module_statements(self, body: list[ast.stmt]) -> Iterator[ast.stmt]:
        """Module-level statements, looking inside ``if`` and ``try``."""
        for stmt in body:
            yield stmt
            if isinstance(stmt, ast.If):
                yield from self._module_statements(stmt.body + stmt.orelse)
            elif isinstance(stmt, ast.Try):
                yield from self._module_statements(stmt.body + stmt.orelse + stmt.finalbody)

    def _index_string(self, file: _PyFile, stmt: ast.stmt, prefix: str) -> None:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            target, value = stmt.target, stmt.value
        if not isinstance(target, ast.Name) or value is None or target.id == "__tablename__":
            return
        text = _string_value(value)
        if text is None:
            return
        head = _head_of(text)
        if head in SQL_HEADS or head in SQL_PREAMBLE:
            file.strings[prefix + target.id] = (text, value.lineno)

    def _index_python(self) -> None:
        for path, file in self.files.items():
            parts = path.removesuffix(".py").split("/")
            tail = parts[-2] if parts[-1] == "__init__" and len(parts) > 1 else parts[-1]
            self.by_tail.setdefault(tail, []).append(path)
            self.string_names |= {name.rsplit(".", 1)[-1] for name in file.strings}

    def _module_file(self, importer: str, module: str, level: int) -> str | None:
        """The repository file for a module, or None for one from outside the repository."""
        if level:
            base = importer.split("/")[:-1]
            if level > 1:
                base = base[: -(level - 1)] if level - 1 <= len(base) else []
            stem = "/".join([*base, *module.split(".")]) if module else "/".join(base)
            for candidate in (f"{stem}.py", f"{stem}/__init__.py" if stem else "__init__.py"):
                if candidate in self.files:
                    return candidate
            return None
        stem = module.replace(".", "/")
        last = module.rsplit(".", 1)[-1]
        options = [
            p
            for p in self.by_tail.get(last, [])
            if p in (f"{stem}.py", f"{stem}/__init__.py")
            or p.endswith((f"/{stem}.py", f"/{stem}/__init__.py"))
        ]
        if not options:
            return None
        # The nearest file to the importer, when several source roots repeat a module name.
        dirs = importer.split("/")

        def distance(option: str) -> tuple[int, str]:
            shared = 0
            for a, b in zip(dirs, option.split("/"), strict=False):
                if a != b:
                    break
                shared += 1
            return (-shared, option)

        return min(options, key=distance)

    def _resolve(
        self, path: str, name: str, seen: set[tuple[str, str]] | None = None
    ) -> tuple[str, str] | None:
        """Where a name used in a file is defined, following imports and re-exports through the repository."""
        seen = set() if seen is None else seen
        if (path, name) in seen or len(seen) > 12:
            return None
        seen.add((path, name))
        file = self.files.get(path)
        if file is None:
            return None
        if name in file.names:
            return path, name
        imported = file.imports.get(name)
        if imported is not None:
            module, original, level = imported
            if original is None:
                return None
            target = self._module_file(path, module, level)
            return self._resolve(target, original, seen) if target is not None else None
        for module, level in file.stars:
            target = self._module_file(path, module, level)
            if target is not None:
                found = self._resolve(target, name, seen)
                if found is not None:
                    return found
        return None

    def _resolve_module(self, path: str, parts: list[str]) -> str | None:
        """The file named by the leading parts of a dotted expression, such as ``models`` in ``models.Order``."""
        file = self.files.get(path)
        imported = file.imports.get(parts[0]) if file and parts else None
        if imported is None:
            return None
        module, original, level = imported
        rest = parts[1:]
        if original is None:
            return self._module_file(path, ".".join([module, *rest]), level)
        base = f"{module}.{original}" if module else original
        return self._module_file(path, ".".join([base, *rest]), level)

    def _resolve_expr(self, path: str, node: ast.expr) -> tuple[str, str] | None:
        if isinstance(node, ast.Name):
            return self._resolve(path, node.id)
        parts = _dotted(node)
        if parts is None or len(parts) < 2:
            return None
        target = self._resolve_module(path, parts[:-1])
        return self._resolve(target, parts[-1]) if target else None

    def _table_at(self, path: str, node: ast.expr) -> str | None:
        """The table a class or ``Table`` variable names, or None for anything else."""
        if _tail(node) not in self.model_names:
            return None
        resolved = self._resolve_expr(path, node)
        return self.models.get(resolved) if resolved else None

    # ------------------------------------------------------------ Python: classes

    def _bases(self, path: str, cls: ast.ClassDef) -> list[tuple[str, ast.ClassDef]]:
        found = []
        for base in cls.bases:
            resolved = self._resolve_expr(path, base)
            target = self.files.get(resolved[0]) if resolved else None
            if resolved and target is not None and resolved[1] in target.classes:
                found.append((resolved[0], target.classes[resolved[1]]))
        return found

    def _is_django(self, path: str, cls: ast.ClassDef, seen: set[str] | None = None) -> bool:
        seen = set() if seen is None else seen
        key = f"{path}:{cls.name}"
        if key in seen:
            return False
        seen.add(key)
        file = self.files[path]
        for base in cls.bases:
            if _tail(base) in DJANGO_BASES and "django" in file.top_modules:
                dotted = _dotted(base)
                if dotted is None or len(dotted) > 1 or self._resolve(path, dotted[0]) is None:
                    return True
        return any(self._is_django(p, c, seen) for p, c in self._bases(path, cls))

    def _is_sqlmodel(self, path: str, cls: ast.ClassDef, seen: set[str] | None = None) -> bool:
        seen = set() if seen is None else seen
        if f"{path}:{cls.name}" in seen:
            return False
        seen.add(f"{path}:{cls.name}")
        if any(_tail(b) == "SQLModel" for b in cls.bases):
            return True
        return any(self._is_sqlmodel(p, c, seen) for p, c in self._bases(path, cls))

    def _meta(self, cls: ast.ClassDef) -> dict[str, Any]:
        for stmt in cls.body:
            if isinstance(stmt, ast.ClassDef) and stmt.name == "Meta":
                out: dict[str, Any] = {}
                for item in stmt.body:
                    if isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name):
                                out[target.id] = _literal(item.value)
                return out
        return {}

    def _assigned(self, cls: ast.ClassDef, name: str) -> ast.expr | None:
        for stmt in cls.body:
            if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in stmt.targets
            ):
                return stmt.value
            if (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and stmt.target.id == name
            ):
                return stmt.value
        return None

    def _name_class(self, path: str, cls: ast.ClassDef) -> tuple[str, str] | None:
        """The table and source of a class that declares one directly, without following bases to other tables."""
        computed = f"{path}:{cls.lineno}: {cls.name} computes its table name, so it isn't known"
        table_kw = _literal(next((k.value for k in cls.keywords if k.arg == "table"), None))
        named = self._assigned(cls, "__tablename__")
        if _literal(self._assigned(cls, "__abstract__")) is True:
            return None
        if table_kw is True and self._is_sqlmodel(path, cls):
            value = _literal(named) if named is not None else cls.name.lower()
            if isinstance(value, str):
                return value, "sqlmodel"
            self._limit(computed)
            return None
        if named is not None:
            value = _literal(named)
            if isinstance(value, str):
                return value, "sqlalchemy"
            self._limit(computed)
            return None
        core = self._assigned(cls, "__table__")
        if isinstance(core, ast.Call) and _tail(core.func) == "Table" and core.args:
            value = _literal(core.args[0])
            if isinstance(value, str):
                return value, "sqlalchemy"
        for stmt in cls.body:
            if (
                isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                and stmt.name == "__tablename__"
            ):
                self._limit(computed)
                return None
        if any(
            _dotted(b) == ["db", "Model"] for b in cls.bases
        ):  # Flask-SQLAlchemy names the table for you
            return _snake(cls.name), "sqlalchemy"
        if not self._is_django(path, cls):
            return None
        meta = self._meta(cls)
        if meta.get("abstract") is True:
            return None
        if meta.get("proxy") is True:
            for base_path, base in self._bases(path, cls):
                found = self._name_class(base_path, base)
                if found:
                    return found
            return None
        db_table = meta.get("db_table")
        if isinstance(db_table, str):
            return db_table, "django"
        label = meta.get("app_label")
        if not isinstance(label, str):
            parts = path.split("/")[:-1]
            if parts and parts[-1] == "models":
                parts = parts[:-1]
            label = parts[-1] if parts else ""
        return f"{label}_{cls.name.lower()}" if label else cls.name.lower(), "django"

    def _read_classes(self) -> None:
        all_classes = [
            (path, cls) for path in sorted(self.files) for cls in self.files[path].classes.values()
        ]
        direct: set[tuple[str, str]] = set()
        for path, cls in all_classes:
            found = self._name_class(path, cls)
            if found:
                key = (path, cls.name)
                direct.add(key)
                self.models[key] = found[0]
                self.classes_of.add(key)
                self.model_names.add(cls.name)
                self.kinds[key] = found[1]
        # A subclass of a mapped class with no table of its own shares its parent's table.
        for _ in range(4):
            changed = False
            for path, cls in all_classes:
                key = (path, cls.name)
                if (
                    key in self.models
                    or self._assigned(cls, "__abstract__") is not None
                    or self._meta(cls).get("abstract")
                ):
                    continue
                for base_path, base in self._bases(path, cls):
                    base_key = (base_path, base.name)
                    if base_key in self.models and self.kinds.get(base_key) in (
                        "sqlalchemy",
                        "sqlmodel",
                    ):
                        self.models[key] = self.models[base_key]
                        self.classes_of.add(key)
                        self.model_names.add(cls.name)
                        self.kinds[key] = self.kinds[base_key]
                        changed = True
                        break
            if not changed:
                break
        for path in sorted(self.files):
            self._bind_core_tables(self.files[path])
        for path, cls in all_classes:
            key = (path, cls.name)
            if key in self.models:
                self._read_class(path, cls, self.models[key], self.kinds[key], key not in direct)

    def _bind_core_tables(self, file: _PyFile) -> None:
        if not file.top_modules & SQLALCHEMY_MODULES:
            return
        for stmt in self._module_statements(file.tree.body):
            if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
                name = self._core_table_name(file, stmt.value)
                if name:
                    for target in stmt.targets:
                        if isinstance(target, ast.Name):
                            self.models[(file.path, target.id)] = name
                            self.model_names.add(target.id)

    def _core_table_name(self, file: _PyFile, call: ast.Call) -> str | None:
        """``Table("name", metadata, ...)`` from SQLAlchemy, not some other library's ``Table``."""
        if (
            _tail(call.func) != "Table"
            or len(call.args) < 2
            or isinstance(call.args[1], ast.Constant)
        ):
            return None
        if _dotted(call.func) == ["Table"]:
            imported = file.imports.get("Table")
            if imported is None or not imported[0].startswith("sqlalchemy"):
                return None
        name = _literal(call.args[0])
        return name if isinstance(name, str) else None

    def _inherited(
        self, path: str, cls: ast.ClassDef, seen: set[str] | None = None
    ) -> list[tuple[str, ast.ClassDef]]:
        """The bases whose fields a table takes on: mixins and abstract classes, nearest last."""
        seen = set() if seen is None else seen
        out: list[tuple[str, ast.ClassDef]] = []
        for base_path, base in self._bases(path, cls):
            key = f"{base_path}:{base.name}"
            if key in seen or (base_path, base.name) in self.models:
                continue
            seen.add(key)
            out += self._inherited(base_path, base, seen)
            out.append((base_path, base))
        return out

    def _read_class(
        self, path: str, cls: ast.ClassDef, name: str, source: str, shares_table: bool
    ) -> None:
        table = _Table(name, None, source, path, cls.lineno, partial=shares_table)
        for owner_path, owner in [*self._inherited(path, cls), (path, cls)]:
            if source == "django":
                self._read_django_body(owner_path, owner, table)
            else:
                self._read_sqlalchemy_body(owner_path, owner, table, source)
        if source == "django":
            self._django_defaults(path, cls, table)
        self.raw.append(table)

    # ------------------------------------------------------------ SQLAlchemy and SQLModel

    def _fk_target(self, path: str, node: ast.expr) -> tuple[str, str | None] | None:
        """``ForeignKey``'s argument: ``"table.col"``, ``"schema.table.col"``, ``Model.col`` or ``table.c.col``."""
        value = _literal(node)
        if isinstance(value, str):
            parts = value.split(".")
            if len(parts) < 2 or not all(parts):
                return None
            return parts[-2], parts[-1]
        if isinstance(node, ast.Attribute):
            inner = node.value
            if isinstance(inner, ast.Attribute) and inner.attr == "c":
                inner = inner.value
            table = self._table_at(path, inner)
            return (table, node.attr) if table else None
        return None

    def _sa_column(
        self,
        path: str,
        call: ast.Call,
        attribute: str | None,
        annotation: ast.expr | None,
        table: _Table,
        line: int | None = None,
    ) -> None:
        name = attribute
        type_text: str | None = None
        nullable: bool | None = None
        if annotation is not None:
            type_text, nullable = _annotation(annotation)
        fks: list[tuple[str, str | None]] = []
        for arg in call.args:
            value = _literal(arg)
            if isinstance(value, str):
                name = value
            elif isinstance(arg, ast.Call) and _tail(arg.func) == "ForeignKey":
                target = self._fk_target(path, arg.args[0]) if arg.args else None
                if target:
                    fks.append(target)
            elif isinstance(arg, (ast.Name, ast.Attribute)) or (
                isinstance(arg, ast.Call) and _tail(arg.func) not in NOT_A_COLUMN_TYPE
            ):
                type_text = _short_type(arg)
        type_kw = _kwarg(call, "type_")
        if type_kw is not None:
            type_text = _short_type(type_kw)
        named = _literal(_kwarg(call, "name"))
        if isinstance(named, str):
            name = named
        if name is None:
            return
        at = line if line is not None else call.lineno
        primary = _literal(_kwarg(call, "primary_key")) is True
        unique = _literal(_kwarg(call, "unique")) is True
        explicit = _literal(_kwarg(call, "nullable"))
        if isinstance(explicit, bool):
            nullable = explicit
        elif primary:
            nullable = False
        elif nullable is None:
            nullable = True
        table.add(_Column(name, type_text, nullable, primary, path, at, unique))
        for to_table, to_column in fks:
            table.fks.append(_Fk(name, to_table, to_column, path, at))

    def _sa_field(self, path: str, stmt: ast.AnnAssign, attribute: str, table: _Table) -> None:
        """A SQLModel field: an annotation with an optional ``Field(...)``."""
        value = stmt.value
        type_text, nullable = _annotation(stmt.annotation)
        primary, unique = False, False
        fk: str | None = None
        if isinstance(value, ast.Call) and _tail(value.func) == "Field":
            inner = _kwarg(value, "sa_column")
            if isinstance(inner, ast.Call):
                self._sa_column(path, inner, attribute, stmt.annotation, table)
                return
            primary = _literal(_kwarg(value, "primary_key")) is True
            unique = _literal(_kwarg(value, "unique")) is True
            target = _literal(_kwarg(value, "foreign_key"))
            fk = target if isinstance(target, str) else None
            explicit = _literal(_kwarg(value, "nullable"))
            if isinstance(explicit, bool):
                nullable = explicit
        table.add(
            _Column(
                attribute,
                type_text,
                False if primary else nullable,
                primary,
                path,
                stmt.lineno,
                unique,
            )
        )
        if fk and "." in fk:
            parts = fk.split(".")
            table.fks.append(_Fk(attribute, parts[-2], parts[-1], path, stmt.lineno))

    def _read_sqlalchemy_body(
        self, path: str, cls: ast.ClassDef, table: _Table, source: str
    ) -> None:
        for stmt in cls.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                attribute, value = stmt.targets[0].id, stmt.value
                if attribute == "__table_args__":
                    self._table_args(path, value, table)
                elif attribute == "__table__" and isinstance(value, ast.Call):
                    self._core_table_body(path, value, table)
                elif isinstance(value, ast.Call):
                    tail = _tail(value.func)
                    if tail in ("Column", "mapped_column"):
                        self._sa_column(path, value, attribute, None, table)
                    elif tail == "relationship":
                        self._sa_relationship(path, value, table)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                self._annotated_field(path, stmt, stmt.target.id, table, source)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(_tail(d) == "declared_attr" for d in stmt.decorator_list):
                    returned = next((s.value for s in stmt.body if isinstance(s, ast.Return)), None)
                    if isinstance(returned, ast.Call) and _tail(returned.func) in (
                        "Column",
                        "mapped_column",
                    ):
                        self._sa_column(path, returned, stmt.name, None, table)

    def _annotated_field(
        self, path: str, stmt: ast.AnnAssign, attribute: str, table: _Table, source: str
    ) -> None:
        value = stmt.value
        outer = _tail(stmt.annotation.value) if isinstance(stmt.annotation, ast.Subscript) else ""
        call = value if isinstance(value, ast.Call) else None
        tail = _tail(call.func) if call else ""
        if call and tail in ("relationship", "Relationship"):
            self._sa_relationship(path, call, table)
        elif call and tail in ("Column", "mapped_column"):
            self._sa_column(
                path, call, attribute, stmt.annotation if outer == "Mapped" else None, table
            )
        elif outer == "Mapped":
            if value is None and not self._mapped_is_relation(stmt.annotation):
                bare = ast.Call(ast.Name("mapped_column"), [], [])
                self._sa_column(path, bare, attribute, stmt.annotation, table, stmt.lineno)
        elif (
            source == "sqlmodel"
            and not attribute.startswith("_")
            and outer != "ClassVar"
            and attribute != "model_config"
            and not _is_list_annotation(stmt.annotation)
        ):
            self._sa_field(path, stmt, attribute, table)

    def _mapped_is_relation(self, annotation: ast.expr) -> bool:
        """``Mapped["Customer"]`` or ``Mapped[list["Order"]]`` with no column call names a mapped class."""
        inner = annotation.slice if isinstance(annotation, ast.Subscript) else annotation
        if _is_list_annotation(inner):
            return True
        type_text, _ = _annotation(inner)
        return bool(type_text) and type_text in self.model_names

    def _sa_relationship(self, path: str, call: ast.Call, table: _Table) -> None:
        secondary = _kwarg(call, "secondary") or _kwarg(call, "link_model")
        if secondary is None or not call.args:
            return
        target = _literal(call.args[0])
        name = target if isinstance(target, str) else _tail(call.args[0])
        if name:
            self.many.append((table.name, f"class:{name}", path, call.lineno))

    def _table_args(self, path: str, value: ast.expr, table: _Table) -> None:
        items: list[ast.expr] = list(value.elts) if isinstance(value, ast.Tuple) else [value]
        for item in items:
            if isinstance(item, ast.Dict):
                for key, val in zip(item.keys, item.values, strict=False):
                    if _literal(key) == "schema" and isinstance(_literal(val), str):
                        table.schema = _literal(val)
            elif isinstance(item, ast.Call):
                self._table_constraint(path, item, table)

    def _table_constraint(self, path: str, call: ast.Call, table: _Table) -> None:
        tail = _tail(call.func)
        names = [
            a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
        ]
        if tail == "PrimaryKeyConstraint":
            table.primary += names
        elif tail == "UniqueConstraint" and len(names) == 1:
            table.unique += names
        elif tail == "ForeignKeyConstraint" and len(call.args) >= 2:
            for column, target in zip(_strings(call.args[0]), _strings(call.args[1]), strict=False):
                parts = target.split(".")
                if len(parts) >= 2:
                    table.fks.append(_Fk(column, parts[-2], parts[-1], path, call.lineno))

    def _core_table_body(self, path: str, call: ast.Call, table: _Table) -> None:
        schema = _literal(_kwarg(call, "schema"))
        if isinstance(schema, str):
            table.schema = schema
        for arg in call.args[1:]:
            if isinstance(arg, ast.Call):
                if _tail(arg.func) == "Column":
                    self._sa_column(path, arg, None, None, table)
                else:
                    self._table_constraint(path, arg, table)

    def _read_core_tables(self, file: _PyFile) -> None:
        if not file.top_modules & SQLALCHEMY_MODULES or file.alembic:
            return
        for node in ast.walk(file.tree):
            if isinstance(node, ast.Call):
                name = self._core_table_name(file, node)
                if name is not None:
                    table = _Table(name, None, "sqlalchemy", file.path, node.lineno)
                    self._core_table_body(file.path, node, table)
                    self.raw.append(table)

    def _read_alembic(self, file: _PyFile) -> None:
        if not file.alembic:
            return
        for stmt in file.tree.body:
            if not isinstance(
                stmt, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) or not stmt.name.startswith("upgrade"):
                continue
            batches: dict[str, str] = {}
            for node in ast.walk(stmt):
                if isinstance(node, (ast.With, ast.AsyncWith)):
                    for item in node.items:
                        expr = item.context_expr
                        if (
                            isinstance(expr, ast.Call)
                            and _tail(expr.func) == "batch_alter_table"
                            and expr.args
                            and isinstance(_literal(expr.args[0]), str)
                            and isinstance(item.optional_vars, ast.Name)
                        ):
                            batches[item.optional_vars.id] = _literal(expr.args[0])
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self._alembic_call(file.path, node, node.func, batches)

    def _alembic_call(
        self, path: str, call: ast.Call, func: ast.Attribute, batches: dict[str, str]
    ) -> None:
        receiver = func.value.id if isinstance(func.value, ast.Name) else ""
        op = func.attr
        batch = receiver in batches
        if receiver != "op" and not batch:
            return
        args = call.args
        if op == "create_table" and not batch and args:
            name = _literal(args[0])
            if isinstance(name, str):
                table = _Table(name, None, "alembic", path, call.lineno)
                self._core_table_body(path, call, table)
                self.raw.append(table)
        elif op == "add_column":
            name = batches[receiver] if batch else _literal(args[0]) if args else None
            added = args[0] if batch and args else args[1] if len(args) > 1 else None
            if isinstance(name, str) and isinstance(added, ast.Call):
                table = _Table(name, None, "alembic", path, call.lineno, partial=True)
                self._sa_column(path, added, None, None, table)
                self.raw.append(table)
        elif op == "create_foreign_key":
            source = batches[receiver] if batch else _literal(args[1]) if len(args) > 1 else None
            offset = 1 if batch else 2
            if isinstance(source, str) and len(args) >= offset + 3:
                target = _literal(args[offset])
                if isinstance(target, str):
                    table = _Table(source, None, "alembic", path, call.lineno, partial=True)
                    for column, to_column in zip(
                        _strings(args[offset + 1]), _strings(args[offset + 2]), strict=False
                    ):
                        table.fks.append(_Fk(column, target, to_column, path, call.lineno))
                    self.raw.append(table)

    # ------------------------------------------------------------ Django

    def _django_target(self, path: str, node: ast.expr | None, own_table: str) -> str | None:
        if node is None:
            return None
        value = _literal(node)
        if isinstance(value, str):
            return own_table if value == "self" else self._django_by_label(path, value)
        if isinstance(node, ast.Call) and _tail(node.func) == "get_user_model":
            return self._django_user_table(path)
        if isinstance(node, ast.Attribute) and node.attr == "AUTH_USER_MODEL":
            return self._django_user_table(path)
        return self._table_at(path, node) or _tail(node).lower() or None

    def _django_user_table(self, path: str) -> str:
        return self._django_by_label(path, self.django_user) if self.django_user else "auth_user"

    def _django_by_label(self, path: str, label: str) -> str:
        name = label.rsplit(".", 1)[-1]
        options = sorted(
            key for key in self.models if key[1] == name and self.kinds.get(key) == "django"
        )
        if options:
            near = [k for k in options if k[0].split("/")[:-1] == path.split("/")[:-1]]
            return self.models[(near or options)[0]]
        return label.replace(".", "_").lower()

    def _read_django_body(self, path: str, cls: ast.ClassDef, table: _Table) -> None:
        for stmt in cls.body:
            if not (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                continue
            attribute, call = stmt.targets[0].id, stmt.value
            if not isinstance(call, ast.Call):
                continue
            tail = _tail(call.func)
            relation = tail in ("ForeignKey", "OneToOneField")
            to = call.args[0] if call.args else _kwarg(call, "to")
            if tail == "ManyToManyField":
                target = self._django_target(path, to, table.name)
                if target:
                    self.many.append((table.name, target, path, call.lineno))
                continue
            if not relation and not tail.endswith("Field"):
                continue
            db_column = _literal(_kwarg(call, "db_column"))
            column = (
                db_column if isinstance(db_column, str) else attribute + ("_id" if relation else "")
            )
            primary = _literal(_kwarg(call, "primary_key")) is True
            nullable = _literal(_kwarg(call, "null")) is True
            one = tail == "OneToOneField"
            unique = _literal(_kwarg(call, "unique")) is True or one
            table.add(
                _Column(column, tail, nullable and not primary, primary, path, call.lineno, unique)
            )
            if relation:
                target = self._django_target(path, to, table.name)
                to_field = _literal(_kwarg(call, "to_field"))
                if target:
                    remote = to_field if isinstance(to_field, str) else None
                    table.fks.append(_Fk(column, target, remote, path, call.lineno, one))

    def _django_defaults(self, path: str, cls: ast.ClassDef, table: _Table) -> None:
        """Multi-table inheritance's parent link, and the ``id`` Django adds when no field is the key."""
        for base_path, base in self._bases(path, cls):
            base_key = (base_path, base.name)
            if base_key in self.models and self.models[base_key] != table.name:
                column = f"{base.name.lower()}_ptr_id"
                table.add(_Column(column, "OneToOneField", False, True, path, cls.lineno, True))
                table.fks.append(_Fk(column, self.models[base_key], None, path, cls.lineno, True))
        if not any(c.primary_key for c in table.columns.values()):
            key = _Column("id", "AutoField", False, True, path, cls.lineno)
            table.columns = {"id": key, **table.columns}

    # ------------------------------------------------------------ SQL files and strings

    def _read_sql_file(self, path: str) -> None:
        text = self._text(path)
        if text is not None:
            sql = _read_sql(text, trusted=True)
            self._define(sql, path, 1, text)
            self._apply_use(sql, path, f"file:{path}", 0.6, _offset(1))

    def _parsed(self, text: str, trusted: bool) -> _Sql:
        key = (text, trusted)
        if key not in self.parsed:
            self.parsed[key] = _read_sql(text, trusted=trusted)
        return self.parsed[key]

    def _define(self, sql: _Sql, path: str, base: int, text: str) -> None:
        """Record the tables a piece of SQL defines. A string is read in several places but counts once."""
        key = (path, base, text)
        if key in self.seen_ddl:
            return
        self.seen_ddl.add(key)
        self.computed += sql.computed
        self.not_replayed += sql.not_replayed
        self.views += sql.views
        for table in sql.tables:
            copy = _Table(
                table.name,
                table.schema,
                table.source,
                path,
                table.line + base,
                partial=table.partial,
            )
            copy.primary, copy.unique = list(table.primary), list(table.unique)
            copy.columns = {
                low: _Column(
                    c.name, c.type, c.nullable, c.primary_key, path, c.line + base, c.unique
                )
                for low, c in table.columns.items()
            }
            copy.fks = [
                _Fk(f.column, f.to_table, f.to_column, path, f.line + base, f.one_to_one)
                for f in table.fks
            ]
            self.raw.append(copy)
        for line in sql.malformed:
            self._limit(f"{path}:{line + base}: a CREATE TABLE isn't closed, so it wasn't read")

    def _apply_use(
        self, sql: _Sql, path: str, symbol: str, confidence: float, line_for: _LineOf
    ) -> None:
        """Record what a piece of SQL uses, cited at ``line_for``: a constant's use cites the line referring to it."""
        for ref in sql.refs:
            low = ref.name.lower()
            self._use(symbol, low, ref.access, confidence, path, line_for(ref.line))
            self.sql_refs.setdefault(low, (ref.name, ref.schema, path, line_for(ref.line)))
        for table, column, line in sql.columns:
            self.sql_columns.setdefault(table, {}).setdefault(
                column.lower(), (column, path, line_for(line))
            )
        for left, left_col, right, right_col, line in sql.joins:
            self.joins.append((left, left_col, right, right_col, path, line_for(line)))

    def _use(
        self, symbol: str, table: str, access: str, confidence: float, path: str, line: int
    ) -> None:
        key = (symbol, table.lower(), access)
        now = self.usage.get(key)
        if now is None or (confidence, -line) > (now.confidence, -now.line):
            self.usage[key] = _Usage(confidence, line, path)

    def _read_unreferenced_strings(self, file: _PyFile) -> None:
        """SQL constants nothing refers to: their DDL still defines tables, and their other SQL counts for the file."""
        for name, (text, line) in sorted(file.strings.items()):
            if (file.path, name) not in self.referenced:
                sql = self._parsed(text, False)
                self._define(sql, file.path, line, text)
                self._apply_use(sql, file.path, f"file:{file.path}", 0.5, _offset(line))

    # ------------------------------------------------------------ Prisma

    def _read_prisma(self, paths: list[str]) -> None:
        models: list[tuple[str, str, int, list[tuple[str, int]]]] = []
        for path in paths:
            text = self._text(path)
            if text is None:
                continue
            current: tuple[str, str, int, list[tuple[str, int]]] | None = None
            for number, raw in enumerate(text.splitlines(), 1):
                comment = raw.find("//")
                if comment >= 0 and '"' not in raw[:comment]:
                    raw = raw[:comment]
                words = raw.split()
                if current is None:
                    if len(words) == 3 and words[2] == "{" and words[0] == "model":
                        current = (path, words[1], number, [])
                        models.append(current)
                elif words[:1] == ["}"]:
                    current = None
                elif words:
                    current[3].append((raw, number))
        self._prisma_tables(models)

    def _prisma_tables(self, models: list[tuple[str, str, int, list[tuple[str, int]]]]) -> None:
        names = {m[1] for m in models}

        def between(line: str, start: str, stop: str) -> str | None:
            at = line.find(start)
            if at < 0:
                return None
            at += len(start)
            end = line.find(stop, at)
            return line[at:end] if end >= 0 else None

        mapped: dict[str, str] = {}
        lists: dict[str, set[str]] = {}
        for _, model, _, lines in models:
            mapped[model] = model
            for raw, _ in lines:
                stripped = raw.strip()
                if stripped.startswith("@@map("):
                    mapped[model] = between(stripped, '"', '"') or model
                words = stripped.split()
                if len(words) >= 2 and words[1].endswith("[]") and words[1][:-2] in names:
                    lists.setdefault(model, set()).add(words[1][:-2])
        done: set[tuple[str, str]] = set()
        for path, model, line, lines in models:
            table = _Table(mapped[model], None, "prisma", path, line)
            columns: dict[str, str] = {}
            for raw, _ in lines:
                words = raw.split()
                if len(words) >= 2 and not raw.strip().startswith("@@"):
                    columns[words[0]] = between(raw, '@map("', '"') or words[0]
            for raw, number in lines:
                stripped = raw.strip()
                if stripped.startswith("@@"):
                    inner = between(stripped, "[", "]")
                    fields = [
                        columns.get(w.strip(), w.strip())
                        for w in (inner or "").split(",")
                        if w.strip()
                    ]
                    if stripped.startswith("@@id"):
                        table.primary += fields
                    elif stripped.startswith("@@unique") and len(fields) == 1:
                        table.unique += fields
                    elif stripped.startswith("@@schema"):
                        table.schema = between(stripped, '"', '"')
                    continue
                words = stripped.split()
                if len(words) < 2 or not _WORD.fullmatch(words[0]):
                    continue
                kind = words[1].rstrip("?")
                base = kind.removesuffix("[]")
                if base in names:
                    fields_text = between(raw, "fields:", "]")
                    if fields_text is not None:
                        local = [w.strip(" [") for w in fields_text.split(",")]
                        remote = [
                            w.strip(" [")
                            for w in (between(raw, "references:", "]") or "").split(",")
                        ]
                        for index, column in enumerate(local):
                            to_column = (
                                remote[index] if index < len(remote) and remote[index] else None
                            )
                            table.fks.append(
                                _Fk(
                                    columns.get(column, column),
                                    mapped[base],
                                    to_column,
                                    path,
                                    number,
                                )
                            )
                    elif kind.endswith("[]") and model in lists.get(base, set()):
                        pair = (min(model, base), max(model, base))
                        if pair not in done:
                            done.add(pair)
                            self.many.append((mapped[pair[0]], mapped[pair[1]], path, number))
                    continue
                primary = "@id" in words
                unique = "@unique" in words
                table.add(
                    _Column(
                        columns[words[0]],
                        kind,
                        words[1].endswith("?"),
                        primary,
                        path,
                        number,
                        unique,
                    )
                )
            self.raw.append(table)

    # ------------------------------------------------------------ TypeORM

    def _read_typeorm(self, paths: list[str]) -> None:
        entities: list[tuple[_Table, list[tuple[str, str, str, int, str | None]]]] = []
        by_class: dict[str, str] = {}
        for path in paths:
            text = self._text(path)
            if text is not None and "@Entity" in text:
                self._read_entities(path, text, entities, by_class)
        for table, relations in entities:
            for kind, prop, target, line, join in relations:
                to_table = by_class.get(target, _snake(target))
                if kind == "ManyToMany":
                    self.many.append((table.name, to_table, table.path, line))
                else:
                    column = join or f"{prop}Id"
                    table.add(
                        _Column(column, None, True, False, table.path, line, kind == "OneToOne")
                    )
                    table.fks.append(
                        _Fk(column, to_table, None, table.path, line, kind == "OneToOne")
                    )
            self.raw.append(table)

    def _read_entities(
        self,
        path: str,
        text: str,
        entities: list[tuple[_Table, list[tuple[str, str, str, int, str | None]]]],
        by_class: dict[str, str],
    ) -> None:
        lines = text.splitlines()
        pending_entity: str | None = None
        table: _Table | None = None
        relations: list[tuple[str, str, str, int, str | None]] = []
        decorators: list[tuple[str, str, int]] = []
        depth = 0
        i = 0
        while i < len(lines):
            line = lines[i]
            number = i + 1
            i += 1
            decorator = _TS_DECORATOR.match(line)
            if decorator:
                name = decorator.group(1)
                body = line[decorator.end() :]
                # Decorator options can run over several lines; read to the balancing bracket.
                balance = body.count("(") - body.count(")")
                extra = 0
                while balance > 0 and i < len(lines) and extra < 50:
                    body += " " + lines[i]
                    balance += lines[i].count("(") - lines[i].count(")")
                    i += 1
                    extra += 1
                if name == "Entity":
                    named = _TS_NAME.search(body) or _TS_QUOTED.search(body)
                    pending_entity = named.group(1) if named else ""
                elif table is not None:
                    decorators.append((name, body, number))
                # A property can share the decorator's line.
                closing = body.rfind(")")
                rest = body[closing + 1 :] if closing >= 0 else ""
                if table is None or ":" not in rest:
                    continue
                line = rest
            klass = _TS_CLASS.search(line) if table is None else None
            if klass and pending_entity is not None:
                table_name = pending_entity or _snake(klass.group(1))
                table = _Table(table_name, None, "typeorm", path, number)
                relations = []
                by_class[klass.group(1)] = table_name
                entities.append((table, relations))
                pending_entity = None
                depth = line.count("{") - line.count("}")
                decorators = []
                continue
            if table is None:
                continue
            prop = _TS_PROPERTY.match(line)
            if prop and decorators:
                self._entity_property(path, prop.group(1), line, decorators, table, relations)
                decorators = []
            if not decorator:
                depth += line.count("{") - line.count("}")
            if depth <= 0:
                table = None

    def _entity_property(
        self,
        path: str,
        prop: str,
        line: str,
        decorators: list[tuple[str, str, int]],
        table: _Table,
        relations: list[tuple[str, str, str, int, str | None]],
    ) -> None:
        join: str | None = None
        has_join = False
        for name, body, _ in decorators:
            if name in ("JoinColumn", "JoinTable"):
                has_join = True
                found = _TS_NAME.search(body)
                join = found.group(1) if found and name == "JoinColumn" else join
        for name, body, number in decorators:
            if name in TYPEORM_COLUMNS:
                named = _TS_NAME.search(body)
                quoted = _TS_QUOTED.search(body)
                declared = line.split(":", 1)[1].split("=")[0].strip(" ;") if ":" in line else None
                type_text = quoted.group(1) if quoted and not named else (declared or None)
                primary = name.startswith("Primary")
                nullable = False if primary else "nullable: true" in body
                column = named.group(1) if named else prop
                table.add(
                    _Column(
                        column, type_text, nullable, primary, path, number, "unique: true" in body
                    )
                )
            elif name in ("ManyToOne", "OneToOne", "ManyToMany"):
                target = _TS_TARGET.search(body)
                if target and (name == "ManyToOne" or has_join):
                    relations.append((name, prop, target.group(1), number, join))

    # ------------------------------------------------------------ limits

    def _note_gaps(self, paths: list[str], skipped: int) -> None:
        if skipped:
            self._limit(
                f"{skipped} test and fixture files weren't read, so their tables and table use aren't shown"
            )
        notebooks = sum(p.endswith(".ipynb") for p in paths)
        if notebooks:
            self._limit(f"{notebooks} notebooks weren't read for tables or SQL")
        if self.unsupported:
            self._limit(
                f"these data libraries are imported but not read: {', '.join(sorted(self.unsupported))}"
            )
        scripts = sum(p.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs")) for p in paths)
        if scripts:
            self._limit(
                f"SQL in strings and ORM use are only traced in Python, not in the {scripts} JavaScript or TypeScript files"
            )
        others = {
            lang for p in paths for suffix, lang in UNSUPPORTED_SOURCE.items() if p.endswith(suffix)
        }
        if others:
            self._limit(f"data code in these languages isn't read: {', '.join(sorted(others))}")
        if self.computed:
            self._limit(
                f"{self.computed} SQL statements choose their table at run time, so those tables aren't known"
            )
        if self.not_replayed:
            self._limit(
                f"{self.not_replayed} DROP, RENAME or ALTER COLUMN statements weren't replayed, so a table may show what it once had"
            )
        if self.views:
            self._limit(f"{self.views} views were seen and aren't listed as tables")

    # ------------------------------------------------------------ the answer

    def _merged(self) -> dict[str, _Table]:
        """One table per name, whichever sources describe it. Different schemas with one name stay separate."""
        ordered = sorted(
            enumerate(self.raw),
            key=lambda item: (PRIORITY.get(item[1].source, 9), item[1].partial, item[0]),
        )
        schemas: dict[str, set[str]] = {}
        for _, raw in ordered:
            if raw.schema:
                schemas.setdefault(raw.name.lower(), set()).add(raw.schema.lower())
        merged: dict[str, _Table] = {}
        for _, raw in ordered:
            low = raw.name.lower()
            key = (
                f"{raw.schema.lower()}.{low}"
                if len(schemas.get(low, set())) > 1 and raw.schema
                else low
            )
            into = merged.get(key)
            if into is None:
                into = merged[key] = _Table(
                    raw.name, raw.schema, raw.source, raw.path, raw.line, partial=raw.partial
                )
            elif into.schema is None:
                into.schema = raw.schema
            for lowered, column in raw.columns.items():
                had = into.columns.get(lowered)
                if had is None:
                    into.columns[lowered] = column
                else:
                    had.type = had.type or column.type
                    had.primary_key = had.primary_key or column.primary_key
                    had.unique = had.unique or column.unique
            into.fks += raw.fks
            into.primary += raw.primary
            into.unique += raw.unique
        for table in merged.values():
            for name in table.primary:
                if name.lower() in table.columns:
                    table.columns[name.lower()].primary_key = True
                    table.columns[name.lower()].nullable = False
            for name in table.unique:
                if name.lower() in table.columns:
                    table.columns[name.lower()].unique = True
        return merged

    def _answer(self) -> dict[str, Any]:
        tables = self._merged()
        by_name: dict[str, _Table] = {}
        for key in sorted(tables):
            by_name.setdefault(tables[key].name.lower(), tables[key])
        missing: set[str] = set()

        def infer(
            low: str, name: str, schema: str | None, path: str, line: int, source: str
        ) -> _Table:
            table = _Table(name, schema, source, path, line)
            tables[low] = by_name[low] = table
            missing.add(low)
            return table

        for low, (name, schema, path, line) in sorted(self.sql_refs.items()):
            if low not in by_name:
                table = infer(low, name, schema, path, line, "inferred-from-sql")
                for lowered, (column, column_path, column_line) in self.sql_columns.get(
                    low, {}
                ).items():
                    table.columns[lowered] = _Column(
                        column, None, None, False, column_path, column_line
                    )
        relationships = self._relationships(tables, by_name, infer)
        usage = []
        for (symbol, low, access), use in self.usage.items():
            used = by_name.get(low)
            if used is not None:
                usage.append(
                    {
                        "symbol": symbol,
                        "table": used.name,
                        "access": access,
                        "origin": "inferred",
                        "confidence": use.confidence,
                        "evidence": f"{use.path}:{use.line}",
                    }
                )
        usage.sort(key=lambda u: (u["symbol"], u["table"], u["access"]))
        listed = sorted(tables.values(), key=lambda t: (t.name.lower(), t.name, t.schema or ""))
        for low in sorted(missing):
            table = by_name[low]
            self._limit(
                f"table {table.name} is used at {table.path}:{table.line} but its definition wasn't found"
            )
        limits = self._capped_limits()
        if not listed:
            return {
                "status": "none",
                "limits": ["no tables, ORM models, migrations or SQL were found", *limits],
                "tables": [],
                "relationships": [],
                "usage": [],
                "erd_mermaid": "",
            }
        counts: dict[str, int] = {}
        for table in listed:
            counts[table.name.lower()] = counts.get(table.name.lower(), 0) + 1
        return {
            "status": "found",
            "limits": limits,
            "tables": [self._table_dict(t, counts[t.name.lower()] > 1) for t in listed],
            "relationships": relationships,
            "usage": usage,
            "erd_mermaid": "",
        }

    def _capped_limits(self) -> list[str]:
        ordered = sorted(self.limits)
        if len(ordered) <= MAX_LISTED:
            return ordered
        return [*ordered[:MAX_LISTED], f"and {len(ordered) - MAX_LISTED} more limits like these"]

    def _table_dict(self, table: _Table, qualify: bool) -> dict[str, Any]:
        fks = {fk.column.lower(): fk for fk in table.fks}
        columns = []
        for column in table.columns.values():
            fk = fks.get(column.name.lower())
            target = (
                f"{fk.to_table}.{fk.to_column}"
                if fk and fk.to_column
                else (fk.to_table if fk else None)
            )
            columns.append(
                {
                    "name": column.name,
                    "type": column.type,
                    "nullable": column.nullable,
                    "primary_key": column.primary_key,
                    "foreign_key": target,
                    "evidence": f"{column.path}:{column.line}",
                }
            )
        prefix = f"{table.schema}." if qualify and table.schema else ""
        return {
            "id": f"table:{prefix}{table.name}",
            "name": table.name,
            "schema": table.schema,
            "source": table.source,
            "evidence": f"{table.path}:{table.line}",
            "columns": columns,
        }

    def _relationships(
        self,
        tables: dict[str, _Table],
        by_name: dict[str, _Table],
        infer: Callable[[str, str, str | None, str, int, str], _Table],
    ) -> list[dict[str, Any]]:
        def primary(table: _Table) -> str | None:
            keys = [c.name for c in table.columns.values() if c.primary_key]
            return keys[0] if len(keys) == 1 else None

        found: dict[tuple[str, str, str, str], dict[str, Any]] = {}

        def add(
            a: str, a_col: str, b: str, b_col: str | None, cardinality: str, origin: str, where: str
        ) -> None:
            key = (a.lower(), a_col.lower(), b.lower(), (b_col or "").lower())
            found.setdefault(
                key,
                {
                    "from_table": a,
                    "from_column": a_col,
                    "to_table": b,
                    "to_column": b_col,
                    "cardinality": cardinality,
                    "origin": origin,
                    "evidence": where,
                },
            )

        for table in list(tables.values()):
            keys = [lowered for lowered, c in table.columns.items() if c.primary_key]
            for fk in sorted(table.fks, key=lambda f: (f.column.lower(), f.path, f.line)):
                low = fk.to_table.lower()
                target = by_name.get(low) or infer(
                    low, fk.to_table, None, fk.path, fk.line, "inferred-from-reference"
                )
                to_column = fk.to_column or primary(target)
                if (
                    to_column
                    and target.source.startswith("inferred")
                    and to_column.lower() not in target.columns
                ):
                    target.add(_Column(to_column, None, None, True, fk.path, fk.line))
                fk.to_table, fk.to_column = target.name, to_column
                column = table.columns.get(fk.column.lower())
                if column is None:
                    column = _Column(fk.column, None, None, False, fk.path, fk.line)
                    table.add(column)
                one = fk.one_to_one or column.unique or keys == [fk.column.lower()]
                add(
                    table.name,
                    fk.column,
                    target.name,
                    to_column,
                    "one-to-one" if one else "many-to-one",
                    "extracted",
                    f"{fk.path}:{fk.line}",
                )
        for source_name, target_ref, path, line in sorted(self.many):
            source = by_name.get(source_name.lower())
            target_name = target_ref.removeprefix("class:")
            if target_ref.startswith("class:"):
                options = sorted(self.models[k] for k in self.classes_of if k[1] == target_name)
                target_name = options[0] if options else target_name
            found_target = by_name.get(target_name.lower())
            if source is not None and found_target is not None:
                add(
                    source.name,
                    primary(source) or "id",
                    found_target.name,
                    primary(found_target) or "id",
                    "many-to-many",
                    "extracted",
                    f"{path}:{line}",
                )
        for left, left_col, right, right_col, path, line in sorted(self.joins):
            a, b = by_name.get(left), by_name.get(right)
            if a is None or b is None:
                continue
            # The side holding the key is the target; if neither side does, a join says nothing about direction.
            if self._is_key(b, right_col) and not self._is_key(a, left_col):
                add(
                    a.name, left_col, b.name, right_col, "many-to-one", "inferred", f"{path}:{line}"
                )
            elif self._is_key(a, left_col) and not self._is_key(b, right_col):
                add(
                    b.name, right_col, a.name, left_col, "many-to-one", "inferred", f"{path}:{line}"
                )
        return [found[k] for k in sorted(found)]

    def _is_key(self, table: _Table, column: str) -> bool:
        keys = [lowered for lowered, c in table.columns.items() if c.primary_key]
        return column.lower() in keys if keys else column.lower() == "id"


def analyze(ctx: RepoContext) -> dict[str, Any]:
    return _Analysis(ctx).run()

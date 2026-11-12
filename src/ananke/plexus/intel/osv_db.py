"""An offline copy of the OSV vulnerability data (FUT-05): CVE checks with no network.

``ananke cve import`` reads an OSV export (the per-ecosystem ``all.zip`` from
``https://osv-vulnerabilities.storage.googleapis.com/<Ecosystem>/all.zip``, downloaded however you like, or a folder of
OSV JSON files) into a local SQLite database. With ``ANANKE_OSV_DB`` pointing at it, ``--check-vulnerabilities``
answers from the database and sends nothing anywhere; the report says the scanner was ``osv-offline`` and when the
data was imported, because an old copy misses new advisories.

Matching follows the OSV schema: an exact ``versions`` list, or ``ECOSYSTEM``/``SEMVER`` ranges of ``introduced``,
``fixed``, ``last_affected`` and ``limit`` events, compared with the same version ordering the online check uses.
``GIT`` ranges are skipped (they name commits, not releases).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_ENV = "ANANKE_OSV_DB"
MAX_ENTRY_BYTES = 5_000_000
MAX_ENTRIES = 2_000_000
MAX_TOTAL_BYTES = 20_000_000_000
_SCHEMA = """
CREATE TABLE IF NOT EXISTS advisory (id TEXT PRIMARY KEY, modified TEXT, withdrawn INTEGER, body TEXT);
CREATE TABLE IF NOT EXISTS affected (id TEXT, ecosystem TEXT, name TEXT, ranges TEXT, versions TEXT);
CREATE INDEX IF NOT EXISTS affected_pkg ON affected (ecosystem, name);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class OsvDbError(ValueError):
    pass


def _norm(ecosystem: str, name: str) -> str:
    from ananke.plexus.intel.dependencies import _pypi_name

    return _pypi_name(name) if ecosystem == "PyPI" else name


def _entries(source: Path) -> Any:
    if source.is_dir():
        for path in sorted(source.rglob("*.json")):
            if path.stat().st_size <= MAX_ENTRY_BYTES:
                yield path.name, path.read_bytes()
        return
    if not zipfile.is_zipfile(source):
        raise OsvDbError(f"{source.name} isn't a zip or a folder of OSV JSON files")
    with zipfile.ZipFile(source) as archive:
        infos = [i for i in archive.infolist() if i.filename.endswith(".json") and not i.is_dir()]
        if len(infos) > MAX_ENTRIES:
            raise OsvDbError(f"{source.name} has {len(infos)} entries, more than {MAX_ENTRIES}")
        if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
            raise OsvDbError(
                f"{source.name} expands to more than {MAX_TOTAL_BYTES // 1_000_000_000} GB"
            )
        for info in infos:
            if info.file_size > MAX_ENTRY_BYTES:
                continue  # an oversized entry isn't an advisory; skip it rather than read it
            yield info.filename, archive.read(info)


def import_export(source: Path, db: Path) -> dict[str, Any]:
    if not source.exists():
        raise OsvDbError(
            f"{source} doesn't exist; download an OSV export such as PyPI/all.zip first"
        )
    db.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    if source.is_file():
        with source.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    count = skipped = 0
    con = sqlite3.connect(db)
    try:
        con.executescript(_SCHEMA)
        with con:
            for _, raw in _entries(source):
                try:
                    adv = json.loads(raw)
                except ValueError:
                    skipped += 1
                    continue
                if not isinstance(adv, dict) or not isinstance(adv.get("id"), str):
                    skipped += 1
                    continue
                vid = adv["id"]
                con.execute("DELETE FROM affected WHERE id = ?", (vid,))
                con.execute(
                    "INSERT OR REPLACE INTO advisory VALUES (?, ?, ?, ?)",
                    (
                        vid,
                        str(adv.get("modified") or ""),
                        1 if adv.get("withdrawn") else 0,
                        json.dumps(adv, separators=(",", ":")),
                    ),
                )
                for entry in adv.get("affected") or []:
                    pkg = (entry or {}).get("package") or {}
                    eco, name = str(pkg.get("ecosystem") or ""), str(pkg.get("name") or "")
                    if not eco or not name:
                        continue
                    eco = eco.split(":", 1)[0]  # "Debian:12" is Debian
                    con.execute(
                        "INSERT INTO affected VALUES (?, ?, ?, ?, ?)",
                        (
                            vid,
                            eco,
                            _norm(eco, name),
                            json.dumps(entry.get("ranges") or []),
                            json.dumps(entry.get("versions") or []),
                        ),
                    )
                count += 1
            info = {
                "source": source.name,
                "source_sha256": digest.hexdigest() if source.is_file() else "",
                "imported_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "advisories": str(con.execute("SELECT COUNT(*) FROM advisory").fetchone()[0]),
                # an export is per ecosystem, but advisories also name others in passing: only an ecosystem holding
                # a fifth of the advisories counts as covered, so a stray npm mention doesn't claim npm is checked
                "ecosystems": ",".join(
                    sorted(
                        {
                            e
                            for (e, n) in con.execute(
                                "SELECT ecosystem, COUNT(DISTINCT id) FROM affected GROUP BY ecosystem"
                            ).fetchall()
                            if n
                            >= 0.2
                            * max(1, con.execute("SELECT COUNT(*) FROM advisory").fetchone()[0])
                        }
                        | (set(_meta(con).get("ecosystems", "").split(",")) - {""})
                    )
                ),
            }
            for k, v in info.items():
                con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (k, v))
    finally:
        con.close()
    if count == 0:
        raise OsvDbError(f"{source.name} held no OSV advisories")
    return {"imported": count, "skipped": skipped, **status(db)}


def _meta(con: sqlite3.Connection) -> dict[str, str]:
    try:
        return dict(con.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error:
        return {}


def status(db: Path) -> dict[str, Any]:
    if not db.is_file():
        return {"db": str(db), "present": False}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        meta = _meta(con)
    finally:
        con.close()
    return {"db": str(db), "present": True, **meta}


def _affected(version: str, ranges: list[dict[str, Any]], versions: list[str]) -> bool:
    from ananke.plexus.intel.dependencies import _version_key

    if version in versions:
        return True
    key = _version_key(version)
    for span in ranges:
        if not isinstance(span, dict) or span.get("type") not in {"ECOSYSTEM", "SEMVER"}:
            continue
        events = [e for e in span.get("events") or [] if isinstance(e, dict)]

        def at(e: dict[str, Any]) -> tuple[Any, ...]:
            value = next(iter(e.values()))
            return tuple(_version_key("0")) if value == "0" else tuple(_version_key(str(value)))

        events.sort(key=at)
        inside = False
        for e in events:
            if "introduced" in e:
                if e["introduced"] == "0" or key >= _version_key(str(e["introduced"])):
                    inside = True
            elif (
                ("fixed" in e and inside and key >= _version_key(str(e["fixed"])))
                or ("last_affected" in e and inside and key > _version_key(str(e["last_affected"])))
                or ("limit" in e and inside and key >= _version_key(str(e["limit"])))
            ):
                inside = False
        if inside:
            return True
    return False


def query(
    db: Path, packages: list[tuple[str, str, str]]
) -> tuple[list[list[str]], dict[str, dict[str, Any]]]:
    """Advisory ids for each (ecosystem, name, version), and the advisories themselves, from the local copy only."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    ids: list[list[str]] = []
    details: dict[str, dict[str, Any]] = {}
    try:
        for eco, name, version in packages:
            found = []
            rows = con.execute(
                "SELECT a.id, a.ranges, a.versions, v.withdrawn FROM affected a JOIN advisory v ON v.id = a.id "
                "WHERE a.ecosystem = ? AND a.name = ?",
                (eco, _norm(eco, name)),
            ).fetchall()
            for vid, ranges, versions, withdrawn in rows:
                if not withdrawn and _affected(version, json.loads(ranges), json.loads(versions)):
                    found.append(vid)
            ids.append(sorted(set(found)))
        for vid in sorted({i for f in ids for i in f}):
            row = con.execute("SELECT body FROM advisory WHERE id = ?", (vid,)).fetchone()
            if row:
                details[vid] = json.loads(row[0])
    finally:
        con.close()
    return ids, details

"""Multi-repository contract mesh (ADV11): every contract that ties repositories together, in one place.

``ananke triage topology`` already joins HTTP calls to the routes another repository serves. The mesh adds:

* **Event topics:** who publishes and who subscribes to each topic or queue, across repositories. Kafka
  (``producer.send("orders")``, ``KafkaConsumer("orders")``, ``consumer.subscribe([...])``), AWS SNS and SQS
  (``TopicArn=``, ``QueueUrl=`` / ``QueueName=``), RabbitMQ (``basic_publish(routing_key=...)``,
  ``basic_consume(queue=...)``), Redis pub/sub and Node.js (``producer.send({ topic: ... })``,
  ``consumer.subscribe({ topic: ... })``).
* **Datasets:** tables each repository reads and writes (from its data analysis) and files and object-store paths
  (``s3://bucket/prefix``, ``read_parquet("...")``), so a writer in one repository is linked to readers in another.
* **API schemas:** an operation served in one repository and called from another, with whether the caller's
  repository holds a copy of the schema (an OpenAPI file) and whether that copy matches.

**Gaps**, each with evidence: topics published with no subscriber in the workspace, subscribed with no publisher,
datasets read but written by no repository, and HTTP calls nothing serves. A topic named by a variable is listed as
unknown, not guessed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ananke.plexus.insight import InsightError, digest

SCHEMA = "ananke/contract-mesh@1"
MAX_FILE_BYTES = 2_000_000
_Q = r"""['"`]([A-Za-z0-9_.\-:/${}]+)['"`]"""
_PUBLISH = [
    re.compile(r"\b(?:producer|kafka_producer|p)\s*\.\s*(?:send|produce)\(\s*" + _Q),
    re.compile(r"\bTopicArn\s*=\s*" + _Q),
    re.compile(r"\bbasic_publish\([^)]*routing_key\s*=\s*" + _Q),
    re.compile(r"\b(?:redis|r|client)\s*\.\s*publish\(\s*" + _Q),
    re.compile(r"\bsend_message\([^)]*QueueUrl\s*=\s*" + _Q),
    re.compile(r"\bproducer\s*\.\s*send\(\s*\{[^}]*topic\s*:\s*" + _Q),
    re.compile(r"\bsendToQueue\(\s*" + _Q),
]
_SUBSCRIBE = [
    re.compile(r"\bKafkaConsumer\(\s*" + _Q),
    re.compile(r"\bsubscribe\(\s*\[\s*" + _Q),
    re.compile(r"\bsubscribe\(\s*\{[^}]*topics?\s*:\s*\[?\s*" + _Q),
    re.compile(r"\bbasic_consume\([^)]*queue\s*=\s*" + _Q),
    re.compile(r"\breceive_message\([^)]*QueueUrl\s*=\s*" + _Q),
    re.compile(r"\b(?:pubsub|p)\s*\.\s*subscribe\(\s*" + _Q),
    re.compile(r"\bQueueName\s*=\s*" + _Q),
    re.compile(r"\bconsume\(\s*" + _Q),
]
_DATA_READ = re.compile(
    r"""\b(?:read_csv|read_parquet|read_json|read_feather|read_table|load|scan_parquet|read_delta|spark\.read\.\w+)\(\s*f?['"]((?:s3a?|gs|abfss?)://[^'"]+|[^'"]+\.(?:csv|parquet|json|feather|delta|jsonl))['"]"""
)
_DATA_WRITE = re.compile(
    r"""\b(?:to_csv|to_parquet|to_json|to_feather|save|write_parquet|put_object|upload_file|write\.\w+)\(\s*f?['"]((?:s3a?|gs|abfss?)://[^'"]+|[^'"]+\.(?:csv|parquet|json|feather|delta|jsonl))['"]"""
)
_DATA_EXT = r"(?:csv|parquet|json|feather|delta|jsonl|pkl|pickle|xlsx|db|sqlite)"
_ASSIGN = re.compile(r"""^\s*([A-Za-z_]\w*)\s*=\s*.*?['"]([^'"\s]+\.""" + _DATA_EXT + r""")['"]""")
_VAR_READ = re.compile(
    r"\b(?:read_csv|read_parquet|read_json|read_feather|read_table|read_excel|read_pickle|load|open|scan_parquet)"
    r"\(\s*([A-Za-z_]\w*)\s*[,)]"
)
_VAR_WRITE = re.compile(
    r"\b(?:to_csv|to_parquet|to_json|to_feather|to_excel|to_pickle|save|write_parquet|dump)\(\s*([A-Za-z_]\w*)\s*[,)]"
)
_SUFFIXES = (".py", ".js", ".ts", ".mjs", ".cjs", ".tsx", ".jsx", ".java", ".go", ".kt", ".scala")


def _topic_name(raw: str) -> str:
    """``arn:aws:sns:eu-west-1:123:orders`` and ``https://sqs.../123/orders`` both name ``orders``."""
    if raw.startswith("arn:"):
        return raw.rsplit(":", 1)[-1]
    if raw.startswith("http") and "/" in raw:
        return raw.rstrip("/").rsplit("/", 1)[-1]
    return raw


def _dataset(raw: str) -> str:
    raw = raw.strip()
    if "://" in raw:
        scheme, rest = raw.split("://", 1)
        parts = rest.split("/")
        return f"{scheme.rstrip('a')}://{'/'.join(parts[:-1]) if len(parts) > 2 else rest}"
    return raw.rsplit("/", 1)[-1]


def scan_repo(root: Path, name: str) -> dict[str, Any]:
    from ananke.plexus import intel
    from ananke.plexus.intel.context import RepoContext

    ctx = RepoContext.open(root)
    from ananke.plexus.intel._common import is_fixture, is_test_file

    pubs: list[dict[str, str]] = []
    subs: list[dict[str, str]] = []
    reads: list[dict[str, str]] = []
    writes: list[dict[str, str]] = []
    unknown: list[dict[str, str]] = []
    for rel in ctx.files:
        if not rel.endswith(_SUFFIXES) or is_test_file(rel) or is_fixture(rel):
            continue
        text = ctx.text(rel)
        if not text:
            continue
        for no, line in enumerate(text.splitlines(), start=1):
            where = f"{name}:{rel}:{no}"
            for pattern in _PUBLISH:
                for m in pattern.finditer(line):
                    (unknown if "${" in m.group(1) or "{" in m.group(1) else pubs).append(
                        {"topic": _topic_name(m.group(1)), "evidence": where}
                    )
            for pattern in _SUBSCRIBE:
                for m in pattern.finditer(line):
                    (unknown if "${" in m.group(1) or "{" in m.group(1) else subs).append(
                        {"topic": _topic_name(m.group(1)), "evidence": where}
                    )
            for m in _DATA_READ.finditer(line):
                reads.append({"dataset": _dataset(m.group(1)), "evidence": where})
            for m in _DATA_WRITE.finditer(line):
                writes.append({"dataset": _dataset(m.group(1)), "evidence": where})
        # one step of indirection: ``path = folder / "screener.parquet"`` then ``df.to_parquet(path)``
        names: dict[str, str] = {}
        for no, line in enumerate(text.splitlines(), start=1):
            a = _ASSIGN.match(line)
            if a:
                names[a.group(1)] = _dataset(a.group(2))
            where = f"{name}:{rel}:{no}"
            for m in _VAR_WRITE.finditer(line):
                if m.group(1) in names:
                    writes.append(
                        {"dataset": names[m.group(1)], "evidence": where, "status": "inferred"}
                    )
            for m in _VAR_READ.finditer(line):
                if m.group(1) in names:
                    reads.append(
                        {"dataset": names[m.group(1)], "evidence": where, "status": "inferred"}
                    )
    data = (intel.analyze(root).get("sections") or {}).get("data") or {}
    for use in data.get("usage") or []:
        if not isinstance(use, dict) or not use.get("table"):
            continue
        row = {"dataset": f"table:{use['table']}", "evidence": f"{name}:{use.get('evidence')}"}
        (
            writes
            if str(use.get("access")) in {"write", "writes", "insert", "update", "delete"}
            else reads
        ).append(row)
    specs = [p for p in ctx.files if re.search(r"(openapi|swagger)[^/]*\.(json|ya?ml)$", p, re.I)]
    return {
        "name": name,
        "publishes": pubs,
        "subscribes": subs,
        "reads": reads,
        "writes": writes,
        "unknown_topics": unknown,
        "specs": specs,
    }


def build(roots: list[dict[str, Any]]) -> dict[str, Any]:
    from ananke.plexus.intel.topology import build as topology

    if len(roots) < 1:
        raise InsightError(
            "add repositories with --project (repeat) or `ananke triage topology --pin`"
        )
    http = topology(roots)
    repos = [scan_repo(Path(r["path"]), r["name"]) for r in roots]
    topics: dict[str, dict[str, list[str]]] = {}
    for repo in repos:
        for p in repo["publishes"]:
            topics.setdefault(p["topic"], {"publishers": [], "subscribers": []})[
                "publishers"
            ].append(p["evidence"])
        for s in repo["subscribes"]:
            topics.setdefault(s["topic"], {"publishers": [], "subscribers": []})[
                "subscribers"
            ].append(s["evidence"])
    datasets: dict[str, dict[str, list[str]]] = {}
    for repo in repos:
        for w in repo["writes"]:
            datasets.setdefault(w["dataset"], {"writers": [], "readers": []})["writers"].append(
                w["evidence"]
            )
        for r in repo["reads"]:
            datasets.setdefault(r["dataset"], {"writers": [], "readers": []})["readers"].append(
                r["evidence"]
            )

    def repos_of(evidence: list[str]) -> set[str]:
        return {e.split(":", 1)[0] for e in evidence}

    topic_rows = []
    gaps = []
    for t, side in sorted(topics.items()):
        pub, sub = repos_of(side["publishers"]), repos_of(side["subscribers"])
        topic_rows.append(
            {
                "topic": t,
                "publishers": sorted(side["publishers"]),
                "subscribers": sorted(side["subscribers"]),
                "crosses_repositories": bool(pub and sub and pub != sub),
            }
        )
        if not sub:
            gaps.append(
                {
                    "kind": "published, no subscriber",
                    "name": t,
                    "evidence": sorted(side["publishers"])[:5],
                }
            )
        if not pub:
            gaps.append(
                {
                    "kind": "subscribed, no publisher",
                    "name": t,
                    "evidence": sorted(side["subscribers"])[:5],
                }
            )
    dataset_rows = []
    for d, side in sorted(datasets.items()):
        w, r = repos_of(side["writers"]), repos_of(side["readers"])
        dataset_rows.append(
            {
                "dataset": d,
                "writers": sorted(side["writers"]),
                "readers": sorted(side["readers"]),
                "crosses_repositories": bool(w and r and w != r),
            }
        )
        if r and not w and "://" in d:
            gaps.append(
                {
                    "kind": "read, written by no repository",
                    "name": d,
                    "evidence": sorted(side["readers"])[:5],
                }
            )
    for m in http["mismatches"]:
        gaps.append(
            {"kind": "HTTP call nothing serves", "name": m["call"], "evidence": [m["from"]]}
        )
    schema_rows = []
    by_name = {r["name"]: r for r in repos}
    for link in http["links"]:
        caller, server = link["from"].split(":", 1)[0], link["to"].split(":", 1)[0]
        schema_rows.append(
            {
                "operation": link["route"],
                "server": server,
                "caller": caller,
                "server_specs": by_name[server]["specs"],
                "caller_specs": by_name[caller]["specs"],
                "changed_since_pin": link["route_changed_since_pin"],
            }
        )
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "repositories": [r["name"] for r in repos],
        "summary": {
            "http_links": len(http["links"]),
            "topics": len(topic_rows),
            "topics_across_repositories": sum(1 for t in topic_rows if t["crosses_repositories"]),
            "datasets": len(dataset_rows),
            "datasets_across_repositories": sum(
                1 for d in dataset_rows if d["crosses_repositories"]
            ),
            "gaps": len(gaps),
            "unknown_topics": sum(len(r["unknown_topics"]) for r in repos),
        },
        "http": http["links"],
        "topics": topic_rows,
        "datasets": dataset_rows,
        "api_schemas": schema_rows,
        "gaps": gaps,
        "unknown_topics": [u for r in repos for u in r["unknown_topics"]],
        "limits": [
            "Topics and datasets are read from literal names in the code; a name built at run time is listed as unknown.",
            "A gap inside the workspace may be filled by a repository that isn't in it.",
        ],
    }
    report["digest"] = digest(report)
    return report


def to_markdown(r: dict[str, Any]) -> str:
    s = r["summary"]
    lines = [
        f"# Contract mesh: {', '.join(r['repositories'])}",
        "",
        f"{s['http_links']} HTTP links, {s['topics']} topics ({s['topics_across_repositories']} across repositories), "
        f"{s['datasets']} datasets ({s['datasets_across_repositories']} across repositories), {s['gaps']} gaps.",
        "",
    ]
    if r["topics"]:
        lines += ["## Topics", "", "| Topic | Publishers | Subscribers |", "| --- | --- | --- |"]
        lines += [
            f"| `{t['topic']}` | {', '.join(t['publishers']) or '-'} | {', '.join(t['subscribers']) or '-'} |"
            for t in r["topics"]
        ]
        lines.append("")
    if r["datasets"]:
        lines += ["## Datasets", "", "| Dataset | Writers | Readers |", "| --- | --- | --- |"]
        lines += [
            f"| `{d['dataset']}` | {', '.join(d['writers'][:3]) or '-'} | {', '.join(d['readers'][:3]) or '-'} |"
            for d in r["datasets"]
        ]
        lines.append("")
    if r["gaps"]:
        lines += [
            "## Gaps",
            "",
            *(f"- {g['kind']}: `{g['name']}` ({', '.join(g['evidence'])})" for g in r["gaps"]),
            "",
        ]
    lines += [f"Digest: `{r['digest']}`", ""]
    return "\n".join(lines)

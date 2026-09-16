"""Canonical graph domain models (F1).

All graph providers normalize their output to these types.
Edge provenance follows: extracted | inferred | ambiguous | declared.
Multi-provider reconciliation: CONSENSUS | SINGLE_SOURCE | CONFLICT | UNKNOWN.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

NodeKind = Literal[
    "repository",
    "module",
    "file",
    "package",
    "function",
    "method",
    "class",
    "type",
    "route",
    "event",
    "table",
    "queue",
    "service",
    "architecture_component",
    "requirement",
    "test",
    "skill",
    "tool",
]

EdgeKind = Literal[
    "imports",
    "calls",
    "inherits",
    "implements",
    "reads",
    "writes",
    "publishes",
    "subscribes",
    "tests",
    "realizes",
    "depends_on",
    "owns",
    "violates",
    "constrained_by",
]

EdgeOrigin = Literal["extracted", "inferred", "ambiguous", "declared"]

ReconciliationStatus = Literal["CONSENSUS", "SINGLE_SOURCE", "CONFLICT", "UNKNOWN"]


class GraphNode(BaseModel):
    node_id: str
    kind: str  # NodeKind or provider-specific extension
    name: str
    path: str = ""
    provider: str = "native"
    metadata: dict[str, object] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: str  # EdgeKind or provider-specific extension
    origin: str = "extracted"  # EdgeOrigin
    provider: str = "native"
    confidence: float = 1.0
    source_location: str = ""
    snapshot_id: str = ""


class GraphSnapshot(BaseModel):
    snapshot_id: str
    provider: str = "native"
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class GraphQuery(BaseModel):
    text: str
    kind_filter: list[str] = Field(default_factory=list)
    limit: int = 50


class GraphResult(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    summary: str = ""


class ImpactReport(BaseModel):
    changed_files: list[str] = Field(default_factory=list)
    impacted_symbols: list[str] = Field(default_factory=list)
    impacted_files: list[str] = Field(default_factory=list)
    forbidden_edges: list[dict[str, object]] = Field(default_factory=list)
    blast_radius: int = 0
    summary: str = ""


class ReconciledEdge(BaseModel):
    edge: GraphEdge
    status: str = "UNKNOWN"  # ReconciliationStatus
    providers: list[str] = Field(default_factory=list)
    conflict_notes: str = ""

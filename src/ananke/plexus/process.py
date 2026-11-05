"""Business processes from reviewed text, as a model and as BPMN 2.0, without guessing.

A process is written as a numbered list, each step naming who does it:

    # Order handling

    1. Sales: Receive the order
    2. Finance: Is the credit good?
       - Yes: go to step 3
       - No: go to step 5
    3. Warehouse: Pick and pack
    4. Warehouse: Ship the order. End.
    5. Sales: Tell the customer

Only what the text says is drawn, and everything cites its line. A step the text doesn't reach, or a step it points to
that doesn't exist, is a problem to report, not something to repair. Nothing here runs a process.
"""

from __future__ import annotations

import re
from typing import Any
from xml.etree import (
    ElementTree as ET,
)

SCHEMA = "ananke-process/1"
BPMN = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI = "http://www.omg.org/spec/BPMN/20100524/DI"
DC = "http://www.omg.org/spec/DD/20100524/DC"
DI = "http://www.omg.org/spec/DD/20100524/DI"
MAX_BYTES = 2_000_000
MAX_STEPS = 500
_STEP = re.compile(r"^\s{0,3}(\d{1,4})[.)]\s+([^:\n]{1,80}):\s+(.+?)\s*$")
_BRANCH = re.compile(
    r"^\s+[-*]\s+([^:\n]{1,80}):\s+(?:go\s+to\s+step\s+(\d{1,4})|(end))\.?\s*$", re.I
)
_GO_TO = re.compile(r"[.;,]?\s*go\s+to\s+step\s+(\d{1,4})\.?\s*$", re.I)
_END = re.compile(r"[.;,]\s*end\.?\s*$", re.I)
_TITLE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")

for _prefix, _uri in (("bpmn", BPMN), ("bpmndi", BPMNDI), ("dc", DC), ("di", DI)):
    ET.register_namespace(_prefix, _uri)


class ProcessError(ValueError):
    """Text or XML that can't be read as a process."""


def extract(text: str, source: str = "") -> dict[str, Any]:
    """The process a document states. ``problems`` says what doesn't hold together."""
    if len(text.encode("utf-8", "replace")) > MAX_BYTES:
        raise ProcessError("this document is too large to read as a process")
    name = ""
    steps: list[dict[str, Any]] = []
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        title = _TITLE.match(line)
        if title and not name and not steps:
            name = title.group(1)
            continue
        match = _STEP.match(line)
        if match:
            if len(steps) >= MAX_STEPS:
                raise ProcessError(f"a process can have at most {MAX_STEPS} steps")
            steps.append(
                {
                    "step": int(match[1]),
                    "lane": match[2].strip(),
                    "text": match[3].strip(),
                    "line": number,
                    "branches": [],
                }
            )
            continue
        branch = _BRANCH.match(line)
        if branch and steps:
            steps[-1]["branches"].append(
                {
                    "label": branch[1].strip(),
                    "to": int(branch[2]) if branch[2] else None,
                    "line": number,
                }
            )
    if not steps:
        raise ProcessError(
            "no steps found: write a numbered list, with each step naming who does it, as 1. Sales: Receive the order"
        )
    numbers = [s["step"] for s in steps]
    for duplicate in sorted({n for n in numbers if numbers.count(n) > 1}):
        problems.append(f"step {duplicate} appears twice")

    def where(line: int) -> str:
        return f"{source}:{line}" if source else f"line {line}"

    node_id = {s["step"]: f"step_{s['step']}" for s in steps}
    nodes: list[dict[str, Any]] = [
        {
            "id": "start",
            "kind": "start",
            "name": "Start",
            "lane": steps[0]["lane"],
            "evidence": where(steps[0]["line"]),
        }
    ]
    flows: list[dict[str, Any]] = [
        {"source": "start", "target": node_id[steps[0]["step"]], "label": ""}
    ]
    ends = 0

    def end_in(lane: str, line: int) -> str:
        nonlocal ends
        ends += 1
        nodes.append(
            {
                "id": f"end_{ends}",
                "kind": "end",
                "name": "End",
                "lane": lane,
                "evidence": where(line),
            }
        )
        return f"end_{ends}"

    for index, step in enumerate(steps):
        text_now = step["text"]
        jump = _GO_TO.search(text_now)
        finishes = _END.search(text_now)
        clean = (
            text_now[: jump.start()]
            if jump
            else (text_now[: finishes.start()] if finishes else text_now)
        )
        is_decision = bool(step["branches"])
        if is_decision and not clean.rstrip().endswith("?"):
            problems.append(f"step {step['step']} has branches, but doesn't ask a question")
        nodes.append(
            {
                "id": node_id[step["step"]],
                "kind": "decision" if is_decision else "task",
                "name": clean.strip().rstrip("."),
                "lane": step["lane"],
                "evidence": where(step["line"]),
            }
        )
        here = node_id[step["step"]]
        if is_decision:
            for branch in step["branches"]:
                if branch["to"] is None:
                    flows.append(
                        {
                            "source": here,
                            "target": end_in(step["lane"], branch["line"]),
                            "label": branch["label"],
                        }
                    )
                elif branch["to"] in node_id:
                    flows.append(
                        {"source": here, "target": node_id[branch["to"]], "label": branch["label"]}
                    )
                else:
                    problems.append(
                        f"step {step['step']} goes to step {branch['to']}, which doesn't exist"
                    )
            continue
        if jump:
            target = int(jump[1])
            if target in node_id:
                flows.append({"source": here, "target": node_id[target], "label": ""})
            else:
                problems.append(f"step {step['step']} goes to step {target}, which doesn't exist")
        elif finishes or index == len(steps) - 1:
            flows.append(
                {"source": here, "target": end_in(step["lane"], step["line"]), "label": ""}
            )
        else:
            flows.append({"source": here, "target": node_id[steps[index + 1]["step"]], "label": ""})
    for number, flow in enumerate(flows, 1):
        flow["id"] = f"flow_{number}"
    model = {
        "schema": SCHEMA,
        "name": name or "Process",
        "source": source,
        "lanes": list(dict.fromkeys(s["lane"] for s in steps)),
        "nodes": nodes,
        "flows": flows,
        "problems": problems,
    }
    model["problems"] = [*problems, *validate(model)]
    return model


def validate(model: dict[str, Any]) -> list[str]:
    """IDs are unique, links lead somewhere real, and everything can be reached and can finish."""
    problems: list[str] = []
    ids = [n["id"] for n in model["nodes"]]
    for duplicate in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"{duplicate} is used twice")
    known = set(ids)
    flow_ids = [f["id"] for f in model["flows"]]
    for duplicate in sorted({i for i in flow_ids if flow_ids.count(i) > 1}):
        problems.append(f"{duplicate} is used twice")
    outgoing: dict[str, list[str]] = {}
    incoming: dict[str, list[str]] = {}
    for flow in model["flows"]:
        for side in ("source", "target"):
            if flow[side] not in known:
                problems.append(f"{flow['id']} leads to {flow[side]}, which doesn't exist")
        outgoing.setdefault(flow["source"], []).append(flow["target"])
        incoming.setdefault(flow["target"], []).append(flow["source"])

    def reach(starts: list[str], links: dict[str, list[str]]) -> set[str]:
        seen = set(starts)
        waiting = list(starts)
        while waiting:
            for next_id in links.get(waiting.pop(), []):
                if next_id not in seen:
                    seen.add(next_id)
                    waiting.append(next_id)
        return seen

    starts = [n["id"] for n in model["nodes"] if n["kind"] == "start"]
    ends = [n["id"] for n in model["nodes"] if n["kind"] == "end"]
    if not starts:
        problems.append("there's no start")
    if not ends:
        problems.append("there's no end")
    for node in model["nodes"]:
        if starts and node["id"] not in reach(starts, outgoing):
            problems.append(f"nothing leads to {node['name']!r} ({node['id']})")
        elif ends and node["id"] not in reach(ends, incoming):
            problems.append(f"{node['name']!r} ({node['id']}) can't reach an end")
    for node in model["nodes"]:
        if node["kind"] == "decision" and len(outgoing.get(node["id"], [])) < 2:
            problems.append(f"the decision {node['name']!r} has fewer than two ways out")
    return problems


def _layout(model: dict[str, Any]) -> dict[str, tuple[int, int, int, int]]:
    """Where each node sits: its lane, and how many steps it is from the start, by the shortest way."""
    outgoing: dict[str, list[str]] = {}
    for flow in model["flows"]:
        outgoing.setdefault(flow["source"], []).append(flow["target"])
    column = {n["id"]: 0 for n in model["nodes"] if n["kind"] == "start"}
    waiting = list(column)
    while waiting:
        now = waiting.pop(0)
        for next_id in outgoing.get(now, []):
            if next_id not in column:
                column[next_id] = column[now] + 1
                waiting.append(next_id)
    lanes = {lane: i for i, lane in enumerate(model["lanes"])}
    box = {"start": (36, 36), "end": (36, 36), "decision": (50, 50), "task": (120, 70)}
    place: dict[str, tuple[int, int, int, int]] = {}
    used: dict[tuple[int, int], int] = {}
    for node in model["nodes"]:
        col = column.get(node["id"], len(column))
        row = lanes.get(node["lane"], 0)
        stacked = used.get((col, row), 0)
        used[(col, row)] = stacked + 1
        w, h = box.get(node["kind"], box["task"])
        place[node["id"]] = (
            190 + col * 170 + (120 - w) // 2,
            30 + row * 160 + stacked * 40 + (70 - h) // 2,
            w,
            h,
        )
    return place


_TAGS = {"start": "startEvent", "end": "endEvent", "decision": "exclusiveGateway", "task": "task"}
_KINDS = {v: k for k, v in _TAGS.items()}


def to_bpmn(model: dict[str, Any]) -> str:
    """BPMN 2.0 XML, with a diagram, that other BPMN tools can open."""

    def q(ns: str, tag: str) -> str:
        return f"{{{ns}}}{tag}"

    root = ET.Element(
        q(BPMN, "definitions"),
        {
            "id": "definitions",
            "targetNamespace": "https://neo-pragnya.github.io/ananke/process",
            "exporter": "Ananke Plexus",
        },
    )
    collaboration = ET.SubElement(root, q(BPMN, "collaboration"), {"id": "collaboration"})
    ET.SubElement(
        collaboration,
        q(BPMN, "participant"),
        {"id": "participant", "name": model["name"], "processRef": "process"},
    )
    process = ET.SubElement(
        root, q(BPMN, "process"), {"id": "process", "name": model["name"], "isExecutable": "false"}
    )
    lane_set = ET.SubElement(process, q(BPMN, "laneSet"), {"id": "lanes"})
    for index, lane in enumerate(model["lanes"]):
        element = ET.SubElement(
            lane_set, q(BPMN, "lane"), {"id": f"lane_{index + 1}", "name": lane}
        )
        for node in model["nodes"]:
            if node["lane"] == lane:
                ET.SubElement(element, q(BPMN, "flowNodeRef")).text = node["id"]
    for node in model["nodes"]:
        element = ET.SubElement(
            process, q(BPMN, _TAGS[node["kind"]]), {"id": node["id"], "name": node["name"]}
        )
        if node.get("evidence"):
            ET.SubElement(element, q(BPMN, "documentation")).text = f"Source: {node['evidence']}"
        for flow in model["flows"]:
            if flow["target"] == node["id"]:
                ET.SubElement(element, q(BPMN, "incoming")).text = flow["id"]
        for flow in model["flows"]:
            if flow["source"] == node["id"]:
                ET.SubElement(element, q(BPMN, "outgoing")).text = flow["id"]
    for flow in model["flows"]:
        attributes = {"id": flow["id"], "sourceRef": flow["source"], "targetRef": flow["target"]}
        if flow.get("label"):
            attributes["name"] = flow["label"]
        ET.SubElement(process, q(BPMN, "sequenceFlow"), attributes)
    diagram = ET.SubElement(root, q(BPMNDI, "BPMNDiagram"), {"id": "diagram"})
    plane = ET.SubElement(
        diagram, q(BPMNDI, "BPMNPlane"), {"id": "plane", "bpmnElement": "collaboration"}
    )
    place = _layout(model)
    width = max((x + w for x, _, w, _ in place.values()), default=400) + 60
    height = 160 * max(1, len(model["lanes"]))

    def shape(element_id: str, x: int, y: int, w: int, h: int, **extra: str) -> None:
        item = ET.SubElement(
            plane,
            q(BPMNDI, "BPMNShape"),
            {"id": f"{element_id}_di", "bpmnElement": element_id, **extra},
        )
        ET.SubElement(
            item, q(DC, "Bounds"), {"x": str(x), "y": str(y), "width": str(w), "height": str(h)}
        )

    shape("participant", 10, 10, width, height, isHorizontal="true")
    for index, _lane in enumerate(model["lanes"]):
        shape(f"lane_{index + 1}", 40, 10 + index * 160, width - 30, 160, isHorizontal="true")
    for node in model["nodes"]:
        shape(node["id"], *place[node["id"]])
    for flow in model["flows"]:
        a, b = place.get(flow["source"]), place.get(flow["target"])
        if not a or not b:
            continue
        item = ET.SubElement(
            plane, q(BPMNDI, "BPMNEdge"), {"id": f"{flow['id']}_di", "bpmnElement": flow["id"]}
        )
        ET.SubElement(item, q(DI, "waypoint"), {"x": str(a[0] + a[2]), "y": str(a[1] + a[3] // 2)})
        ET.SubElement(item, q(DI, "waypoint"), {"x": str(b[0]), "y": str(b[1] + b[3] // 2)})
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def from_bpmn(xml: str) -> dict[str, Any]:
    """A model from BPMN XML, as another tool may have edited it. Refuses what could attack the parser."""
    if len(xml.encode("utf-8", "replace")) > MAX_BYTES * 5:
        raise ProcessError("this file is too large to read as BPMN")
    from ananke.plexus.intel.coverage import _BadReport, _safe_xml

    try:
        root = _safe_xml(xml.encode("utf-8", "replace"))
    except _BadReport as exc:
        raise ProcessError(f"this file isn't read: {exc}") from exc
    process = root.find(f"{{{BPMN}}}process")
    if process is None:
        raise ProcessError("no BPMN process in this file")
    lanes: list[str] = []
    lane_of: dict[str, str] = {}
    for lane in process.iter(f"{{{BPMN}}}lane"):
        name = lane.get("name", "")
        lanes.append(name)
        for ref in lane.findall(f"{{{BPMN}}}flowNodeRef"):
            lane_of[(ref.text or "").strip()] = name
    nodes: list[dict[str, Any]] = []
    flows: list[dict[str, Any]] = []
    for element in process:
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in _KINDS:
            note = element.find(f"{{{BPMN}}}documentation")
            text = (note.text or "") if note is not None else ""
            nodes.append(
                {
                    "id": element.get("id", ""),
                    "kind": _KINDS[tag],
                    "name": element.get("name", ""),
                    "lane": lane_of.get(element.get("id", ""), ""),
                    "evidence": text.removeprefix("Source: "),
                }
            )
        elif tag == "sequenceFlow":
            flows.append(
                {
                    "id": element.get("id", ""),
                    "source": element.get("sourceRef", ""),
                    "target": element.get("targetRef", ""),
                    "label": element.get("name", ""),
                }
            )
    model = {
        "schema": SCHEMA,
        "name": process.get("name", "Process"),
        "source": "",
        "lanes": lanes,
        "nodes": nodes,
        "flows": flows,
    }
    model["problems"] = validate(model)
    return model


def same_process(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """Differences that matter between two models: ids, names, lanes and links, not where anything sits."""
    differences: list[str] = []

    def nodes(m: dict[str, Any]) -> dict[str, tuple[str, str, str]]:
        return {n["id"]: (n["kind"], n["name"], n["lane"]) for n in m["nodes"]}

    def flows(m: dict[str, Any]) -> set[tuple[str, str, str]]:
        return {(f["source"], f["target"], f.get("label", "")) for f in m["flows"]}

    left, right = nodes(a), nodes(b)
    for key in sorted(left.keys() | right.keys()):
        if left.get(key) != right.get(key):
            differences.append(f"{key}: {left.get(key)} and {right.get(key)}")
    differences += [f"link only in the first: {f}" for f in sorted(flows(a) - flows(b))]
    differences += [f"link only in the second: {f}" for f in sorted(flows(b) - flows(a))]
    if list(a["lanes"]) != list(b["lanes"]):
        differences.append(f"lanes: {a['lanes']} and {b['lanes']}")
    return differences


__all__ = ["SCHEMA", "ProcessError", "extract", "from_bpmn", "same_process", "to_bpmn", "validate"]

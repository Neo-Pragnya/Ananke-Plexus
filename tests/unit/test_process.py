"""Processes from reviewed text: only what it says, with BPMN that round-trips."""

from __future__ import annotations

import re

import pytest

from ananke.plexus.process import ProcessError, extract, from_bpmn, same_process, to_bpmn, validate

ORDER = """# Order handling

Some context that isn't a step.

1. Sales: Receive the order
2. Finance: Is the credit good?
   - Yes: go to step 3
   - No: go to step 5
3. Warehouse: Pick and pack
4. Warehouse: Ship the order. End.
5. Sales: Tell the customer
"""


def test_only_what_the_text_says_is_drawn_and_it_cites_its_line() -> None:
    m = extract(ORDER, "process.md")
    assert m["name"] == "Order handling" and m["problems"] == []
    assert m["lanes"] == ["Sales", "Finance", "Warehouse"]
    by = {n["id"]: n for n in m["nodes"]}
    assert by["step_2"]["kind"] == "decision" and by["step_2"]["evidence"] == "process.md:6"
    assert by["step_4"]["name"] == "Ship the order"
    assert ("step_2", "step_5", "No") in {
        (f["source"], f["target"], f["label"]) for f in m["flows"]
    }
    assert sum(n["kind"] == "end" for n in m["nodes"]) == 2


def test_bpmn_round_trips_exactly() -> None:
    m = extract(ORDER, "process.md")
    back = from_bpmn(to_bpmn(m))
    assert same_process(m, back) == [] and back["problems"] == []


def test_the_bpmn_never_says_it_can_run() -> None:
    assert 'isExecutable="false"' in to_bpmn(extract(ORDER))


def test_a_changed_link_is_noticed() -> None:
    m = extract(ORDER)
    xml = re.sub(r'targetRef="step_5"', 'targetRef="step_3"', to_bpmn(m))
    assert same_process(m, from_bpmn(xml))


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("1. A: Do it. Go to step 9\n2. A: Next\n", "doesn't exist"),
        ("1. A: Is it?\n   - Yes: go to step 7\n   - No: end\n", "doesn't exist"),
        ("1. A: Do\n1. A: Again\n", "appears twice"),
        ("1. A: Do it. End.\n2. A: Never reached\n", "nothing leads to"),
        ("1. A: Is it?\n   - Yes: go to step 2\n2. A: Then. End.\n", "fewer than two"),
        ("1. A: One. Go to step 2\n2. A: Two. Go to step 1\n", "there's no end"),
    ],
)
def test_what_doesnt_hold_together_is_reported_not_repaired(text: str, problem: str) -> None:
    assert problem in " ".join(extract(text)["problems"])


@pytest.mark.parametrize("text", ["", "Just some prose.\n", "- a: b\n"])
def test_text_with_no_steps_is_refused(text: str) -> None:
    with pytest.raises(ProcessError, match="no steps"):
        extract(text)


def test_hostile_names_stay_data() -> None:
    m = extract('1. <b>"Sales"</b>: Do <script>alert(1)</script> & go\n')
    xml = to_bpmn(m)
    assert "<script>" not in xml and "<b>" not in xml
    assert same_process(m, from_bpmn(xml)) == []


@pytest.mark.parametrize(
    "xml",
    [
        '<!DOCTYPE x [<!ENTITY a "b">]><definitions/>',
        '<?xml version="1.0"?><!ENTITY a "b"><definitions/>',
        "not xml at all",
        "<definitions/>",
    ],
)
def test_xml_it_cant_trust_is_refused(xml: str) -> None:
    with pytest.raises(ProcessError):
        from_bpmn(xml)


def test_a_loop_with_no_way_out_of_it_is_noticed() -> None:
    text = "1. A: Is it?\n   - Yes: go to step 2\n   - No: end\n2. A: Again. Go to step 3\n3. A: Back. Go to step 2\n"
    assert any("can't reach an end" in p for p in extract(text)["problems"])


def test_validate_finds_a_link_to_nowhere() -> None:
    m = extract(ORDER)
    m["flows"][0]["target"] = "missing"
    assert any("missing" in p for p in validate(m))

"""Rust in the graph: items, spans, docs, imports across workspace crates, and only unambiguous call edges."""

from __future__ import annotations

from pathlib import Path

from ananke.plexus.graph.rust import _use_paths, build_rust, tokenize
from ananke.plexus.graph.service import build_graph


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


WORKSPACE = {
    "crates/core/Cargo.toml": '[package]\nname = "my-core"\nversion = "0.1.0"\n',
    "crates/core/src/lib.rs": (
        "pub mod units;\n"
        "/// Degrees, normalised.\n"
        "pub fn norm(x: f64) -> f64 { x.rem_euclid(360.0) }\n"
    ),
    "crates/core/src/units.rs": "pub struct Deg(pub f64);\nimpl Deg { pub fn value(&self) -> f64 { self.0 } }\n",
    "crates/app/Cargo.toml": '[package]\nname = "my-app"\nversion = "0.1.0"\n',
    "crates/app/src/lib.rs": "pub mod api;\nmod util;\n",
    "crates/app/src/util.rs": "pub fn helper() -> u8 { 1 }\n",
    "crates/app/src/api.rs": (
        "use my_core::norm;\n"
        "use my_core::units::Deg;\n"
        "use crate::util;\n"
        "\n"
        "pub trait Shape { fn area(&self) -> f64; }\n"
        "\n"
        "/// The engine.\n"
        "#[derive(Debug)]\n"
        "pub struct Engine { lifetime_holder: &'static str }\n"
        "\n"
        "impl<'a> Shape for Engine {\n"
        "    fn area(&self) -> f64 { 0.0 }\n"
        "}\n"
        "\n"
        "impl Engine {\n"
        '    pub fn new() -> Self { Self { lifetime_holder: "{ not a brace }" } }\n'
        "    pub fn run(&self) -> f64 {\n"
        "        let c = '{';\n"
        '        let s = r#"fn fake() { }"#;\n'
        "        /* fn hidden() { /* nested */ } */\n"
        "        let _ = util::helper();\n"
        "        self.step();\n"
        "        Engine::new();\n"
        "        Deg(1.0).value();\n"
        "        norm(1.0)\n"
        "    }\n"
        "    fn step(&self) {}\n"
        "}\n"
        "\n"
        "#[cfg(test)]\n"
        "mod tests {\n"
        "    use super::*;\n"
        "    #[test]\n"
        "    fn runs() { Engine::new().run(); }\n"
        "}\n"
    ),
    "target/debug/build/out.rs": "pub fn generated() {}\n",
}


def test_tokenize_hides_comments_strings_and_keeps_lifetimes_apart_from_chars() -> None:
    toks, docs = tokenize(
        "/// Doc.\nfn a<'x>(s: &'x str) { let c = '}'; let r = r#\"}\"#; /* } /* } */ */ }"
    )
    texts = [t.text for t in toks]
    assert texts.count("{") == 1 and texts.count("}") == 1
    assert "fake" not in texts and docs == {1: "Doc."}


def test_use_paths_expand_groups_and_aliases() -> None:
    toks, _ = tokenize("a::{b, c::d as e, f::*, self}")
    assert _use_paths(toks) == [
        (["a", "b"], "b"),
        (["a", "c", "d"], "e"),
        (["a", "f", "*"], "*"),
        (["a", "self"], "a"),
    ]


def test_items_spans_docs_and_tests(tmp_path: Path) -> None:
    write(tmp_path, WORKSPACE)
    graph = build_graph(tmp_path)
    by_id = {n.node_id: n for n in graph.nodes}
    engine = by_id["class:crates/app/src/api.rs:Engine"]
    assert engine.metadata["rust_kind"] == "struct" and engine.metadata["doc"] == "The engine."
    assert by_id["class:crates/app/src/api.rs:Shape"].metadata["rust_kind"] == "trait"
    run = by_id["method:crates/app/src/api.rs:Engine.run"]
    assert (run.metadata["line"], run.metadata["end_line"]) == (17, 26)
    assert run.metadata["signature"] == "pub fn run(&self) -> f64"
    assert by_id["function:crates/app/src/api.rs:tests::runs"].metadata["test"] is True
    assert by_id["function:crates/core/src/lib.rs:norm"].metadata["doc"] == "Degrees, normalised."
    assert "function:crates/app/src/api.rs:fake" not in by_id
    assert "function:crates/app/src/api.rs:hidden" not in by_id
    assert not any(n.path.startswith("target/") for n in graph.nodes)


def test_imports_resolve_through_workspace_crates(tmp_path: Path) -> None:
    write(tmp_path, WORKSPACE)
    graph = build_graph(tmp_path)
    resolved = {
        e.target: e.resolved_target
        for e in graph.edges
        if e.kind == "imports" and e.source == "file:crates/app/src/api.rs"
    }
    assert resolved["module:my_core::norm"] == "file:crates/core/src/lib.rs"
    assert resolved["module:my_core::units::Deg"] == "file:crates/core/src/units.rs"
    assert resolved["module:crate::util"] == "file:crates/app/src/util.rs"
    mods = {
        e.target: e.resolved_target for e in graph.edges if e.source == "file:crates/app/src/lib.rs"
    }
    assert mods["module:util"] == "file:crates/app/src/util.rs"


def test_calls_are_drawn_only_when_unambiguous(tmp_path: Path) -> None:
    write(tmp_path, WORKSPACE)
    _, edges = build_rust(tmp_path, sorted(tmp_path.rglob("crates/**/*.rs")))
    calls = {
        (e.source.split(":", 2)[-1], e.target.split(":", 2)[-1]) for e in edges if e.kind == "calls"
    }
    assert {
        ("Engine.run", "helper"),  # module alias
        ("Engine.run", "Engine.step"),  # self.
        ("Engine.run", "Engine.new"),  # Type::
        ("Engine.run", "Deg.value"),  # the only method of that name
        ("Engine.run", "norm"),  # imported from another crate
        ("tests::runs", "Engine.new"),
    } <= calls
    implements = {(e.source, e.target) for e in edges if e.kind == "implements"}
    assert implements == {
        ("class:crates/app/src/api.rs:Engine", "class:crates/app/src/api.rs:Shape")
    }


def test_ambiguous_method_names_draw_no_edge(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "src/lib.rs": (
                "struct A; struct B;\n"
                "impl A { fn go(&self) {} }\n"
                "impl B { fn go(&self) {} }\n"
                "fn caller(x: A) { x.go(); }\n"
            )
        },
    )
    _, edges = build_rust(tmp_path, [tmp_path / "src/lib.rs"])
    assert not [e for e in edges if e.kind == "calls"]


def test_a_broken_file_still_maps_the_rest(tmp_path: Path) -> None:
    write(tmp_path, {"src/lib.rs": "fn ok() {}\n", "src/bad.rs": 'fn broken( { ] "unterminated'})
    nodes, _ = build_rust(tmp_path, sorted(tmp_path.rglob("*.rs")))
    ids = {n.node_id for n in nodes}
    assert "function:src/lib.rs:ok" in ids and "file:src/bad.rs" in ids

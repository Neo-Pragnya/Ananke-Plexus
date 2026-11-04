"""JavaScript and TypeScript in the graph: read as text, with no false edges and no slow regular expressions."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ananke.plexus.graph.javascript import (
    ModuleResolver,
    build_javascript,
    external_name,
    is_minified,
    parse_file,
    strip_jsonc,
    tokenize,
)
from ananke.plexus.graph.models import GraphSnapshot
from ananke.plexus.graph.service import (
    build_graph,
    graph_digest,
    impact_from_paths,
    persist_graph,
)


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def links(snapshot: GraphSnapshot) -> set[tuple[str, str]]:
    return {
        (e.source.removeprefix("file:"), e.resolved_target.removeprefix("file:"))
        for e in snapshot.edges
        if e.kind == "imports" and e.resolved_target
    }


def targets(snapshot: GraphSnapshot, kind: str = "imports") -> set[str]:
    return {e.target for e in snapshot.edges if e.kind == kind}


def calls(snapshot: GraphSnapshot) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in snapshot.edges if e.kind == "calls"}


def ids(snapshot: GraphSnapshot) -> set[str]:
    return {n.node_id for n in snapshot.nodes}


def test_symbols_take_the_python_id_forms_with_lines(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "a.ts": (
                "export function one() {}\n"
                "async function two(): Promise<void> {}\n"
                "export const three = async (x: number): Promise<number> => x;\n"
                "const four = function () {};\n"
                "const five: Handler = y => y;\n"
                "export const Six = memo(() => null);\n"
                "const notAFunction = compute(1);\n"
                "if (ready) { function hidden() {} }\n"
                "export default class Page extends Base {\n"
                "  private count = 0;\n"
                "  constructor(private api: Api) { super(); }\n"
                "  static make<T>(): Page { return new Page(); }\n"
                "  async load(): Promise<void> {}\n"
                "  onClick = () => { this.load(); };\n"
                "}\n"
            ),
        },
    )
    snap = build_graph(tmp_path)
    assert {
        "file:a.ts",
        "function:a.ts:one",
        "function:a.ts:two",
        "function:a.ts:three",
        "function:a.ts:four",
        "function:a.ts:five",
        "function:a.ts:Six",
        "class:a.ts:Page",
        "method:a.ts:Page.constructor",
        "method:a.ts:Page.make",
        "method:a.ts:Page.load",
        "method:a.ts:Page.onClick",
    } == ids(snap)
    nodes = {n.node_id: n for n in snap.nodes}
    assert nodes["file:a.ts"].metadata["language"] == "typescript"
    assert nodes["method:a.ts:Page.load"].metadata["line"] == 13
    assert nodes["class:a.ts:Page"].metadata["end_line"] == 15
    owns = {(e.source, e.target): e.source_location for e in snap.edges if e.kind == "owns"}
    assert owns[("file:a.ts", "function:a.ts:one")] == "a.ts:1"
    assert owns[("class:a.ts:Page", "method:a.ts:Page.load")] == "a.ts:13"
    assert ("method:a.ts:Page.onClick", "method:a.ts:Page.load") in calls(snap)


def test_every_import_form(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "lib/a.js": "",
            "lib/b.jsx": "",
            "lib/c.ts": "",
            "lib/d.tsx": "",
            "lib/e.mjs": "",
            "lib/f.cjs": "",
            "lib/index.js": "",
            "lib/g/index.ts": "",
            "lib/h.ts": "",
            "lib/i.ts": "",
            "main.js": (
                "import './lib/a';\n"
                "import B from './lib/b';\n"
                "import { c as see } from './lib/c';\n"
                "import * as d from './lib/d';\n"
                "export { e } from './lib/e';\n"
                "const f = require('./lib/f');\n"
                "const later = () => import('./lib');\n"
                "export * from './lib/g';\n"
                "import type { H } from './lib/h.js';\n"
                "export * as i from './lib/i';\n"
                "import react from 'react';\n"
                "import debounce from 'lodash/debounce';\n"
                "import x from '@scope/pkg/deep';\n"
                "import fs from 'node:fs';\n"
                "import gone from './lib/gone';\n"
                "const computed = require(name);\n"
                "const dynamic = import(`./lib/${name}`);\n"
            ),
        },
    )
    snap = build_graph(tmp_path)
    assert links(snap) == {
        ("main.js", p)
        for p in (
            "lib/a.js",
            "lib/b.jsx",
            "lib/c.ts",
            "lib/d.tsx",
            "lib/e.mjs",
            "lib/f.cjs",
            "lib/index.js",
            "lib/g/index.ts",
            "lib/h.ts",
            "lib/i.ts",
        )
    }
    assert {
        "module:react",
        "module:lodash",
        "module:@scope/pkg",
        "module:node:fs",
        "module:./lib/gone",
    } <= targets(snap)
    assert all(e.source_location.startswith("main.js:") for e in snap.edges if e.kind == "imports")
    located = {e.target: e.source_location for e in snap.edges if e.kind == "imports"}
    assert located["module:./lib/f"] == "main.js:6"
    assert not any("name" in t for t in targets(snap))


def test_comments_and_strings_create_nothing(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "real.ts": "export function target() {}\nexport function other() {}\n",
            "main.ts": (
                "import { target, other } from './real';\n"
                "// import a from './ghost-line';\n"
                "/* import b from './ghost-block';\n"
                "   target();\n"
                "*/\n"
                "const s = \"import c from './ghost-string'; require('./ghost-require')\";\n"
                "const t = `import d from './ghost-template'; target()`;\n"
                "const r = /import e from '.\\/ghost-regex'/;\n"
                "function quiet() {\n"
                "  const note = 'target()';\n"
                "  // target();\n"
                "  return note;\n"
                "}\n"
                "// function ghost() {}\n"
                "const u = 'function ghost2() {}';\n"
                "function loud() {\n"
                "  return `${other()} and target()`;\n"
                "}\n"
            ),
        },
    )
    snap = build_graph(tmp_path)
    assert not any("ghost" in t for t in targets(snap))
    assert not any("ghost" in i for i in ids(snap))
    assert not any(s == "function:main.ts:quiet" for s, _ in calls(snap))
    assert {t for s, t in calls(snap) if s == "function:main.ts:loud"} == {"function:real.ts:other"}


def test_line_numbers_hold_after_multiline_comments_and_strings(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "a.js": (
                "/* one\n two\n three */\n"
                "const s = `a\nb\nc`;\n"
                "const c = 'x\\\ny';\n"
                "const t = `p${q}\nr${v}\ns`;\n"
                "function here() {}\n"
            )
        },
    )
    node = next(n for n in build_graph(tmp_path).nodes if n.node_id == "function:a.js:here")
    assert node.metadata["line"] == 12


@pytest.mark.parametrize(
    "line",
    [
        "const a = x / y; function after() {}",
        "const a = /['\"`]/; function after() {}",
        "const a = /[/]'/.test(s); function after() {}",
        "const el = <p>Don't</p>; function after() {}",
        'const el = <a href="x">It\'s</a>; function after() {}',
        "const a = `${`${1}`}`; function after() {}",
    ],
)
def test_tricky_tokens_do_not_swallow_the_code_after_them(tmp_path: Path, line: str) -> None:
    write(tmp_path, {"a.jsx": f"{line}\n"})
    assert "function:a.jsx:after" in ids(build_graph(tmp_path))


def test_calls_follow_imports_aliases_namespaces_and_re_exports(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "lib/core.ts": (
                "export function work() {}\n"
                "export class Tool { run() {} static make() {} }\n"
                "export default function main() {}\n"
            ),
            "lib/index.ts": "export { work as renamed, Tool } from './core';\nexport * from './more';\n",
            "lib/more.ts": "export const extra = () => 1;\n",
            "cjs.js": "function a() {}\nmodule.exports = { a, b: a, c() {}, d: () => 1 };\n",
            "cjs2.js": "exports.e = function () {};\n",
            "use.ts": (
                "import { renamed, Tool, extra } from './lib';\n"
                "import start from './lib/core';\n"
                "import * as core from './lib/core';\n"
                "const { b, c: see } = require('./cjs');\n"
                "const cjs2 = require('./cjs2');\n"
                "class Child extends Tool {}\n"
                "export function go() {\n"
                "  renamed(); extra(); start(); core.work(); Tool.make(); new Tool();\n"
                "  b(); see(); cjs2.e(); fetch('/x');\n"
                "}\n"
            ),
        },
    )
    snap = build_graph(tmp_path)
    assert {t for s, t in calls(snap) if s == "function:use.ts:go"} == {
        "function:lib/core.ts:work",
        "function:lib/more.ts:extra",
        "function:lib/core.ts:main",
        "method:lib/core.ts:Tool.make",
        "class:lib/core.ts:Tool",
        "function:cjs.js:a",
        "function:cjs.js:c",
        "function:cjs2.js:e",
    }
    assert "class:lib/core.ts:Tool" in targets(snap, "inherits")
    assert all(
        e.origin == "inferred" and e.source_location for e in snap.edges if e.kind == "calls"
    )


def test_an_injected_service_is_reached_through_its_declared_type(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "api.service.ts": "export class ApiService { send() {} }\n",
            "page.ts": (
                "import { ApiService } from './api.service';\n"
                "export class Page {\n"
                "  private other = inject(ApiService);\n"
                "  third: ApiService;\n"
                "  constructor(private api: ApiService) {}\n"
                "  a() { this.api.send(); }\n"
                "  b() { this.other.send(); }\n"
                "  c() { this.third.send(); }\n"
                "  d() { this.unknown.send(); }\n"
                "}\n"
            ),
        },
    )
    found = calls(build_graph(tmp_path))
    for name in "abc":
        assert (f"method:page.ts:Page.{name}", "method:api.service.ts:ApiService.send") in found
    assert not any(s == "method:page.ts:Page.d" for s, _ in found)


def test_things_that_only_look_like_calls_are_not_calls(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "real.ts": "export function save() {}\nexport function load() {}\n",
            "main.ts": (
                "import { save, load } from './real';\n"
                "interface Store { save(x: string): void; }\n"
                "type Api = { load(): void };\n"
                "const object = { save() { return 1; } };\n"
                "function shadowed(save: Fn) { return save(); }\n"
                "function local() { const load = make(); return load(); }\n"
                "function member(thing: T) { return thing.save(); }\n"
                "function chained() { return get().load(); }\n"
            ),
        },
    )
    assert calls(build_graph(tmp_path)) == set()


def test_tsconfig_paths_and_base_url(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "web/tsconfig.json": (
                '{\n  // comment with "quotes"\n  /* and a block */\n'
                '  "compilerOptions": {\n    "baseUrl": ".",\n'
                '    "paths": {"@/*": ["src/*"], "~lib": ["src/lib/main.ts"], "@x/*": ["nope/*", "src/x/*"],},\n'
                "  },\n}\n"
            ),
            "web/src/a.ts": "export function a() {}\n",
            "web/src/lib/main.ts": "",
            "web/src/x/one.ts": "",
            "web/src/from_base.ts": "",
            "web/src/deep/use.ts": (
                "import { a } from '@/a';\n"
                "import '~lib';\n"
                "import '@x/one';\n"
                "import 'src/from_base';\n"
                "import '@/missing';\n"
                "import 'react';\n"
                "export function go() { a(); }\n"
            ),
        },
    )
    snap = build_graph(tmp_path)
    assert links(snap) == {
        ("web/src/deep/use.ts", p)
        for p in ("web/src/a.ts", "web/src/lib/main.ts", "web/src/x/one.ts", "web/src/from_base.ts")
    }
    assert {"module:react", "module:@/missing"} <= targets(snap)
    assert ("function:web/src/deep/use.ts:go", "function:web/src/a.ts:a") in calls(snap)


def test_config_extends_and_a_sibling_app_config(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "tsconfig.base.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"#/*": ["shared/*"]}}}',
            "shared/s.ts": "",
            "apps/one/tsconfig.json": '{"extends": "../../tsconfig.base.json"}',
            "apps/one/use.ts": "import '#/s';\n",
            "apps/two/tsconfig.json": '{"files": [], "references": [{"path": "./tsconfig.app.json"}]}',
            "apps/two/tsconfig.app.json": '{"compilerOptions": {"paths": {"$/*": ["./src/*"]}}}',
            "apps/two/src/t.ts": "",
            "apps/two/use.ts": "import '$/t';\n",
            "apps/three/jsconfig.json": '{"compilerOptions": {"baseUrl": "src"}}',
            "apps/three/src/u.js": "",
            "apps/three/use.js": "import 'u';\n",
        },
    )
    assert links(build_graph(tmp_path)) == {
        ("apps/one/use.ts", "shared/s.ts"),
        ("apps/two/use.ts", "apps/two/src/t.ts"),
        ("apps/three/use.js", "apps/three/src/u.js"),
    }


def test_a_bad_config_or_one_outside_the_repository_is_ignored(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write(
        tmp_path,
        {
            "secret.json": '{"compilerOptions": {"baseUrl": "."}}',
            "repo/tsconfig.json": '{"extends": "../secret.json"}',
            "repo/broken/tsconfig.json": "{ not json",
            "repo/broken/use.ts": "import 'x';\n",
            "repo/loop/tsconfig.json": '{"extends": "./tsconfig.json"}',
            "repo/loop/use.ts": "import 'x';\n",
        },
    )
    assert links(build_graph(root)) == set()


def test_paths_cannot_escape_the_repository(tmp_path: Path) -> None:
    resolver = ModuleResolver(tmp_path, {"a/b.js"})
    assert resolver.resolve("a/b.js", "../../../etc/passwd") == ""
    assert resolver.resolve("c.js", "../a/b") == ""
    assert resolver.resolve("a/c.js", "./b") == "a/b.js"
    assert resolver.resolve("a/c.js", "/a/b") == ""
    assert resolver.resolve("a/c.js", "https://x.test/b.js") == ""


def test_files_left_out_and_folders_skipped(tmp_path: Path) -> None:
    long_line = "var a=1;" * 2000
    write(
        tmp_path,
        {
            "types.d.ts": "export function typed(): void;\n",
            "lib.min.js": "function minified() {}\n",
            "bundle.js": long_line,
            "big.js": "// x\n" * 500_000,
            "node_modules/p/index.js": "function dep() {}\n",
            "dist/app.js": "function built() {}\n",
            "build/app.js": "function built2() {}\n",
            "vendor/v.js": "function vendored() {}\n",
            ".next/n.js": "function hidden() {}\n",
            "keep.js": "function kept() {}\n",
        },
    )
    assert {n.path for n in build_graph(tmp_path).nodes} == {"keep.js"}
    assert is_minified("x.js", long_line) and is_minified("x.min.js", "")
    assert not is_minified("x.js", "var a = 1;\n" * 2000)


def test_a_file_that_cannot_be_read_does_not_stop_the_build(tmp_path: Path) -> None:
    (tmp_path / "bad.js").write_bytes(b"\xff\xfe\x00function x() {}")
    write(tmp_path, {"good.js": "function y() {}\n"})
    nodes = {n.node_id: n for n in build_graph(tmp_path).nodes}
    assert nodes["file:bad.js"].metadata["parse_error"] is True
    assert "function:good.js:y" in nodes


def test_a_parser_failure_is_contained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ananke.plexus.graph import javascript

    def explode(text: str) -> list[object]:
        raise RecursionError

    write(tmp_path, {"a.js": ""})
    monkeypatch.setattr(javascript, "tokenize", explode)
    nodes, edges = build_javascript(tmp_path, [tmp_path / "a.js"])
    assert [n.metadata.get("parse_error") for n in nodes] == [True] and not edges


def test_broken_code_is_still_read(tmp_path: Path) -> None:
    write(tmp_path, {"a.ts": "function one( { ] ) \nclass {\n}}}}\nexport function two() {}\n"})
    build_graph(tmp_path)  # nothing to assert but that it returns


def test_deep_nesting_needs_no_recursion(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "deep.js": "[" * 200_000,
            "calls.js": "f(" * 200_000,
            "mix.ts": "({[`${" * 50_000,
            "chain.js": "x = " + "+".join(["a"] * 200_000),
            "ok.js": "function fine() {}\n",
        },
    )
    assert "function:ok.js:fine" in ids(build_graph(tmp_path))


def test_impact_reaches_javascript_importers(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "src/api.ts": "export function a() {}\n",
            "src/page.ts": "import { a } from './api';\nexport function p() { a(); }\n",
            "src/none.ts": "export const n = 1;\n",
        },
    )
    persist_graph(tmp_path, build_graph(tmp_path))
    assert impact_from_paths(tmp_path, ["src/api.ts"]).impacted_files == ["src/page.ts"]


def test_a_javascript_import_never_lands_on_a_python_module(tmp_path: Path) -> None:
    from ananke.plexus.app import AppFacade

    write(tmp_path, {"config.py": "", "web.js": "import config from 'config';\n"})
    persist_graph(tmp_path, build_graph(tmp_path))
    assert impact_from_paths(tmp_path, ["config.py"]).impacted_files == []
    assert not [e for e in AppFacade(tmp_path).code_view()["edges"] if e["kind"] == "imports"]


def test_rebuilds_give_the_same_digest(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "tsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["./*"]}}}',
            "a.ts": "import { b } from '@/b';\nexport function a() { b(); }\n",
            "b.ts": "import { a } from './a';\nexport function b() { a(); }\n",
        },
    )
    assert graph_digest(build_graph(tmp_path)) == graph_digest(build_graph(tmp_path))


def test_external_names() -> None:
    assert external_name("lodash/debounce") == "lodash"
    assert external_name("@a/b/c") == "@a/b"
    assert external_name("node:fs") == "node:fs"
    assert external_name("./x") == "./x"


ADVERSARIAL = {
    "spaces": " " * 100_000,
    "letters": "a" * 100_000,
    "digits": "1" * 100_000,
    "single_quotes": "'" * 100_001,
    "escaped_quotes": "'" + "\\'" * 50_000,
    "double_quotes": '"\\"' * 33_000,
    "backslashes": "\\" * 100_000,
    "backticks": "`" * 100_001,
    "escaped_backticks": "`" + "\\`" * 50_000,
    "interpolations": "`${" * 33_000,
    "slashes": "/" * 100_000,
    "regex_starts": "=/" * 50_000,
    "unclosed_classes": "=/[" * 33_000,
    "comment_starts": "/*" * 50_000,
    "stars": "*" * 100_000,
    "function_gap": "function" + " " * 100_000,
    "function_star": "function " + "* " * 50_000,
    "async_gap": "async " * 20_000,
    "imports": "import " * 15_000,
    "import_from": "import a from " * 8_000,
    "exports": "export " * 15_000,
    "open_braces": "import {" * 12_000,
    "commas": "import {" + "a," * 50_000,
    "class_heads": "class A " * 12_000,
    "class_extends": "class A extends " * 6_000,
    "consts": "const a = " * 10_000,
    "arrows": "const a = b => " * 7_000,
    "generics": "foo<" * 25_000,
    "typed_generics": "a<b>" * 25_000,
    "dots": "a." * 50_000,
    "dotted_call": "a.b(" * 25_000,
    "parens": "(" * 100_000,
    "closers": ")" * 100_000,
    "requires": "require(" * 12_000,
    "module_exports": "module.exports = " * 6_000,
    "object_exports": "module.exports = {" + "a:" * 50_000,
    "class_members": "class A {" + "a() " * 25_000,
    "class_modifiers": "class A {" + "static " * 15_000,
    "decorators": "class A {" + "@a " * 30_000,
    "interfaces": "interface A " * 12_000,
    "nested_interfaces": "interface A {" * 12_000,
    "nested_types": "type A = {" * 12_000,
    "export_braces": "export {" * 12_000,
    "nested_destructuring": "function f() {" + "const {" * 15_000,
    "nested_require": "const {" * 8_000 + "} = require('x');" * 8_000,
    "nested_functions": "function f() {" * 12_000,
    "nested_classes": "class A {" * 12_000,
    "many_params": "function f(" + "a," * 50_000 + ") {}",
    "unique_functions": "".join(f"function f{i}(){{}}\n" for i in range(10_000)),
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
@pytest.mark.parametrize("rel", ["a.ts"])
def test_adversarial_source_stays_fast(name: str, rel: str) -> None:
    started = time.perf_counter()
    parse_file(rel, ADVERSARIAL[name])
    assert time.perf_counter() - started < 1.0, name


@pytest.mark.parametrize(
    "text",
    [
        "/*" * 50_000,
        "//" * 50_000,
        '"' * 100_001,
        '"\\' * 50_000,
        "," * 100_000,
        ", " * 50_000,
        "," + " " * 100_000,
    ],
)
def test_adversarial_config_stays_fast(text: str) -> None:
    started = time.perf_counter()
    strip_jsonc(text)
    assert time.perf_counter() - started < 1.0


def test_strip_jsonc_leaves_strings_alone() -> None:
    text = '{"paths": {"@/*": ["src/*"], "//": "/* kept */",}, /* gone */ "a": 1, // gone\n}'
    assert json.loads(strip_jsonc(text)) == {
        "paths": {"@/*": ["src/*"], "//": "/* kept */"},
        "a": 1,
    }


def test_tokenize_keeps_lines_and_drops_comments() -> None:
    tokens = tokenize("a // b\n/* c\n*/ d\n'e'")
    assert [(t.text, t.line) for t in tokens] == [("a", 1), ("d", 3), ("e", 4)]

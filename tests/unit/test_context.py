"""A small map that can't go stale, and the right tool for each question."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from ananke.plexus.context import (
    BLOCK_START,
    CLIENTS,
    ROUTES,
    SESSION_HOOK,
    ContextError,
    benchmark,
    callers,
    disable,
    enable,
    estimate_tokens,
    outline,
    pack,
    repo_map,
    serena_setup,
    source_state,
    status,
    symbol,
)
from ananke.plexus.graph.providers.graphifyy import GraphifyyAdapter

SHOP = Path(__file__).resolve().parent.parent / "fixtures" / "repo-mapping" / "shop"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "core.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-qm",
            "i",
        ],
        check=True,
    )
    return root


def fake_tool(folder: Path, name: str, script: str) -> None:
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_the_map_shows_what_matters_within_its_budget(tmp_path: Path) -> None:
    copy = tmp_path / "shop"
    shutil.copytree(SHOP, copy)
    result = repo_map(copy, budget=600)
    text = result["map"]
    assert result["tokens_estimate"] <= 660  # a small allowance for the closing lines
    for wanted in ("backend", "POST /api/orders", "orders", "Next:"):
        assert wanted in text


def test_a_tight_budget_says_what_it_cut(repo: Path) -> None:
    for i in range(80):
        (repo / "pkg" / f"mod{i}.py").write_text("def f():\n    return 1\n")
    assert "more" in repo_map(repo, budget=200)["map"]


def test_the_map_rebuilds_when_the_code_changes_and_only_then(repo: Path) -> None:
    first = repo_map(repo)
    assert first["cached"] is False
    assert repo_map(repo)["cached"] is True
    (repo / "pkg" / "core.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
    )
    changed = repo_map(repo)
    assert changed["cached"] is False and changed["source_state"] != first["source_state"]
    # an edit to a file that was already edited must count too
    before = source_state(repo)
    (repo / "pkg" / "core.py").write_text("def only():\n    return 1\n")
    assert source_state(repo) != before
    (repo / "new.py").write_text("x = 1\n")
    assert repo_map(repo)["cached"] is False


def test_its_own_cache_doesnt_count_as_a_change(repo: Path) -> None:
    repo_map(repo)
    assert repo_map(repo)["cached"] is True


def test_a_corrupt_cache_is_rebuilt(repo: Path) -> None:
    repo_map(repo)
    (repo / ".ananke" / "context" / "map-1500.json").write_text("not json")
    assert repo_map(repo)["cached"] is False


@pytest.mark.parametrize("budget", [0, 199, 20_001])
def test_a_silly_budget_is_refused(repo: Path, budget: int) -> None:
    with pytest.raises(ContextError) as exc:
        repo_map(repo, budget=budget)
    assert exc.value.code == "INVALID_REQUEST"


def test_a_hostile_file_cant_run_or_stop_the_map(repo: Path) -> None:
    (repo / "boom.py").write_text("open('ran.txt', 'w').write('x')\n")
    (repo / "bad.py").write_text("def broken(\n")
    assert repo_map(repo)["map"]
    assert not (repo / "ran.txt").exists()


def test_status_runs_nothing_and_says_how_to_get_each_tool(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    tools = status(repo)["tools"]
    assert tools["map"]["ready"] is True
    assert tools["repomix"]["ready"] is False and "context" in tools["repomix"]["setup"]
    assert tools["graphify"]["ready"] is False and "update" in tools["graphify"]["setup"]
    assert "GPL" in tools["serena"]["licence"]


def test_a_graphify_graph_older_than_the_code_is_called_stale(repo: Path) -> None:
    out = repo / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text("{}")
    os.utime(out / "graph.json", (1, 1))
    assert status(repo)["tools"]["graphify"]["fresh"] is False
    os.utime(out / "graph.json")
    assert status(repo)["tools"]["graphify"]["fresh"] is True


def test_every_question_has_a_route_and_a_reason() -> None:
    assert len(ROUTES) >= 6 and all(all(part for part in route) for route in ROUTES)


def test_serena_is_pinned_runs_apart_and_opens_no_browser(repo: Path) -> None:
    setup = serena_setup(repo)
    args = setup["mcp_server"]["args"]
    assert "serena-agent==2.1.0" in args and args[args.index("--open-web-dashboard") + 1] == "false"
    assert "--language python" in setup["each_repository"]


def test_ananke_doesnt_depend_on_serena() -> None:
    project = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()
    assert "serena" not in project.lower()


def test_a_pack_must_say_what_to_pack(repo: Path) -> None:
    with pytest.raises(ContextError) as exc:
        pack(repo, include=[""])
    assert exc.value.code == "INVALID_REQUEST"


def test_a_pack_runs_repomix_with_the_scope_and_without_noise(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    log = tmp_path / "args.txt"
    fake_tool(
        bin_dir,
        "repomix",
        f'printf "%s\\n" "$@" > "{log}"\nwhile [ $# -gt 0 ]; do [ "$1" = "--output" ] && echo packed > "$2"; shift; done\n',
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = pack(repo, include=["pkg/**/*.py"])
    args = log.read_text().split()
    assert "--compress" in args and "pkg/**/*.py" in args and any("min.js" in a for a in args)
    assert result["tokens_estimate"] == estimate_tokens("packed\n")


def test_a_failed_pack_says_so(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    fake_tool(bin_dir, "repomix", 'echo "it broke" >&2\nexit 3\n')
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(ContextError) as exc:
        pack(repo, include=["*.py"], output=tmp_path / "nothing" / "pack.xml")
    assert exc.value.code == "TOOL_FAILED" and "it broke" in exc.value.message


def test_a_missing_tool_is_a_clear_error(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ContextError) as exc:
        pack(repo, include=["*.py"])
    assert exc.value.code == "TOOL_MISSING"


def test_benchmark_compares_a_first_look_each_way(repo: Path) -> None:
    (repo / "big.py").write_text("x = 1\n" * 5000)
    subprocess.run(["git", "-C", str(repo), "add", "big.py"], check=True)
    result = benchmark(repo)
    assert result["all_source"] > result["map"] and result["biggest_file"]["path"] == "big.py"


def test_the_graphify_adapter_reads_what_the_real_tool_writes(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = {
        "nodes": [
            {
                "id": "a",
                "label": "a()",
                "file_type": "code",
                "source_file": "pkg/core.py",
                "community": 1,
            }
        ],
        "links": [
            {
                "source": "a",
                "target": "b",
                "relation": "calls",
                "confidence": "INFERRED",
                "source_file": "pkg/core.py",
            }
        ],
    }
    bin_dir = tmp_path / "bin"
    fake_tool(
        bin_dir,
        "graphify",
        f'mkdir -p "$2/graphify-out"\ncat > "$2/graphify-out/graph.json" <<\'J\'\n{json.dumps(graph)}\nJ\n',
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    adapter = GraphifyyAdapter()
    assert adapter.available()
    snapshot = adapter.build(repo)
    assert snapshot.nodes[0].name == "a()" and snapshot.nodes[0].path == "pkg/core.py"
    assert snapshot.edges[0].kind == "calls" and snapshot.edges[0].origin == "inferred"


CODE = """\
class Cart:
    def total(self, rate):
        return tax(self.price, rate)


def tax(price, rate):
    return price * rate
"""


@pytest.fixture
def shop(repo: Path) -> Path:
    (repo / "pkg" / "cart.py").write_text(CODE)
    (repo / "pkg" / "note.md").write_text("tax is explained here\n")
    return repo


def test_an_outline_lists_symbols_for_less_than_reading_the_file(shop: Path) -> None:
    result = outline(shop, "pkg/cart.py")
    assert result["symbols"] == 3
    assert all(name in result["text"] for name in ("Cart", "total", "tax"))
    assert "1-3" in result["text"] and "function" in result["text"]
    assert "def tax(price, rate)" in outline(shop, "pkg/cart.py", signatures=True)["text"]


def test_an_outline_says_when_there_is_nothing_to_list(shop: Path) -> None:
    with pytest.raises(ContextError, match="read it directly"):
        outline(shop, "pkg/note.md")


@pytest.mark.parametrize("path", ["../../etc/passwd", "/etc/passwd"])
def test_an_outline_stays_inside_the_repository(shop: Path, path: str) -> None:
    with pytest.raises(ContextError):
        outline(shop, path)


def test_a_link_cant_lead_an_outline_out_of_the_repository(shop: Path, tmp_path: Path) -> None:
    (tmp_path / "secret.py").write_text("def secret():\n    return 1\n")
    (shop / "pkg" / "out.py").symlink_to(tmp_path / "secret.py")
    with pytest.raises(ContextError):
        outline(shop, "pkg/out.py")


def test_a_symbol_comes_with_its_source_and_can_be_narrowed(shop: Path) -> None:
    (shop / "pkg" / "other.py").write_text("def tax():\n    return 'other'\n")
    both = symbol(shop, "tax")["text"]
    assert "price * rate" in both and "'other'" in both
    narrowed = symbol(shop, "tax", path="pkg/other.py")["text"]
    assert "'other'" in narrowed and "price * rate" not in narrowed


def test_a_method_can_be_named_with_its_class(shop: Path) -> None:
    assert "return tax" in symbol(shop, "Cart.total")["text"]


@pytest.mark.parametrize("name", ["", "../../x", "a b", "$(touch pwned)", "-rf"])
def test_a_name_that_isnt_a_symbol_is_refused(shop: Path, name: str) -> None:
    for lookup in (symbol, callers):
        with pytest.raises(ContextError):
            lookup(shop, name)
    assert not (shop / "pwned").exists()


def test_callers_keep_resolved_calls_apart_from_text_matches(shop: Path) -> None:
    text = callers(shop, "tax")["text"]
    resolved, _, mentions = text.partition("other mentions")
    assert "pkg/cart.py" in resolved and "total" in resolved
    assert "pkg/note.md" in mentions


def test_every_lookup_sees_an_edit_without_being_told(shop: Path) -> None:
    assert symbol(shop, "tax")["text"]
    (shop / "pkg" / "cart.py").write_text(CODE.replace("price * rate", "price * rate * 2"))
    assert "rate * 2" in symbol(shop, "tax")["text"]


def test_every_lookup_sees_a_new_file_and_a_deleted_one(shop: Path) -> None:
    (shop / "pkg" / "brand_new.py").write_text("def fresh():\n    return 1\n")
    assert "return 1" in symbol(shop, "fresh")["text"]
    (shop / "pkg" / "brand_new.py").unlink()
    with pytest.raises(ContextError, match="no symbol named fresh"):
        symbol(shop, "fresh")


def test_a_file_in_a_new_folder_counts_as_a_change(shop: Path) -> None:
    before = source_state(shop)
    (shop / "fresh" / "deep").mkdir(parents=True)
    (shop / "fresh" / "deep" / "x.py").write_text("x = 1\n")
    assert source_state(shop) != before


def test_a_hostile_file_cant_run_during_a_lookup(shop: Path) -> None:
    (shop / "pkg" / "evil.py").write_text("open('pwned', 'w').close()\ndef evil(:\n")
    outline(shop, "pkg/cart.py")
    symbol(shop, "tax")
    assert not (shop / "pwned").exists()


def test_the_graph_cache_keeps_itself_out_of_git(shop: Path) -> None:
    symbol(shop, "tax")
    state = subprocess.run(
        ["git", "-C", str(shop), "status", "--short"], check=True, capture_output=True, text=True
    ).stdout
    assert ".ananke" not in state


@pytest.mark.parametrize("client", sorted(CLIENTS))
def test_enabling_writes_where_each_assistant_looks(shop: Path, client: str) -> None:
    result = enable(shop, clients=[client])
    relative, header = CLIENTS[client]
    text = (shop / relative).read_text()
    assert text.startswith(header) and "ananke context outline" in text
    assert result["clients"] == [client]
    assert (shop / ".claude" / "settings.json").exists() == (client == "claude-code")


def test_enabling_detects_the_assistants_this_project_uses(shop: Path) -> None:
    (shop / ".kiro").mkdir()
    (shop / ".cursor").mkdir()
    assert enable(shop, dry_run=True)["clients"] == ["cursor", "kiro"]


def test_with_no_signs_it_covers_claude_code_and_agents_md(shop: Path) -> None:
    assert enable(shop, dry_run=True)["clients"] == ["claude-code", "codex"]


def test_an_unknown_client_is_refused_and_writes_nothing(shop: Path) -> None:
    with pytest.raises(ContextError, match="unknown client"):
        enable(shop, clients=["kiro", "notepad"])
    assert not (shop / ".kiro").exists()


def test_a_dry_run_changes_nothing(shop: Path) -> None:
    result = enable(shop, clients=["all"], dry_run=True)
    assert len(result["changes"]) == len(CLIENTS) + 1
    assert not any((shop / relative).exists() for relative, _ in CLIENTS.values())
    assert not (shop / ".claude").exists()


def test_enabling_twice_changes_nothing_the_second_time(shop: Path) -> None:
    enable(shop, clients=["all"])
    before = (shop / "CLAUDE.md").read_text()
    assert enable(shop, clients=["all"])["changes"] == []
    assert (shop / "CLAUDE.md").read_text() == before


def test_disabling_keeps_what_was_there_and_removes_what_we_added(shop: Path) -> None:
    (shop / "CLAUDE.md").write_text("# House rules\n\nBe kind.\n")
    other_hook = {"hooks": [{"type": "command", "command": "echo mine"}]}
    (shop / ".claude").mkdir()
    (shop / ".claude" / "settings.json").write_text(
        json.dumps({"model": "x", "hooks": {"SessionStart": [other_hook]}})
    )
    enable(shop, clients=["all"])
    assert BLOCK_START in (shop / "CLAUDE.md").read_text()
    assert SESSION_HOOK in str(json.loads((shop / ".claude" / "settings.json").read_text()))

    disable(shop)
    assert (shop / "CLAUDE.md").read_text() == "# House rules\n\nBe kind.\n"
    settings = json.loads((shop / ".claude" / "settings.json").read_text())
    assert settings == {"model": "x", "hooks": {"SessionStart": [other_hook]}}
    assert not (shop / "AGENTS.md").exists()
    assert not (shop / ".kiro" / "steering" / "ananke-context.md").exists()


def test_disabling_removes_a_settings_file_it_created(shop: Path) -> None:
    enable(shop, clients=["claude-code"])
    disable(shop)
    assert not (shop / ".claude" / "settings.json").exists()
    assert not (shop / "CLAUDE.md").exists()


def test_disabling_keeps_text_written_after_the_block(shop: Path) -> None:
    enable(shop, clients=["codex"])
    with (shop / "AGENTS.md").open("a") as handle:
        handle.write("\n## Mine\n")
    disable(shop)
    assert (shop / "AGENTS.md").read_text() == "## Mine\n"


def test_broken_settings_are_refused_rather_than_overwritten(shop: Path) -> None:
    (shop / ".claude").mkdir()
    (shop / ".claude" / "settings.json").write_text("{not json")
    with pytest.raises(ContextError):
        enable(shop, clients=["claude-code"])
    assert (shop / ".claude" / "settings.json").read_text() == "{not json"


def test_status_says_which_assistants_are_enabled(shop: Path) -> None:
    enable(shop, clients=["kiro", "copilot"])
    assert status(shop)["tools"]["graph"]["enabled_for"] == ["copilot", "kiro"]


def test_a_pack_reports_files_it_left_out(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = tmp_path / "tools"
    fake_tool(
        tools,
        "repomix",
        'echo "Detected 2 suspicious files and excluded them"\n'
        'for a in "$@"; do [ "$prev" = "--output" ] || [ "$prev" = "-o" ] && echo packed > "$a"; prev="$a"; done\n',
    )
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    result = pack(repo, include="pkg/**")
    assert result["complete"] is False and result["excluded"] == 2
    assert "2" in result["warning"]


def test_the_mcp_tools_answer_and_only_read(shop: Path) -> None:
    from ananke.plexus.mcp.auth import DEFAULT_PERMISSION_MAP
    from ananke.plexus.mcp.tools import call_tool

    assert "price * rate" in str(
        call_tool(shop, "ananke.context.symbol", {"name": "tax"})["details"]
    )
    assert "tax" in str(
        call_tool(shop, "ananke.context.outline", {"path": "pkg/cart.py"})["details"]
    )
    assert call_tool(shop, "ananke.context.map", {})["ok"] is True
    refused = call_tool(shop, "ananke.context.symbol", {"name": "a b"})
    assert refused["ok"] is False
    assert "ananke.context.callers" in DEFAULT_PERMISSION_MAP.read_only_tools

---
title: Saving tokens
description: Spend far fewer tokens understanding a repository. A small map and symbol lookups from Ananke's own graph, always current with your edits, and used by default in Claude Code, Codex, Copilot, Kiro, Cursor and Amazon Q.
---

# Saving tokens

Reading a repository costs an AI assistant tokens, and most of that is avoidable because most questions need a small part of it.
Ananke answers those questions from its own graph of your code, with nothing else to install.

```bash
ananke context enable                       # this project now uses all of this by default
ananke context map                          # a small picture of the repository
ananke context outline src/shop/orders.py   # what's in a file, without reading it
ananke context symbol place_order           # one function with its source
ananke context callers place_order          # what calls it
```

## What it saves

Measured on Ananke itself, about 790,000 tokens of source, counted with `tiktoken`:

| Question | Command | Reading instead | With the command |
| --- | --- | ---: | ---: |
| Where do I start? | `ananke context map` | 790,000 | 1,162 |
| What's in this big file? | `ananke context outline` | 8,480 | 462 |
| The same, with signatures | `ananke context outline --signatures` | 8,480 | 1,150 |
| A short function from a big file | `ananke context symbol` | 8,480 | 115 |
| The longest function in it | `ananke context symbol` | 8,480 | 1,625 |
| What's in a small file? | `ananke context outline` | 980 | 61 |
| Who calls this? | `ananke context callers` | 167 by `grep` | 191 |

Your numbers will differ, so measure your own repository with `ananke context benchmark`.

## Using it

### Start with the map

```bash
ananke context map
```

You get the languages, components, routes, tables, largest files and most complex functions, usually in under 2,000 tokens.
`--budget` sets roughly how many it may use, and a map that had to cut something says how much:

```bash
ananke context map --budget 500
```

### Look inside a file before reading it

```console
$ ananke context outline src/ananke/plexus/execution/scheduler.py --signatures
src/ananke/plexus/execution/scheduler.py (about 1077 tokens to read)
24-25 function def _noop_handler(step: ExecutionStep, repository_root: Path) -> dict[str, object]
28-31 function def _no_executor(step: ExecutionStep, repository_root: Path) -> dict[str, object]
34-82 function def run_plan(repository_root: Path, plan: ExecutionPlan, *, handlers: dict[...
85-116 function def _execute_step(step: ExecutionStep, repository_root: Path, handlers: dict[...
```

Each line starts with line numbers, so if you do need the code you can read just those lines. Leave off `--signatures` for
names only, which is cheaper. A file with nothing to list, such as Markdown, tells you to read it directly and what that costs.

### Get one function

```console
$ ananke context symbol context_map
src/ananke/plexus/app.py:679-686 method AppFacade.context_map
    def context_map(self, *, budget: int = 1500, refresh: bool = False) -> dict[str, Any]:
        """A small picture of the repository, rebuilt only when its sources change."""
        ...
```

A method can be named with its class, as in `Cart.total`. If several things share a name you get up to three, and `--path` picks one:

```bash
ananke context symbol total --path src/shop/cart.py
ananke context symbol Cart.total
```

### See what calls it

```console
$ ananke context callers run_plan
5 resolved, from the code graph:
src/ananke/plexus/workflow_run.py:206 execute (calls)
tests/unit/test_assembly.py:250 test_compiled_plan_runs_on_the_existing_scheduler (calls)
...
7 other mentions of run_plan, not resolved (text only):
CHANGELOG.md:707
docs/reference/three-repository-audit.md:96
```

The two groups are kept apart on purpose. The first is what the graph worked out. The second is every other place the word
appears, so a call made through a variable or from documentation isn't lost, and you can judge it for yourself.

### From a script or an assistant's tools

Add `--json` for structured output. If your assistant prefers MCP tools, `ananke.context.map`, `outline`, `symbol` and `callers` give
the same answers and are read-only. Plain commands cost nothing until they're used, whereas each tool's description is sent with every chat, so use commands where you can.

## It stays current by itself

You don't run an update step, and no background process watches your files.

Every question first works out the state of your sources: the current commit, plus a hash of every changed or new file that Git
sees. If that matches the saved graph, the answer takes about half a second. If it doesn't, the graph is rebuilt, which took
about two and a half seconds on Ananke, and then answered.

| You did this | The next question sees it |
| --- | --- |
| Edited a file and didn't commit | Yes |
| Added a file, even in a new folder | Yes |
| Deleted a file | Yes |
| Switched branch or pulled | Yes |
| Changed a file Git ignores | No, ignored files aren't part of the graph |

Because the check runs when you ask, an answer can't be out of date, and nothing runs while you aren't asking.

## Use it by default

```bash
ananke context enable --local --dry-run   # see what would change
ananke context enable --local             # just for you
ananke context enable                     # for everyone who works on this project
```

`enable` adds a short note where each assistant looks for project instructions, telling it to use these commands before reading files. It works out which assistants you use, and you can name them with `--client`:

```bash
ananke context enable --client kiro --client copilot
ananke context enable --client all
```

| Assistant | `--client` | Where the note goes | With `--local` |
| --- | --- | --- | --- |
| Claude Code | `claude-code` | `CLAUDE.md` | `CLAUDE.local.md`, which Claude Code also reads |
| OpenAI Codex | `codex` | `AGENTS.md` | `AGENTS.md`, unless Git tracks it |
| GitHub Copilot | `copilot` | `.github/copilot-instructions.md` | The same file, unless Git tracks it |
| Kiro | `kiro` | `.kiro/steering/ananke-context.md` | The same file |
| Cursor | `cursor` | `.cursor/rules/ananke-context.mdc` | The same file |
| Amazon Q | `amazon-q` | `.amazonq/rules/ananke-context.md` | The same file |

Many other tools read `AGENTS.md`, so `--client codex` often covers them too.

### Sharing it, or keeping it to yourself

Without `--local`, these are ordinary project files: they show up in `git status`, and once committed, everyone who works on the repository gets them. That suits a team that has agreed to use this, but it needs Ananke installed for everyone.

With `--local`, nothing reaches a commit. Each file is listed in `.git/info/exclude`, which works like `.gitignore` but never leaves your checkout. If Git already tracks a file, such as a team's `AGENTS.md`, it's left alone, and the result names it under `skipped`.

Running `enable` again changes nothing, and `ananke context status` shows who's enabled. To undo either kind:

```bash
ananke context disable
```

That removes only what was added, and leaves your own text, hooks and exclude lines as they were.

### What it costs

| What | Tokens | When |
| --- | ---: | --- |
| The note | about 80 | Every chat |
| Each lookup | 60 to 1,600 | Only when used |
| The map | about 1,200 here | Only when used |
| The map with `--session-map` | about 1,200 here | Every Claude Code chat, needed or not |
| Serena connected, for comparison | about 1,100 | Every chat, used or not |

So the cheapest setup is the default: the note, with the map run only when it helps. One avoided read of a large file pays for about 100 chats' worth of the note. `--session-map` is worth adding only if nearly every chat starts by exploring.

The note is an instruction, so an assistant usually follows it but isn't forced to. Nothing stops it reading a file, which is sometimes the right thing to do.

### The skill for your assistant

The `repo-context` skill teaches an assistant the same routing, for every repository and not just the ones you've enabled. It's in
[Dex Trove](https://github.com/Neo-Pragnya/Dex-Trove):

```bash
dex install neo-pragnya/repo-context --target claude
dex install neo-pragnya/repo-context --target copilot
```

Use `enable` for a project you want set up for everyone who works on it, and the skill for your own habits across all of them.

## Notebooks

A folder of notebooks works too. A file a notebook writes with `%%writefile` counts as a file at that path, even before anyone runs the notebook, so you can look inside it:

```bash
ananke context outline Week-1/app/service.py
ananke context symbol load_orders
```

## It all runs offline

The map, `outline`, `symbol`, `callers`, `enable` and `disable` make no network connection, and a test checks that. They need no account, key or licence, and your code stays on your machine until an assistant sends what it reads to its own provider.

## How it compares with Serena

[Serena](https://github.com/oraios/serena) is a very good tool, built on language servers, and the comparison is worth being straight about.
Same machine, same repository, same questions:

| Question | Serena | Ananke | |
| --- | ---: | ---: | --- |
| Names in a big file | 277 | 462 | Serena, though Ananke's include line numbers |
| The longest function in a big file | 4,427 | 1,625 | Ananke |
| A small file | 1,191 | 403 | Ananke; reading it costs 980 |
| Who calls this? | 1,730 | 130 | Ananke; `grep` costs 167 |

Serena returns more around each answer, which is why it costs more. On a small file its overhead costs more than the file.
Connecting it also costs about 1,100 tokens per chat in its default mode, or about 8,000 with `--agent-interface tools`. Ananke's commands cost nothing until used.

Here's what each does:

| | Ananke | Serena |
| --- | --- | --- |
| Outline a file, find a symbol | Yes | Yes |
| Find what calls a symbol | Yes, inferred from names and imports, plus text matches | Yes, following types |
| Stay current with edits | Yes | Yes |
| Whole-repository map, routes, tables, components | Yes | No |
| Works with no language server or download | Yes | No |
| Languages | Python, JavaScript, TypeScript | Many, through language servers |
| Rename a symbol across a project | No | Yes |
| Edit by symbol: replace a body, insert before or after | No | Yes |
| Compiler diagnostics | No | Yes |
| Memory across chats | No, use your notes or `CLAUDE.md` | Yes |
| Licence | Apache-2.0 | GPL-3.0-or-later |

So Ananke is the better default for reading and saves more on most questions. If you work in another language, need references
that follow types through a large refactor, or want symbol-level editing, Serena does things Ananke doesn't. Those gaps are on the
[roadmap](https://github.com/Neo-Pragnya/Ananke-Plexus/blob/main/ROADMAP.md).

### Adding Serena if you want it

Ananke never imports or bundles Serena, because of its licence. It's a separate program that you connect to your assistant yourself.
`ananke context status` prints this, pinned to a version we tested, with its dashboard and its usage ping off:

```bash
claude mcp add --scope user serena --env SERENA_USAGE_REPORTING=false -- \
  uvx --python 3.12 --from serena-agent==2.1.0 serena start-mcp-server \
  --context claude-code --project-from-cwd --enable-web-dashboard false --open-web-dashboard false
```

Each repository then needs a project file, or lookups quietly find nothing:

```bash
uvx --python 3.12 --from serena-agent==2.1.0 serena project create . --language python
```

Your code stays on your machine either way. Left on, Serena's ping sends its version, your operating system and its settings, but
no code, to its authors. Its language servers may be downloaded the first time.

## Other tools for other jobs

Both are optional.

**[repomix](https://github.com/AndersonBY/python-repomix): many files at once.** For when you need to read a lot of source together.
It's an optional extra, `pip install "ananke-plexus[context]"`:

```bash
ananke context pack --include 'src/**/*.py'
```

`--include` is required. On a whole repository, documentation can dominate and compression saved as little as 5%, while on source alone
it saved 68%: 288,740 tokens down to 92,766. repomix's secret check can leave out files, which once silently cost us `app.py`, so the
result now says what was dropped. repomix has no way to stay current on its own: you pack when you ask.

**[Graphify](https://github.com/Graphify-Labs/graphify): structure across repositories.** For how things hang together across a large
repository or several. It needs `graphify update` to stay current, and `ananke context status` tells you when it's behind.
Put `**/*.min.js` and similar in `.graphifyignore`. Don't read `graph.json` directly, because it can be hundreds of thousands of tokens.
Read `GRAPH_REPORT.md`, or use `graphify query` and `graphify explain`.

## Your assistant's chat

Most of the cost of a long chat is the chat itself, because everything said so far is sent with each turn. Start a new chat for a new
task, and let the map and your notes carry what matters across.

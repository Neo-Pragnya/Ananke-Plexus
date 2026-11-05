---
title: Saving tokens
description: Spend far fewer tokens understanding a repository. A small map that can't go stale, then Serena, repomix or Graphify, whichever measured best for the question.
---

# Saving tokens

Reading a repository costs an AI assistant tokens. Most of that is avoidable, because most questions need a small part of it.
Ananke gives you a small map first, then points you at the cheapest tool for each question.

```bash
ananke context map                # a small picture of this repository
ananke context status             # which tools are ready, and how to get the others
ananke context pack --include 'src/**/*.py'
ananke context benchmark          # what a first look costs here
```

## The map

A map of a repository usually costs under 2,000 tokens: languages, components, routes, tables, largest files and most complex
functions. On Ananke itself that's about 900 tokens, against about 790,000 for reading its source.

**It can't silently go stale.** It records a fingerprint of the current commit and the content of any changed files. If they differ,
it rebuilds. If not, it returns the saved copy in about half a second.

`--budget` sets the most tokens it may use, and a map that had to cut something says how much.

## Which tool for which question

Measured on Ananke (about 790,000 tokens of source, counted with `tiktoken`):

| Question | Use | Reading it instead | With the tool |
| --- | --- | ---: | ---: |
| Where do I start? | `ananke context map` | 790,000 | 900 |
| What's in this big file? | Serena `get_symbols_overview` | 8,138 | 277 |
| One function from a big file | Serena `find_symbol` | 8,138 | 4,427 |
| Who calls this? | `grep`, not Serena | 167 by `grep` | 1,730 |
| What's in this small file? | Read it | 980 | 1,191 with Serena |
| All the source at once | `ananke context pack` | 288,740 | 92,766 |
| How does it hang together? | Graphify's report | n/a | 20,881 |

Some of that is surprising. Serena's references cost ten times what `grep` does when you only need locations. On a small file, its
overhead costs more than the file.

## The tools

All three are optional, and Ananke works without them.

| Tool | Best at | Stays current? | Licence |
| --- | --- | --- | --- |
| [Serena](https://github.com/oraios/serena) | Symbols in big files, through a language server | Yes, it reads the code each time | GPL-3.0-or-later |
| [repomix](https://github.com/AndersonBY/python-repomix) | Many files at once, compressed | Yes, packed when you ask | MIT |
| [Graphify](https://github.com/Graphify-Labs/graphify) | Structure across one repository or several | Only after `graphify update` | Apache-2.0 |

**Serena runs apart from Ananke.** Because it's GPL, Ananke never imports or bundles it. It's a separate MCP server you connect to
your assistant. `ananke context status` prints the command, pinned to a tested version:

```bash
claude mcp add --scope user serena -- uvx --python 3.12 --from serena-agent==2.1.0 serena start-mcp-server \
  --context claude-code --project-from-cwd --enable-web-dashboard false --open-web-dashboard false
```

Each repository then needs a project file, or lookups quietly find nothing:

```bash
uvx --python 3.12 --from serena-agent==2.1.0 serena project create . --language python
```

Connecting Serena costs about 1,100 tokens in its default mode, with 2 tools. `--agent-interface tools` exposes 22 tools for
about 8,000 tokens, so leave it on the default unless your client loads tools lazily.

**Scope repomix.** `--include` is required. On a whole repository, documentation can dominate, and compression saved as little as
5%. On source alone, it saved 68%.

**Keep Graphify away from generated files.** On Ananke, its top communities were a minified file until those were ignored. Put
`**/*.min.js` and similar in `.graphifyignore`. Never read `graph.json` directly: it's hundreds of thousands of tokens or more. Read
`GRAPH_REPORT.md`, or use `graphify query` and `graphify explain`.

## Your assistant's chat

Most of the cost of a long chat is the chat itself: everything said so far is sent with each turn. Start a new chat for a new task,
and let the map and your notes carry what matters across.

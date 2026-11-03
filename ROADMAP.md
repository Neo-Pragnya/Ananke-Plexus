# Roadmap

What's planned for [Ananke Plexus](https://pypi.org/project/ananke-plexus/). Release notes for each version are in
[CHANGELOG.md](CHANGELOG.md). Plans change; this says what's intended, and nothing here is promised until it's released.

## Now: what Dev Guru needs

Dev Guru is the current priority, and Ananke does the computing for it.

| Feature | What it does |
| --- | --- |
| **Connected code graph** | Resolves imports across folders, including relative imports and subfolder source roots. Reads JavaScript and TypeScript |
| **Data model** | Finds tables, fields and keys from ORMs and SQL, and which code reads or writes them |
| **API pathways** | Finds routes and HTTP clients, and links a frontend call to the route it reaches |
| **End-to-end flows** | Traces from an entry point through handlers and services to data stores |
| **Inferred architecture** | Produces a CALM model from the repository, without overwriting a declared one |
| **Quality and coverage** | Complexity, size and hotspots, plus existing coverage reports |
| **Dependencies and CVEs** | Lists Python and npm packages, and checks them against OSV when asked |

All of it is one analysis (`ananke app run repo.analyze`), described in
[Repo intelligence](docs/reference/repo-intelligence.md).

## Next

- A real agent backend tested end to end for workflow runs, which are tested only with the fake backend today.
- Enforcing permissions on real agents, rather than only checking what they declare.
- Running tool capabilities in a sandbox. Today, only instruction capabilities run.
- Retries and parallel steps in workflow runs.

## Later

Capability families, each with a Dex skill once its operation exists:

- Code review findings, normalised quality and CVE findings, and API contract intelligence.
- API test generation, BDD and TDD strategy selection.
- Repository documentation from the graph, context retrieval with token budgets, and session learning.
- Multi-repository topology, PR and release evidence, browser debugging, infrastructure and Docker analysis.
- Connectors that need real external systems: Jenkins, Kibana, Copilot analytics and SAS modernisation.

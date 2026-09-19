# Contributing to Ananke Plexus

Thanks for your interest in contributing to Ananke Plexus. We welcome code, documentation, tests, and design feedback from the community.

Before you begin, please read our [Code of Conduct](CODE_OF_CONDUCT.md). We expect all contributors to uphold the standards in that document.

## How to contribute

### 1. Fork and clone

Fork the repository and clone your fork locally:

```bash
git clone https://github.com/Neo-Pragnya/Ananke-Plexus.git
cd Ananke-Plexus
```

### 2. Create a branch

Use a short, descriptive branch name:

```bash
git checkout -b feature/my-change
```

### 3. Set up the project

Use the project's recommended local dev flow:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

If you are working on docs or examples, you can also install the docs extras:

```bash
pip install -e ".[docs]"
```

### 4. Make changes

- Keep changes focused and easy to review.
- Prefer small, well-documented pull requests.
- Update docs when behavior, configuration, or workflows change.
- Keep tests and formatting in mind.

### 5. Validate locally

Run the relevant checks before opening a PR:

```bash
pytest
ruff check .
mypy src
mkdocs build --strict
```

### 6. Commit and push

```bash
git add .
git commit -m "Describe your change"
git push origin feature/my-change
```

### 7. Open a pull request

Open a pull request against the `main` branch and include:

- a brief summary of the change
- why the change is needed
- any validation or test results
- screenshots or examples when relevant

## Reporting issues

Use the GitHub issue tracker to report bugs, feature requests, or docs problems:

- https://github.com/Neo-Pragnya/Ananke-Plexus/issues

When creating an issue, include:

- a clear description of the problem
- reproduction steps or sample inputs
- expected vs actual behavior
- relevant logs, screenshots, or stack traces

## Documentation

Project docs live in the `docs/` directory and are built with MkDocs. Please keep the docs in sync with user-facing changes.

## Code of conduct

Participation in this project is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). Please treat everyone with respect and professionalism.

## Thanks

Thank you for helping improve Ananke Plexus.
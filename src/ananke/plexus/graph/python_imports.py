"""Which repository file a Python import means, without running anything.

A module's name depends on where its source root is, and a repository can hold several: ``import app.db`` means
``backend/app/db.py`` when ``backend/`` is the root. Roots are worked out from the code itself.
"""

import ast
import posixpath
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field

ROOT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")
# A copy of the code rather than the code: ``build/lib/app/db.py`` must not compete with ``src/app/db.py``.
_ARTIFACT_DIRS = {"dist", "build", "vendor", "vendored", "third_party"}


def _parent(directory: str) -> str:
    return posixpath.dirname(directory)


def _is_artifact(root: str) -> bool:
    return bool(_ARTIFACT_DIRS & set(root.split("/")))


@dataclass(frozen=True)
class Resolved:
    file: str = ""
    ambiguous: bool = False


@dataclass(frozen=True)
class ImportLink:
    """One edge's worth of an import: the name it uses, and the file that name means if it is in the repository."""

    target: str
    file: str
    ambiguous: bool
    line: int


@dataclass
class FileImports:
    links: list[ImportLink] = field(default_factory=list)
    # local name → (file, member); a member of None is the module itself
    top_level: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    nested: dict[str, tuple[str, str | None]] = field(default_factory=dict)  # inside a block
    stars: list[str] = field(default_factory=list)
    dotted: dict[str, str] = field(default_factory=dict)  # ``import a.b`` → {"a": …, "a.b": …}


class ModuleIndex:
    """Every module's possible names, and the source root each name implies."""

    def __init__(self, rel_paths: Iterable[str], declared_roots: Iterable[str] = ()) -> None:
        self.files = set(rel_paths)
        self.declared_roots = set(declared_roots)
        self.packages = {
            _parent(path) for path in self.files if posixpath.basename(path) == "__init__.py"
        }
        self._by_name: dict[str, dict[str, str]] = {}  # dotted name → implied root → file
        # A package wins over a module of the same name, as it does when Python imports it.
        for path in sorted(self.files, key=lambda p: (posixpath.basename(p) != "__init__.py", p)):
            parts = path.removesuffix(".py").split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for start in range(len(parts) - 1, -1, -1):
                if not parts[start].isidentifier():
                    break
                root = "/".join(parts[:start])
                self._by_name.setdefault(".".join(parts[start:]), {}).setdefault(root, path)

    def root_of(self, rel_path: str) -> str:
        """The folder a file's top-level package sits in; for a file outside any package, its own folder."""
        directory = _parent(rel_path)
        # the repository's own folder can be a package (an __init__.py at the top); its parent is itself, so stop
        while directory and directory in self.packages:
            directory = _parent(directory)
        return directory

    def source_roots(self) -> list[str]:
        """Folders below the repository root that hold a top-level package or declare a project."""
        roots = {
            self.root_of(path) for path in self.files if _parent(path) in self.packages
        } | self.declared_roots
        return sorted(root for root in roots if root and not _is_artifact(root))

    def _reachable_root(self, root: str, top: str) -> bool:
        """Whether code outside a root can import from it: it is declared, or holds a real package."""
        if root in self.packages:
            return False
        return (
            not root
            or root in self.declared_roots
            or posixpath.basename(root) == "src"
            or posixpath.join(root, top) in self.packages
        )

    def resolve_absolute(self, importer: str, dotted: str) -> Resolved:
        """The importer's own root, then the folders above it, then one root elsewhere. Never a guess."""
        by_root = self._by_name.get(dotted)
        if not by_root:
            return Resolved()
        top = dotted.split(".")[0]
        own_root = self.root_of(importer)
        if top in sys.stdlib_module_names:
            # ``import logging`` beside ``app/logging.py`` means the standard library, unless a real package shadows it.
            found = by_root.get(own_root, "")
            shadowing = posixpath.join(own_root, top) in self.packages
            return Resolved(found if shadowing and found != importer else "")
        directory = own_root
        while True:
            found = by_root.get(directory, "")
            usable = directory == own_root or directory not in self.packages
            if found and found != importer and usable:
                return Resolved(found)
            if not directory:
                break
            directory = _parent(directory)
        # Code in ``build/`` can import its neighbours, but nothing else should reach into a copy.
        others = {
            root: file
            for root, file in by_root.items()
            if file != importer and not _is_artifact(root)
        }
        reachable = [file for root, file in others.items() if self._reachable_root(root, top)]
        if len(reachable) == 1:
            return Resolved(reachable[0])
        if reachable:
            return Resolved(ambiguous=True)
        if "." in dotted and others:  # a dotted path is strong evidence even without __init__.py
            only = next(iter(others.values())) if len(others) == 1 else ""
            return Resolved(only, ambiguous=not only)
        return Resolved()

    def relative_base(self, importer: str, level: int, module: str | None) -> str | None:
        """The path a relative import names, or None when it climbs out of the repository."""
        directory = _parent(importer)
        for _ in range(level - 1):
            if not directory:
                return None
            directory = _parent(directory)
        return posixpath.join(directory, *module.split(".")) if module else directory

    def module_at(self, path: str) -> str:
        """The module a path names, as ``<path>/__init__.py`` or ``<path>.py``."""
        package = posixpath.join(path, "__init__.py")
        if package in self.files:
            return package
        return f"{path}.py" if path and f"{path}.py" in self.files else ""

    def submodule(self, package_file: str, name: str) -> str:
        """``name`` as a module directly inside the package that ``package_file`` initialises."""
        if posixpath.basename(package_file) != "__init__.py":
            return ""
        return self.module_at(posixpath.join(_parent(package_file), name))


def _bind(imports: FileImports, top: bool, local: str, value: tuple[str, str | None]) -> None:
    if top:
        imports.top_level[local] = value
    else:
        imports.nested.setdefault(local, value)


def _read_import(
    index: ModuleIndex, rel: str, item: ast.Import, imports: FileImports, top: bool
) -> None:
    for alias in item.names:
        found = index.resolve_absolute(rel, alias.name)
        imports.links.append(
            ImportLink(f"module:{alias.name}", found.file, found.ambiguous, item.lineno)
        )
        if not found.file:
            continue
        if alias.asname:
            _bind(imports, top, alias.asname, (found.file, None))
            continue
        imports.dotted[alias.name] = found.file
        parts = alias.name.split(".")
        for end in range(1, len(parts)):  # ``import a.b.c`` makes ``a`` and ``a.b`` usable too
            prefix = ".".join(parts[:end])
            parent = index.resolve_absolute(rel, prefix)
            if parent.file:
                imports.dotted[prefix] = parent.file


def _read_from_import(
    index: ModuleIndex, rel: str, item: ast.ImportFrom, imports: FileImports, top: bool
) -> None:
    written = f"{'.' * item.level}{item.module or ''}"  # as in the source, so it needs no root
    line = item.lineno
    ambiguous = False
    base_file = ""
    base_path: str | None = None
    if item.level:
        base_path = index.relative_base(rel, item.level, item.module)
        base_file = index.module_at(base_path) if base_path is not None else ""
    elif item.module:
        found = index.resolve_absolute(rel, item.module)
        base_file, ambiguous = found.file, found.ambiguous

    from_module = False  # does the statement take anything from the module itself?
    submodules: list[ImportLink] = []
    for alias in item.names:
        if alias.name == "*":
            from_module = True
            if base_file:
                imports.stars.append(base_file)
            continue
        local = alias.asname or alias.name
        name = f"module:{written}{'' if written.endswith('.') else '.'}{alias.name}"
        submodule = ""
        sub_ambiguous = False
        if base_path is not None:
            submodule = index.module_at(posixpath.join(base_path, alias.name))
        elif base_file:
            submodule = index.submodule(base_file, alias.name)
        elif item.module and not item.level and not ambiguous:
            # a folder without __init__.py is still a namespace
            sub = index.resolve_absolute(rel, f"{item.module}.{alias.name}")
            submodule, sub_ambiguous = sub.file, sub.ambiguous
        if submodule and submodule != rel:
            submodules.append(ImportLink(name, submodule, False, line))
            # the package may define this name itself; if not, it is the submodule
            _bind(imports, top, local, (base_file, alias.name) if base_file else (submodule, None))
        elif sub_ambiguous:
            submodules.append(ImportLink(name, "", True, line))
        else:
            from_module = True
            if base_file:
                _bind(imports, top, local, (base_file, alias.name))
    if from_module and base_file != rel:
        imports.links.append(ImportLink(f"module:{written}", base_file, ambiguous, line))
    imports.links.extend(submodules)


def read_imports(index: ModuleIndex, rel: str, tree: ast.Module) -> FileImports:
    """Imports anywhere in a file: at the top, inside ``try``, under ``TYPE_CHECKING``, or in a function."""
    imports = FileImports()
    top_level = {id(item) for item in tree.body}
    for item in ast.walk(tree):
        if isinstance(item, ast.Import):
            _read_import(index, rel, item, imports, id(item) in top_level)
        elif isinstance(item, ast.ImportFrom):
            _read_from_import(index, rel, item, imports, id(item) in top_level)
    return imports


class Symbols:
    """Follows names through imports and re-exports to the definition they mean."""

    MAX_HOPS = (
        200  # visits per lookup: enough for many star imports, and it stops a pathological one
    )

    def __init__(
        self,
        index: ModuleIndex,
        imports: dict[str, FileImports],
        defs: dict[str, dict[str, str]],
        methods: dict[str, dict[str, dict[str, str]]],
    ) -> None:
        self.index = index
        self.imports = imports
        self.defs = defs
        self.methods = methods

    def _bound(self, value: tuple[str, str | None], seen: set[tuple[str, str]]) -> str | None:
        file, member = value
        return f"file:{file}" if member is None else self.member(file, member, seen)

    def local(self, rel: str, name: str, seen: set[tuple[str, str]] | None = None) -> str | None:
        """What a bare name means in a file: a symbol id, or ``file:<path>`` for a module."""
        seen = set() if seen is None else seen
        imports = self.imports.get(rel)
        if imports and name in imports.top_level:
            found = self._bound(imports.top_level[name], seen)
            if found:
                return found
        own = self.defs.get(rel, {}).get(name)
        if own:
            return own
        if not imports:
            return None
        if name in imports.nested:
            found = self._bound(imports.nested[name], seen)
            if found:
                return found
        for star in imports.stars:
            found = self.member(star, name, seen)
            if found and not found.startswith("file:"):
                return found
        return None

    def member(self, rel: str, name: str, seen: set[tuple[str, str]] | None = None) -> str | None:
        seen = set() if seen is None else seen
        if (rel, name) in seen or len(seen) >= self.MAX_HOPS:
            return None
        seen.add((rel, name))
        found = self.local(rel, name, seen)
        if found:
            return found
        submodule = self.index.submodule(rel, name)
        return f"file:{submodule}" if submodule else None

    def resolve(self, chain: list[str], rel: str, cls: str | None) -> str | None:
        """``a.b.c`` as written at a call or base class, in a file and optionally a class."""
        if not chain:
            return None
        if chain[0] in ("self", "cls") and cls is not None:
            return self.methods.get(rel, {}).get(cls, {}).get(chain[1]) if len(chain) == 2 else None
        imports = self.imports.get(rel)
        current: str | None = None
        rest = chain[1:]
        if imports:
            for end in range(len(chain) - 1, 0, -1):
                file = imports.dotted.get(".".join(chain[:end]))
                if file:
                    current, rest = f"file:{file}", chain[end:]
                    break
        current = current or self.local(rel, chain[0])
        for attr in rest:
            if current is None:
                return None
            kind, _, tail = current.partition(":")
            if kind == "file":
                current = self.member(tail, attr)
            elif kind == "class":
                cls_file, _, cls_name = tail.partition(":")
                current = self.methods.get(cls_file, {}).get(cls_name, {}).get(attr)
            else:
                return None
        return None if current is None or current.startswith("file:") else current


def attribute_chain(node: ast.expr) -> list[str]:
    """``a.b.c`` as ``["a", "b", "c"]``; empty for anything that isn't a plain dotted name."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return []
    parts.append(node.id)
    return parts[::-1]

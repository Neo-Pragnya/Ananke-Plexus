"""HTML rendering of the documentation model (spec §72-§80, §112, §131, §143).

Templates use Jinja2 with **autoescape on**; the only ``Markup`` values are output of the
safe Markdown renderer and of the SVG generator (which escapes its own text). Pages use no
inline styles or scripts in multi-page mode so a strict CSP (``default-src 'none'``) works.
"""

from __future__ import annotations

import base64
import hashlib
import json
import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from jinja2 import DictLoader, Environment, pass_context
from jinja2.runtime import Context
from markupsafe import Markup

from ananke.plexus.registry.docsgen.assets import CSS, JS
from ananke.plexus.registry.docsgen.markdown import render_markdown
from ananke.plexus.registry.docsgen.model import (
    DocArtifact,
    DocComparison,
    DocVersion,
    RegistryDocumentationModel,
)
from ananke.plexus.registry.docsgen.svg import render_graph_svg, render_lineage_svg
from ananke.plexus.registry.models import ArtifactKind

TEMPLATE_VERSION = "1"
CSP_MULTI = (
    "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'"
)


def pg_id(key: str) -> str:
    return "pg-" + re.sub(r"[^A-Za-z0-9]+", "-", key).strip("-")


def safe_ver(version: str) -> str:
    return version.replace("+", "_")


class Linker:
    def __init__(self, single: bool) -> None:
        self.single = single

    def href(self, current: str, target: str) -> str:
        if self.single:
            return "#" + pg_id(target)
        # anchored at "/" so relpath never has to ask the OS for the working directory
        return posixpath.relpath("/" + target, start="/" + (posixpath.dirname(current) or "."))


@dataclass
class PageSpec:
    key: str
    title: str
    template: str
    ctx: dict[str, Any] = field(default_factory=dict)
    refs: set[str] = field(
        default_factory=set
    )  # artifacts whose content shapes this page (graph closure)
    link_refs: set[str] = field(default_factory=set)  # artifacts only linked to (related / used-by)


_MACROS = r"""
{% macro badges(items) -%}
<div class="badges">{% for b in items %}<span class="badge {{ b|badge_class }}">{{ b }}</span>{% endfor %}</div>
{%- endmacro %}

{% macro state(v) -%}
<span class="badge {{ v.lifecycle }}">{{ v.lifecycle }}</span>
<span class="badge {{ v.trust }}">{{ v.trust }}</span>
<span class="badge kind">{{ v.channel }}</span>
{%- endmacro %}

{% macro table_of_pairs(pairs, empty) -%}
{% if pairs %}<table><tbody>{% for k, val in pairs %}<tr><td class="mono">{{ k }}</td><td class="mono">{{ val }}</td></tr>{% endfor %}</tbody></table>
{% else %}<p class="muted">{{ empty }}</p>{% endif %}
{%- endmacro %}

{% macro detail(a, v, graph, lineage, is_default) -%}
{% if v.lifecycle == 'quarantined' %}<div class="banner bad"><strong>Quarantined.</strong> This version must not be used.{% if v.lifecycle_message %} {{ v.lifecycle_message }}{% endif %}</div>
{% elif v.lifecycle == 'yanked' %}<div class="banner warn"><strong>Yanked.</strong> Kept for lockfile reproducibility; new resolution avoids it.{% if v.lifecycle_message %} {{ v.lifecycle_message }}{% endif %}</div>
{% elif v.lifecycle == 'deprecated' %}<div class="banner warn"><strong>Deprecated.</strong>{% if v.lifecycle_message %} {{ v.lifecycle_message }}{% endif %}{% if v.replacement %} Use <span class="mono">{{ v.replacement }}</span> instead.{% endif %}</div>
{% endif %}
{{ badges(v.badges) }}
{% if v.summary %}<p class="lead">{{ v.summary }}</p>{% endif %}
{% if v.description %}<div class="prose">{{ v.description|md }}</div>{% endif %}

<h2>Capabilities</h2>
{% if v.capabilities %}<div class="badges">{% for c in v.capabilities %}<a class="badge kind" href="{{ link('capabilities/index.html') }}">{{ c }}</a>{% endfor %}</div>{% else %}<p class="muted">None declared.</p>{% endif %}
<h3>Runtime support</h3>
{% if v.runtimes %}<div class="badges">{% for r in v.runtimes %}<span class="badge runtime">{{ r }}</span>{% endfor %}</div>{% else %}<p class="muted">No runtime restriction declared.</p>{% endif %}

{% if v.tools %}<h2>Tools</h2>
<table><thead><tr><th>Name</th><th>Description</th></tr></thead><tbody>
{% for t in v.tools %}<tr><td class="mono">{{ t.name }}</td><td>{{ t.description }}
{% if t.input_schema %}<details><summary>input schema</summary><pre><code>{{ t.input_schema }}</code></pre></details>{% endif %}
{% if t.output_schema %}<details><summary>output schema</summary><pre><code>{{ t.output_schema }}</code></pre></details>{% endif %}</td></tr>{% endfor %}
</tbody></table>{% endif %}

{% if v.inputs or v.outputs %}<h2>Inputs &amp; outputs</h2>
{% if v.inputs %}<h3>Input schema</h3><pre><code>{{ v.inputs }}</code></pre>{% endif %}
{% if v.outputs %}<h3>Output schema</h3><pre><code>{{ v.outputs }}</code></pre>{% endif %}{% endif %}

{% if v.instructions_ref or v.skills or v.policies or v.eval_suite or v.model_requirements %}<h2>Agent configuration</h2>
<table><tbody>
{% if v.instructions_ref %}<tr><td>Instructions</td><td class="mono">{{ v.instructions_ref }}</td></tr>{% endif %}
{% if v.skills %}<tr><td>Skills</td><td class="mono">{{ v.skills|join(', ') }}</td></tr>{% endif %}
{% if v.policies %}<tr><td>Policies</td><td class="mono">{{ v.policies|join(', ') }}</td></tr>{% endif %}
{% if v.eval_suite %}<tr><td>Evaluation suite</td><td class="mono">{{ v.eval_suite }}</td></tr>{% endif %}
{% for k, val in v.model_requirements.items() %}<tr><td>Model requires</td><td class="mono">{{ k }} = {{ val }}</td></tr>{% endfor %}
</tbody></table>{% endif %}

<h2>Permissions</h2>
{{ table_of_pairs(v.permissions, 'No permissions requested (no filesystem, shell or network access).') }}

<h2>Dependencies</h2>
{% if v.dependencies %}<table><thead><tr><th>Dependency</th><th>Type</th><th>Requirement</th><th>Resolves to</th></tr></thead><tbody>
{% for d in v.dependencies %}<tr><td class="mono">{% if d.target_uri and d.resolved_version %}<a href="{{ link(d.target_uri|uri_page(d.resolved_version)) }}">{{ d.label }}</a>{% else %}{{ d.label }}{% endif %}{% if d.optional %} <span class="muted">(optional)</span>{% endif %}</td>
<td>{{ d.type }}</td><td class="mono">{{ d.version or '*' }}</td>
<td class="mono">{% if d.resolved_version %}{{ d.resolved_version }}{% elif d.type == 'artifact' %}<span class="no">not registered</span>{% else %}<span class="muted">external</span>{% endif %}</td></tr>{% endfor %}
</tbody></table>{% else %}<p class="muted">No dependencies.</p>{% endif %}
{% if graph %}<h3>Dependency graph</h3>{{ graph }}{% endif %}

{% if v.compatibility %}<h2>Compatibility</h2>{{ table_of_pairs(v.compatibility, '') }}{% endif %}

{% if v.readme %}<h2>README</h2><div class="prose">{{ v.readme|md }}</div>{% endif %}

<h2>Tests</h2>
{% if v.test_files %}<ul class="plain">{% for f in v.test_files %}<li class="mono">{{ f }}</li>{% endfor %}</ul>{% else %}<p class="muted">This version ships no tests.</p>{% endif %}
<p>Recorded test status: <strong>{{ v.quality.get('tests_status', 'unknown') }}</strong>
{% if v.quality.get('evaluation_suite') %} · evaluation <span class="mono">{{ v.quality.get('evaluation_suite') }}</span>{% if v.quality.get('evaluation_score') is not none %} = {{ v.quality.get('evaluation_score') }}{% endif %}{% endif %}
{% if v.quality.get('last_verified_at') %} · verified {{ v.quality.get('last_verified_at') }}{% endif %}</p>

<h2>Trust, license &amp; security</h2>
<table><tbody>
<tr><td>Trust</td><td>{{ v.trust }}</td></tr><tr><td>Channel</td><td>{{ v.channel }}</td></tr>
<tr><td>Lifecycle</td><td>{{ v.lifecycle }}</td></tr><tr><td>Registry revision</td><td>{{ v.revision }}</td></tr>
<tr><td>License</td><td>{{ v.license or 'unknown' }} <span class="muted">({{ v.license_approval }})</span></td></tr>
<tr><td>Security</td><td>{{ v.security.get('status', 'unknown') }}{% if v.security.get('scanner') %} · scanner {{ v.security.get('scanner') }}{% endif %}
· findings {{ v.security.get('findings_count', 0) }} · critical {{ v.security.get('critical_count', 0) }}</td></tr>
{% if v.owners %}<tr><td>Owners</td><td>{{ v.owners|join(', ') }}</td></tr>{% endif %}
{% if v.maintainers %}<tr><td>Maintainers</td><td>{{ v.maintainers|join(', ') }}</td></tr>{% endif %}
</tbody></table>

<h2>Provenance</h2>
<table><tbody>{% for k, val in v.provenance.items() %}<tr><td>{{ k|replace('_', ' ') }}</td><td class="mono">{{ val }}</td></tr>{% endfor %}</tbody></table>

<h2>Source files</h2>
{% if v.files %}<table><thead><tr><th>Path</th><th class="c">Bytes</th></tr></thead><tbody>{% for p, n in v.files %}<tr><td class="mono">{{ p }}</td><td class="c">{{ n }}</td></tr>{% endfor %}</tbody></table>{% else %}<p class="muted">No files.</p>{% endif %}
{%- endmacro %}
"""

_TEMPLATES: dict[str, str] = {
    "macros": _MACROS,
    "home": r"""{% import "macros" as m with context %}
<h1>Ananke Plexus Registry</h1>
<p class="muted">Local-first, versioned catalogue of skills, agents and other capabilities. Snapshot <span class="mono">{{ model.snapshot[:23] }}</span>.</p>
<div class="cards">
{% for k in model.kinds_present %}<div class="card"><div class="n">{{ model.counts.get(k, 0) }}</div><div class="l">{{ k|plural }}</div></div>{% endfor %}
<div class="card"><div class="n">{{ model.counts.get('versions', 0) }}</div><div class="l">versions</div></div>
<div class="card"><div class="n">{{ model.counts.get('active', 0) }}</div><div class="l">active</div></div>
<div class="card"><div class="n">{{ model.counts.get('deprecated', 0) }}</div><div class="l">deprecated</div></div>
<div class="card"><div class="n">{{ model.counts.get('yanked', 0) }}</div><div class="l">yanked</div></div>
<div class="card"><div class="n">{{ model.counts.get('quarantined', 0) }}</div><div class="l">quarantined</div></div>
</div>
{% if not model.artifacts %}<div class="banner info">The registry is empty. Run <code>ananke registry learn &lt;source&gt;</code> to add capabilities.</div>{% endif %}
<h2>Browse</h2>
<ul class="plain">{% for k in model.kinds_present %}<li><a href="{{ link(k|kind_index) }}">{{ k|plural|capitalize }}</a> <span class="muted">({{ model.counts.get(k, 0) }})</span></li>{% endfor %}
<li><a href="{{ link('capabilities/index.html') }}">Capabilities &amp; matrix</a></li>
<li><a href="{{ link('frameworks/index.html') }}">Frameworks &amp; runtimes</a></li>
<li><a href="{{ link('publishers/index.html') }}">Publishers</a></li>
<li><a href="{{ link('search.html') }}">Search</a></li></ul>
<h2>Trust</h2>
<table><thead><tr><th>Status</th><th class="c">Versions</th></tr></thead><tbody>{% for k, n in model.trust_breakdown.items() %}<tr><td><span class="badge {{ k }}">{{ k }}</span></td><td class="c">{{ n }}</td></tr>{% endfor %}</tbody></table>
<h2>Licenses</h2>
<table><thead><tr><th>License</th><th class="c">Versions</th></tr></thead><tbody>{% for k, n in model.license_breakdown.items() %}<tr><td class="mono">{{ k }}</td><td class="c">{{ n }}</td></tr>{% endfor %}</tbody></table>
<h2>Capability categories</h2>
<div class="badges">{% for c, n in categories %}<span class="badge kind">{{ c }} · {{ n }}</span>{% endfor %}</div>
<h2>Latest changes</h2>
<table><thead><tr><th>Version</th><th>Registered</th></tr></thead><tbody>{% for uri, ver, at in model.latest_changes %}<tr><td><a href="{{ link(uri|uri_page(ver)) }}">{{ uri|short }}@{{ ver }}</a></td><td class="muted">{{ at }}</td></tr>{% endfor %}</tbody></table>
""",
    "kind_index": r"""{% import "macros" as m with context %}
<h1>{{ kind|plural|capitalize }}</h1>
<p><input type="search" data-filter="artifacts" placeholder="Filter {{ kind|plural }}…" aria-label="Filter"></p>
<table id="artifacts"><thead><tr><th>Name</th><th>Version</th><th>Summary</th><th>State</th><th>Capabilities</th></tr></thead><tbody>
{% for a in artifacts %}{% set v = a.version(a.default_version) %}<tr>
<td><a href="{{ link(a|art_page) }}">{{ a.title }}</a></td><td class="mono">{{ a.default_version }}{% if a.versions|length > 1 %} <span class="muted">({{ a.versions|length }} versions)</span>{% endif %}</td>
<td>{{ v.summary }}</td><td>{{ m.state(v) }}</td><td class="mono">{{ v.capabilities|join(', ') }}</td></tr>{% endfor %}
</tbody></table>
""",
    "artifact": r"""{% import "macros" as m with context %}
<h1>{{ a.title }} <span class="muted mono">{{ v.version }}</span></h1>
<p class="muted mono">{{ a.uri }}</p>
<div class="selector"><strong>Version:</strong>
{% if a.latest_approved %}<a href="{{ link(a|art_page) }}" class="{{ 'on' if a.latest_approved == v.version else '' }}">Latest approved ({{ a.latest_approved }})</a>{% endif %}
{% if a.latest_stable %}<a href="{{ link(a|art_page(a.latest_stable)) }}">Latest stable ({{ a.latest_stable }})</a>{% endif %}
<a href="{{ link(a|art_page(a.latest)) }}">Latest ({{ a.latest }})</a><a href="#history">All versions ({{ a.versions|length }})</a></div>
{{ m.detail(a, v, graph, lineage, true) }}
<h2 id="history">Version history</h2>
{{ lineage }}
<table><thead><tr><th>Version</th><th>State</th><th>Registered</th><th>Changes</th></tr></thead><tbody>
{% for x in a.versions|reverse %}<tr><td><a href="{{ link(a|art_page(x.version)) }}">{{ x.version }}</a></td><td>{{ m.state(x) }}</td><td class="muted">{{ x.created_at }}</td>
<td>{% if x.changes_from_previous %}{% set c = x.changes_from_previous %}{% if c.breaking %}<span class="breaking">BREAKING</span> {% endif %}<span class="muted">{{ c.suggested_bump }}</span>
{% for r in c.reasons[:3] %}<div class="muted">{{ r }}</div>{% endfor %}
<a href="{{ link(a|compare_page(c.from_version, c.to_version)) }}">compare {{ c.from_version }}…{{ c.to_version }}</a>{% else %}<span class="muted">first version</span>{% endif %}</td></tr>{% endfor %}
</tbody></table>
{% if a.used_by %}<h2>Used by</h2><ul class="plain">{% for t in a.used_by %}<li>{% if t in by_title %}<a href="{{ link(by_title[t]|art_page) }}">{{ t }}</a>{% else %}{{ t }}{% endif %}</li>{% endfor %}</ul>{% endif %}
{% if a.related %}<h2>Related</h2><ul class="plain">{% for u in a.related %}{% if u in by_uri %}<li><a href="{{ link(by_uri[u]|art_page) }}">{{ by_uri[u].title }}</a></li>{% endif %}{% endfor %}</ul>{% endif %}
""",
    "version": r"""{% import "macros" as m with context %}
<h1>{{ a.title }} <span class="muted mono">{{ v.version }}</span></h1>
<p class="muted mono">{{ v.uri }}@{{ v.version }} · {{ v.digest }}</p>
<div class="banner info">Historical page — this version's payload is immutable. <a href="{{ link(a|art_page) }}">Back to {{ a.title }}</a>.</div>
{{ m.detail(a, v, graph, '', false) }}
""",
    "compare": r"""{% import "macros" as m with context %}
<h1>{{ a.title }}: {{ d.from_version }} → {{ d.to_version }}</h1>
<p><a href="{{ link(a|art_page) }}">{{ a.title }}</a> · suggested bump <strong class="{{ 'breaking' if d.diff.breaking else '' }}">{{ d.diff.suggested_bump }}</strong>
· change classes: {{ d.diff.change_classes|join(', ') or 'none' }}</p>
{% if d.diff.reasons %}<ul class="plain">{% for r in d.diff.reasons %}<li>{{ r }}</li>{% endfor %}</ul>{% endif %}
<h2>Trust changes</h2>
<table><thead><tr><th></th><th>{{ d.from_version }}</th><th>{{ d.to_version }}</th></tr></thead><tbody>
<tr><td>trust</td><td>{{ va.trust }}</td><td>{{ vb.trust }}</td></tr><tr><td>channel</td><td>{{ va.channel }}</td><td>{{ vb.channel }}</td></tr>
<tr><td>lifecycle</td><td>{{ va.lifecycle }}</td><td>{{ vb.lifecycle }}</td></tr></tbody></table>
<h2>Manifest</h2>
{% if d.diff.metadata_changed %}<p>Changed: {{ d.diff.metadata_changed|join(', ') }}</p>{% else %}<p class="muted">No metadata changes.</p>{% endif %}
<h2>Capabilities</h2>
{% if d.diff.capabilities_added or d.diff.capabilities_removed %}<ul class="plain">{% for c in d.diff.capabilities_added %}<li class="add">+ {{ c }}</li>{% endfor %}{% for c in d.diff.capabilities_removed %}<li class="del">&minus; {{ c }}</li>{% endfor %}</ul>{% else %}<p class="muted">Unchanged.</p>{% endif %}
{% if d.diff.tools_added or d.diff.tools_removed %}<h3>Tools</h3><ul class="plain">{% for c in d.diff.tools_added %}<li class="add">+ {{ c }}</li>{% endfor %}{% for c in d.diff.tools_removed %}<li class="del">&minus; {{ c }}</li>{% endfor %}</ul>{% endif %}
<h2>Permissions</h2>
{% if d.diff.permissions.added or d.diff.permissions.removed %}<ul class="plain">{% for c in d.diff.permissions.added %}<li class="add">+ {{ c }}</li>{% endfor %}{% for c in d.diff.permissions.removed %}<li class="del">&minus; {{ c }}</li>{% endfor %}</ul>
{% if d.diff.permissions.expanded %}<div class="banner bad"><strong>Permissions expanded</strong> — security-significant even if the API is compatible.</div>{% endif %}{% else %}<p class="muted">Unchanged.</p>{% endif %}
<h2>Dependencies</h2>
{% if d.diff.dependencies_added or d.diff.dependencies_removed or d.diff.dependencies_changed %}<ul class="plain">
{% for c in d.diff.dependencies_added %}<li class="add">+ {{ c.id }} {{ c.new }}</li>{% endfor %}{% for c in d.diff.dependencies_removed %}<li class="del">&minus; {{ c.id }} {{ c.old }}</li>{% endfor %}
{% for c in d.diff.dependencies_changed %}<li>{{ c.id }}: {{ c.old }} → {{ c.new }}</li>{% endfor %}</ul>{% else %}<p class="muted">Unchanged.</p>{% endif %}
<h2>Schemas</h2>
{% if d.diff.input_breaking or d.diff.output_breaking %}<ul class="plain">{% for r in d.diff.input_breaking %}<li class="breaking">input: {{ r }}</li>{% endfor %}{% for r in d.diff.output_breaking %}<li class="breaking">output: {{ r }}</li>{% endfor %}</ul>
{% else %}<p class="muted">{{ 'Changed compatibly.' if d.diff.input_changed or d.diff.output_changed else 'Unchanged.' }}</p>{% endif %}
{% if d.diff.runtimes_added or d.diff.runtimes_removed %}<h2>Runtimes</h2><ul class="plain">{% for c in d.diff.runtimes_added %}<li class="add">+ {{ c }}</li>{% endfor %}{% for c in d.diff.runtimes_removed %}<li class="del">&minus; {{ c }}</li>{% endfor %}</ul>{% endif %}
{% for kind, label in [('prompt', 'Prompts'), ('docs', 'Docs'), ('schema', 'Schema files'), ('code', 'Code'), ('test', 'Tests'), ('other', 'Other files')] %}
{% set group = d.diff.files|selectattr('kind', 'equalto', kind)|list %}{% if group %}<h2>{{ label }}</h2>
{% for f in group %}<h3 class="mono">{{ f.change }}: {{ f.path }}</h3>{% if f.text_diff %}<pre class="diff"><code>{% for line in f.text_diff %}{% if line.startswith('+') and not line.startswith('+++') %}<span class="add">{{ line }}</span>
{% elif line.startswith('-') and not line.startswith('---') %}<span class="del">{{ line }}</span>
{% else %}{{ line }}
{% endif %}{% endfor %}</code></pre>{% endif %}{% endfor %}{% endif %}{% endfor %}
""",
    "capabilities": r"""{% import "macros" as m with context %}
<h1>Capabilities</h1>
<h2>Capability matrix</h2>
<p class="muted">Default version of each artifact. A check means the capability is declared or implied by a granted permission.</p>
{% if model.capability_matrix %}<div class="scroll"><table><thead><tr><th>Artifact</th>{% for c in model.capability_columns %}<th class="c">{{ c }}</th>{% endfor %}</tr></thead><tbody>
{% for row in model.capability_matrix %}<tr><td><a href="{{ link(by_uri[row.uri]|art_page) }}">{{ row.title }}</a> <span class="muted">{{ row.kind }}</span></td>
{% for cell in row.cells %}<td class="c">{% if cell %}<span class="yes" title="yes">✓</span>{% else %}<span class="no" title="no">-</span>{% endif %}</td>{% endfor %}</tr>{% endfor %}
</tbody></table></div>{% else %}<p class="muted">No artifacts.</p>{% endif %}
<h2>By capability</h2>
{% for cap, uris in model.capabilities.items() %}<h3 class="mono">{{ cap }}</h3><ul class="plain">{% for u in uris %}{% if u in by_uri %}<li><a href="{{ link(by_uri[u]|art_page) }}">{{ by_uri[u].title }}</a></li>{% endif %}{% endfor %}</ul>{% endfor %}
""",
    "frameworks": r"""{% import "macros" as m with context %}
<h1>Frameworks &amp; runtimes</h1>
{% for name, uris in model.frameworks.items() %}<h2 class="mono">{{ name }}</h2><ul class="plain">{% for u in uris %}<li class="mono">{{ u|short }}</li>{% endfor %}</ul>{% else %}<p class="muted">No framework information recorded.</p>{% endfor %}
""",
    "publishers": r"""{% import "macros" as m with context %}
<h1>Publishers</h1>
{% for name, uris in model.publishers.items() %}<h2>{{ name }}</h2><ul class="plain">{% for u in uris %}<li class="mono">{{ u|short }}</li>{% endfor %}</ul>{% else %}<p class="muted">No publishers recorded.</p>{% endfor %}
""",
    "search": r"""{% import "macros" as m with context %}
<h1>Search</h1>
<p><input id="search-box" type="search" placeholder="e.g. kind:skill capability:graph.query runtime:pydantic review" aria-label="Search the registry"></p>
<p class="muted">Filters: kind, capability, runtime, trust, channel, license, tag, lifecycle.</p>
<ul id="search-results" class="plain"></ul>
<script type="application/json" id="search-data">{{ data }}</script>
""",
}


def _plural(kind: str) -> str:
    return ArtifactKind(kind).plural


def _short(uri: str) -> str:
    return uri.replace("ananke://", "")


def _badge_class(text: str) -> str:
    head = re.split(r"[\s:]+", text.strip().lower())[0]
    return re.sub(r"[^a-z]+", "", head) or "kind"


class Site:
    """Turns a documentation model into pages (``PageSpec`` + HTML)."""

    def __init__(self, model: RegistryDocumentationModel, *, single: bool = False) -> None:
        self.model = model
        self.single = single
        self.linker = Linker(single)
        self.by_uri = {a.uri: a for a in model.artifacts}
        self.by_title = {a.title: a for a in model.artifacts}
        linker = self.linker

        @pass_context
        def link(ctx: Context, target: str) -> str:
            return linker.href(str(ctx.get("page", "index.html")), target)

        env = Environment(
            loader=DictLoader(_TEMPLATES),
            autoescape=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        env.globals["link"] = link
        env.filters.update(
            {
                "md": lambda s: Markup(render_markdown(s or "")),
                "plural": _plural,
                "short": _short,
                "badge_class": _badge_class,
                "kind_index": lambda k: f"{_plural(k)}/index.html",
                "art_page": self._art_page,
                "uri_page": self._uri_page,
                "compare_page": self._compare_page,
            }
        )
        self.env = env

    # ---- page-key helpers
    @staticmethod
    def _dir(a: DocArtifact) -> str:
        return f"{ArtifactKind(a.kind).plural}/{a.namespace}/{a.name}"

    def _art_page(self, a: DocArtifact, version: str | None = None) -> str:
        if version is None or version == a.default_version:
            return f"{self._dir(a)}/index.html"
        return f"{self._dir(a)}/{safe_ver(version)}.html"

    def _uri_page(self, uri: str, version: str) -> str:
        art = self.by_uri.get(uri)
        return self._art_page(art, version) if art else "index.html"

    def _compare_page(self, a: DocArtifact, v1: str, v2: str) -> str:
        return f"{self._dir(a)}/compare/{safe_ver(v1)}...{safe_ver(v2)}.html"

    # ---- diagrams
    def graph_svg(self, a: DocArtifact) -> Markup:
        edges: dict[str, list[str]] = {}
        labels: dict[str, str] = {}
        kinds: dict[str, str] = {}
        missing: set[str] = set()
        stack = [a.uri]
        seen: set[str] = set()
        while stack:
            uri = stack.pop()
            if uri in seen:
                continue
            seen.add(uri)
            art = self.by_uri.get(uri)
            if art is None:
                continue
            labels[uri], kinds[uri] = art.title, art.kind
            targets: list[str] = []
            for d in art.version(art.default_version).dependencies:
                if d.type != "artifact":
                    continue
                node = d.target_uri if d.target_uri in self.by_uri else f"missing:{d.label}"
                if node.startswith("missing:"):
                    missing.add(node)
                    labels[node] = d.label
                else:
                    stack.append(node)
                targets.append(node)
            edges[uri] = targets
        svg = render_graph_svg(
            [a.uri], edges, labels, kinds, title=f"Dependencies of {a.title}", missing=missing
        )
        return Markup(svg) if svg and any(edges.values()) else Markup("")

    def lineage_svg(self, a: DocArtifact) -> Markup:
        return Markup(
            render_lineage_svg(
                [(v.version, v.lifecycle) for v in a.versions], f"Versions of {a.title}"
            )
        )

    # ---- pages
    def _artifact_refs(self, a: DocArtifact) -> tuple[set[str], set[str]]:
        """Return ``(closure, link_only)``: dependency-graph nodes vs. merely-linked artifacts."""
        closure = {a.uri}
        stack = [a.uri]
        while stack:
            art = self.by_uri.get(stack.pop())
            if art is None:
                continue
            for d in art.version(art.default_version).dependencies:
                if d.target_uri and d.target_uri not in closure:
                    closure.add(d.target_uri)
                    stack.append(d.target_uri)
        links = {
            *a.related,
            *(self.by_title[t].uri for t in a.used_by if t in self.by_title),
        } - closure
        return closure, links

    def pages(self) -> Iterator[PageSpec]:
        m = self.model
        cats: dict[str, int] = {}
        for cap in m.capabilities:
            cats[cap.split(".")[0]] = cats.get(cap.split(".")[0], 0) + len(m.capabilities[cap])
        yield PageSpec(
            "index.html",
            "Home",
            "home",
            {"model": m, "categories": sorted(cats.items())},
            {a.uri for a in m.artifacts},
        )
        for kind in m.kinds_present:
            arts = [a for a in m.artifacts if a.kind == kind]
            yield PageSpec(
                f"{ArtifactKind(kind).plural}/index.html",
                ArtifactKind(kind).plural.capitalize(),
                "kind_index",
                {"kind": kind, "artifacts": arts},
                {a.uri for a in arts},
            )
        for a in m.artifacts:
            refs, link_refs = self._artifact_refs(a)
            dv = a.version(a.default_version)
            yield PageSpec(
                self._art_page(a),
                a.title,
                "artifact",
                {
                    "a": a,
                    "v": dv,
                    "graph": self.graph_svg(a),
                    "lineage": self.lineage_svg(a),
                    "by_title": self.by_title,
                    "by_uri": self.by_uri,
                },
                refs,
                link_refs,
            )
            for v in a.versions:
                if v.version == a.default_version:
                    continue
                yield PageSpec(
                    self._art_page(a, v.version),
                    f"{a.title} {v.version}",
                    "version",
                    {"a": a, "v": v, "graph": self.graph_svg(a)},
                    refs,
                    link_refs,
                )
        for cmp_ in m.comparisons:
            art = self.by_uri[cmp_.uri]
            yield PageSpec(
                self._compare_page(art, cmp_.from_version, cmp_.to_version),
                f"{art.title} {cmp_.from_version} → {cmp_.to_version}",
                "compare",
                {
                    "a": art,
                    "d": cmp_,
                    "va": art.version(cmp_.from_version),
                    "vb": art.version(cmp_.to_version),
                },
                {art.uri},
            )
        yield PageSpec(
            "capabilities/index.html",
            "Capabilities",
            "capabilities",
            {"model": m, "by_uri": self.by_uri},
            {a.uri for a in m.artifacts},
        )
        yield PageSpec("frameworks/index.html", "Frameworks", "frameworks", {"model": m}, set())
        yield PageSpec("publishers/index.html", "Publishers", "publishers", {"model": m}, set())
        yield PageSpec(
            "search.html", "Search", "search", {"data": Markup(self.search_json())}, set()
        )

    def search_json(self) -> str:
        entries = []
        for e in self.model.search_index:
            row = dict(e)
            if self.single:
                art = self.by_uri.get(f"ananke://{e['kind']}/{e['name']}")
                row["href"] = "#" + pg_id(self._art_page(art, e["version"])) if art else "#"
            entries.append(row)
        return (
            json.dumps(entries, separators=(",", ":"))
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
        )

    def render_body(self, spec: PageSpec) -> str:
        tmpl = self.env.get_template(spec.template)
        return tmpl.render({"page": spec.key, "model": self.model, **spec.ctx})

    # ---- assembly
    def _nav(self, page: str) -> str:
        def link(t: str) -> str:
            return self.linker.href(page, t)

        items = [f'<a href="{link("index.html")}">Home</a>']
        for k in self.model.kinds_present:
            items.append(
                f'<a href="{link(f"{ArtifactKind(k).plural}/index.html")}">{ArtifactKind(k).plural.capitalize()}</a>'
            )
        items.append(f'<a href="{link("capabilities/index.html")}">Capabilities</a>')
        items.append(f'<a href="{link("search.html")}">Search</a>')
        return "".join(items)

    def wrap(self, spec: PageSpec, body: str) -> str:
        """Full HTML document for multi-page output."""
        from markupsafe import escape

        def href(t: str) -> str:
            return self.linker.href(spec.key, t)

        return (
            '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta http-equiv="Content-Security-Policy" content="{CSP_MULTI}">'
            f"<title>{escape(spec.title)} · Ananke Registry</title>"
            f'<link rel="stylesheet" href="{href("assets/style.css")}"></head><body>'
            f'<header class="top"><a class="brand" href="{href("index.html")}">Ananke Registry</a>'
            f'<nav>{self._nav(spec.key)}</nav><button id="theme-toggle" type="button" aria-label="Toggle theme">◐</button></header>'
            f"<main>{body}</main>"
            f'<footer>Generated by Ananke Plexus · registry snapshot <span class="mono">{escape(self.model.snapshot[:23])}</span></footer>'
            f'<script src="{href("assets/app.js")}" defer></script></body></html>\n'
        )

    def assemble_single(self, specs: list[PageSpec] | None = None) -> str:
        """One self-contained offline HTML file: inline CSS/JS, all pages, hash router."""
        from markupsafe import escape

        css_hash = base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()
        js_hash = base64.b64encode(hashlib.sha256(JS.encode()).digest()).decode()
        csp = (
            f"default-src 'none'; style-src 'sha256-{css_hash}'; script-src 'sha256-{js_hash}'; "
            "img-src data:; base-uri 'none'; form-action 'none'"
        )
        sections = []
        for spec in specs if specs is not None else list(self.pages()):
            sections.append(
                f'<section class="page" id="{pg_id(spec.key)}" data-title="{escape(spec.title)}">'
                f"{self.render_body(spec)}</section>"
            )
        return (
            '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
            "<title>Ananke Plexus Registry</title>"
            f"<style>{CSS}</style></head><body>"
            f'<header class="top"><a class="brand" href="#{pg_id("index.html")}">Ananke Registry</a>'
            f'<nav>{self._nav("index.html")}</nav><button id="theme-toggle" type="button">◐</button></header>'
            f"<main>{''.join(sections)}</main>"
            f'<footer>Generated by Ananke Plexus · registry snapshot <span class="mono">{escape(self.model.snapshot[:23])}</span> · works offline</footer>'
            f"<script>{JS}</script></body></html>\n"
        )


# --------------------------------------------------------------------------- inspection report


def inspection_report_html(report: Any) -> str:
    """Standalone pre-registration report for ``registry inspect --report`` (spec §108)."""
    from markupsafe import escape

    def esc(x: object) -> str:
        return str(escape("" if x is None else x))

    rows: list[str] = []
    for c in report.candidates:
        diags = "".join(
            f"<li class='{esc(d.level)}'><b>{esc(d.code)}</b> {esc(d.message)}</li>"
            for d in c.diagnostics
        )
        notes = "".join(f"<li>{esc(n)}</li>" for n in c.identity_notes)
        concerns = "".join(f"<li>{esc(n)}</li>" for n in c.permission_concerns)
        diff = ""
        if c.diff is not None:
            diff = f"<p>Changes vs latest: {esc(', '.join(c.diff.change_classes) or 'none')} — suggested bump <b>{esc(c.diff.suggested_bump)}</b></p>"
        rows.append(
            f"<section><h2>{esc(c.uri or c.source)}</h2>"
            f"<p><b>Action:</b> {esc(c.action)} · <b>version:</b> {esc(c.version)} · importer {esc(c.importer)}"
            f"{' · <b>requires dynamic introspection</b>' if c.dynamic else ''}</p>"
            f"<p>{esc(c.message)}</p>{diff}"
            + (f"<p><b>Missing metadata:</b> {esc(', '.join(c.missing))}</p>" if c.missing else "")
            + (f"<h3>Permission concerns</h3><ul>{concerns}</ul>" if concerns else "")
            + (f"<h3>Identity</h3><ul>{notes}</ul>" if notes else "")
            + (f"<h3>Diagnostics</h3><ul>{diags}</ul>" if diags else "")
            + (
                f"<p><b>Suggested version:</b> {esc(c.suggested_version)}</p>"
                if c.suggested_version
                else ""
            )
            + "</section>"
        )
    css = (
        "body{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}"
        "section{border:1px solid #ddd;border-radius:8px;padding:0 1rem 1rem;margin:1rem 0}"
        "li.warning{color:#a16207}li.error{color:#b91c1c}"
    )
    css_hash = base64.b64encode(hashlib.sha256(css.encode()).digest()).decode()
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'sha256-{css_hash}'\">"
        f"<title>Registry inspection report</title><style>{css}</style></head><body>"
        f"<h1>Registry inspection report</h1><p>Source: <code>{esc(report.source)}</code>. "
        "Nothing was registered.</p>" + "".join(rows) + "</body></html>\n"
    )


__all__ = [
    "CSP_MULTI",
    "TEMPLATE_VERSION",
    "DocComparison",
    "DocVersion",
    "Linker",
    "PageSpec",
    "Site",
    "inspection_report_html",
    "pg_id",
    "safe_ver",
]

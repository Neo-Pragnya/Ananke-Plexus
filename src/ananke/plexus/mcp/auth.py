"""MCP permission and request guard helpers."""

from dataclasses import dataclass

MAX_REQUEST_BYTES = 100_000


@dataclass(frozen=True)
class PermissionMap:
    read_only_tools: set[str]
    mutation_tools: set[str]

    def can_call(self, tool_name: str, *, allow_mutations: bool = False) -> tuple[bool, str]:
        if tool_name in self.mutation_tools:
            if not allow_mutations:
                return False, "mutation_tool_blocked"
            return True, "allowed"
        if tool_name not in self.read_only_tools:
            return False, "unknown_tool"
        return True, "allowed"

    @property
    def all_tools(self) -> set[str]:
        return self.read_only_tools | self.mutation_tools


DEFAULT_PERMISSION_MAP = PermissionMap(
    read_only_tools={
        "ananke.project.status",
        "ananke.spec.get",
        "ananke.spec.validate",
        "ananke.bmad.get_contract",
        "ananke.arch.get",
        "ananke.arch.validate",
        "ananke.arch.render",
        "ananke.graph.query",
        "ananke.graph.impact",
        "ananke.graph.update",
        "ananke.policy.explain",
        "ananke.run.status",
        "ananke.evidence.get",
        "ananke.verify.run",
        "ananke.spec.create",
        "registry_search",
        "registry_get_skill",
        "registry_get_agent",
        "registry_resolve",
        "registry_compare_versions",
        "registry_list_capabilities",
    },
    mutation_tools={
        "ananke.run.execute",
        "ananke.git.create_branch",
        "ananke.git.commit",
        "ananke.lifecycle.transition_issue",
        "ananke.lifecycle.create_pr",
    },
)

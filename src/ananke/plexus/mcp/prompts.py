"""MCP prompt handlers."""

PROMPTS = {
    "architecture-aware-implementation": (
        "Implement requested changes while preserving architecture boundaries "
        "and reporting impacted symbols."
    ),
    "blast-radius-review": "Summarize blast radius based on changed files and graph impact.",
    "spec-clarification": "List ambiguities in requirement and suggest clarifying questions.",
    "bmad-contract-generation": "Generate behavior/model/architecture contract skeletons.",
    "pr-evidence-summary": "Summarize verification evidence for PR body.",
}


def list_prompts() -> list[str]:
    return sorted(PROMPTS.keys())


def get_prompt(prompt_name: str) -> str:
    return PROMPTS.get(prompt_name, "prompt not found")

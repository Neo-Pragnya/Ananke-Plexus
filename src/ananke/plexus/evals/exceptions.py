"""Ananke eval harness exceptions."""


class EvalError(Exception):
    """Base class for eval harness errors."""


class EvalSuiteNotFoundError(EvalError):
    pass


class EvalDatasetError(EvalError):
    pass


class EvalAdapterUnavailableError(EvalError):
    """Raised when a required adapter is not installed."""


class EvalPolicyViolationError(EvalError):
    """Raised when evaluation gate blocks progression."""


class EvalJudgeError(EvalError):
    """Raised when judge scoring fails."""


class EvalDataGovernanceError(EvalError):
    """Raised when data classification/egress policy prevents an export."""

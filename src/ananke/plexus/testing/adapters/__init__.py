"""Test adapters package."""

from __future__ import annotations

from ananke.plexus.testing.adapters.base import AdapterCapabilities, TestAdapter
from ananke.plexus.testing.adapters.cargo_fuzz import CargoFuzzAdapter
from ananke.plexus.testing.adapters.hypothesis import HypothesisAdapter
from ananke.plexus.testing.adapters.kani import KaniAdapter
from ananke.plexus.testing.adapters.mutmut import MutmutAdapter
from ananke.plexus.testing.adapters.nextest import NextestAdapter
from ananke.plexus.testing.adapters.pytest import PytestAdapter
from ananke.plexus.testing.adapters.schemathesis import SchemathesisAdapter

__all__ = [
    "AdapterCapabilities",
    "CargoFuzzAdapter",
    "HypothesisAdapter",
    "KaniAdapter",
    "MutmutAdapter",
    "NextestAdapter",
    "PytestAdapter",
    "SchemathesisAdapter",
    "TestAdapter",
]


def default_adapters() -> list[TestAdapter]:
    """Return the default set of adapters."""
    return [
        PytestAdapter(),
        HypothesisAdapter(),
        NextestAdapter(),
        MutmutAdapter(),
        CargoFuzzAdapter(),
        KaniAdapter(),
        SchemathesisAdapter(),
    ]

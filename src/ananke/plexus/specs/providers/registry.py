"""Spec provider registry and resolution."""

from ananke.plexus.specs.providers.base import SpecProvider
from ananke.plexus.specs.providers.native import NativeSpecProvider
from ananke.plexus.specs.providers.speckit import SpecKitProvider


def resolve_spec_provider(provider_name: str) -> SpecProvider:
    provider = provider_name.strip().lower()
    if provider == "speckit":
        return SpecKitProvider()
    return NativeSpecProvider()

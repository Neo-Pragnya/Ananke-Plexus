"""Collects every Gherkin feature under acceptance/features as pytest scenarios."""

from pytest_bdd import scenarios

scenarios("features")

"""Tests for dataset loading, validation, and versioning in the eval harness."""

from __future__ import annotations

import json

from ananke.plexus.evals.datasets.loader import load_dataset, load_datasets_from_dir
from ananke.plexus.evals.datasets.validator import check_egress_policy, validate_dataset
from ananke.plexus.evals.datasets.versioning import case_hashes, content_hash, stamp_provenance
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.dataset import (
    DataClassification,
    EvalDataset,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

YAML_SIMPLE = """\
id: test-dataset
version: 1
cases:
  - id: c1
    input: "hello world"
    expected: "world"
  - id: c2
    input: "foo"
"""

YAML_WITH_TAGS = """\
id: tagged-ds
cases:
  - id: c1
    input: test
    tags:
      - regression
      - smoke
"""

JSON_DATASET = json.dumps(
    {
        "id": "json-ds",
        "version": 2,
        "cases": [
            {"id": "jc1", "input": "hi", "expected": "hello"},
        ],
    }
)


def make_dataset(cases=None, ds_id="ds1") -> EvalDataset:
    return EvalDataset(
        id=ds_id,
        cases=cases or [EvalCase(id="c1", input="hello")],
    )


# ---------------------------------------------------------------------------
# load_dataset
# ---------------------------------------------------------------------------


class TestLoadDataset:
    def test_load_yaml_id(self, tmp_path):
        p = tmp_path / "ds.yaml"
        p.write_text(YAML_SIMPLE)
        ds = load_dataset(p)
        assert ds.id == "test-dataset"

    def test_load_yaml_version(self, tmp_path):
        p = tmp_path / "ds.yaml"
        p.write_text(YAML_SIMPLE)
        ds = load_dataset(p)
        assert ds.version == 1

    def test_load_yaml_case_count(self, tmp_path):
        p = tmp_path / "ds.yaml"
        p.write_text(YAML_SIMPLE)
        ds = load_dataset(p)
        assert len(ds.cases) == 2

    def test_load_yaml_case_ids(self, tmp_path):
        p = tmp_path / "ds.yaml"
        p.write_text(YAML_SIMPLE)
        ds = load_dataset(p)
        assert ds.cases[0].id == "c1"
        assert ds.cases[1].id == "c2"

    def test_load_yaml_case_input(self, tmp_path):
        p = tmp_path / "ds.yaml"
        p.write_text(YAML_SIMPLE)
        ds = load_dataset(p)
        assert ds.cases[0].input == "hello world"

    def test_load_yaml_case_expected(self, tmp_path):
        p = tmp_path / "ds.yaml"
        p.write_text(YAML_SIMPLE)
        ds = load_dataset(p)
        assert ds.cases[0].expected == "world"

    def test_load_yaml_case_no_expected_is_none(self, tmp_path):
        p = tmp_path / "ds.yaml"
        p.write_text(YAML_SIMPLE)
        ds = load_dataset(p)
        assert ds.cases[1].expected is None

    def test_load_yaml_with_tags(self, tmp_path):
        p = tmp_path / "tagged.yaml"
        p.write_text(YAML_WITH_TAGS)
        ds = load_dataset(p)
        assert "regression" in ds.cases[0].tags

    def test_load_json_dataset(self, tmp_path):
        p = tmp_path / "ds.json"
        p.write_text(JSON_DATASET)
        ds = load_dataset(p)
        assert ds.id == "json-ds"
        assert ds.version == 2

    def test_load_json_cases(self, tmp_path):
        p = tmp_path / "ds.json"
        p.write_text(JSON_DATASET)
        ds = load_dataset(p)
        assert len(ds.cases) == 1
        assert ds.cases[0].id == "jc1"

    def test_id_defaults_to_stem(self, tmp_path):
        p = tmp_path / "my_dataset.yaml"
        p.write_text("cases:\n  - id: c1\n    input: x\n")
        ds = load_dataset(p)
        assert ds.id == "my_dataset"


# ---------------------------------------------------------------------------
# load_datasets_from_dir
# ---------------------------------------------------------------------------


class TestLoadDatasetsFromDir:
    def test_loads_multiple_yaml_files(self, tmp_path):
        for i in range(3):
            (tmp_path / f"ds{i}.yaml").write_text(
                f"id: dataset-{i}\ncases:\n  - id: c1\n    input: x\n"
            )
        datasets = load_datasets_from_dir(tmp_path)
        assert len(datasets) == 3

    def test_returns_dict_keyed_by_id(self, tmp_path):
        (tmp_path / "ds1.yaml").write_text("id: alpha\ncases:\n  - id: c1\n    input: x\n")
        (tmp_path / "ds2.yaml").write_text("id: beta\ncases:\n  - id: c1\n    input: y\n")
        datasets = load_datasets_from_dir(tmp_path)
        assert "alpha" in datasets
        assert "beta" in datasets

    def test_loads_json_and_yaml(self, tmp_path):
        (tmp_path / "a.yaml").write_text("id: yaml-ds\ncases:\n  - id: c1\n    input: x\n")
        (tmp_path / "b.json").write_text(
            json.dumps({"id": "json-ds", "cases": [{"id": "c1", "input": "x"}]})
        )
        datasets = load_datasets_from_dir(tmp_path)
        assert "yaml-ds" in datasets
        assert "json-ds" in datasets

    def test_empty_dir_returns_empty_dict(self, tmp_path):
        datasets = load_datasets_from_dir(tmp_path)
        assert datasets == {}

    def test_invalid_file_skipped(self, tmp_path):
        (tmp_path / "bad.yaml").write_text(":::invalid yaml:::")
        (tmp_path / "good.yaml").write_text("id: good\ncases:\n  - id: c1\n    input: x\n")
        datasets = load_datasets_from_dir(tmp_path)
        assert "good" in datasets


# ---------------------------------------------------------------------------
# validate_dataset
# ---------------------------------------------------------------------------


class TestValidateDataset:
    def test_clean_dataset_no_issues(self):
        ds = make_dataset()
        issues = validate_dataset(ds)
        assert issues == []

    def test_missing_id_issue(self):
        ds = EvalDataset(id="", cases=[EvalCase(id="c1", input="x")])
        issues = validate_dataset(ds)
        assert any("id" in i for i in issues)

    def test_no_cases_issue(self):
        ds = EvalDataset(id="ds", cases=[])
        issues = validate_dataset(ds)
        assert any("no cases" in i for i in issues)

    def test_case_missing_input_issue(self):
        case = EvalCase(id="c1", input=None)
        ds = EvalDataset(id="ds", cases=[case])
        issues = validate_dataset(ds)
        assert any("no input" in i for i in issues)

    def test_multiple_cases_with_issues(self):
        cases = [
            EvalCase(id="c1", input=None),
            EvalCase(id="c2", input=None),
        ]
        ds = EvalDataset(id="ds", cases=cases)
        issues = validate_dataset(ds)
        assert len(issues) >= 2


# ---------------------------------------------------------------------------
# check_egress_policy
# ---------------------------------------------------------------------------


class TestCheckEgressPolicy:
    def test_public_dataset_allowed_for_public(self):
        ds = make_dataset()
        ds.provenance.sensitivity = DataClassification.PUBLIC
        assert check_egress_policy(ds, DataClassification.PUBLIC) is True

    def test_internal_dataset_allowed_for_confidential(self):
        ds = make_dataset()
        ds.provenance.sensitivity = DataClassification.INTERNAL
        assert check_egress_policy(ds, DataClassification.CONFIDENTIAL) is True

    def test_secret_dataset_not_allowed_for_public(self):
        ds = make_dataset()
        ds.provenance.sensitivity = DataClassification.SECRET
        assert check_egress_policy(ds, DataClassification.PUBLIC) is False

    def test_confidential_not_allowed_for_internal(self):
        ds = make_dataset()
        ds.provenance.sensitivity = DataClassification.CONFIDENTIAL
        assert check_egress_policy(ds, DataClassification.INTERNAL) is False

    def test_restricted_allowed_for_secret(self):
        ds = make_dataset()
        ds.provenance.sensitivity = DataClassification.RESTRICTED
        assert check_egress_policy(ds, DataClassification.SECRET) is True


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------


class TestContentHash:
    def test_returns_string(self):
        ds = make_dataset()
        h = content_hash(ds)
        assert isinstance(h, str)

    def test_deterministic_same_content(self):
        ds1 = make_dataset()
        ds2 = make_dataset()
        assert content_hash(ds1) == content_hash(ds2)

    def test_different_content_different_hash(self):
        ds1 = make_dataset(cases=[EvalCase(id="c1", input="hello")])
        ds2 = make_dataset(cases=[EvalCase(id="c1", input="world")])
        assert content_hash(ds1) != content_hash(ds2)

    def test_hash_is_hex(self):
        ds = make_dataset()
        h = content_hash(ds)
        int(h, 16)  # should not raise

    def test_hash_length_64(self):
        ds = make_dataset()
        h = content_hash(ds)
        assert len(h) == 64


# ---------------------------------------------------------------------------
# case_hashes
# ---------------------------------------------------------------------------


class TestCaseHashes:
    def test_returns_dict(self):
        ds = make_dataset()
        hashes = case_hashes(ds)
        assert isinstance(hashes, dict)

    def test_each_case_gets_hash(self):
        cases = [EvalCase(id="c1", input="a"), EvalCase(id="c2", input="b")]
        ds = make_dataset(cases=cases)
        hashes = case_hashes(ds)
        assert "c1" in hashes
        assert "c2" in hashes

    def test_different_cases_different_hashes(self):
        cases = [EvalCase(id="c1", input="x"), EvalCase(id="c2", input="y")]
        ds = make_dataset(cases=cases)
        hashes = case_hashes(ds)
        assert hashes["c1"] != hashes["c2"]

    def test_same_case_deterministic(self):
        cases = [EvalCase(id="c1", input="same")]
        h1 = case_hashes(make_dataset(cases=cases))
        h2 = case_hashes(make_dataset(cases=cases))
        assert h1["c1"] == h2["c1"]


# ---------------------------------------------------------------------------
# stamp_provenance
# ---------------------------------------------------------------------------


class TestStampProvenance:
    def test_returns_new_dataset(self):
        ds = make_dataset()
        stamped = stamp_provenance(ds)
        assert stamped is not ds

    def test_case_hashes_populated(self):
        ds = make_dataset(cases=[EvalCase(id="c1", input="hello")])
        stamped = stamp_provenance(ds)
        assert "c1" in stamped.provenance.case_hashes

    def test_original_not_mutated(self):
        ds = make_dataset(cases=[EvalCase(id="c1", input="hello")])
        stamp_provenance(ds)
        assert ds.provenance.case_hashes == {}

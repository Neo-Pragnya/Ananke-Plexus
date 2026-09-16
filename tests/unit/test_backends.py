"""Tests for backend API and adapters (K1-K2, K-bonus)."""

from ananke.plexus.backends.adapters.fake import FakeBackend
from ananke.plexus.backends.adapters.generic_cli import GenericCLIBackend
from ananke.plexus.backends.base import BackendRequest, BackendTask
from ananke.plexus.backends.capabilities import detect_cli_capabilities
from ananke.plexus.backends.registry import BackendRegistry


def _task(instruction: str = "ping") -> BackendTask:
    from uuid import uuid4

    return BackendTask(task_id=uuid4().hex, instruction=instruction)


def _req() -> BackendRequest:
    return BackendRequest(spec_id="test", task_description="testing")


# ---------- FakeBackend ----------


def test_fake_backend_always_available():
    assert FakeBackend().available() is True


def test_fake_backend_ping():
    b = FakeBackend()
    session = b.start(_req())
    result = b.send(session, _task("ping"))
    assert result.ok is True
    assert result.output == "pong"


def test_fake_backend_edit(tmp_path):
    b = FakeBackend()
    session = b.start(_req())
    task = _task("implement the feature")
    task.context = {"worktree_path": str(tmp_path)}
    result = b.send(session, task)
    assert result.ok is True
    marker = tmp_path / "fake_backend_edit.txt"
    assert marker.exists()


def test_fake_backend_cancel():
    b = FakeBackend()
    session = b.start(_req())
    b.cancel(session)  # should not raise


# ---------- GenericCLIBackend ----------


def test_generic_cli_unavailable():
    b = GenericCLIBackend(["__not_a_real_binary__"])
    assert b.available() is False


def test_generic_cli_unavailable_returns_error():
    b = GenericCLIBackend(["__no_such_bin__"])
    session = b.start(_req())
    result = b.send(session, _task())
    assert result.ok is False
    assert "not found" in result.error.lower()


def test_generic_cli_available_true_for_echo():
    import shutil

    if shutil.which("echo"):
        b = GenericCLIBackend(["echo"])
        assert b.available() is True


# ---------- Capabilities ----------


def test_detect_cli_capabilities_unavailable():
    caps = detect_cli_capabilities("__no_such_bin__")
    assert caps.filesystem_read is False


def test_detect_cli_capabilities_available():
    import shutil

    if shutil.which("python"):
        caps = detect_cli_capabilities("python")
        assert caps.filesystem_read is True


# ---------- Registry ----------


def test_registry_list(tmp_path):
    reg = BackendRegistry.load(tmp_path)
    entries = reg.to_list()
    assert any(e["name"] == "fake" for e in entries)


def test_registry_doctor_known(tmp_path):
    reg = BackendRegistry.load(tmp_path)
    result = reg.doctor("fake")
    assert result["name"] == "fake"
    assert result["ok"] is True


def test_registry_doctor_unknown(tmp_path):
    reg = BackendRegistry.load(tmp_path)
    result = reg.doctor("__no_such_backend__")
    assert result["ok"] is False


def test_registry_test_fake(tmp_path):
    reg = BackendRegistry.load(tmp_path)
    result = reg.test("fake")
    assert result["ok"] is True

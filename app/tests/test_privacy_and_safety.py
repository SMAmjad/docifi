"""Privacy (masking, logs) and safety (secure delete, folder rules). Uses pytest's temporary folders."""

import json

import pytest

from pcs_tool.audit import AuditLog
from pcs_tool.engine.pipeline import OUTPUT_MARKER, PipelineError, validate_folders
from pcs_tool.privacy import mask_ssn, redact_address, redact_free_text, redact_name
from pcs_tool.secure_delete import SecureDeleteRefused, secure_delete


def test_masking():
    assert mask_ssn("123-45-6789") == "***-**-6789"
    assert mask_ssn(None) == ""
    assert redact_name("Jane Q Doe") == "J. Q. D."
    assert redact_address("45 OAK DR, Houston, TX 77001") == "[redacted], TX 7****"
    assert "123-45" not in redact_free_text("ssn 123-45-6789 here")


def test_audit_log_withholds_personal_fields(tmp_path):
    path = tmp_path / "audit.jsonl"
    with AuditLog(path) as log:
        log.event("x", "y", doc="D001", name="Jane Doe", ssn="123-45-6789", note="ssn 123-45-6789")
    rec = json.loads(path.read_text())
    assert rec["name"] == "[withheld]" and rec["ssn"] == "[withheld]"
    assert "123-45-6789" not in path.read_text()


class TestFolderRules:
    def test_output_inside_input_refused(self, tmp_path):
        (tmp_path / "in").mkdir()
        with pytest.raises(PipelineError):
            validate_folders(tmp_path / "in", tmp_path / "in" / "out")

    def test_same_folder_refused(self, tmp_path):
        with pytest.raises(PipelineError):
            validate_folders(tmp_path, tmp_path)

    def test_non_empty_foreign_output_refused(self, tmp_path):
        (tmp_path / "in").mkdir()
        out = tmp_path / "out"
        out.mkdir()
        (out / "someone_elses_file.txt").write_text("x")
        with pytest.raises(PipelineError):
            validate_folders(tmp_path / "in", out)


class TestSecureDelete:
    def _make(self, tmp_path, marker=True):
        out = tmp_path / "results"
        (out / "run" / "documents").mkdir(parents=True)
        (out / "run" / "documents" / "a.pdf").write_bytes(b"secret" * 1000)
        if marker:
            (out / OUTPUT_MARKER).write_text("x")
        return out

    def test_deletes_marked_folder_after_typed_confirmation(self, tmp_path):
        out = self._make(tmp_path)
        rep = secure_delete(out, "results")
        assert not out.exists() and rep.files == 2 and rep.failures == 0

    def test_refuses_folder_without_marker(self, tmp_path):
        out = self._make(tmp_path, marker=False)
        with pytest.raises(SecureDeleteRefused):
            secure_delete(out, "results")
        assert out.exists()

    def test_refuses_wrong_confirmation(self, tmp_path):
        out = self._make(tmp_path)
        with pytest.raises(SecureDeleteRefused):
            secure_delete(out, "Results ")
        assert out.exists()

    def test_refuses_home_folder(self, tmp_path):
        from pathlib import Path
        with pytest.raises(SecureDeleteRefused):
            secure_delete(Path.home(), Path.home().name)

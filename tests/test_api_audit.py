"""Tests for HTTP API audit logging."""

import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from engine.gateway.api import _audit_log
from engine.gateway.server import create_app
from engine.gateway.config import GatewayConfig


@pytest.fixture
def audit_log_path(tmp_path, monkeypatch):
    """Redirect audit log to a temp file."""
    path = str(tmp_path / "api_audit.jsonl")
    monkeypatch.setattr("engine.gateway.api._AUDIT_LOG_PATH", path)
    return path


@pytest.fixture
def client(audit_log_path):
    """FastAPI test client with a known API token."""
    config = GatewayConfig(port=18899, api_token="test-token-123")
    app = create_app(config)
    return TestClient(app)


def _read_audit(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


# --- Unit tests for _audit_log ---

class TestAuditLogFunction:
    def test_success_entry(self, audit_log_path):
        _audit_log("log_meal", "default",
                    {"description": "Subway", "protein_g": 78},
                    {"logged": True, "date": "2026-03-21"}, None, 45)
        entries = _read_audit(audit_log_path)
        assert len(entries) == 1
        e = entries[0]
        assert e["tool"] == "log_meal"
        assert e["user_id"] == "default"
        assert e["status"] == "ok"
        assert e["ms"] == 45
        assert e["result_keys"] == ["logged", "date"]
        assert e["params"]["description"] == "Subway"
        assert "ts" in e

    def test_error_entry(self, audit_log_path):
        _audit_log("pull_garmin", "paul", {"history": True},
                    None, "Token expired", 120)
        entries = _read_audit(audit_log_path)
        e = entries[0]
        assert e["status"] == "error"
        assert e["error"] == "Token expired"
        assert "result_keys" not in e

    def test_token_stripped_from_params(self, audit_log_path):
        _audit_log("checkin", "default",
                    {"greeting": "hi", "token": "SECRET"},
                    {"status": "ok"}, None, 10)
        entries = _read_audit(audit_log_path)
        assert "token" not in entries[0]["params"]

    def test_non_dict_result_has_no_result_keys(self, audit_log_path):
        _audit_log("checkin", "default", {}, "string result", None, 50)
        entries = _read_audit(audit_log_path)
        assert "result_keys" not in entries[0]

    def test_multiple_entries_append(self, audit_log_path):
        for i in range(3):
            _audit_log(f"tool_{i}", "default", {}, {}, None, i)
        entries = _read_audit(audit_log_path)
        assert len(entries) == 3


# --- Integration tests via TestClient ---

class TestAuditLogIntegration:
    def test_successful_tool_call_audited(self, client, audit_log_path):
        resp = client.get("/api/get_status?token=test-token-123")
        assert resp.status_code == 200
        entries = _read_audit(audit_log_path)
        assert len(entries) == 1
        e = entries[0]
        assert e["tool"] == "get_status"
        assert e["status"] == "ok"
        assert e["ms"] >= 0
        assert "result_keys" in e

    def test_unknown_tool_not_audited(self, client, audit_log_path):
        resp = client.get("/api/nonexistent_tool?token=test-token-123")
        assert resp.status_code == 404
        entries = _read_audit(audit_log_path)
        assert len(entries) == 0  # 404 happens before dispatch

    def test_bad_token_not_audited(self, client, audit_log_path):
        resp = client.get("/api/get_status?token=wrong")
        assert resp.status_code == 403
        entries = _read_audit(audit_log_path)
        assert len(entries) == 0

    def test_user_id_captured(self, client, audit_log_path):
        resp = client.get("/api/get_status?token=test-token-123&user_id=paul")
        assert resp.status_code == 200
        entries = _read_audit(audit_log_path)
        assert entries[0]["user_id"] == "paul"

    def test_default_user_id(self, client, audit_log_path):
        resp = client.get("/api/get_status?token=test-token-123")
        assert resp.status_code == 200
        entries = _read_audit(audit_log_path)
        assert entries[0]["user_id"] == "default"

    def test_iso_timestamp_format(self, client, audit_log_path):
        client.get("/api/get_status?token=test-token-123")
        entries = _read_audit(audit_log_path)
        ts = entries[0]["ts"]
        # Should have timezone offset like -07:00 or +00:00
        assert "T" in ts
        assert ("+" in ts or ts.endswith("Z") or "-" in ts.split("T")[1])


class TestAsyncApi:
    """Tests for the _async background job API."""

    def test_async_returns_job_id(self, client, audit_log_path):
        """Calling tool_async returns immediately with a job_id."""
        resp = client.get("/api/get_status_async?token=test-token-123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert "job_id" in data
        assert data["job_id"].startswith("get_status_")

    def test_async_job_completes(self, client, audit_log_path):
        """Background job eventually completes and result is retrievable."""
        import time
        resp = client.get("/api/get_status_async?token=test-token-123")
        job_id = resp.json()["job_id"]

        # Poll for completion (get_status is fast, should finish quickly)
        for _ in range(20):
            status_resp = client.get(f"/api/job_status?token=test-token-123&job_id={job_id}")
            assert status_resp.status_code == 200
            if status_resp.json()["status"] == "completed":
                break
            time.sleep(0.1)
        else:
            pytest.fail("Job did not complete within 2 seconds")

        result = status_resp.json()
        assert result["status"] == "completed"
        assert "result" in result
        assert "elapsed_ms" in result

    def test_async_unknown_tool_404(self, client):
        resp = client.get("/api/nonexistent_tool_async?token=test-token-123")
        assert resp.status_code == 404

    def test_job_status_unknown_id_404(self, client):
        resp = client.get("/api/job_status?token=test-token-123&job_id=fake_123")
        assert resp.status_code == 404

    def test_async_audited(self, client, audit_log_path):
        """Async jobs still produce audit log entries."""
        import time
        resp = client.get("/api/get_status_async?token=test-token-123")
        job_id = resp.json()["job_id"]

        for _ in range(20):
            status = client.get(f"/api/job_status?token=test-token-123&job_id={job_id}")
            if status.json()["status"] == "completed":
                break
            time.sleep(0.1)

        entries = _read_audit(audit_log_path)
        assert len(entries) >= 1
        assert entries[0]["tool"] == "get_status"

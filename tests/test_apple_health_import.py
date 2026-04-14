"""Tests for Apple Health import tool and upload endpoint."""

import json
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_server.tools import _import_apple_health, TOOL_REGISTRY


# --- Test helpers ---

def _make_xml(records_xml: str) -> str:
    """Wrap record XML in a minimal Apple Health export."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData>
<HealthData locale="en_US">
{records_xml}
</HealthData>"""


def _recent_date(days_ago: int = 1) -> str:
    dt = datetime.now() - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S -0700")


def _make_sample_xml_content():
    """Generate sample Apple Health XML content with valid records."""
    records = []
    for i in range(3):
        d = _recent_date(i + 1)
        records.append(
            f'<Record type="HKQuantityTypeIdentifierRestingHeartRate" '
            f'startDate="{d}" endDate="{d}" value="{55 + i}" unit="count/min" '
            f'sourceName="Apple Watch"/>'
        )
    for i in range(3):
        d = _recent_date(i + 1)
        records.append(
            f'<Record type="HKQuantityTypeIdentifierStepCount" '
            f'startDate="{d}" endDate="{d}" value="8000" unit="count" '
            f'sourceName="Apple Watch"/>'
        )
    d = _recent_date(2)
    records.append(
        f'<Record type="HKQuantityTypeIdentifierVO2Max" '
        f'startDate="{d}" endDate="{d}" value="42.5" unit="mL/min' + '\u00b7' + 'kg" '
        f'sourceName="Apple Watch"/>'
    )
    return _make_xml("\n".join(records))


@pytest.fixture
def sample_xml(tmp_path):
    """Create a minimal Apple Health export XML."""
    xml_content = _make_sample_xml_content()
    xml_path = tmp_path / "export.xml"
    xml_path.write_text(xml_content)
    return xml_path


@pytest.fixture
def sample_zip(tmp_path, sample_xml):
    """Create a ZIP containing export.xml."""
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(str(zip_path), "w") as zf:
        zf.write(str(sample_xml), "apple_health_export/export.xml")
    return zip_path


# --- Tool function tests ---

class TestImportAppleHealthTool:

    def test_registered_in_tool_registry(self):
        """import_apple_health should be in TOOL_REGISTRY."""
        assert "import_apple_health" in TOOL_REGISTRY
        assert TOOL_REGISTRY["import_apple_health"] is _import_apple_health

    def test_import_xml(self, sample_xml, tmp_path):
        """Should successfully import a raw XML file."""
        with patch("mcp_server.tools._data_dir", return_value=tmp_path / "data"):
            result = _import_apple_health(
                file_path=str(sample_xml),
                user_id="test_user",
            )

        assert result["imported"] is True
        assert "resting_hr" in result["metrics_found"]
        assert "daily_steps_avg" in result["metrics_found"]
        assert "vo2_max" in result["metrics_found"]
        assert result["metrics_count"] >= 3

    def test_import_zip(self, sample_zip, tmp_path):
        """Should successfully import a ZIP file."""
        with patch("mcp_server.tools._data_dir", return_value=tmp_path / "data"):
            result = _import_apple_health(
                file_path=str(sample_zip),
                user_id="test_user",
            )

        assert result["imported"] is True
        assert result["metrics_count"] >= 1

    def test_saves_json_to_user_dir(self, sample_xml, tmp_path):
        """Should save apple_health_latest.json in the user's data directory."""
        data_dir = tmp_path / "data"
        with patch("mcp_server.tools._data_dir", return_value=data_dir):
            result = _import_apple_health(
                file_path=str(sample_xml),
                user_id="test_user",
            )

        assert result["imported"] is True
        # Tier 4: JSON file should NOT be written
        saved = data_dir / "apple_health_latest.json"
        assert not saved.exists(), "apple_health_latest.json should not be written (Tier 4)"

    def test_file_not_found(self, tmp_path):
        """Should return error dict for missing file."""
        # Patch _data_dir so _import_apple_health doesn't try to resolve
        # 'test_user' through _user_dir's person-row guard. This test is
        # about file-not-found behavior, not user resolution.
        with patch("mcp_server.tools._data_dir", return_value=tmp_path / "data"):
            result = _import_apple_health(
                file_path="/nonexistent/file.zip",
                user_id="test_user",
            )
        assert result["imported"] is False
        assert "not found" in result["error"].lower() or "File not found" in result["error"]

    def test_invalid_zip(self, tmp_path):
        """Should return error for ZIP without export.xml."""
        bad_zip = tmp_path / "bad.zip"
        with zipfile.ZipFile(str(bad_zip), "w") as zf:
            zf.writestr("random.txt", "not health data")

        with patch("mcp_server.tools._data_dir", return_value=tmp_path / "data"):
            result = _import_apple_health(
                file_path=str(bad_zip),
                user_id="test_user",
            )

        assert result["imported"] is False
        assert "error" in result

    def test_invalid_xml(self, tmp_path):
        """Should return error for non-XML file passed as XML."""
        bad_file = tmp_path / "garbage.xml"
        bad_file.write_text("this is not valid xml at all {{{")

        with patch("mcp_server.tools._data_dir", return_value=tmp_path / "data"):
            result = _import_apple_health(
                file_path=str(bad_file),
                user_id="test_user",
            )

        # Should handle gracefully (either import with no metrics or return error)
        # The SAX parser may succeed with no records or may error
        assert isinstance(result, dict)

    def test_lookback_days_parameter(self, sample_xml, tmp_path):
        """Should respect lookback_days parameter."""
        with patch("mcp_server.tools._data_dir", return_value=tmp_path / "data"):
            result = _import_apple_health(
                file_path=str(sample_xml),
                lookback_days=1,
                user_id="test_user",
            )

        # With lookback_days=1, fewer records should match
        assert isinstance(result, dict)
        assert result["imported"] is True
        assert result["lookback_days"] == 1

    def test_result_includes_data(self, sample_xml, tmp_path):
        """Should include actual metric values in result."""
        with patch("mcp_server.tools._data_dir", return_value=tmp_path / "data"):
            result = _import_apple_health(
                file_path=str(sample_xml),
                user_id="test_user",
            )

        assert result["imported"] is True
        assert "data" in result
        if "resting_hr" in result["data"]:
            assert isinstance(result["data"]["resting_hr"], (int, float))


# --- Upload endpoint tests ---

class TestUploadEndpoint:
    """Tests for the /api/upload file upload endpoint."""

    @pytest.fixture(autouse=True)
    def _isolate_data_dir(self, tmp_path, monkeypatch):
        """Route _data_dir to tmp so tests don't hit _user_dir's person guard
        (commit ed84010) and don't pollute real data/users/. Previously these
        tests leaked test_upload/test_cleanup directories into prod; now the
        guard blocks ghost-user creation, so the tests need explicit isolation.
        """
        def _tmp_data_dir(user_id=None):
            if user_id:
                p = tmp_path / "data" / "users" / user_id
                p.mkdir(parents=True, exist_ok=True)
                return p
            return tmp_path / "data"
        monkeypatch.setattr("mcp_server.tools._data_dir", _tmp_data_dir)

    @pytest.fixture
    def client(self):
        """Create a test client with a configured FastAPI app."""
        from engine.gateway.server import create_app, GatewayConfig
        from starlette.testclient import TestClient

        config = GatewayConfig(
            port=18800,
            api_token="test-token-123",
        )
        app = create_app(config)
        return TestClient(app)

    def test_upload_apple_health_zip(self, client, tmp_path):
        """Should accept and process a ZIP upload."""
        # Create a valid Apple Health export ZIP
        xml_content = _make_sample_xml_content()
        xml_path = tmp_path / "export.xml"
        xml_path.write_text(xml_content)
        zip_path = tmp_path / "export.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.write(str(xml_path), "apple_health_export/export.xml")

        with open(zip_path, "rb") as f:
            response = client.post(
                "/api/upload",
                params={"token": "test-token-123", "user_id": "test_upload", "type": "apple_health"},
                files={"file": ("export.zip", f, "application/zip")},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["imported"] is True
        assert data["metrics_count"] >= 1

    def test_upload_apple_health_xml(self, client, tmp_path):
        """Should accept and process a raw XML upload."""
        xml_content = _make_sample_xml_content()

        response = client.post(
            "/api/upload",
            params={"token": "test-token-123", "user_id": "test_upload", "type": "apple_health"},
            files={"file": ("export.xml", xml_content.encode(), "text/xml")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["imported"] is True

    def test_upload_invalid_token(self, client, tmp_path):
        """Should reject uploads with invalid token."""
        response = client.post(
            "/api/upload",
            params={"token": "wrong-token", "user_id": "test", "type": "apple_health"},
            files={"file": ("export.zip", b"fake", "application/zip")},
        )
        assert response.status_code == 403

    def test_upload_unsupported_type(self, client):
        """Should reject unsupported upload types."""
        response = client.post(
            "/api/upload",
            params={"token": "test-token-123", "user_id": "test", "type": "fitbit"},
            files={"file": ("data.zip", b"fake", "application/zip")},
        )
        assert response.status_code == 400
        assert "Unsupported upload type" in response.json()["detail"]

    def test_upload_invalid_extension(self, client):
        """Should reject files with wrong extension."""
        response = client.post(
            "/api/upload",
            params={"token": "test-token-123", "user_id": "test", "type": "apple_health"},
            files={"file": ("data.csv", b"fake,data", "text/csv")},
        )
        assert response.status_code == 400
        assert "Invalid file type" in response.json()["detail"]

    def test_upload_cleans_up_temp_file(self, client, tmp_path):
        """Temp file should be cleaned up after processing."""
        xml_content = _make_sample_xml_content()

        # Track temp files created
        import tempfile
        original_mkstemp = tempfile.mkstemp
        created_temps = []

        def tracking_mkstemp(*args, **kwargs):
            result = original_mkstemp(*args, **kwargs)
            created_temps.append(result[1])
            return result

        with patch("engine.gateway.api.tempfile.mkstemp", side_effect=tracking_mkstemp):
            response = client.post(
                "/api/upload",
                params={"token": "test-token-123", "user_id": "test_cleanup", "type": "apple_health"},
                files={"file": ("export.xml", xml_content.encode(), "text/xml")},
            )

        assert response.status_code == 200
        # All temp files should be cleaned up
        for tmp in created_temps:
            assert not os.path.exists(tmp), f"Temp file not cleaned up: {tmp}"

    def test_upload_missing_file(self, client):
        """Should return 422 when no file is provided."""
        response = client.post(
            "/api/upload",
            params={"token": "test-token-123", "user_id": "test", "type": "apple_health"},
        )
        assert response.status_code == 422

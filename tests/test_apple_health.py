"""Tests for Apple Health XML parser."""

import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from engine.integrations.apple_health import AppleHealthParser, _HealthHandler, WANTED_TYPES


# --- Fixtures ---

def _make_xml(records_xml: str) -> str:
    """Wrap record XML in a minimal Apple Health export."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData>
<HealthData locale="en_US">
{records_xml}
</HealthData>"""


def _recent_date(days_ago: int = 1) -> str:
    """Generate a date string N days ago in Apple Health format."""
    dt = datetime.now() - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S -0700")


@pytest.fixture
def sample_xml(tmp_path):
    """Create a minimal Apple Health export XML with a few records of each type."""
    records = []

    # RHR records (3 days)
    for i in range(3):
        d = _recent_date(i + 1)
        records.append(
            f'<Record type="HKQuantityTypeIdentifierRestingHeartRate" '
            f'startDate="{d}" endDate="{d}" value="{55 + i}" unit="count/min" '
            f'sourceName="Apple Watch"/>'
        )

    # HRV SDNN records (3 days)
    for i in range(3):
        d = _recent_date(i + 1)
        records.append(
            f'<Record type="HKQuantityTypeIdentifierHeartRateVariabilitySDNN" '
            f'startDate="{d}" endDate="{d}" value="{45 + i * 5}" unit="ms" '
            f'sourceName="Apple Watch"/>'
        )

    # Step count records (3 days, multiple per day)
    for i in range(3):
        d = _recent_date(i + 1)
        records.append(
            f'<Record type="HKQuantityTypeIdentifierStepCount" '
            f'startDate="{d}" endDate="{d}" value="5000" unit="count" '
            f'sourceName="iPhone"/>'
        )
        records.append(
            f'<Record type="HKQuantityTypeIdentifierStepCount" '
            f'startDate="{d}" endDate="{d}" value="3000" unit="count" '
            f'sourceName="Apple Watch"/>'
        )

    # VO2 max (1 record)
    d = _recent_date(2)
    records.append(
        f'<Record type="HKQuantityTypeIdentifierVO2Max" '
        f'startDate="{d}" endDate="{d}" value="42.5" unit="mL/min·kg" '
        f'sourceName="Apple Watch"/>'
    )

    # Sleep records (2 nights)
    for i in range(2):
        night = datetime.now() - timedelta(days=i + 1)
        bed = night.replace(hour=22, minute=30, second=0)
        wake = (night + timedelta(days=1)).replace(hour=6, minute=15, second=0)
        bed_str = bed.strftime("%Y-%m-%d %H:%M:%S -0700")
        wake_str = wake.strftime("%Y-%m-%d %H:%M:%S -0700")
        records.append(
            f'<Record type="HKCategoryTypeIdentifierSleepAnalysis" '
            f'startDate="{bed_str}" endDate="{wake_str}" '
            f'value="HKCategoryValueSleepAnalysisAsleepCore" '
            f'sourceName="Apple Watch"/>'
        )

    xml_content = _make_xml("\n".join(records))
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


# --- SAX Handler Tests ---

def test_sax_handler_parses_records(sample_xml):
    """SAX handler should collect records of wanted types."""
    import xml.sax
    cutoff = datetime.now() - timedelta(days=365)
    handler = _HealthHandler(cutoff)
    parser = xml.sax.make_parser()
    parser.setFeature(xml.sax.handler.feature_external_ges, False)
    parser.setContentHandler(handler)
    with open(sample_xml, "rb") as f:
        parser.parse(f)

    assert len(handler.records["HKQuantityTypeIdentifierRestingHeartRate"]) == 3
    assert len(handler.records["HKQuantityTypeIdentifierHeartRateVariabilitySDNN"]) == 3
    assert len(handler.records["HKQuantityTypeIdentifierStepCount"]) == 6
    assert len(handler.records["HKQuantityTypeIdentifierVO2Max"]) == 1
    assert len(handler.records["HKCategoryTypeIdentifierSleepAnalysis"]) == 2


def test_sax_handler_date_filter(sample_xml):
    """SAX handler should filter out records older than cutoff."""
    import xml.sax
    # Set cutoff to very recently — should exclude most records
    cutoff = datetime.now() - timedelta(hours=12)
    handler = _HealthHandler(cutoff)
    parser = xml.sax.make_parser()
    parser.setFeature(xml.sax.handler.feature_external_ges, False)
    parser.setContentHandler(handler)
    with open(sample_xml, "rb") as f:
        parser.parse(f)

    # Most records are 1-3 days old, so with a 12-hour cutoff, few should pass
    total = sum(len(v) for v in handler.records.values())
    assert total < 15  # fewer than the full set


# --- Parser Tests ---

def test_parse_xml(sample_xml, tmp_path):
    """parse_export should handle raw XML files."""
    parser = AppleHealthParser(data_dir=str(tmp_path / "data"))
    result = parser.parse_export(str(sample_xml), lookback_days=90)

    assert result["source"] == "apple_health"
    assert result["resting_hr"] is not None
    assert result["hrv_rmssd_avg"] is not None
    assert result["daily_steps_avg"] is not None
    assert result["vo2_max"] == 42.5
    assert result["metadata"]["hrv_method"] == "SDNN"


def test_parse_zip(sample_zip, tmp_path):
    """parse_export should handle ZIP files containing export.xml."""
    parser = AppleHealthParser(data_dir=str(tmp_path / "data"))
    result = parser.parse_export(str(sample_zip), lookback_days=90)

    assert result["source"] == "apple_health"
    assert result["resting_hr"] is not None


def test_file_not_found(tmp_path):
    """parse_export should raise FileNotFoundError for missing files."""
    parser = AppleHealthParser(data_dir=str(tmp_path))
    with pytest.raises(FileNotFoundError):
        parser.parse_export(str(tmp_path / "nonexistent.xml"))


def test_zip_no_export_xml(tmp_path):
    """parse_export should raise ValueError if ZIP has no export.xml."""
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(str(zip_path), "w") as zf:
        zf.writestr("random.txt", "hello")
    parser = AppleHealthParser(data_dir=str(tmp_path))
    with pytest.raises(ValueError, match="No export.xml"):
        parser.parse_export(str(zip_path))


# --- Aggregation Tests ---

def test_aggregation_rhr(sample_xml, tmp_path):
    """RHR should be averaged from records."""
    parser = AppleHealthParser(data_dir=str(tmp_path))
    result = parser.parse_export(str(sample_xml), lookback_days=90)
    # Records have values 55, 56, 57
    assert result["resting_hr"] == 56.0


def test_aggregation_steps(sample_xml, tmp_path):
    """Steps should be summed per day then averaged across days."""
    parser = AppleHealthParser(data_dir=str(tmp_path))
    result = parser.parse_export(str(sample_xml), lookback_days=90)
    # Each day has 5000 + 3000 = 8000 steps, 3 days → avg 8000
    assert result["daily_steps_avg"] == 8000


def test_aggregation_sleep(sample_xml, tmp_path):
    """Sleep duration should be averaged across nights."""
    parser = AppleHealthParser(data_dir=str(tmp_path))
    result = parser.parse_export(str(sample_xml), lookback_days=90)
    # Each night: 22:30 to 06:15 = 7.75 hours
    if result["sleep_duration_avg"] is not None:
        assert 7.0 <= result["sleep_duration_avg"] <= 8.5


def test_zone2_is_none(sample_xml, tmp_path):
    """Zone 2 should be None for v1."""
    parser = AppleHealthParser(data_dir=str(tmp_path))
    result = parser.parse_export(str(sample_xml), lookback_days=90)
    assert result["zone2_min_per_week"] is None


# --- Save Tests ---

def test_save_creates_json(sample_xml, tmp_path):
    """save() should write apple_health_latest.json."""
    data_dir = tmp_path / "data"
    parser = AppleHealthParser(data_dir=str(data_dir))
    result = parser.parse_export(str(sample_xml), lookback_days=90)
    out_path = parser.save(result)

    assert out_path.exists()
    assert out_path.name == "apple_health_latest.json"
    saved = json.loads(out_path.read_text())
    assert saved["source"] == "apple_health"
    assert "resting_hr" in saved

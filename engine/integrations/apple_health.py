"""Apple Health export parser — SAX streaming for XML/ZIP exports.

Parses Apple Health export files (XML inside ZIP, or raw XML) using SAX
streaming to handle large files without loading into memory. Outputs the
same JSON schema as garmin_latest.json for scoring compatibility.

Usage:
    python3 cli.py import apple-health /path/to/export.zip
"""

import json
import os
import statistics
import xml.sax
import xml.sax.handler
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# Record types we care about
WANTED_TYPES = {
    "HKQuantityTypeIdentifierRestingHeartRate",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
    "HKQuantityTypeIdentifierStepCount",
    "HKQuantityTypeIdentifierVO2Max",
    "HKCategoryTypeIdentifierSleepAnalysis",
}


class _HealthHandler(xml.sax.handler.ContentHandler):
    """SAX handler that collects Apple Health records with date filtering."""

    def __init__(self, cutoff_date: datetime):
        super().__init__()
        self.cutoff = cutoff_date
        self.records: dict[str, list] = defaultdict(list)

    def startElement(self, name, attrs):
        if name != "Record":
            return

        record_type = attrs.get("type", "")
        if record_type not in WANTED_TYPES:
            return

        # Parse date — Apple Health uses "YYYY-MM-DD HH:MM:SS -HHMM" format
        start_date_str = attrs.get("startDate", "")
        if not start_date_str:
            return

        try:
            # Handle timezone offset format
            dt = datetime.strptime(start_date_str[:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, IndexError):
            return

        if dt < self.cutoff:
            return

        record = {
            "type": record_type,
            "startDate": start_date_str,
            "endDate": attrs.get("endDate", ""),
            "value": attrs.get("value", ""),
            "unit": attrs.get("unit", ""),
            "sourceName": attrs.get("sourceName", ""),
        }
        self.records[record_type].append(record)


class AppleHealthParser:
    """Parse Apple Health exports and produce scoring-compatible JSON."""

    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)

    def parse_export(self, path: str, lookback_days: int = 90) -> dict:
        """Parse an Apple Health export (ZIP or XML).

        Args:
            path: Path to export.zip or export.xml
            lookback_days: Only include records from this many days back

        Returns:
            Dict matching garmin_latest.json schema + source metadata
        """
        cutoff = datetime.now() - timedelta(days=lookback_days)
        handler = _HealthHandler(cutoff)

        export_path = Path(path)
        if not export_path.exists():
            raise FileNotFoundError(f"Export not found: {path}")

        if zipfile.is_zipfile(str(export_path)):
            self._parse_zip(export_path, handler)
        else:
            self._parse_xml(export_path, handler)

        return self._aggregate(handler)

    def _parse_zip(self, zip_path: Path, handler: _HealthHandler):
        """Extract and parse export.xml from a ZIP file."""
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            # Apple Health exports contain apple_health_export/export.xml
            xml_name = None
            for name in zf.namelist():
                if name.endswith("export.xml"):
                    xml_name = name
                    break
            if xml_name is None:
                raise ValueError("No export.xml found in ZIP file")

            with zf.open(xml_name) as xml_file:
                parser = xml.sax.make_parser()
                parser.setFeature(xml.sax.handler.feature_external_ges, False)
                parser.setContentHandler(handler)
                parser.parse(xml_file)

    def _parse_xml(self, xml_path: Path, handler: _HealthHandler):
        """Parse a raw XML export file."""
        parser = xml.sax.make_parser()
        parser.setFeature(xml.sax.handler.feature_external_ges, False)
        parser.setContentHandler(handler)
        with open(xml_path, "rb") as f:
            parser.parse(f)

    def _aggregate(self, handler: _HealthHandler) -> dict:
        """Aggregate parsed records into scoring-compatible metrics."""
        records = handler.records

        result = {
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "source": "apple_health",
            "resting_hr": self._avg_rhr(records),
            "hrv_rmssd_avg": self._avg_hrv(records),
            "daily_steps_avg": self._avg_daily_steps(records),
            "vo2_max": self._latest_vo2(records),
            "sleep_duration_avg": None,
            "sleep_regularity_stddev": None,
            "zone2_min_per_week": None,  # v1: skip zone2 estimation
            "metadata": {
                "hrv_method": "SDNN",
            },
        }

        sleep = self._compute_sleep(records)
        if sleep:
            result["sleep_duration_avg"] = sleep["duration_avg"]
            result["sleep_regularity_stddev"] = sleep["bedtime_stddev"]

        return result

    def _avg_rhr(self, records: dict) -> Optional[float]:
        """Average resting heart rate over available records (up to 30 days)."""
        rhr_records = records.get("HKQuantityTypeIdentifierRestingHeartRate", [])
        if not rhr_records:
            return None
        values = []
        for r in rhr_records:
            try:
                values.append(float(r["value"]))
            except (ValueError, TypeError):
                continue
        if not values:
            return None
        # Take last 30 days worth
        return round(statistics.mean(values[-30:]), 1)

    def _avg_hrv(self, records: dict) -> Optional[float]:
        """Average HRV SDNN over last 7 days of records."""
        hrv_records = records.get("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", [])
        if not hrv_records:
            return None
        values = []
        for r in hrv_records:
            try:
                values.append(float(r["value"]))
            except (ValueError, TypeError):
                continue
        if not values:
            return None
        # Take last 7 days worth (records are already filtered by lookback)
        return round(statistics.mean(values[-7:]), 1)

    def _avg_daily_steps(self, records: dict) -> Optional[int]:
        """Average daily steps: sum per day, then mean across days."""
        step_records = records.get("HKQuantityTypeIdentifierStepCount", [])
        if not step_records:
            return None

        daily_totals: dict[str, float] = defaultdict(float)
        for r in step_records:
            try:
                val = float(r["value"])
                date_str = r["startDate"][:10]
                daily_totals[date_str] += val
            except (ValueError, TypeError, IndexError):
                continue

        if not daily_totals:
            return None
        return round(statistics.mean(daily_totals.values()))

    def _latest_vo2(self, records: dict) -> Optional[float]:
        """Latest VO2 max reading."""
        vo2_records = records.get("HKQuantityTypeIdentifierVO2Max", [])
        if not vo2_records:
            return None
        # Take the last (most recent) record
        try:
            return round(float(vo2_records[-1]["value"]), 1)
        except (ValueError, TypeError, IndexError):
            return None

    def _compute_sleep(self, records: dict) -> Optional[dict]:
        """Compute sleep duration average and bedtime regularity.

        Groups SleepAnalysis records by night (contiguous records after 6 PM),
        sums asleep durations, computes bedtime stddev across nights.
        """
        sleep_records = records.get("HKCategoryTypeIdentifierSleepAnalysis", [])
        if not sleep_records:
            return None

        # Filter to asleep categories
        asleep_values = {
            "HKCategoryValueSleepAnalysisAsleep",
            "HKCategoryValueSleepAnalysisAsleepCore",
            "HKCategoryValueSleepAnalysisAsleepDeep",
            "HKCategoryValueSleepAnalysisAsleepREM",
        }

        # Group by night: use the date of the start time, shifted so
        # records after 6 PM belong to "that night"
        nights: dict[str, dict] = defaultdict(lambda: {"duration_hrs": 0.0, "bedtime_min": None})

        for r in sleep_records:
            if r["value"] not in asleep_values:
                continue

            try:
                start = datetime.strptime(r["startDate"][:19], "%Y-%m-%d %H:%M:%S")
                end = datetime.strptime(r["endDate"][:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, IndexError):
                continue

            duration_hrs = (end - start).total_seconds() / 3600
            if duration_hrs <= 0:
                continue

            # Assign to a night: if hour >= 18, it's "tonight"; otherwise "last night"
            if start.hour >= 18:
                night_key = start.strftime("%Y-%m-%d")
            else:
                night_key = (start - timedelta(days=1)).strftime("%Y-%m-%d")

            nights[night_key]["duration_hrs"] += duration_hrs

            # Track earliest bedtime for this night
            bedtime_min = start.hour * 60 + start.minute
            if bedtime_min < 720:  # before noon = after midnight
                bedtime_min += 1440
            current = nights[night_key]["bedtime_min"]
            if current is None or bedtime_min < current:
                nights[night_key]["bedtime_min"] = bedtime_min

        if not nights:
            return None

        durations = [n["duration_hrs"] for n in nights.values() if n["duration_hrs"] > 0]
        bedtimes = [n["bedtime_min"] for n in nights.values() if n["bedtime_min"] is not None]

        result = {}
        if durations:
            result["duration_avg"] = round(statistics.mean(durations), 1)
        else:
            return None

        if len(bedtimes) > 1:
            result["bedtime_stddev"] = round(statistics.stdev(bedtimes), 1)
        else:
            result["bedtime_stddev"] = None

        return result

    def save(self, latest: dict) -> Path:
        """No-op: JSON writes removed (Tier 4). SQLite is the write target.

        Returns the legacy path for backward compatibility with callers.
        """
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir / "apple_health_latest.json"

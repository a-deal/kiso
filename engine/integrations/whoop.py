"""WHOOP integration — pull health metrics for scoring.

Uses WHOOP API v1: https://developer.whoop.com/api
Auth: Run `python3 cli.py auth whoop` for interactive OAuth setup.
"""

import json
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from engine.gateway.token_store import TokenStore

SERVICE_NAME = "whoop"
API_BASE = "https://api.prod.whoop.com/developer/v1"


class WhoopClient:
    """Pull health metrics from WHOOP API v1."""

    def __init__(
        self,
        user_id: str = "default",
        token_store: Optional[TokenStore] = None,
        data_dir: Optional[str] = None,
    ):
        self.user_id = user_id
        self.store = token_store or TokenStore()
        self.data_dir = Path(data_dir or "./data")
        self._access_token: Optional[str] = None
        self._token_data: Optional[dict] = None

    @classmethod
    def from_config(cls, config: dict, user_id: str = "default") -> "WhoopClient":
        """Create a WhoopClient from a parsed config dict."""
        return cls(
            user_id=user_id,
            data_dir=config.get("data_dir"),
        )

    @classmethod
    def has_tokens(cls, user_id: str = "default", token_store: Optional[TokenStore] = None) -> bool:
        """Check if OAuth tokens exist for a user."""
        store = token_store or TokenStore()
        return store.has_token(SERVICE_NAME, user_id)

    def _load_tokens(self) -> dict:
        """Load tokens from store. Raises RuntimeError if not found."""
        if self._token_data is not None:
            return self._token_data

        data = self.store.load_token(SERVICE_NAME, self.user_id)
        if not data:
            raise RuntimeError(
                f"No WHOOP tokens found for user '{self.user_id}'. "
                "Run: python3 cli.py auth whoop"
            )
        self._token_data = data
        self._access_token = data["access_token"]
        return data

    def _refresh_token(self) -> bool:
        """Refresh the access token using the refresh token."""
        token_data = self._load_tokens()
        refresh_token = token_data.get("refresh_token")
        client_id = token_data.get("client_id")
        client_secret = token_data.get("client_secret")

        if not refresh_token or not client_id or not client_secret:
            return False

        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }).encode()

        req = urllib.request.Request(
            "https://api.prod.whoop.com/oauth/oauth2/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                new_tokens = json.loads(resp.read())
        except Exception as e:
            print(f"Token refresh failed: {e}", file=sys.stderr)
            return False

        if "access_token" not in new_tokens:
            return False

        # Update stored tokens
        token_data["access_token"] = new_tokens["access_token"]
        if new_tokens.get("refresh_token"):
            token_data["refresh_token"] = new_tokens["refresh_token"]
        token_data["expires_in"] = new_tokens.get("expires_in", 86400)
        token_data["obtained_at"] = int(time.time())

        self.store.save_token(SERVICE_NAME, self.user_id, token_data)
        self._access_token = new_tokens["access_token"]
        self._token_data = token_data
        return True

    def _api_get(self, endpoint: str, params: Optional[dict] = None, retry_on_401: bool = True) -> Optional[dict]:
        """Make an authenticated GET request to the WHOOP API.

        Auto-refreshes tokens on 401 (once).
        """
        self._load_tokens()

        url = f"{API_BASE}/{endpoint}"
        if params:
            url += f"?{urllib.parse.urlencode(params)}"

        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 401 and retry_on_401:
                if self._refresh_token():
                    return self._api_get(endpoint, params, retry_on_401=False)
                raise RuntimeError(
                    "WHOOP token expired and refresh failed. "
                    "Re-authenticate with: python3 cli.py auth whoop"
                )
            raise
        except Exception:
            raise

    def _api_get_all(self, endpoint: str, params: Optional[dict] = None) -> list[dict]:
        """Paginate through a WHOOP API endpoint using cursor-based nextToken.

        Returns all records across pages.
        """
        all_records = []
        request_params = dict(params or {})
        request_params.setdefault("limit", "25")

        while True:
            data = self._api_get(endpoint, request_params)
            if not data:
                break

            records = data.get("records", [])
            all_records.extend(records)

            next_token = data.get("next_token")
            if not next_token:
                break
            request_params["nextToken"] = next_token

        return all_records

    def pull_recovery(self, days: int = 30) -> list[dict]:
        """Pull recovery data (includes RHR and HRV)."""
        start = (date.today() - timedelta(days=days)).isoformat() + "T00:00:00.000Z"
        end = date.today().isoformat() + "T23:59:59.999Z"
        return self._api_get_all("recovery", {"start": start, "end": end})

    def pull_sleep(self, days: int = 30) -> list[dict]:
        """Pull sleep data."""
        start = (date.today() - timedelta(days=days)).isoformat() + "T00:00:00.000Z"
        end = date.today().isoformat() + "T23:59:59.999Z"
        return self._api_get_all("activity/sleep", {"start": start, "end": end})

    def pull_workouts(self, days: int = 30) -> list[dict]:
        """Pull workout data."""
        start = (date.today() - timedelta(days=days)).isoformat() + "T00:00:00.000Z"
        end = date.today().isoformat() + "T23:59:59.999Z"
        return self._api_get_all("activity/workout", {"start": start, "end": end})

    def pull_cycles(self, days: int = 30) -> list[dict]:
        """Pull physiological cycle data."""
        start = (date.today() - timedelta(days=days)).isoformat() + "T00:00:00.000Z"
        end = date.today().isoformat() + "T23:59:59.999Z"
        return self._api_get_all("cycle", {"start": start, "end": end})

    def _extract_resting_hr(self, recovery_data: list[dict]) -> Optional[float]:
        """Extract average resting heart rate from recovery data."""
        values = []
        for entry in recovery_data:
            score = entry.get("score", {})
            rhr = score.get("resting_heart_rate")
            if rhr is not None and isinstance(rhr, (int, float)) and rhr > 0:
                values.append(rhr)

        if values:
            avg = round(statistics.mean(values), 1)
            print(f"  Resting HR: {avg} bpm (from {len(values)} days)")
            return avg

        print("  Resting HR: no data found")
        return None

    def _extract_hrv(self, recovery_data: list[dict]) -> Optional[float]:
        """Extract average HRV RMSSD from recovery data.

        WHOOP provides hrv_rmssd_milli in milliseconds. We keep it in ms
        to match the standard schema (same unit as Garmin/Oura).
        """
        values = []
        for entry in recovery_data:
            score = entry.get("score", {})
            hrv = score.get("hrv_rmssd_milli")
            if hrv is not None and isinstance(hrv, (int, float)) and hrv > 0:
                values.append(hrv)

        if values:
            avg = round(statistics.mean(values), 1)
            print(f"  HRV RMSSD: {avg} ms (from {len(values)} days)")
            return avg

        print("  HRV RMSSD: no data found")
        return None

    def _extract_sleep_duration(self, sleep_data: list[dict]) -> Optional[float]:
        """Extract average sleep duration in hours from sleep data.

        WHOOP provides total_in_bed_time_milli (milliseconds).
        We use stage durations for actual sleep time if available.
        """
        values = []
        for entry in sleep_data:
            score = entry.get("score", {})
            # Prefer actual sleep time over total in bed
            stage_summary = score.get("stage_summary", {})
            total_ms = stage_summary.get("total_in_bed_time_milli")
            if total_ms is not None and isinstance(total_ms, (int, float)) and total_ms > 0:
                hours = total_ms / (1000 * 3600)
                if 1 < hours < 24:  # Sanity check
                    values.append(hours)

        if values:
            avg = round(statistics.mean(values), 1)
            print(f"  Sleep duration: {avg} hrs avg (from {len(values)} days)")
            return avg

        print("  Sleep duration: no data found")
        return None

    def _extract_sleep_regularity(self, sleep_data: list[dict]) -> Optional[float]:
        """Calculate bedtime standard deviation from sleep data."""
        bedtimes = []
        for entry in sleep_data:
            start_str = entry.get("start")
            if not start_str:
                continue

            # Only primary sleep (not naps)
            is_nap = entry.get("nap", False)
            if is_nap:
                continue

            try:
                if "+" in start_str or start_str.endswith("Z"):
                    dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                else:
                    dt = datetime.fromisoformat(start_str)
                minutes = dt.hour * 60 + dt.minute
                # Normalize: times after midnight are next-day
                if minutes < 720:  # before noon = after midnight bedtime
                    minutes += 1440
                bedtimes.append(minutes)
            except (ValueError, TypeError):
                pass

        if len(bedtimes) > 1:
            stdev = round(statistics.stdev(bedtimes), 1)
            avg_time = statistics.mean(bedtimes) % 1440
            avg_h = int(avg_time // 60)
            avg_m = int(avg_time % 60)
            print(f"  Sleep regularity: +/-{stdev} min stdev, avg bedtime ~{avg_h}:{avg_m:02d} (from {len(bedtimes)} nights)")
            return stdev

        print("  Sleep regularity: insufficient data")
        return None

    def _extract_zone2_from_workouts(self, workout_data: list[dict], days: int = 7) -> Optional[int]:
        """Extract Zone 2 minutes from workout data over the past week.

        WHOOP provides zone_duration in workouts. Zone 2 corresponds to
        the moderate intensity zone.
        """
        today = date.today()
        cutoff = today - timedelta(days=days)
        total_z2 = 0
        count = 0

        for entry in workout_data:
            start_str = entry.get("start")
            if not start_str:
                continue
            try:
                if "+" in start_str or start_str.endswith("Z"):
                    dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                else:
                    dt = datetime.fromisoformat(start_str)
                entry_date = dt.date()
            except (ValueError, TypeError):
                continue
            if entry_date < cutoff:
                continue

            # WHOOP zone_duration is array of zone durations in milliseconds
            # Zones: 0=below, 1=zone1, 2=zone2, 3=zone3, 4=zone4, 5=zone5
            score = entry.get("score", {})
            zone_durations = score.get("zone_durations", {})

            # Zone 2 duration in ms
            z2_ms = zone_durations.get("zone_two_milli", 0) or 0
            if isinstance(z2_ms, (int, float)) and z2_ms > 0:
                total_z2 += z2_ms / (1000 * 60)  # Convert ms to minutes
                count += 1

        if count > 0:
            total_z2 = round(total_z2)
            print(f"  Zone 2: ~{total_z2} min/week (from {count} workouts)")
            return total_z2 if total_z2 > 0 else None

        print("  Zone 2: no data found")
        return None

    def pull_all(self, history: bool = False, history_days: int = 90, person_id: str | None = None) -> dict:
        """Pull all WHOOP metrics. Returns dict compatible with scoring engine.

        The output schema matches garmin_latest.json exactly.
        """
        print("\nPulling WHOOP data...")

        # Pull raw data from API
        days = history_days if history else 30
        recovery = self.pull_recovery(days=days)
        sleep = self.pull_sleep(days=days)
        workouts = self.pull_workouts(days=days)

        # Extract metrics
        rhr = self._extract_resting_hr(recovery)
        hrv = self._extract_hrv(recovery)
        sleep_duration = self._extract_sleep_duration(sleep)
        sleep_stdev = self._extract_sleep_regularity(sleep)
        zone2 = self._extract_zone2_from_workouts(workouts, days=7)

        whoop_data = {
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "source": "whoop",
            "resting_hr": rhr,
            "daily_steps_avg": None,  # WHOOP doesn't track steps
            "sleep_regularity_stddev": sleep_stdev,
            "sleep_duration_avg": sleep_duration,
            "vo2_max": None,  # WHOOP doesn't provide VO2 max
            "hrv_rmssd_avg": hrv,
            "zone2_min_per_week": zone2,
        }

        metric_keys = [k for k in whoop_data if k not in ("last_updated", "source")]
        filled = sum(1 for k in metric_keys if whoop_data[k] is not None)

        print(f"\n{filled}/{len(metric_keys)} metrics pulled successfully.")

        missing = [k for k in metric_keys if whoop_data[k] is None]
        if missing:
            print(f"Missing: {', '.join(missing)}")

        # Build daily series for trend analysis
        if history:
            series = self._build_daily_series(recovery, sleep, days=history_days)
            if series and person_id:
                self._write_series_to_sqlite(series, person_id)

        return whoop_data

    def _write_series_to_sqlite(self, series: list[dict], person_id: str):
        """Write daily series rows to wearable_daily table."""
        try:
            from engine.gateway.db import init_db, write_wearable_daily_row
            init_db()
            count = 0
            for day in series:
                if not day.get("date"):
                    continue
                write_wearable_daily_row(person_id, day, source="whoop")
                count += 1
            print(f"  SQLite: wrote {count} whoop rows to wearable_daily.")
        except Exception as e:
            print(f"  SQLite wearable_daily write error: {e}", file=sys.stderr)

    def _build_daily_series(
        self,
        recovery_data: list[dict],
        sleep_data: list[dict],
        days: int = 90,
    ) -> list[dict]:
        """Build a daily time series matching garmin_daily.json schema."""
        today = date.today()

        # Index recovery by date
        recovery_by_date = {}
        for entry in recovery_data:
            created = entry.get("created_at") or entry.get("start")
            if created:
                try:
                    if "+" in created or created.endswith("Z"):
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    else:
                        dt = datetime.fromisoformat(created)
                    d_str = dt.date().isoformat()
                    recovery_by_date[d_str] = entry
                except (ValueError, TypeError):
                    pass

        # Index sleep by date
        sleep_by_date = {}
        for entry in sleep_data:
            start_str = entry.get("start")
            if start_str:
                try:
                    if "+" in start_str or start_str.endswith("Z"):
                        dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    else:
                        dt = datetime.fromisoformat(start_str)
                    # Use the date the sleep ended on (wake date)
                    end_str = entry.get("end")
                    if end_str:
                        if "+" in end_str or end_str.endswith("Z"):
                            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        else:
                            end_dt = datetime.fromisoformat(end_str)
                        d_str = end_dt.date().isoformat()
                    else:
                        d_str = dt.date().isoformat()
                    if not entry.get("nap", False):
                        sleep_by_date[d_str] = entry
                except (ValueError, TypeError):
                    pass

        series = []
        for i in range(days):
            d = today - timedelta(days=i)
            d_str = d.isoformat()

            entry = {
                "date": d_str,
                "rhr": None,
                "hrv": None,
                "steps": None,  # WHOOP doesn't track steps
                "sleep_hrs": None,
                "sleep_start": None,
                "sleep_end": None,
            }

            # Recovery data (RHR, HRV)
            rec = recovery_by_date.get(d_str)
            if rec:
                score = rec.get("score", {})
                rhr = score.get("resting_heart_rate")
                if rhr and isinstance(rhr, (int, float)) and rhr > 0:
                    entry["rhr"] = round(rhr, 1)
                hrv = score.get("hrv_rmssd_milli")
                if hrv and isinstance(hrv, (int, float)) and hrv > 0:
                    entry["hrv"] = round(hrv, 1)

            # Sleep data
            slp = sleep_by_date.get(d_str)
            if slp:
                sleep_score = slp.get("score", {})
                stage_summary = sleep_score.get("stage_summary", {})
                total_ms = stage_summary.get("total_in_bed_time_milli")
                if total_ms and isinstance(total_ms, (int, float)) and total_ms > 0:
                    entry["sleep_hrs"] = round(total_ms / (1000 * 3600), 1)

                start = slp.get("start")
                end = slp.get("end")
                if start:
                    try:
                        if "+" in start or start.endswith("Z"):
                            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                        else:
                            start_dt = datetime.fromisoformat(start)
                        entry["sleep_start"] = start_dt.strftime("%H:%M")
                    except (ValueError, TypeError):
                        pass
                if end:
                    try:
                        if "+" in end or end.endswith("Z"):
                            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                        else:
                            end_dt = datetime.fromisoformat(end)
                        entry["sleep_end"] = end_dt.strftime("%H:%M")
                    except (ValueError, TypeError):
                        pass

            series.append(entry)

        series.reverse()  # oldest first
        filled_rhr = sum(1 for e in series if e["rhr"] is not None)
        filled_hrv = sum(1 for e in series if e["hrv"] is not None)
        filled_sleep = sum(1 for e in series if e["sleep_hrs"] is not None)
        print(f"  Daily series: {filled_rhr} RHR, {filled_hrv} HRV, {filled_sleep} sleep days (of {days})")
        return series

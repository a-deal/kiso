"""Oura Ring integration — pull health metrics for scoring.

Uses Oura API v2: https://cloud.ouraring.com/v2/docs
Auth: Run `python3 cli.py auth oura` for interactive OAuth setup.
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

SERVICE_NAME = "oura"
API_BASE = "https://api.ouraring.com/v2/usercollection"


class OuraClient:
    """Pull health metrics from Oura Ring API v2."""

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
    def from_config(cls, config: dict, user_id: str = "default") -> "OuraClient":
        """Create an OuraClient from a parsed config dict."""
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
                f"No Oura tokens found for user '{self.user_id}'. "
                "Run: python3 cli.py auth oura"
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
            "https://api.ouraring.com/oauth/token",
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

    def _api_get(self, endpoint: str, params: Optional[dict] = None, retry_on_401: bool = True) -> Optional[dict | list]:
        """Make an authenticated GET request to the Oura API.

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
                    "Oura token expired and refresh failed. "
                    "Re-authenticate with: python3 cli.py auth oura"
                )
            raise
        except Exception:
            raise

    def pull_sleep(self, days: int = 30) -> list[dict]:
        """Pull daily sleep data."""
        start = (date.today() - timedelta(days=days)).isoformat()
        end = date.today().isoformat()

        data = self._api_get("daily_sleep", {"start_date": start, "end_date": end})
        if not data:
            return []
        return data.get("data", [])

    def pull_sleep_periods(self, days: int = 30) -> list[dict]:
        """Pull individual sleep periods (for HRV and regularity)."""
        start = (date.today() - timedelta(days=days)).isoformat()
        end = date.today().isoformat()

        data = self._api_get("sleep", {"start_date": start, "end_date": end})
        if not data:
            return []
        return data.get("data", [])

    def pull_activity(self, days: int = 30) -> list[dict]:
        """Pull daily activity data."""
        start = (date.today() - timedelta(days=days)).isoformat()
        end = date.today().isoformat()

        data = self._api_get("daily_activity", {"start_date": start, "end_date": end})
        if not data:
            return []
        return data.get("data", [])

    def pull_readiness(self, days: int = 30) -> list[dict]:
        """Pull daily readiness data."""
        start = (date.today() - timedelta(days=days)).isoformat()
        end = date.today().isoformat()

        data = self._api_get("daily_readiness", {"start_date": start, "end_date": end})
        if not data:
            return []
        return data.get("data", [])

    def pull_heart_rate(self, days: int = 7) -> list[dict]:
        """Pull heart rate data."""
        start = (date.today() - timedelta(days=days)).isoformat()
        end = date.today().isoformat()

        data = self._api_get("heartrate", {"start_datetime": f"{start}T00:00:00+00:00", "end_datetime": f"{end}T23:59:59+00:00"})
        if not data:
            return []
        return data.get("data", [])

    def pull_workouts(self, days: int = 7) -> list[dict]:
        """Pull workout data."""
        start = (date.today() - timedelta(days=days)).isoformat()
        end = date.today().isoformat()

        data = self._api_get("workout", {"start_date": start, "end_date": end})
        if not data:
            return []
        return data.get("data", [])

    def _extract_resting_hr(self, readiness_data: list[dict], sleep_data: list[dict]) -> Optional[float]:
        """Extract average resting heart rate.

        NOTE: readiness_data contributors.resting_heart_rate is a contribution
        SCORE (1-100), NOT actual bpm. We skip it and go straight to sleep
        periods which have the real lowest_heart_rate in bpm.
        """
        # Go directly to sleep periods for actual HR data
        return self._extract_resting_hr_from_sleep_periods(sleep_data)

    def _extract_resting_hr_from_sleep_periods(self, sleep_periods: list[dict]) -> Optional[float]:
        """Extract resting HR from sleep period data."""
        values = []
        for period in sleep_periods:
            hr = period.get("lowest_heart_rate")
            if hr and isinstance(hr, (int, float)) and hr > 0:
                values.append(hr)

        if values:
            avg = round(statistics.mean(values), 1)
            print(f"  Resting HR (from sleep): {avg} bpm (from {len(values)} periods)")
            return avg
        return None

    def _extract_hrv(self, sleep_periods: list[dict]) -> Optional[float]:
        """Extract average HRV RMSSD from sleep periods."""
        values = []
        for period in sleep_periods:
            hrv = period.get("average_hrv")
            if hrv and isinstance(hrv, (int, float)) and hrv > 0:
                values.append(hrv)

        if values:
            avg = round(statistics.mean(values), 1)
            print(f"  HRV RMSSD: {avg} ms (from {len(values)} periods)")
            return avg

        print("  HRV RMSSD: no data found")
        return None

    def _extract_sleep_duration(self, daily_sleep: list[dict]) -> Optional[float]:
        """Extract average sleep duration in hours."""
        values = []
        for entry in daily_sleep:
            contributors = entry.get("contributors", {})
            # total_sleep is in seconds in the sleep periods
            # daily_sleep has total_sleep_duration as seconds
            total = entry.get("total_sleep_duration")
            if total and isinstance(total, (int, float)) and total > 0:
                values.append(total / 3600)

        if not values:
            # Fallback: look for score-level duration
            for entry in daily_sleep:
                ts = entry.get("timestamp")
                if ts:
                    # Sometimes duration is nested differently
                    pass

        if values:
            avg = round(statistics.mean(values), 1)
            print(f"  Sleep duration: {avg} hrs avg (from {len(values)} days)")
            return avg

        print("  Sleep duration: no data found")
        return None

    def _extract_sleep_regularity(self, sleep_periods: list[dict]) -> Optional[float]:
        """Calculate bedtime standard deviation from sleep periods."""
        bedtimes = []
        for period in sleep_periods:
            # Only look at "long_sleep" type (not naps)
            sleep_type = period.get("type")
            if sleep_type and sleep_type != "long_sleep":
                continue

            bedtime_start = period.get("bedtime_start")
            if bedtime_start:
                try:
                    # Parse ISO datetime
                    if "+" in bedtime_start or bedtime_start.endswith("Z"):
                        # Has timezone info
                        dt = datetime.fromisoformat(bedtime_start.replace("Z", "+00:00"))
                    else:
                        dt = datetime.fromisoformat(bedtime_start)
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

    def _extract_steps(self, activity_data: list[dict]) -> Optional[int]:
        """Extract average daily steps."""
        values = []
        for entry in activity_data:
            steps = entry.get("steps")
            if steps and isinstance(steps, (int, float)) and steps > 0:
                values.append(steps)

        if values:
            avg = round(statistics.mean(values))
            print(f"  Daily steps: {avg} avg (from {len(values)} days)")
            return avg

        print("  Daily steps: no data found")
        return None

    def _extract_zone2_minutes(self, activity_data: list[dict], days: int = 7) -> Optional[int]:
        """Extract Zone 2 minutes from activity data over the past week."""
        today = date.today()
        cutoff = today - timedelta(days=days)
        total_z2 = 0
        count = 0

        for entry in activity_data:
            entry_date_str = entry.get("day")
            if not entry_date_str:
                continue
            try:
                entry_date = date.fromisoformat(entry_date_str)
            except ValueError:
                continue
            if entry_date < cutoff:
                continue

            # Oura provides medium_activity_met_minutes or active time
            # Zone 2 ~ "medium" activity
            medium_mins = entry.get("medium_activity_met_minutes")
            if medium_mins and isinstance(medium_mins, (int, float)):
                # MET minutes to actual minutes: divide by ~4 (zone 2 MET ~4)
                total_z2 += medium_mins / 4
                count += 1

        if count > 0:
            total_z2 = round(total_z2)
            print(f"  Zone 2: ~{total_z2} min/week (estimated from {count} days)")
            return total_z2 if total_z2 > 0 else None

        print("  Zone 2: no data found")
        return None

    def pull_all(self, history: bool = False, history_days: int = 90, person_id: str | None = None) -> dict:
        """Pull all Oura metrics. Returns dict compatible with scoring engine.

        The output schema matches garmin_latest.json exactly.
        """
        print("\nPulling Oura Ring data...")

        # Pull raw data from API
        days = history_days if history else 30
        daily_sleep = self.pull_sleep(days=days)
        sleep_periods = self.pull_sleep_periods(days=days)
        activity = self.pull_activity(days=days)
        readiness = self.pull_readiness(days=days)

        # Extract metrics
        rhr = self._extract_resting_hr(readiness, daily_sleep)
        if rhr is None:
            rhr = self._extract_resting_hr_from_sleep_periods(sleep_periods)

        hrv = self._extract_hrv(sleep_periods)
        sleep_duration = self._extract_sleep_duration(daily_sleep)
        sleep_stdev = self._extract_sleep_regularity(sleep_periods)
        steps = self._extract_steps(activity)
        zone2 = self._extract_zone2_minutes(activity, days=7)

        oura_data = {
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "source": "oura",
            "resting_hr": rhr,
            "daily_steps_avg": steps,
            "sleep_regularity_stddev": sleep_stdev,
            "sleep_duration_avg": sleep_duration,
            "vo2_max": None,  # Oura doesn't provide VO2 max
            "hrv_rmssd_avg": hrv,
            "zone2_min_per_week": zone2,
        }

        metric_keys = [k for k in oura_data if k not in ("last_updated", "source")]
        filled = sum(1 for k in metric_keys if oura_data[k] is not None)

        print(f"\n{filled}/{len(metric_keys)} metrics pulled successfully.")

        missing = [k for k in metric_keys if oura_data[k] is None]
        if missing:
            print(f"Missing: {', '.join(missing)}")

        # Build daily series for trend analysis
        if history:
            series = self._build_daily_series(daily_sleep, sleep_periods, activity, days=history_days)
            if series and person_id:
                self._write_series_to_sqlite(series, person_id)

        return oura_data

    def _write_series_to_sqlite(self, series: list[dict], person_id: str):
        """Write daily series rows to wearable_daily table."""
        try:
            from engine.gateway.db import init_db, write_wearable_daily_row
            init_db()
            count = 0
            for day in series:
                if not day.get("date"):
                    continue
                write_wearable_daily_row(person_id, day, source="oura")
                count += 1
            print(f"  SQLite: wrote {count} oura rows to wearable_daily.")
        except Exception as e:
            print(f"  SQLite wearable_daily write error: {e}", file=sys.stderr)

    def _build_daily_series(
        self,
        daily_sleep: list[dict],
        sleep_periods: list[dict],
        activity: list[dict],
        days: int = 90,
    ) -> list[dict]:
        """Build a daily time series matching garmin_daily.json schema."""
        today = date.today()

        # Index data by date
        sleep_by_date = {}
        for entry in daily_sleep:
            d = entry.get("day")
            if d:
                sleep_by_date[d] = entry

        periods_by_date = {}
        for period in sleep_periods:
            d = period.get("day")
            if d:
                if d not in periods_by_date:
                    periods_by_date[d] = []
                periods_by_date[d].append(period)

        activity_by_date = {}
        for entry in activity:
            d = entry.get("day")
            if d:
                activity_by_date[d] = entry

        series = []
        for i in range(days):
            d = today - timedelta(days=i)
            d_str = d.isoformat()

            entry = {
                "date": d_str,
                "rhr": None,
                "hrv": None,
                "steps": None,
                "sleep_hrs": None,
                "sleep_start": None,
                "sleep_end": None,
            }

            # Sleep data
            sleep = sleep_by_date.get(d_str)
            if sleep:
                total = sleep.get("total_sleep_duration")
                if total and isinstance(total, (int, float)) and total > 0:
                    entry["sleep_hrs"] = round(total / 3600, 1)

            # Sleep period data (HRV, bedtime)
            periods = periods_by_date.get(d_str, [])
            long_sleep = [p for p in periods if p.get("type") == "long_sleep"]
            if not long_sleep:
                long_sleep = periods  # fallback to all periods

            if long_sleep:
                # HRV from sleep
                hrvs = [p.get("average_hrv") for p in long_sleep if p.get("average_hrv")]
                if hrvs:
                    entry["hrv"] = round(statistics.mean(hrvs), 1)

                # RHR from sleep (lowest HR)
                rhrs = [p.get("lowest_heart_rate") for p in long_sleep if p.get("lowest_heart_rate")]
                if rhrs:
                    entry["rhr"] = round(statistics.mean(rhrs), 1)

                # Bedtime from first long sleep period
                bt = long_sleep[0].get("bedtime_start")
                if bt:
                    try:
                        dt = datetime.fromisoformat(bt.replace("Z", "+00:00"))
                        entry["sleep_start"] = dt.strftime("%H:%M")
                        total_secs = long_sleep[0].get("total_sleep_duration")
                        if total_secs and total_secs > 0:
                            end_dt = dt + timedelta(seconds=total_secs)
                            entry["sleep_end"] = end_dt.strftime("%H:%M")
                    except (ValueError, TypeError):
                        pass

            # Activity data
            act = activity_by_date.get(d_str)
            if act:
                steps = act.get("steps")
                if steps and isinstance(steps, (int, float)) and steps > 0:
                    entry["steps"] = int(steps)

            series.append(entry)

        series.reverse()  # oldest first
        filled_rhr = sum(1 for e in series if e["rhr"] is not None)
        filled_hrv = sum(1 for e in series if e["hrv"] is not None)
        filled_sleep = sum(1 for e in series if e["sleep_hrs"] is not None)
        print(f"  Daily series: {filled_rhr} RHR, {filled_hrv} HRV, {filled_sleep} sleep days (of {days})")
        return series

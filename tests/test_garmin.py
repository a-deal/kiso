"""Tests for Garmin integration (unit tests, no API calls)."""

from engine.integrations.garmin import GarminClient, DEFAULT_EXERCISE_MAP


def test_default_exercise_map():
    """Default exercise map should contain common lifts."""
    assert "barbell deadlift" in DEFAULT_EXERCISE_MAP
    assert "barbell bench press" in DEFAULT_EXERCISE_MAP
    assert "barbell back squat" in DEFAULT_EXERCISE_MAP


def test_normalize_exercise_mapped():
    """Known exercises should map to normalized names."""
    client = GarminClient()
    assert client.normalize_exercise("Barbell Deadlift") == "deadlift"
    assert client.normalize_exercise("dumbbell bench press") == "bench_press"
    assert client.normalize_exercise("Back Squat") == "squat"


def test_normalize_exercise_unknown():
    """Unknown exercises should be lowercased and underscored."""
    client = GarminClient()
    assert client.normalize_exercise("Lat Pulldown") == "lat_pulldown"
    assert client.normalize_exercise("Seated Row") == "seated_row"


def test_custom_exercise_map():
    """Custom exercise map should override defaults."""
    custom_map = {"cable fly": "chest_fly", "hammer curl": "bicep_curl"}
    client = GarminClient(exercise_map=custom_map)
    assert client.normalize_exercise("Cable Fly") == "chest_fly"
    assert client.normalize_exercise("Hammer Curl") == "bicep_curl"
    # Unknown exercises still get normalized
    assert client.normalize_exercise("Deadlift") == "deadlift"  # not in custom map


def test_from_config():
    """GarminClient.from_config should parse config dict."""
    config = {
        "garmin": {
            "email": "test@example.com",
            "token_dir": "/tmp/tokens",
        },
        "exercise_name_map": {"front squat": "squat"},
        "data_dir": "/tmp/data",
    }
    client = GarminClient.from_config(config)
    assert client.email == "test@example.com"
    assert str(client.token_dir) == "/tmp/tokens"
    assert client.exercise_map == {"front squat": "squat"}
    assert str(client.data_dir) == "/tmp/data"


def test_has_tokens_no_dir(tmp_path):
    """has_tokens returns False when token dir doesn't exist."""
    assert GarminClient.has_tokens(token_dir=str(tmp_path / "nonexistent")) is False


def test_has_tokens_with_dir(tmp_path):
    """has_tokens returns True when token dir has files."""
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    (token_dir / "oauth1_token.json").write_text("{}")
    assert GarminClient.has_tokens(token_dir=str(token_dir)) is True


def test_has_tokens_empty_dir(tmp_path):
    """has_tokens returns False when token dir exists but is empty."""
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    assert GarminClient.has_tokens(token_dir=str(token_dir)) is False


def test_deprecation_warning(capsys):
    """from_config prints deprecation warning when credentials are in config."""
    config = {
        "garmin": {
            "email": "test@example.com",
            "password": "secret",
            "token_dir": "/tmp/tokens",
        },
    }
    GarminClient.from_config(config)
    captured = capsys.readouterr()
    assert "deprecated" in captured.err.lower()

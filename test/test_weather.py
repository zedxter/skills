"""Tests for skills/weather/scripts/weather.py.

Run: uv run --no-project --with pytest python -I -m pytest test/test_weather.py -q

Offline: Net.get is replaced with canned answers shaped like Open-Meteo's geocoding, forecast, archive and
air-quality APIs and OpenStreetMap Nominatim; Net itself is tested against a local HTTP server. WEATHER_LIVE=1 adds
smoke tests for a fixed coordinate (Berlin, 52.52,13.41) against the real services.
"""

import datetime as dt
import http.server
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "weather" / "scripts" / "weather.py"
sys.path.insert(0, str(SCRIPT.parent))
import weather as w  # noqa: E402

ORIGINAL_GET = w.Net.get
PARIS = [{"name": "Paris", "latitude": 48.85341, "longitude": 2.3488, "country": "France", "country_code": "FR",
          "admin1": "Île-de-France", "timezone": "Europe/Paris", "population": 2138551, "elevation": 42.0},
         {"name": "Paris", "latitude": 33.66094, "longitude": -95.55551, "country": "United States",
          "country_code": "US", "admin1": "Texas", "timezone": "America/Chicago", "population": 24782},
         {"name": "Paris", "latitude": 36.302, "longitude": -88.32671, "country": "United States",
          "country_code": "US", "admin1": "Tennessee", "population": 10150}]
SPRINGFIELD = [{"name": "Springfield", "latitude": 37.2, "longitude": -93.3, "country": "United States",
                "admin1": "Missouri", "population": 169176},
               {"name": "Springfield", "latitude": 39.8, "longitude": -89.6, "country": "United States",
                "admin1": "Illinois", "population": 116250}]


SALEM = [{"name": "Salem", "latitude": 11.65, "longitude": 78.16, "country": "India", "country_code": "IN",
          "admin1": "Tamil Nadu", "population": 917414},
         {"name": "Salem", "latitude": 44.94, "longitude": -123.04, "country": "United States", "country_code": "US",
          "admin1": "Oregon", "population": 174365},
         {"name": "Salem", "latitude": 38.6, "longitude": -86.1, "country": "United States", "country_code": "US",
          "admin1": "Indiana", "population": 6319}]


def forecast_answer(q):
    days = int(q.get("forecast_days", 7))
    imperial = q.get("temperature_unit") == "fahrenheit"
    d = {"latitude": 48.86, "longitude": 2.35, "elevation": 46.0, "timezone": "Europe/Paris",
         "timezone_abbreviation": "GMT+1",
         "current_units": {"time": "iso8601", "interval": "seconds", "temperature_2m": "°F" if imperial else "°C",
                           "wind_speed_10m": "mp/h" if imperial else "km/h", "weather_code": "wmo code"},
         "current": {"time": "2026-10-30T20:00", "interval": 900, "temperature_2m": 11.8, "apparent_temperature": 9.5,
                     "relative_humidity_2m": 64, "precipitation": 0.0, "weather_code": 2, "cloud_cover": 40,
                     "pressure_msl": 1027.3, "wind_speed_10m": 7.9, "wind_direction_10m": 47, "wind_gusts_10m": 16.9,
                     "is_day": 0},
         "daily_units": {"time": "iso8601", "temperature_2m_max": "°F" if imperial else "°C",
                         "precipitation_sum": "inch" if imperial else "mm"},
         "daily": {"time": [f"2026-10-{30 + i:02d}" if i < 2 else f"2026-11-{i - 1:02d}" for i in range(days)],
                   **{k: [None] * days for k in w.DAILY if k != "sunrise" and k != "sunset"},
                   "sunrise": ["2026-10-30T07:31"] * days, "sunset": ["2026-10-30T17:28"] * days}}
    d["daily"]["weather_code"] = [61] + [0] * (days - 1)
    d["daily"]["temperature_2m_min"] = [8.1] * days
    d["daily"]["temperature_2m_max"] = [15.04] * days
    d["daily"]["precipitation_sum"] = [2.4] + [0.0] * (days - 1)
    d["daily"]["precipitation_probability_max"] = [80] + [5] * (days - 1)
    d["daily"]["wind_direction_10m_dominant"] = [270] * days
    if "hourly" in q:
        n = int(q.get("forecast_hours", 24))
        d["hourly"] = {"time": [f"2026-10-30T{20 + i:02d}:00" if i < 4 else f"2026-10-31T{i - 4:02d}:00" for i in range(n)],
                       **{k: [1] * n for k in w.HOURLY}}
    return d


class FakeNet:
    calls: list[str] = []
    extra: list = []

    @staticmethod
    def get(self, url, what):  # noqa: ANN001, ANN205 - replaces a method
        FakeNet.calls.append(url)
        u = urllib.parse.urlsplit(url)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.hostname.endswith("geocoding-api.open-meteo.com"):
            res = {"Paris": PARIS, "Springfield": SPRINGFIELD, "Salem": SALEM, "Berlin": [
                {"name": "Berlin", "latitude": 52.52, "longitude": 13.41, "country": "Germany", "admin1": "Berlin",
                 "population": 3426354},
                {"name": "Berlin", "latitude": 44.5, "longitude": -71.2, "country": "United States",
                 "admin1": "New Hampshire", "population": 9425}]}.get(q["name"], [])
            if q.get("countryCode"):
                res = [r for r in res if r["country_code"] == q["countryCode"]] if res and "country_code" in res[0] else res
            return {"results": res} if res else {"generationtime_ms": 0.1}
        if u.hostname == "nominatim.openstreetmap.org":
            if "Eiffel" in q["q"]:
                return [{"lat": "48.8582", "lon": "2.2945", "name": "Tour Eiffel", "display_name": "Tour Eiffel, Paris, France",
                         "address": {"state": "Île-de-France", "country": "France", "country_code": "fr"}}]
            return []
        if u.path.endswith("/forecast"):
            return forecast_answer(q)
        if u.path.endswith("/archive"):
            start, end = dt.date.fromisoformat(q["start_date"]), dt.date.fromisoformat(q["end_date"])
            days = [(start + dt.timedelta(i)).isoformat() for i in range((end - start).days + 1)]
            n = len(days)
            return {"latitude": 55.75, "longitude": 37.62, "elevation": 151.0, "timezone": "Europe/Moscow",
                    "daily_units": {"temperature_2m_max": "°C", "precipitation_sum": "mm"},
                    "daily": {"time": days, "weather_code": [3] * n,
                              "temperature_2m_min": [10.0 + i % 5 for i in range(n)],
                              "temperature_2m_max": [20.0 + i % 7 for i in range(n)],
                              "temperature_2m_mean": [15.0] * n, "precipitation_sum": [2.0 if i % 3 == 0 else 0.0 for i in range(n)],
                              "snowfall_sum": [0.0] * n, "wind_speed_10m_max": [12.0] * n,
                              "sunshine_duration": [36000.0] * (n - 1) + [None]},
                    **({"hourly": {"time": [f"{days[0]}T00:00"], **{k: [1] for k in w.HOURLY}}} if "hourly" in q else {})}
        if u.path.endswith("/air-quality"):
            return {"latitude": 28.6, "longitude": 77.2, "timezone": "Asia/Kolkata",
                    "current": {"time": "2026-10-31T00:30", "european_aqi": 68, "us_aqi": 89, "pm2_5": 65.2,
                                "pm10": 117.1, "ozone": 17.0, "nitrogen_dioxide": 61.8, "sulphur_dioxide": 30.2,
                                "carbon_monoxide": 900.0, "uv_index": 0.0, "birch_pollen": 3.0, "grass_pollen": None},
                    "hourly": {"time": ["2026-10-31T00:00", "2026-10-31T01:00", "2026-11-01T00:00"],
                               "european_aqi": [68, 123, 40], "us_aqi": [89, 156, 50], "pm2_5": [65.2, 113.6, 20.0]}}
        raise AssertionError(f"unexpected request {url}")


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    FakeNet.calls = []
    FakeNet.extra = []
    monkeypatch.setattr(w.Net, "get", FakeNet.get)
    monkeypatch.setattr(w, "KEY", None)


def run(*argv, capsys):
    code = w.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


def js(*argv, capsys):
    code, out, err = run(*argv, "-f", "json", capsys=capsys)
    assert code == 0, err
    return json.loads(out)


@pytest.mark.parametrize("text,expected", [("52.52,13.41", (52.52, 13.41)), (" -33.9 , 18.4 ", (-33.9, 18.4)),
                                           ("40.7 -74", (40.7, -74.0)), ("52,52 13,41", (52.52, 13.41)),
                                           ("-33,87; 151,21", (-33.87, 151.21)), ("Berlin", None), ("52 Festivals", None)])
def test_coordinates(text, expected):
    assert w.parse_coords(text) == expected


@pytest.mark.parametrize("text", ["52.5", ",", "1,2,3", "12.3.4"])
def test_numbers_that_are_not_coordinates(text):
    with pytest.raises(w.Failure) as e:
        w.parse_coords(text)
    assert e.value.code == w.EXIT_BAD_INPUT


def test_place_qualifiers_and_country(capsys):
    d = js("Paris, Texas", "--days", "1", capsys=capsys)
    assert d["place"]["admin1"] == "Texas" and d["place"]["latitude"] == 33.66094
    d = js("Paris", "--days", "1", capsys=capsys)
    assert d["place"]["country"] == "France" and d["place"]["alternatives"][0] == "Paris, Texas, United States"
    code, _, err = run("Paris, Narnia", capsys=capsys)
    assert code == 1 and "no place found" in err


def test_ambiguity_note_only_when_unclear(capsys):
    _, _, err = run("Berlin", "--days", "1", capsys=capsys)
    assert "matched" not in err
    _, _, err = run("Springfield", "--days", "1", capsys=capsys)
    assert "'Springfield' matched Springfield, Missouri, United States; also Springfield, Illinois" in err


def test_nominatim_fallback_with_attribution(capsys):
    code, out, err = run("Eiffel Tower", "--days", "1", capsys=capsys)
    assert code == 0 and "# Tour Eiffel, Île-de-France, France" in out and "© OpenStreetMap contributors" in out
    assert any("nominatim" in c for c in FakeNet.calls)


def test_forecast(capsys):
    d = js("48.85,2.35", "--days", "3", capsys=capsys)
    assert set(d) == {"kind", "place", "units", "model", "current", "daily", "hourly", "attribution"}
    assert d["current"]["weather"] == "Partly cloudy" and d["current"]["wind_from"] == "NE"
    assert [r["date"] for r in d["daily"]] == ["2026-10-30", "2026-10-31", "2026-11-01"]
    assert d["daily"][0]["weather"] == "Light rain" and d["daily"][0]["wind_from"] == "W"
    assert d["hourly"] == [] and d["place"]["timezone"] == "Europe/Paris"
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(FakeNet.calls[-1]).query)
    assert q["timezone"] == ["auto"] and q["models"] == ["best_match"] and q["forecast_days"] == ["3"]
    code, out, _ = run("48.85,2.35", "--days", "2", capsys=capsys)
    assert out.splitlines()[0] == "# 48.8500, 2.3500 · 48.86°N 2.35°E · 46 m · Europe/Paris (GMT+1)"
    assert "now (20:00): 11.8°C, feels 9.5°C · Partly cloudy · wind 8 km/h NE (gusts 17)" in out
    assert "Fri 10-30  Light rain" in out and "2.4 (80%)" in out and "07:31–17:28" in out
    assert "non-commercial" in out and "Open-Meteo.com (CC BY 4.0)" in out


def test_hourly_imperial_and_markdown(capsys):
    d = js("48.85,2.35", "--days", "1", "--hourly", "--hours", "6", "--units", "imperial", capsys=capsys)
    assert len(d["hourly"]) == 6 and d["units"]["wind_speed_10m"] == "mph"
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(FakeNet.calls[-1]).query)
    assert q["temperature_unit"] == ["fahrenheit"] and q["wind_speed_unit"] == ["mph"] and q["forecast_hours"] == ["6"]
    code, out, _ = run("48.85,2.35", "--days", "1", "-f", "markdown", capsys=capsys)
    assert "| date | weather | min/max °C |" in out and "|---|---|" in out


def test_history_days_and_months(capsys):
    d = js("history", "55.75,37.62", "--from", "2024-07-01", "--to", "2024-07-04", capsys=capsys)
    s = d["summary"]
    assert s["days"] == 4 and s["mean_max"] == 21.5 and s["precipitation_total"] == 4.0 and s["wet_days"] == 2
    assert s["hottest"] == {"date": "2024-07-04", "temperature_2m_max": 23.0}
    assert d["daily"][0]["sunshine_hours"] == 10.0 and d["daily"][-1]["sunshine_hours"] is None
    assert "monthly" not in d and "note" not in d
    d = js("history", "55.75,37.62", "--from", "2024-01-01", "--to", "2024-06-30", capsys=capsys)
    assert [m["month"] for m in d["monthly"]] == [f"2024-{m:02d}" for m in range(1, 7)]
    code, out, _ = run("history", "55.75,37.62", "--from", "2024-01-01", "--to", "2024-06-30", capsys=capsys)
    assert "month    mean max °C" in out and "2024-03" in out
    recent = (dt.date.today() - dt.timedelta(2)).isoformat()
    d = js("history", "55.75,37.62", "--from", recent, capsys=capsys)
    assert "preliminary" in d["note"] and d["from"] == d["to"] == recent


@pytest.mark.parametrize("argv", [
    ["--from", "2024-13-01"], ["--from", "2024-07-05", "--to", "2024-07-01"], ["--from", "1939-12-31"],
    ["--from", "2999-01-01"], ["--from", "2024-07-01", "--to", "2024-07-20", "--hourly"],
])
def test_history_bad_dates(argv, capsys):
    code, _, err = run("history", "55.75,37.62", *argv, capsys=capsys)
    assert code == 2 and err.startswith("error: ")


def test_air(capsys):
    d = js("air", "28.6,77.2", "--days", "2", capsys=capsys)
    assert d["current"]["european_aqi_level"] == "poor" and d["current"]["us_aqi_level"] == "moderate"
    assert d["pollen"] == {"birch": 3.0}
    assert d["daily"][0] == {"date": "2026-10-31", "european_aqi_max": 123, "us_aqi_max": 156, "pm2_5_max": 113.6,
                             "european_aqi_level": "extremely poor", "us_aqi_level": "unhealthy"}
    code, out, _ = run("air", "28.6,77.2", capsys=capsys)
    assert "European AQI 68 (poor) · US AQI 89 (moderate)" in out and "pollen (grains/m³): birch 3" in out


@pytest.mark.parametrize("value,scale,label", [(20, w.EAQI, "good"), (20.5, w.EAQI, "fair"), (101, w.EAQI, "extremely poor"),
                                               (50, w.USAQI, "good"), (151, w.USAQI, "unhealthy"), (None, w.USAQI, None)])
def test_bands(value, scale, label):
    assert w.band(value, scale) == label


def test_compass_and_codes():
    assert [w.compass(d) for d in (0, 11.2, 11.3, 359, 180, None)] == ["N", "N", "NNE", "N", "S", None]
    assert w.weather_text(95) == "Thunderstorm" and w.weather_text(42) == "code 42" and w.weather_text(None) is None


def test_commercial_key_uses_customer_hosts(capsys, monkeypatch):
    monkeypatch.setattr(w, "KEY", "secret")
    js("48.85,2.35", "--days", "1", capsys=capsys)
    u = urllib.parse.urlsplit(FakeNet.calls[-1])
    assert u.hostname == "customer-api.open-meteo.com" and "apikey=secret" in u.query


@pytest.mark.parametrize("argv", [[""], ["x", "--days", "0"], ["x", "--days", "17"], ["x", "--hours", "400"],
                                  ["air", "x", "--days", "8"], ["x", "--lang", "english!"], ["x", "--country", "USA"],
                                  ["x", "--model", "best match"], ["91,10"], ["x", "--timeout", "0"]])
def test_bad_input(argv, capsys):
    code, out, err = run(*argv, capsys=capsys)
    assert code == 2 and out == "" and err.startswith("error: ")


def test_hostile_place_names_stay_on_one_line(capsys, monkeypatch):
    def evil(self, url, what):
        if "geocoding" in url:
            return {"results": [{"name": "X\n# fake\x1b[2J", "latitude": 1, "longitude": 2, "country": "Y‮"}]}
        return FakeNet.get(self, url, what)
    monkeypatch.setattr(w.Net, "get", evil)
    code, out, _ = run("X", "--days", "1", capsys=capsys)
    assert out.splitlines()[0].startswith("# X # fake [2J, Y") and "\x1b" not in out and "‮" not in out


# ------------------------------------------------------------------ the network layer, against a local server

class Server(http.server.BaseHTTPRequestHandler):
    script: list = []
    seen: list = []

    def do_GET(self):  # noqa: N802
        Server.seen.append(dict(self.headers))
        status, body, headers = Server.script.pop(0) if Server.script else (200, b"{}", {})
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(w.Net, "get", ORIGINAL_GET)
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    srv = http.server.HTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Server.script, Server.seen = [], []
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


@pytest.mark.parametrize("script,code,text", [
    ([(400, b'{"error":true,"reason":"Forecast days is invalid."}', {})], w.EXIT_BAD_INPUT, "Forecast days is invalid"),
    ([(200, b'{"error":true,"reason":"Latitude must be in range"}', {})], w.EXIT_BAD_INPUT, "Latitude must be"),
    ([(429, b'{"reason":"Minutely API request limit exceeded"}', {"Retry-After": "1"})] * 2, w.EXIT_RATE, "rate limit"),
    ([(502, b"", {})] * 2, w.EXIT_NETWORK, "not answering"),
    ([(401, b'{"reason":"Invalid API key"}', {})], w.EXIT_API, "Invalid API key"),
    ([(200, b"<html>", {})], w.EXIT_API, "not JSON"),
])
def test_http_failures(server, script, code, text):
    Server.script = list(script)
    with pytest.raises(w.Failure) as e:
        w.Net(5, lambda m: None).get(server, "test")
    assert e.value.code == code and text in str(e.value)


def test_user_agent_retry_and_cut_off(server):
    Server.script = [(429, b"{}", {"Retry-After": "1"}), (200, b'{"ok": 1}', {})]
    assert w.Net(5, lambda m: None).get(server, "t") == {"ok": 1}
    assert Server.seen[0]["User-Agent"] == w.UA and "github.com/zedxter/utility-belt" in w.UA
    Server.script = [(200, b'{"par', {"Content-Length": "100"}), (200, b'{"ok": 2}', {})]
    assert w.Net(5, lambda m: None).get(server, "t") == {"ok": 2}


def test_help_runs_standalone():
    out = subprocess.run([sys.executable, "-I", str(SCRIPT), "history", "--help"], capture_output=True, text=True,
                         check=True)
    assert "--from" in out.stdout


LIVE = pytest.mark.skipif(os.environ.get("WEATHER_LIVE") != "1", reason="set WEATHER_LIVE=1 to query the real services")


def live(*argv):
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), *argv, "-f", "json"], capture_output=True, text=True,
                       timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@LIVE
def test_live_fixed_coordinate():
    d = live("52.52,13.41", "--days", "3", "--hourly", "--hours", "6")
    assert d["place"]["timezone"] == "Europe/Berlin" and len(d["daily"]) == 3 and len(d["hourly"]) == 6
    assert isinstance(d["current"]["temperature_2m"], (int, float)) and d["daily"][0]["weather"]
    h = live("history", "52.52,13.41", "--from", "2024-07-01", "--to", "2024-07-07")
    assert h["summary"]["days_with_data"] == 7 and 10 < h["summary"]["mean_max"] < 40
    a = live("air", "52.52,13.41")
    assert isinstance(a["current"]["european_aqi"], (int, float))
    g = live("Springfield, Illinois", "--days", "1")
    assert g["place"]["admin1"] == "Illinois"


# ------------------------------------------------------------------ review findings

def test_negative_coordinates_are_places_not_options(capsys):
    d = js("-33.87,151.21", "--days", "1", capsys=capsys)
    assert d["place"]["latitude"] == -33.87
    d = js("history", "-33.87,151.21", "--from", "2024-01-01", capsys=capsys)
    assert d["from"] == "2024-01-01"


@pytest.mark.parametrize("place,admin1", [("Salem, IN", "Indiana"), ("Salem, OR", "Oregon"),
                                          ("Salem, India", "Tamil Nadu"), ("Paris, TX", "Texas"),
                                          ("Paris, FR", "Île-de-France"), ("Paris, Tennessee", "Tennessee")])
def test_state_and_country_codes(place, admin1, capsys):
    assert js(place, "--days", "1", capsys=capsys)["place"]["admin1"] == admin1


def test_qualifier_codes():
    gb = {"country_code": "GB", "country": "United Kingdom", "admin1": "England"}
    ca = {"country_code": "CA", "country": "Canada", "admin1": "British Columbia"}
    assert w.qualifier_fits("UK", gb) and w.qualifier_fits("BC", ca) and w.qualifier_fits("Canada", ca)
    assert w.qualifier_fits("IN", {"country_code": "US", "admin1": "Indiana", "country": "United States"})
    assert not w.qualifier_fits("GB", ca) and not w.qualifier_fits("Ind", gb)


def test_imperial_wet_days_and_inches(capsys):
    days = [{"date": f"2024-01-{i:02d}", "temperature_2m_max": 40.0, "temperature_2m_min": 30.0,
             "precipitation_sum": x} for i, x in enumerate([0.0, 0.03, 0.04, 0.5, 1.2], 1)]
    assert w.summarize(days, imperial=True)["wet_days"] == 3 and w.summarize(days)["wet_days"] == 1
    assert w.summarize(days, imperial=True)["precipitation_total"] == 1.77


def test_forecast_columns_polar_night_and_hours(capsys, monkeypatch):
    original = forecast_answer

    def polar(q):
        d = original(q)
        d["daily"]["daylight_duration"] = [0.0] * len(d["daily"]["time"])
        d["daily"]["apparent_temperature_min"] = [-9.0] * len(d["daily"]["time"])
        d["daily"]["wind_gusts_10m_max"] = [52.0] * len(d["daily"]["time"])
        return d
    monkeypatch.setitem(globals(), "forecast_answer", polar)
    code, out, _ = run("78.2,15.6", "--days", "1", "--hours", "2", capsys=capsys)
    assert "polar night" in out and "-9 / –" in out and "/52 W" in out
    assert "hourly:" in out and "feels °C" in out and "precip mm" in out  # --hours alone implies --hourly
    assert "regional blend" in out


def test_history_hourly_is_rendered(capsys):
    code, out, _ = run("history", "55.75,37.62", "--from", "2024-07-01", "--hourly", capsys=capsys)
    assert code == 0 and "hourly:" in out and "chance" not in out


def test_air_has_no_units_flag(capsys):
    with pytest.raises(SystemExit):
        w.main(["air", "28.6,77.2", "--units", "imperial"])
    capsys.readouterr()


@pytest.mark.parametrize("script,code,text", [
    ([(400, b'{"error":true,"reason":"The supplied API key is invalid."}', {})], w.EXIT_API, "API key is invalid"),
])
def test_bad_api_key_is_exit_7(server, script, code, text):
    Server.script = list(script)
    with pytest.raises(w.Failure) as e:
        w.Net(5, lambda m: None).get(server, "test")
    assert e.value.code == code and text in str(e.value) and "OPEN_METEO_API_KEY" in e.value.hint


def test_nominatim_block_and_truncated_chunks(monkeypatch):
    monkeypatch.setattr(w.Net, "get", ORIGINAL_GET)
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    import http.client
    import urllib.error
    calls = []

    def blocked(req, timeout):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
    monkeypatch.setattr(w.urllib.request, "urlopen", blocked)
    with pytest.raises(w.Failure) as e:
        w.Net(5, lambda m: None).get("https://nominatim.openstreetmap.org/search?q=x", "OpenStreetMap Nominatim")
    assert e.value.code == w.EXIT_RATE and "lat,lon" in e.value.hint

    def cut(req, timeout):
        calls.append(req.full_url)
        raise http.client.IncompleteRead(b"{")
    monkeypatch.setattr(w.urllib.request, "urlopen", cut)
    calls.clear()
    with pytest.raises(w.Failure) as e:
        w.Net(5, lambda m: None).get("https://api.open-meteo.com/v1/forecast", "Open-Meteo forecast")
    assert e.value.code == w.EXIT_NETWORK and len(calls) == 2

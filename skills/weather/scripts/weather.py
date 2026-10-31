#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Weather for any place without a key, from Open-Meteo: current conditions with an hourly and daily forecast
(up to 16 days), past weather back to 1940 (ERA5 reanalysis), and air quality with pollen. Place names are
resolved with Open-Meteo's geocoding (OpenStreetMap Nominatim as a fallback for addresses and landmarks).
Standard library only.

    uv run weather.py Berlin                                  # now and the next 7 days
    uv run weather.py "Paris, Texas" --days 3 --hourly        # also the next 24 hours, hour by hour
    uv run weather.py 40.71,-74.01 --units imperial
    uv run weather.py history Moscow --from 2024-07-01 --to 2024-07-31
    uv run weather.py air "Delhi"                            # AQI, PM2.5, PM10, ozone, NO2, pollen

Exit codes:
    0 done · 1 place not found · 2 bad input (also: dates or days out of range) · 4 rate-limited · 6 network
    error or the service down · 7 an unexpected answer or failure · 130 interrupted
"""

import argparse
import http.client
import contextlib
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

EXIT_OK, EXIT_NOT_FOUND, EXIT_BAD_INPUT, EXIT_RATE, EXIT_NETWORK, EXIT_API = 0, 1, 2, 4, 6, 7

UA = "utility-belt-weather/1.0 (+https://github.com/zedxter/utility-belt)"
KEY = os.environ.get("OPEN_METEO_API_KEY")  # a commercial key switches to the customer-* hosts
HOST = {"forecast": "api.open-meteo.com/v1/forecast", "archive": "archive-api.open-meteo.com/v1/archive",
        "air": "air-quality-api.open-meteo.com/v1/air-quality", "geo": "geocoding-api.open-meteo.com/v1/search"}
NOMINATIM = "https://nominatim.openstreetmap.org/search"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
OSM = "Place search © OpenStreetMap contributors (ODbL)"
FIRST_ARCHIVE_DAY = dt.date(1940, 1, 1)
WMO = {0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
       51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle", 56: "Light freezing drizzle",
       57: "Freezing drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Light freezing rain",
       67: "Freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
       80: "Light showers", 81: "Showers", 82: "Violent showers", 85: "Light snow showers", 86: "Snow showers",
       95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail"}
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
CURRENT = ["temperature_2m", "apparent_temperature", "relative_humidity_2m", "precipitation", "weather_code",
           "cloud_cover", "pressure_msl", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "is_day"]
DAILY = ["weather_code", "temperature_2m_min", "temperature_2m_max", "apparent_temperature_min",
         "apparent_temperature_max", "precipitation_sum", "precipitation_probability_max", "wind_speed_10m_max",
         "wind_gusts_10m_max", "wind_direction_10m_dominant", "uv_index_max", "sunrise", "sunset",
         "daylight_duration"]
HOURLY = ["temperature_2m", "apparent_temperature", "precipitation_probability", "precipitation", "weather_code",
          "wind_speed_10m", "wind_direction_10m", "relative_humidity_2m"]
HISTORY_DAILY = ["weather_code", "temperature_2m_min", "temperature_2m_max", "temperature_2m_mean",
                 "precipitation_sum", "snowfall_sum", "wind_speed_10m_max", "sunshine_duration"]
AIR = ["european_aqi", "us_aqi", "pm2_5", "pm10", "ozone", "nitrogen_dioxide", "sulphur_dioxide",
       "carbon_monoxide", "uv_index"]
POLLEN = ["alder_pollen", "birch_pollen", "grass_pollen", "mugwort_pollen", "olive_pollen", "ragweed_pollen"]
US_STATES = dict(zip(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK "
    "OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split(),
    ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
     "District of Columbia", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas",
     "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
     "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
     "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
     "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
     "West Virginia", "Wisconsin", "Wyoming"]))
CA_PROVINCES = {"AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
                "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories",
                "NU": "Nunavut", "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
                "SK": "Saskatchewan", "YT": "Yukon"}
COUNTRY_ALIAS = {"UK": "GB", "USA": "US", "U.S.": "US", "U.S.A.": "US", "UAE": "AE"}
EAQI = [(20, "good"), (40, "fair"), (60, "moderate"), (80, "poor"), (100, "very poor"), (10**9, "extremely poor")]
USAQI = [(50, "good"), (100, "moderate"), (150, "unhealthy for sensitive groups"), (200, "unhealthy"),
         (300, "very unhealthy"), (10**9, "hazardous")]


class Failure(Exception):
    def __init__(self, code: int, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.hint = code, hint


def safe(s: Any) -> str:
    """One printable line: no control, C1 or bidi-override characters, whitespace collapsed."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f‪-‮⁦-⁩]", " ", str(s if s is not None else "")).split())


# ------------------------------------------------------------------ network

class Net:
    def __init__(self, timeout: float, note) -> None:
        self.timeout, self.note = timeout, note

    def get(self, url: str, what: str) -> Any:
        """Parsed JSON. Open-Meteo answers bad parameters with HTTP 400 and a reason (exit 2); 429 is retried once,
        then exit 4; 5xx and network errors are retried once, then exit 6."""
        host = urllib.parse.urlsplit(url).hostname or ""
        for attempt in (1, 2):
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read(50_000_001)
                    length = resp.headers.get("Content-Length", "")
                if length.isdigit() and len(body) < int(length):
                    raise ConnectionError(f"answer cut off after {len(body)} of {length} bytes")
                break
            except urllib.error.HTTPError as e:
                reason = ""
                with contextlib.suppress(Exception):
                    reason = str(json.loads(e.read(20_000)).get("reason") or "")
                osm = host.endswith("openstreetmap.org")
                if osm and e.code in (403, 429):
                    raise Failure(EXIT_RATE, f"{what} refused the request (HTTP {e.code})",
                                  "Nominatim blocks heavy or anonymous use; give lat,lon instead of an address") from None
                if "api key" in reason.lower():
                    raise Failure(EXIT_API, f"{what}: {reason}", "check OPEN_METEO_API_KEY, or unset it for the free "
                                                                 "tier") from None
                if e.code == 429:
                    ra = (e.headers or {}).get("Retry-After", "")
                    secs = int(ra) if ra.isdigit() else 10
                    if attempt == 1 and secs <= 30:
                        self.note(f"{host} rate limit; waiting {secs} s once")
                        time.sleep(secs)
                        continue
                    raise Failure(EXIT_RATE, f"{what}: rate limit (HTTP 429)" + (f": {reason}" if reason else ""),
                                  "the free tier allows 600 calls a minute, 5,000 an hour, 10,000 a day and "
                                  "300,000 a month; wait and do not loop") from None
                if e.code >= 500:
                    if attempt == 1:
                        time.sleep(2)
                        continue
                    raise Failure(EXIT_NETWORK, f"{what} is not answering (HTTP {e.code})", "try again later") from None
                if e.code == 400 and reason:
                    raise Failure(EXIT_BAD_INPUT, f"{what}: {reason}", "fix the argument") from None
                if e.code in (401, 403):
                    raise Failure(EXIT_API, f"{what} refused the request (HTTP {e.code})" + (f": {reason}" if reason else ""),
                                  "check OPEN_METEO_API_KEY, or unset it for the free tier") from None
                raise Failure(EXIT_API, f"{what} answered HTTP {e.code}" + (f": {reason}" if reason else ""),
                              "try again later") from None
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
                why = getattr(e, "reason", e) or type(e).__name__
                if "CERTIFICATE_VERIFY_FAILED" in str(why):
                    raise Failure(EXIT_NETWORK, f"TLS certificate verification failed ({why})",
                                  "on a python.org macOS build run 'Install Certificates.command', or use uv's "
                                  "managed Python") from None
                if attempt == 2:
                    raise Failure(EXIT_NETWORK, f"{host}: {why}", "check the connection (HTTPS_PROXY is honoured)") from None
                time.sleep(1)
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise Failure(EXIT_API, f"{what} answered something that is not JSON", "try again later") from None
        if isinstance(data, dict) and data.get("error"):
            raise Failure(EXIT_BAD_INPUT, f"{what}: {data.get('reason', 'error')}", "fix the argument")
        return data

    def meteo(self, kind: str, what: str, **params: Any) -> Any:
        host = HOST[kind]
        if KEY:
            host, params = "customer-" + host, {**params, "apikey": KEY}
        return self.get(f"https://{host}?{urllib.parse.urlencode(params)}", what)


# ------------------------------------------------------------------ places

def parse_coords(s: str) -> tuple[float, float] | None:
    """lat,lon in decimal degrees ("52.52,13.41", "52.52 13.41", "-33.87;151.21", or with decimal commas
    "52,52 13,41"). Input made only of numbers that is not a coordinate pair is bad input, never a place name."""
    m = re.fullmatch(r"\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*", s) \
        or re.fullmatch(r"\s*(-?\d{1,3},\d+)\s*[;\s]\s*(-?\d{1,3},\d+)\s*", s)
    if not m:
        if re.fullmatch(r"[\s\d.,;+-]+", s):
            raise Failure(EXIT_BAD_INPUT, f"{s!r} is neither a place nor a lat,lon pair",
                          "give lat,lon in decimal degrees, latitude first: 52.52,13.41")
        return None
    lat, lon = float(m[1].replace(",", ".")), float(m[2].replace(",", "."))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise Failure(EXIT_BAD_INPUT, f"{s!r}: latitude must be -90..90 and longitude -180..180",
                      "give lat,lon in decimal degrees, latitude first")
    return lat, lon


def fold(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s.casefold()) if not unicodedata.combining(c))


def qualifier_fits(q: str, r: dict[str, Any]) -> bool:
    """Whether "Texas", "TX", "US", "UK", "Germany" or "Bavaria" describes the geocoding result r. Two- and
    three-letter codes match exactly (a US state, a Canadian province or a country code), never as a prefix."""
    up = q.upper().replace(" ", "")
    cc = str(r.get("country_code") or "").upper()
    admin1 = fold(str(r.get("admin1") or ""))
    if re.fullmatch(r"[A-Z.]{2,6}", up):
        if up in COUNTRY_ALIAS:
            return cc == COUNTRY_ALIAS[up]
        if cc == "US" and up in US_STATES:
            return admin1 == fold(US_STATES[up])
        if cc == "CA" and up in CA_PROVINCES:
            return admin1 == fold(CA_PROVINCES[up])
        if len(up) == 2:
            return cc == up
    fq = fold(q)
    return any(fold(str(r.get(k) or "")).startswith(fq) for k in ("country", "admin1", "admin2", "admin3"))


def describe(p: dict[str, Any]) -> str:
    parts = [p.get("name")] + [p.get(k) for k in ("admin1", "country")]
    return ", ".join(dict.fromkeys(x for x in parts if x))


def resolve(net: Net, place: str, lang: str, country: str | None, note) -> dict[str, Any]:
    """The place to report on: coordinates as given, or the best geocoding match. A "Name, Region, Country" query
    keeps only the matches whose region or country fits; a less certain pick lists the alternatives."""
    if coords := parse_coords(place):
        return {"name": f"{coords[0]:.4f}, {coords[1]:.4f}", "latitude": coords[0], "longitude": coords[1],
                "source": "coordinates", "alternatives": []}
    parts = [p.strip() for p in place.split(",") if p.strip()]
    if not parts:
        raise Failure(EXIT_BAD_INPUT, "the place is empty", "give a place name or lat,lon")
    name, *quals = parts
    params: dict[str, Any] = {"name": name, "count": 10, "language": lang, "format": "json"}
    if country:
        params["countryCode"] = country.upper()
    d = net.meteo("geo", "Open-Meteo geocoding", **params)
    results = [r for r in (d or {}).get("results") or [] if isinstance(r, dict) and "latitude" in r]
    for q in quals:
        if q.upper() in US_STATES:  # "Salem, IN" is Indiana, not India; "Victoria, CA" is Canada only if no Californian one
            state = [r for r in results if str(r.get("country_code") or "").upper() == "US"
                     and fold(str(r.get("admin1") or "")) == fold(US_STATES[q.upper()])]
            if state:
                results = state
                continue
        results = [r for r in results if qualifier_fits(q, r)]
    if results:
        top = results[0]
        place_out = {k: top.get(k) for k in ("name", "admin1", "admin2", "country", "country_code", "latitude",
                                              "longitude", "elevation", "timezone", "population")}
        place_out["source"] = "Open-Meteo geocoding (GeoNames)"
        alts = [describe(r) for r in results[1:4]]
        place_out["alternatives"] = alts
        # Say so only when the pick is not clearly the main place of that name (Berlin, Germany vs Berlin, NH).
        top_pop = top.get("population") or 0
        rival = max((r.get("population") or 0 for r in results[1:4]), default=0)
        if alts and (not top_pop or rival * 10 > top_pop):
            note(f"{place!r} matched {describe(top)}; also {'; '.join(alts)} — add the region or country "
                 "(\"Paris, Texas\") or --country if you meant another")
        return place_out
    # Addresses, landmarks and districts: OpenStreetMap Nominatim (one request; its policy allows 1 a second).
    q = {"q": place, "format": "jsonv2", "limit": 3, "accept-language": lang, "addressdetails": 1}
    if country:
        q["countrycodes"] = country.lower()
    hits = net.get(f"{NOMINATIM}?{urllib.parse.urlencode(q)}", "OpenStreetMap Nominatim")
    hits = [h for h in hits or [] if isinstance(h, dict) and h.get("lat")]
    if not hits:
        raise Failure(EXIT_NOT_FOUND, f"no place found for {place!r}",
                      "check the spelling, add the country (\"Springfield, Illinois\"), or give lat,lon")
    h = hits[0]
    addr = h.get("address") or {}
    return {"name": h.get("name") or h.get("display_name", "").split(",")[0], "display_name": h.get("display_name"),
            "admin1": addr.get("state"), "country": addr.get("country"),
            "country_code": (addr.get("country_code") or "").upper() or None,
            "latitude": float(h["lat"]), "longitude": float(h["lon"]), "source": OSM,
            "alternatives": [x.get("display_name") for x in hits[1:]]}


# ------------------------------------------------------------------ values

def compass(deg: Any) -> str | None:
    return COMPASS[int((float(deg) % 360) / 22.5 + 0.5) % 16] if isinstance(deg, (int, float)) else None


def weather_text(code: Any) -> str | None:
    return WMO.get(int(code), f"code {code}") if isinstance(code, (int, float)) else None


def band(value: Any, scale: list[tuple[int, str]]) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return next(label for limit, label in scale if value <= limit)


def columns(block: dict[str, Any] | None, names: list[str]) -> list[dict[str, Any]]:
    """Open-Meteo's column arrays as a list of rows."""
    if not isinstance(block, dict) or not isinstance(block.get("time"), list):
        return []
    return [{"time": t, **{n: (block.get(n) or [None] * len(block["time"]))[i] for n in names}}
            for i, t in enumerate(block["time"])]


def unit_params(units: str) -> dict[str, str]:
    return {"temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch"} \
        if units == "imperial" else {}


def place_header(p: dict[str, Any], d: dict[str, Any]) -> dict[str, Any]:
    """The resolved place with what the weather service reports for it (grid point, elevation, timezone)."""
    return {**p, "grid_latitude": d.get("latitude"), "grid_longitude": d.get("longitude"),
            "elevation": d.get("elevation", p.get("elevation")), "timezone": d.get("timezone", p.get("timezone")),
            "timezone_abbreviation": d.get("timezone_abbreviation")}


# ------------------------------------------------------------------ commands

def cmd_forecast(net: Net, args: argparse.Namespace, place: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {"latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto",
                              "forecast_days": args.days, "current": ",".join(CURRENT), "daily": ",".join(DAILY),
                              "models": args.model, **unit_params(args.units)}
    if args.hourly or args.hours is not None:
        params.update(hourly=",".join(HOURLY), forecast_hours=args.hours or 24)
    d = net.meteo("forecast", "Open-Meteo forecast", **params)
    cur = d.get("current") or {}
    current = {k: cur.get(k) for k in ["time"] + CURRENT}
    current["weather"] = weather_text(cur.get("weather_code"))
    current["wind_from"] = compass(cur.get("wind_direction_10m"))
    daily = []
    for r in columns(d.get("daily"), DAILY):
        r["date"] = r.pop("time")
        r["weather"] = weather_text(r.get("weather_code"))
        r["wind_from"] = compass(r.get("wind_direction_10m_dominant"))
        daily.append(r)
    hourly = []
    for r in columns(d.get("hourly"), HOURLY):
        r["weather"] = weather_text(r.get("weather_code"))
        r["wind_from"] = compass(r.get("wind_direction_10m"))
        hourly.append(r)
    if not daily and not current.get("time"):
        raise Failure(EXIT_API, "the forecast came back empty", "try again later")
    return {"kind": "forecast", "place": place_header(place, d), "units": units_of(d), "model": args.model,
            "current": current, "daily": daily, "hourly": hourly, "attribution": ATTRIBUTION}


def units_of(d: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in ("current_units", "daily_units", "hourly_units"):
        for name, unit in (d.get(k) or {}).items():
            if name not in ("time", "interval") and unit not in ("iso8601", "wmo code", "unixtime"):
                out.setdefault(name, {"mp/h": "mph"}.get(unit, unit))
    return out


def summarize(days: list[dict[str, Any]], imperial: bool = False) -> dict[str, Any]:
    """Means, extremes, precipitation and wet days (at least 1 mm, or 0.04 inch)."""
    wet, nd = (0.04, 2) if imperial else (1.0, 1)
    def vals(k: str) -> list[float]:
        return [r[k] for r in days if isinstance(r.get(k), (int, float))]
    hi, lo, pr = vals("temperature_2m_max"), vals("temperature_2m_min"), vals("precipitation_sum")
    mean = vals("temperature_2m_mean")
    hottest = max((r for r in days if isinstance(r.get("temperature_2m_max"), (int, float))),
                  key=lambda r: r["temperature_2m_max"], default=None)
    coldest = min((r for r in days if isinstance(r.get("temperature_2m_min"), (int, float))),
                  key=lambda r: r["temperature_2m_min"], default=None)
    return {"days": len(days), "days_with_data": len(hi),
            "mean_temperature": round(sum(mean) / len(mean), 1) if mean else None,
            "mean_max": round(sum(hi) / len(hi), 1) if hi else None,
            "mean_min": round(sum(lo) / len(lo), 1) if lo else None,
            "hottest": {"date": hottest["date"], "temperature_2m_max": hottest["temperature_2m_max"]} if hottest else None,
            "coldest": {"date": coldest["date"], "temperature_2m_min": coldest["temperature_2m_min"]} if coldest else None,
            "precipitation_total": round(sum(pr), nd) if pr else None,
            "wet_days": sum(1 for x in pr if x >= wet) if pr else None}


def monthly(days: list[dict[str, Any]], imperial: bool = False) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in days:
        groups.setdefault(r["date"][:7], []).append(r)
    out = []
    for month, rows in groups.items():
        s = summarize(rows, imperial)
        out.append({"month": month, "mean_max": s["mean_max"], "mean_min": s["mean_min"],
                    "precipitation_total": s["precipitation_total"], "wet_days": s["wet_days"]})
    return out


def cmd_history(net: Net, args: argparse.Namespace, place: dict[str, Any]) -> dict[str, Any]:
    today = dt.datetime.now(dt.UTC).date()  # the archive's last day is today in UTC
    try:
        start = dt.date.fromisoformat(args.date_from)
        end = dt.date.fromisoformat(args.date_to) if args.date_to else start
    except ValueError:
        raise Failure(EXIT_BAD_INPUT, "dates must be YYYY-MM-DD", "e.g. --from 2024-07-01 --to 2024-07-31") from None
    if start > end:
        raise Failure(EXIT_BAD_INPUT, "--from is after --to")
    if start < FIRST_ARCHIVE_DAY or end > today:
        raise Failure(EXIT_BAD_INPUT, f"history covers {FIRST_ARCHIVE_DAY} to today ({today})",
                      "for coming days use the forecast")
    if (end - start).days > 366 * 50:
        raise Failure(EXIT_BAD_INPUT, "the range is longer than 50 years", "split it")
    if args.hourly and (end - start).days > 6:
        raise Failure(EXIT_BAD_INPUT, "--hourly history is limited to 7 days", "narrow --from/--to")
    params: dict[str, Any] = {"latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto",
                              "start_date": start.isoformat(), "end_date": end.isoformat(),
                              "daily": ",".join(HISTORY_DAILY), **unit_params(args.units)}
    if args.hourly:
        params["hourly"] = ",".join(h for h in HOURLY if h != "precipitation_probability")
    d = net.meteo("archive", "Open-Meteo historical weather", **params)
    days = []
    for r in columns(d.get("daily"), HISTORY_DAILY):
        r["date"] = r.pop("time")
        r["weather"] = weather_text(r.get("weather_code"))
        if isinstance(r.get("sunshine_duration"), (int, float)):
            r["sunshine_hours"] = round(r.pop("sunshine_duration") / 3600, 1)
        else:
            r.pop("sunshine_duration", None)
            r["sunshine_hours"] = None
        days.append(r)
    hourly = []
    for r in columns(d.get("hourly"), [h for h in HOURLY if h != "precipitation_probability"]):
        r["weather"] = weather_text(r.get("weather_code"))
        hourly.append(r)
    if not days:
        raise Failure(EXIT_API, "the archive came back empty", "try again later")
    out = {"kind": "history", "place": place_header(place, d), "units": units_of(d), "from": start.isoformat(),
           "to": end.isoformat(), "summary": summarize(days, args.units == "imperial"), "daily": days,
           "hourly": hourly,
           "dataset": "ERA5 reanalysis (and ERA5-Land, ECMWF IFS for recent days), 1940 onwards",
           "attribution": ATTRIBUTION + "; ERA5: Copernicus Climate Change Service"}
    if (today - end).days < 6:
        out["note"] = "the last days are preliminary: ERA5 arrives about 5 days late, so recent values come from analyses"
    if len(days) > 62:
        out["monthly"] = monthly(days, args.units == "imperial")
    return out


def cmd_air(net: Net, args: argparse.Namespace, place: dict[str, Any]) -> dict[str, Any]:
    params = {"latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto",
              "current": ",".join(AIR + POLLEN), "hourly": "european_aqi,us_aqi,pm2_5",
              "forecast_days": args.days}
    d = net.meteo("air", "Open-Meteo air quality", **params)
    cur = d.get("current") or {}
    current = {k: cur.get(k) for k in ["time"] + AIR}
    current["european_aqi_level"] = band(cur.get("european_aqi"), EAQI)
    current["us_aqi_level"] = band(cur.get("us_aqi"), USAQI)
    pollen = {k.replace("_pollen", ""): cur.get(k) for k in POLLEN if isinstance(cur.get(k), (int, float))}
    days: dict[str, dict[str, Any]] = {}
    for r in columns(d.get("hourly"), ["european_aqi", "us_aqi", "pm2_5"]):
        day = days.setdefault(r["time"][:10], {"date": r["time"][:10]})
        for k in ("european_aqi", "us_aqi", "pm2_5"):
            if isinstance(r.get(k), (int, float)):
                day[f"{k}_max"] = max(day.get(f"{k}_max", r[k]), r[k])
    daily = list(days.values())
    for r in daily:
        r["european_aqi_level"] = band(r.get("european_aqi_max"), EAQI)
        r["us_aqi_level"] = band(r.get("us_aqi_max"), USAQI)
    if current.get("time") is None and not daily:
        raise Failure(EXIT_API, "the air quality answer came back empty", "try again later")
    return {"kind": "air", "place": place_header(place, d), "units": units_of(d), "current": current,
            "pollen": pollen or None, "daily": daily,
            "model": "CAMS (Copernicus Atmosphere Monitoring Service): global about 45 km, Europe 11 km",
            "attribution": ATTRIBUTION + "; air quality: CAMS"}


# ------------------------------------------------------------------ output

def fmt(v: Any, nd: int = 0, unit: str = "") -> str:
    if not isinstance(v, (int, float)):
        return "–"
    return (f"{v:.{nd}f}" if nd else f"{round(v):d}") + unit


def hm(iso: Any) -> str:
    return str(iso)[11:16] if isinstance(iso, str) and len(iso) >= 16 else "–"


def day_label(date: str) -> str:
    with contextlib.suppress(ValueError):
        d = dt.date.fromisoformat(date)
        return d.strftime("%a ") + date[5:]
    return date


def header(d: dict[str, Any]) -> str:
    p = d["place"]
    lat, lon = p.get("grid_latitude") or p["latitude"], p.get("grid_longitude") or p["longitude"]
    where = f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"
    bits = [describe(p) if p.get("source") != "coordinates" else p["name"], where,
            f"{fmt(p.get('elevation'))} m" if isinstance(p.get("elevation"), (int, float)) else "",
            safe(p.get("timezone") or "") + (f" ({safe(p.get('timezone_abbreviation'))})" if p.get("timezone_abbreviation") else "")]
    return "# " + " · ".join(safe(b) for b in bits if b)


def render(d: dict[str, Any], args: argparse.Namespace) -> str:
    if args.format == "json":
        return json.dumps(d, ensure_ascii=False, indent=2) + "\n"
    md = args.format == "markdown"
    u = d["units"]
    t_unit = u.get("temperature_2m", u.get("temperature_2m_max", "°C"))
    w_unit = u.get("wind_speed_10m", u.get("wind_speed_10m_max", "km/h"))
    p_unit = u.get("precipitation", u.get("precipitation_sum", "mm"))
    pd = 2 if p_unit.startswith("inch") else 1  # 0.04 inch is a wet day; one decimal would show 0.0
    lines = [header(d)]

    def sun(r: dict[str, Any]) -> str:
        day = r.get("daylight_duration")
        if isinstance(day, (int, float)) and day <= 0:
            return "polar night"
        if isinstance(day, (int, float)) and day >= 86_300:
            return "polar day"
        return f"{hm(r.get('sunrise'))}–{hm(r.get('sunset'))}"

    def hourly_table(rows: list[dict[str, Any]], chance: bool) -> list[str]:
        head = ["time", "weather", f"temp {t_unit}", f"feels {t_unit}", f"precip {p_unit}"] \
            + (["chance"] if chance else []) + [f"wind {w_unit}"]
        return table(head, [[f"{day_label(r['time'][:10])[:3]} {hm(r['time'])}", r.get("weather") or "–",
                             fmt(r.get("temperature_2m")), fmt(r.get("apparent_temperature")),
                             fmt(r.get("precipitation"), pd)]
                            + ([f"{fmt(r.get('precipitation_probability'))}%"] if chance else [])
                            + [f"{fmt(r.get('wind_speed_10m'))} {r.get('wind_from') or ''}".strip()] for r in rows])

    def table(head: list[str], rows: list[list[str]]) -> list[str]:
        if md:
            return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + \
                   ["| " + " | ".join(r) + " |" for r in rows]
        widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(head)] if rows else [len(h) for h in head]
        return ["  ".join(h.ljust(w) for h, w in zip(head, widths)).rstrip()] + \
               ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]

    if d["kind"] == "forecast":
        c = d["current"]
        if c.get("time"):
            lines.append(f"now ({hm(c['time'])}): {fmt(c.get('temperature_2m'), 1)}{t_unit}, feels "
                         f"{fmt(c.get('apparent_temperature'), 1)}{t_unit} · {c.get('weather') or '–'} · wind "
                         f"{fmt(c.get('wind_speed_10m'))} {w_unit} {c.get('wind_from') or ''} (gusts "
                         f"{fmt(c.get('wind_gusts_10m'))}) · humidity {fmt(c.get('relative_humidity_2m'))}% · "
                         f"{fmt(c.get('pressure_msl'))} hPa")
        lines.append("")
        lines += table(["date", "weather", f"min/max {t_unit}", "feels", f"precip {p_unit} (chance)",
                        f"wind/gusts {w_unit}", "UV", "sun"],
                       [[day_label(r["date"]), r.get("weather") or "–",
                         f"{fmt(r.get('temperature_2m_min'))} / {fmt(r.get('temperature_2m_max'))}",
                         f"{fmt(r.get('apparent_temperature_min'))} / {fmt(r.get('apparent_temperature_max'))}",
                         f"{fmt(r.get('precipitation_sum'), pd)} ({fmt(r.get('precipitation_probability_max'))}%)",
                         f"{fmt(r.get('wind_speed_10m_max'))}/{fmt(r.get('wind_gusts_10m_max'))} "
                         f"{r.get('wind_from') or ''}".strip(),
                         fmt(r.get("uv_index_max"), 1), sun(r)]
                        for r in d["daily"]])
        if d["hourly"]:
            lines += ["", "hourly:"] + hourly_table(d["hourly"], True)
        model = d["model"] + (" (Open-Meteo's regional blend of national models)" if d["model"] == "best_match" else "")
        lines += ["", f"model: {model} · {d['attribution']} · free tier for non-commercial use"]
    elif d["kind"] == "history":
        s = d["summary"]
        wet = s["wet_days"]
        lines.append(f"{d['from']} to {d['to']}: mean max {fmt(s['mean_max'], 1)}{t_unit}, mean min "
                     f"{fmt(s['mean_min'], 1)}{t_unit}, precipitation {fmt(s['precipitation_total'], pd)} {p_unit} "
                     f"({'–' if wet is None else wet} wet day{'' if wet == 1 else 's'})")
        if s["hottest"]:
            lines.append(f"hottest {s['hottest']['date']} ({fmt(s['hottest']['temperature_2m_max'], 1)}{t_unit}), "
                         f"coldest {s['coldest']['date']} ({fmt(s['coldest']['temperature_2m_min'], 1)}{t_unit})")
        lines.append("")
        if d.get("monthly"):
            lines += table(["month", f"mean max {t_unit}", f"mean min {t_unit}", f"precip {p_unit}", "wet days"],
                           [[m["month"], fmt(m["mean_max"], 1), fmt(m["mean_min"], 1),
                             fmt(m["precipitation_total"], pd), str(m["wet_days"] if m["wet_days"] is not None else "–")]
                            for m in d["monthly"]])
        else:
            lines += table(["date", "weather", f"min/max {t_unit}", f"precip {p_unit}", f"wind max {w_unit}", "sun h"],
                           [[day_label(r["date"]), r.get("weather") or "–",
                             f"{fmt(r.get('temperature_2m_min'), 1)} / {fmt(r.get('temperature_2m_max'), 1)}",
                             fmt(r.get("precipitation_sum"), pd), fmt(r.get("wind_speed_10m_max")),
                             fmt(r.get("sunshine_hours"), 1)] for r in d["daily"]])
        if d.get("hourly"):
            lines += ["", "hourly:"] + hourly_table(d["hourly"], False)
        if d.get("note"):
            lines += ["", f"note: {d['note']}"]
        lines += ["", f"{d['dataset']} · {d['attribution']}"]
    else:
        c = d["current"]
        lines.append(f"now ({hm(c.get('time'))}): European AQI {fmt(c.get('european_aqi'))} "
                     f"({c.get('european_aqi_level') or '–'}) · US AQI {fmt(c.get('us_aqi'))} "
                     f"({c.get('us_aqi_level') or '–'})")
        lines.append(f"PM2.5 {fmt(c.get('pm2_5'), 1)} · PM10 {fmt(c.get('pm10'), 1)} · ozone {fmt(c.get('ozone'))} · "
                     f"NO2 {fmt(c.get('nitrogen_dioxide'), 1)} · SO2 {fmt(c.get('sulphur_dioxide'), 1)} μg/m³ · "
                     f"UV {fmt(c.get('uv_index'), 1)}")
        if d.get("pollen"):
            lines.append("pollen (grains/m³): " + ", ".join(f"{k} {fmt(v)}" for k, v in d["pollen"].items()))
        if d["daily"]:
            lines.append("")
            lines += table(["date", "European AQI max", "US AQI max", "PM2.5 max"],
                           [[day_label(r["date"]), f"{fmt(r.get('european_aqi_max'))} ({r.get('european_aqi_level') or '–'})",
                             f"{fmt(r.get('us_aqi_max'))} ({r.get('us_aqi_level') or '–'})", fmt(r.get("pm2_5_max"), 1)]
                            for r in d["daily"]])
        lines += ["", f"model: {d['model']} · {d['attribution']}"]
    if d["place"].get("source") == OSM:
        lines.append(OSM)
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------ main

def make_parser(command: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"weather.py{'' if command == 'forecast' else ' ' + command}", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("place", nargs="+", help='a place ("Berlin", "Paris, Texas", "Eiffel Tower") or lat,lon')
    if command == "forecast":
        p.add_argument("--days", type=int, default=7, help="days of forecast, 1-16 (default 7)")
        p.add_argument("--hourly", action="store_true", help="also hour by hour (the next --hours)")
        p.add_argument("--hours", type=int, help="hours shown hour by hour, 1-384 (default 24; implies --hourly)")
        p.add_argument("--model", default="best_match",
                       help="weather model: best_match (default), ecmwf_ifs025, icon_seamless, gfs_seamless, "
                            "meteofrance_seamless, ukmo_seamless, jma_seamless, gem_seamless...")
    elif command == "history":
        p.add_argument("--from", dest="date_from", required=True, help="first day, YYYY-MM-DD (1940 onwards)")
        p.add_argument("--to", dest="date_to", help="last day, YYYY-MM-DD (default: the --from day)")
        p.add_argument("--hourly", action="store_true", help="also hour by hour (ranges up to 7 days)")
    else:
        p.add_argument("--days", type=int, default=3, help="days of air-quality forecast, 1-7 (default 3)")
    if command != "air":  # air quality has no imperial variant (μg/m³ and AQI everywhere)
        p.add_argument("--units", choices=["metric", "imperial"], default="metric",
                       help="metric (°C, km/h, mm; default) or imperial (°F, mph, inch)")
    p.add_argument("--lang", default="en", help="language of place names (default en)")
    p.add_argument("--country", help="two-letter country code to look the place up in (e.g. US)")
    p.add_argument("-f", "--format", choices=["table", "markdown", "json"], default="table")
    p.add_argument("-o", "--output", metavar="FILE", help="write to FILE instead of stdout")
    p.add_argument("--timeout", type=float, default=30.0, help="network timeout in seconds (default 30)")
    p.add_argument("-q", "--quiet", action="store_true", help="no notes on stderr")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv.pop(0) if argv and argv[0] in ("forecast", "history", "air") else "forecast"
    # "-33.87,151.21" would be taken for an option: a leading space keeps it a place (parse_coords strips it).
    argv = [" " + a if re.fullmatch(r"-\d{1,3}(?:[.,]\d+)?\s*[,;\s].*", a) else a for a in argv]
    args = make_parser(command).parse_args(argv)
    note = (lambda m: None) if args.quiet else (lambda m: sys.stderr.write(f"note: {safe(m)}\n"))

    def fail(code: int, message: str, hint: str = "") -> int:
        sys.stderr.write(f"error: {safe(message)}" + (f" — {safe(hint)}" if hint else "") + "\n")
        return code

    place = " ".join(" ".join(args.place).split())
    if not place:
        return fail(EXIT_BAD_INPUT, "the place is empty", "give a place name or lat,lon")
    if not 1 <= args.timeout <= 300:
        return fail(EXIT_BAD_INPUT, "--timeout must be between 1 and 300 seconds")
    if command == "forecast" and not (1 <= args.days <= 16 and (args.hours is None or 1 <= args.hours <= 384)):
        return fail(EXIT_BAD_INPUT, "--days must be 1-16 and --hours 1-384")
    if command == "forecast" and not re.fullmatch(r"[a-z0-9_]+", args.model):
        return fail(EXIT_BAD_INPUT, f"--model {args.model!r} is not a model name", "e.g. best_match, ecmwf_ifs025")
    if command == "air" and not 1 <= args.days <= 7:
        return fail(EXIT_BAD_INPUT, "--days must be 1-7 for air quality")
    if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z]{2,4})?", args.lang):
        return fail(EXIT_BAD_INPUT, f"--lang {args.lang!r} is not a language code", "e.g. en, de, ru")
    if args.country and not re.fullmatch(r"[A-Za-z]{2}", args.country):
        return fail(EXIT_BAD_INPUT, f"--country {args.country!r} is not a two-letter code", "e.g. US, DE, FR")
    net = Net(args.timeout, note)
    try:
        where = resolve(net, place, args.lang, args.country, note)
        data = {"forecast": cmd_forecast, "history": cmd_history, "air": cmd_air}[command](net, args, where)
    except Failure as e:
        return fail(e.code, str(e), e.hint)
    text = render(data, args)
    if args.output:
        try:
            with open(os.path.expanduser(args.output), "w", encoding="utf-8") as fh:
                fh.write(text)
            return EXIT_OK
        except OSError as e:
            sys.stdout.write(text)
            return fail(EXIT_BAD_INPUT, f"cannot write {args.output}: {e.strerror or e}", "the output went to stdout")
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("error: interrupted\n")
        sys.exit(130)
    except BrokenPipeError as e:
        try:
            sys.stdout.flush()
        except BrokenPipeError:  # the reader closed stdout (`| head`): stop quietly, as a pipe expects
            try:
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            except OSError:
                pass
            sys.exit(0)
        sys.stderr.write(f"error: unexpected BrokenPipeError: {e} — a bug; report it with the command line\n")
        sys.exit(EXIT_API)
    except Exception as e:  # noqa: BLE001 - exit 1 means "place not found"; a crash must not look like that
        sys.stderr.write(f"error: unexpected {type(e).__name__}: {(str(e).splitlines() or [''])[0][:300]} "
                         "— a bug; report it with the command line\n")
        sys.exit(EXIT_API)

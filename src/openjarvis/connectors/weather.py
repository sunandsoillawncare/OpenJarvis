"""Weather connector — current conditions and forecast via OpenWeatherMap API.

Uses an API key stored in the connector config dir.
All API calls are in module-level functions for easy mocking in tests.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import httpx

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ConnectorRegistry

_DEFAULT_TOKEN_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "weather.json")
# OpenWeatherMap free tier refreshes data every 10 minutes.
_CACHE_TTL = timedelta(minutes=10)
_UNIT_LABELS: Dict[str, Tuple[str, str]] = {
    "imperial": ("°F", "mph"),
    "metric": ("°C", "m/s"),
    "standard": ("K", "m/s"),
}


class WeatherConnectorError(RuntimeError):
    """Raised when an OpenWeatherMap API call fails with a known error."""


def _weather_api_get(url: str, params: Dict[str, str]) -> Dict[str, Any]:
    """Call an OpenWeatherMap API endpoint."""
    try:
        resp = httpx.get(url, params=params, timeout=30.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        location = params.get("q", "unknown")
        status = exc.response.status_code
        if status == 401:
            raise WeatherConnectorError(
                "Invalid API key — check the api_key in your weather.json config"
            ) from exc
        if status == 404:
            raise WeatherConnectorError(
                f"Location not found: {location!r} — check the location in your weather.json config"
            ) from exc
        raise WeatherConnectorError(
            f"OpenWeatherMap request failed for {location!r}: HTTP {status}"
        ) from exc


def _load_location_cache(
    cache_path: Path, location: str
) -> Optional[Dict[str, Any]]:
    """Return cached responses for a location if still within TTL, else None."""
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        entry = data.get("locations", {}).get(location)
        if entry is None:
            return None
        fetched_at = datetime.fromisoformat(entry["fetched_at"])
        if datetime.now() - fetched_at < _CACHE_TTL:
            return entry
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        pass
    return None


def _save_location_cache(
    cache_path: Path,
    location: str,
    current: Dict[str, Any],
    forecast: Dict[str, Any],
) -> None:
    """Persist API responses for one location alongside any other cached locations."""
    try:
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        else:
            data = {}
        data.setdefault("locations", {})[location] = {
            "fetched_at": datetime.now().isoformat(),
            "current": current,
            "forecast": forecast,
        }
        cache_path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass  # cache write failure is non-fatal


@ConnectorRegistry.register("weather")
class WeatherConnector(BaseConnector):
    """Fetch current weather and short-term forecast from OpenWeatherMap."""

    connector_id = "weather"
    display_name = "Weather"
    auth_type = "token"

    def __init__(self, *, token_path: str = _DEFAULT_TOKEN_PATH) -> None:
        self._token_path = Path(token_path)
        self._cache_path = self._token_path.with_name("weather_cache.json")
        self._status = SyncStatus()

    def _load_config(self) -> Dict[str, Any]:
        """Load API key, location(s), and units from disk."""
        data = json.loads(self._token_path.read_text(encoding="utf-8"))
        return data

    def is_connected(self) -> bool:
        if not self._token_path.exists():
            return False
        try:
            data = json.loads(self._token_path.read_text(encoding="utf-8"))
            return bool(data.get("api_key"))
        except (json.JSONDecodeError, OSError):
            return False

    def disconnect(self) -> None:
        if self._token_path.exists():
            self._token_path.unlink()

    def sync(
        self, *, since: Optional[datetime] = None, cursor: Optional[str] = None
    ) -> Iterator[Document]:
        """Yield current-weather and forecast Documents for each configured location."""
        config = self._load_config()
        api_key = config["api_key"]

        locations_raw = config.get("location", "San Francisco,CA")
        locations: List[str] = (
            [locations_raw] if isinstance(locations_raw, str) else list(locations_raw)
        )

        units = config.get("units", "imperial")
        temp_unit, wind_unit = _UNIT_LABELS.get(units, _UNIT_LABELS["imperial"])

        for location in locations:
            cached = _load_location_cache(self._cache_path, location)
            if cached is not None:
                current = cached["current"]
                forecast = cached["forecast"]
            else:
                current = _weather_api_get(
                    "https://api.openweathermap.org/data/2.5/weather",
                    params={"q": location, "appid": api_key, "units": units},
                )
                forecast = _weather_api_get(
                    "https://api.openweathermap.org/data/2.5/forecast",
                    params={
                        "q": location,
                        "appid": api_key,
                        "units": units,
                        "cnt": "4",
                    },
                )
                _save_location_cache(self._cache_path, location, current, forecast)

            main = current.get("main", {})
            weather_desc = ", ".join(
                w.get("description", "") for w in current.get("weather", [])
            )
            content = (
                f"Temperature: {main.get('temp')}{temp_unit}, "
                f"Conditions: {weather_desc}, "
                f"Humidity: {main.get('humidity')}%, "
                f"Wind: {current.get('wind', {}).get('speed')} {wind_unit}"
            )
            yield Document(
                doc_id=f"weather-current-{location}",
                source="weather",
                doc_type="current",
                content=content,
                title=f"Current Weather — {location}",
                timestamp=datetime.now(),
                metadata={
                    "location": location,
                    "temp": main.get("temp"),
                    "conditions": weather_desc,
                    "humidity": main.get("humidity"),
                    "wind_speed": current.get("wind", {}).get("speed"),
                },
            )

            summaries = []
            for entry in forecast.get("list", []):
                dt_txt = entry.get("dt_txt", "")
                temp = entry.get("main", {}).get("temp")
                desc = ", ".join(
                    w.get("description", "") for w in entry.get("weather", [])
                )
                summaries.append(f"{dt_txt}: {temp}{temp_unit}, {desc}")
            forecast_content = "Forecast:\n" + "\n".join(summaries)

            yield Document(
                doc_id=f"weather-forecast-{location}",
                source="weather",
                doc_type="forecast",
                content=forecast_content,
                title=f"Weather Forecast — {location}",
                timestamp=datetime.now(),
                metadata={"location": location},
            )

        self._status.state = "idle"
        self._status.last_sync = datetime.now()

    def sync_status(self) -> SyncStatus:
        return self._status

"""Tests for WeatherConnector — OpenWeatherMap API."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import httpx
import pytest

from openjarvis.connectors._stubs import Document
from openjarvis.core.registry import ConnectorRegistry


def test_weather_registered():
    """WeatherConnector is discoverable via ConnectorRegistry."""
    from openjarvis.connectors.weather import WeatherConnector

    ConnectorRegistry.register_value("weather", WeatherConnector)
    assert ConnectorRegistry.contains("weather")
    cls = ConnectorRegistry.get("weather")
    assert cls.connector_id == "weather"
    assert cls.display_name == "Weather"
    assert cls.auth_type == "token"


_CURRENT_RESPONSE = {
    "main": {"temp": 62.5, "humidity": 55},
    "weather": [{"description": "clear sky"}],
    "wind": {"speed": 8.2},
}

_FORECAST_RESPONSE = {
    "list": [
        {
            "dt_txt": "2026-04-02 12:00:00",
            "main": {"temp": 64.0},
            "weather": [{"description": "few clouds"}],
        },
        {
            "dt_txt": "2026-04-02 15:00:00",
            "main": {"temp": 66.0},
            "weather": [{"description": "scattered clouds"}],
        },
    ],
}


@pytest.fixture()
def connector(tmp_path):
    """WeatherConnector with fake config file."""
    from openjarvis.connectors.weather import WeatherConnector

    config_path = tmp_path / "weather.json"
    config_path.write_text(
        '{"api_key": "fake-key", "location": "San Francisco,CA"}',
        encoding="utf-8",
    )
    return WeatherConnector(token_path=str(config_path))


def test_is_connected(connector):
    assert connector.is_connected() is True


def test_is_connected_no_file(tmp_path):
    from openjarvis.connectors.weather import WeatherConnector

    c = WeatherConnector(token_path=str(tmp_path / "missing.json"))
    assert c.is_connected() is False


def test_sync_yields_two_documents(connector):
    """Sync returns one current weather and one forecast Document."""
    with patch(
        "openjarvis.connectors.weather._weather_api_get",
        side_effect=[_CURRENT_RESPONSE, _FORECAST_RESPONSE],
    ):
        docs = list(connector.sync())

    assert len(docs) == 2
    assert all(isinstance(d, Document) for d in docs)

    current = docs[0]
    assert current.source == "weather"
    assert current.doc_type == "current"
    assert "62.5" in current.content
    assert "clear sky" in current.content
    assert "55" in current.content

    forecast = docs[1]
    assert forecast.doc_type == "forecast"
    assert "64.0" in forecast.content


def test_disconnect(connector):
    connector.disconnect()
    assert connector.is_connected() is False


# --- cache tests ---

def test_sync_writes_cache(connector):
    """After a live fetch the cache file is written keyed by location."""
    with patch(
        "openjarvis.connectors.weather._weather_api_get",
        side_effect=[_CURRENT_RESPONSE, _FORECAST_RESPONSE],
    ):
        list(connector.sync())

    assert connector._cache_path.exists()
    data = json.loads(connector._cache_path.read_text())
    loc = data["locations"]["San Francisco,CA"]
    assert "fetched_at" in loc
    assert loc["current"] == _CURRENT_RESPONSE
    assert loc["forecast"] == _FORECAST_RESPONSE


def test_sync_uses_fresh_cache(connector):
    """A fresh cache skips all HTTP calls entirely."""
    cache_data = {
        "locations": {
            "San Francisco,CA": {
                "fetched_at": datetime.now().isoformat(),
                "current": _CURRENT_RESPONSE,
                "forecast": _FORECAST_RESPONSE,
            }
        }
    }
    connector._cache_path.write_text(json.dumps(cache_data), encoding="utf-8")

    with patch("openjarvis.connectors.weather._weather_api_get") as mock_get:
        docs = list(connector.sync())

    mock_get.assert_not_called()
    assert len(docs) == 2


def test_sync_bypasses_stale_cache(connector):
    """A cache older than 10 minutes triggers a live fetch."""
    stale_time = (datetime.now() - timedelta(minutes=11)).isoformat()
    cache_data = {
        "locations": {
            "San Francisco,CA": {
                "fetched_at": stale_time,
                "current": _CURRENT_RESPONSE,
                "forecast": _FORECAST_RESPONSE,
            }
        }
    }
    connector._cache_path.write_text(json.dumps(cache_data), encoding="utf-8")

    with patch(
        "openjarvis.connectors.weather._weather_api_get",
        side_effect=[_CURRENT_RESPONSE, _FORECAST_RESPONSE],
    ) as mock_get:
        list(connector.sync())

    assert mock_get.call_count == 2


# --- error handling tests (#2) ---

def _make_http_error(status_code: int, url: str = "https://api.openweathermap.org") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


def test_api_get_raises_on_401():
    """401 from OWM raises WeatherConnectorError with an API key hint."""
    from openjarvis.connectors.weather import WeatherConnectorError, _weather_api_get

    with patch("httpx.get", side_effect=_make_http_error(401)):
        with pytest.raises(WeatherConnectorError, match="Invalid API key"):
            _weather_api_get("https://api.openweathermap.org/data/2.5/weather", {"q": "Paris,FR", "appid": "bad"})


def test_api_get_raises_on_404():
    """404 from OWM raises WeatherConnectorError naming the bad location."""
    from openjarvis.connectors.weather import WeatherConnectorError, _weather_api_get

    with patch("httpx.get", side_effect=_make_http_error(404)):
        with pytest.raises(WeatherConnectorError, match="Location not found.*Nowhereland"):
            _weather_api_get("https://api.openweathermap.org/data/2.5/weather", {"q": "Nowhereland", "appid": "key"})


def test_api_get_raises_on_generic_http_error():
    """Unexpected HTTP status raises WeatherConnectorError with the status code."""
    from openjarvis.connectors.weather import WeatherConnectorError, _weather_api_get

    with patch("httpx.get", side_effect=_make_http_error(503)):
        with pytest.raises(WeatherConnectorError, match="HTTP 503"):
            _weather_api_get("https://api.openweathermap.org/data/2.5/weather", {"q": "Tokyo,JP", "appid": "key"})


# --- units and multi-location tests (#3) ---

def test_sync_imperial_units(connector):
    """Default imperial config produces °F and mph labels."""
    with patch(
        "openjarvis.connectors.weather._weather_api_get",
        side_effect=[_CURRENT_RESPONSE, _FORECAST_RESPONSE],
    ):
        docs = list(connector.sync())

    assert "°F" in docs[0].content
    assert "mph" in docs[0].content
    assert "°F" in docs[1].content


def test_sync_metric_units(tmp_path):
    """Metric config produces °C and m/s labels."""
    from openjarvis.connectors.weather import WeatherConnector

    config_path = tmp_path / "weather.json"
    config_path.write_text(
        '{"api_key": "fake-key", "location": "Paris,FR", "units": "metric"}',
        encoding="utf-8",
    )
    connector = WeatherConnector(token_path=str(config_path))

    with patch(
        "openjarvis.connectors.weather._weather_api_get",
        side_effect=[_CURRENT_RESPONSE, _FORECAST_RESPONSE],
    ):
        docs = list(connector.sync())

    assert "°C" in docs[0].content
    assert "m/s" in docs[0].content
    assert "°C" in docs[1].content


def test_sync_multi_location(tmp_path):
    """A location list yields two Documents per location."""
    from openjarvis.connectors.weather import WeatherConnector

    config_path = tmp_path / "weather.json"
    config_path.write_text(
        json.dumps({
            "api_key": "fake-key",
            "location": ["San Francisco,CA", "New York,NY"],
        }),
        encoding="utf-8",
    )
    connector = WeatherConnector(token_path=str(config_path))

    with patch(
        "openjarvis.connectors.weather._weather_api_get",
        side_effect=[
            _CURRENT_RESPONSE, _FORECAST_RESPONSE,
            _CURRENT_RESPONSE, _FORECAST_RESPONSE,
        ],
    ):
        docs = list(connector.sync())

    assert len(docs) == 4
    doc_ids = {d.doc_id for d in docs}
    assert "weather-current-San Francisco,CA" in doc_ids
    assert "weather-forecast-San Francisco,CA" in doc_ids
    assert "weather-current-New York,NY" in doc_ids
    assert "weather-forecast-New York,NY" in doc_ids


def test_sync_multi_location_caches_independently(tmp_path):
    """Each location's cache entry is independent — a fresh entry skips its fetch."""
    from openjarvis.connectors.weather import WeatherConnector

    config_path = tmp_path / "weather.json"
    config_path.write_text(
        json.dumps({
            "api_key": "fake-key",
            "location": ["San Francisco,CA", "New York,NY"],
        }),
        encoding="utf-8",
    )
    connector = WeatherConnector(token_path=str(config_path))

    # Pre-populate cache only for San Francisco
    cache_data = {
        "locations": {
            "San Francisco,CA": {
                "fetched_at": datetime.now().isoformat(),
                "current": _CURRENT_RESPONSE,
                "forecast": _FORECAST_RESPONSE,
            }
        }
    }
    connector._cache_path.write_text(json.dumps(cache_data), encoding="utf-8")

    with patch(
        "openjarvis.connectors.weather._weather_api_get",
        side_effect=[_CURRENT_RESPONSE, _FORECAST_RESPONSE],
    ) as mock_get:
        docs = list(connector.sync())

    # Only New York triggers live fetches
    assert mock_get.call_count == 2
    assert len(docs) == 4

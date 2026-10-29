from __future__ import annotations

from typing import Any

from se_mcp import forecast
from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame, default_handlers


def test_battery_and_reactor_forecasts():
    g = {"power": {"batteries": {"count": 2, "storedMWh": 6, "maxMWh": 12, "fill": 50, "inputMW": 0, "outputMW": 3},
                   "producers": [{"type": "Reactor", "outputMW": 10, "maxMW": 300}]}}
    assert forecast.batteries(g).endswith("draining 3.00 MW: empty in 2.0 h")
    assert "lasts 30 min at this output" in forecast.reactors(g, 5)  # 5 kg / 10 MWh per hour
    charging = {"power": {"batteries": {**g["power"]["batteries"], "inputMW": 4, "outputMW": 1}}}
    assert forecast.batteries(charging).endswith("charging 3.00 MW: full in 2.0 h")


def test_gas_rate_from_two_samples():
    before = {"count": 1, "fill": 50, "storedL": 50_000, "capacityL": 100_000}
    after = {"count": 1, "fill": 49, "storedL": 49_000, "capacityL": 100_000}
    assert forecast.gas("Hydrogen", before, after, 10).endswith("draining 100 L/s: empty in 8 min")
    assert forecast.gas("Oxygen", after, after, 10).endswith("steady")


def test_suit_drain():
    assert forecast.suit({"oxygen": 50}, {"oxygen": 49}, 10)[0] == "Suit oxygen: 49%, falling: empty in 8 min"


PLANET_ENV = {
    "position": [0, 0, 0], "naturalGravityG": 0.42, "artificialGravityG": 0, "gravityDirection": [0, -1, 0],
    "planet": {"name": "EarthLike", "distanceToCenterKm": 62, "altitude": 1520.4, "radiusKm": 60, "surfaceGravityG": 1,
               "hasAtmosphere": True, "atmosphereTopAltitude": 8000, "inAtmosphere": True, "airDensity": 0.71,
               "oxygen": 0.65, "sunElevation": -12.0},
    "weather": {"name": "RainHeavy", "intensity": 0.8, "solar": 0.3, "wind": 1.5, "oxygen": 1},
    "sunDirection": [0, 1, 0], "dayLengthMinutes": 120,
}


async def test_environment_on_a_planet():
    h = default_handlers()
    h["environment.get"] = lambda p: PLANET_ENV
    async with FakeGame(h) as game:
        bridge = Bridge(port=game.port, token=TOKEN)
        try:
            result = await create_server(bridge).call_tool("environment", {})
        finally:
            await bridge.close()
    text = "\n".join(c.text for c in result.content)
    assert "Altitude 1,520 m, in atmosphere (air density 0.71, oxygen 65%)" in text
    assert "(night)" in text and "Weather: RainHeavy" in text

# pylint: disable=protected-access, unused-argument
"""Regression tests for #2194: the minimal deactivation delay must be honoured
without losing the ON phase of the cycle.

Home Assistant re-stamps last_changed when it restarts, so a relay looks as if it
had just been switched off. With a 60 min cycle, minimal_deactivation_delay=600 s and
minimal_activation_delay=899 s, an ON phase of 1224 s postponed by that delay must
start later and last 1224 s.
"""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.versatile_thermostat.cycle_scheduler import CycleScheduler
from custom_components.versatile_thermostat.cycle_tick_logic import keep_on_duration_when_postponed
from custom_components.versatile_thermostat.vtherm_hvac_mode import VThermHvacMode_HEAT

from .test_cycle_scheduler import make_hass, make_thermostat, make_underlying

CYCLE_SEC = 3600
MIN_ACTIVATION = 899
MIN_DEACTIVATION = 600
ON_PERCENT = 0.34
ON_TIME = 1224


async def make_relay(hass, last_change_ago: float, active: bool = False):
    """Build a real switch whose last change HA shows is `last_change_ago` seconds old."""
    return await make_underlying(hass, "R1", active=active, last_change_ago=last_change_ago)


def make_scheduler(relay):
    """Build a scheduler with the configuration of the issue."""
    return CycleScheduler(
        make_hass(),
        make_thermostat(),
        [relay],
        CYCLE_SEC,
        min_activation_delay=MIN_ACTIVATION,
        min_deactivation_delay=MIN_DEACTIVATION,
    )


@patch("custom_components.versatile_thermostat.cycle_scheduler.async_call_later", return_value=MagicMock())
async def test_restart_keeps_the_whole_on_phase(mock_call_later, freezer, hass):
    """A relay re-stamped 39 s before the first cycle starts later and runs its full ON time."""
    freezer.move_to("2026-11-06T07:34:10Z")
    relay = await make_relay(hass, 39)
    scheduler = make_scheduler(relay)

    await scheduler.start_cycle(VThermHvacMode_HEAT, ON_PERCENT, force=True)

    relay.turn_on.assert_not_called()
    assert scheduler._states[0].on_t == pytest.approx(561)
    assert scheduler._states[0].off_t == pytest.approx(561 + ON_TIME)
    assert scheduler._penalty == pytest.approx(0)

    freezer.tick(561)
    await scheduler._tick()
    relay.turn_on.assert_called_once()

    freezer.tick(ON_TIME - 1)
    await scheduler._tick()
    relay.turn_off.assert_not_called()

    freezer.tick(1)
    await scheduler._tick()
    relay.turn_off.assert_called_once()


@patch("custom_components.versatile_thermostat.cycle_scheduler.async_call_later", return_value=MagicMock())
async def test_reload_after_a_real_stop_honours_min_deactivation(mock_call_later, freezer, hass):
    """A new scheduler (entry reload) keeps the OFF delay of a relay really stopped 30 s ago."""
    freezer.move_to("2026-11-06T07:34:10Z")
    relay = await make_relay(hass, 30)
    scheduler = make_scheduler(relay)

    await scheduler.start_cycle(VThermHvacMode_HEAT, ON_PERCENT, force=True)
    relay.turn_on.assert_not_called()

    freezer.tick(569)
    await scheduler._tick()
    relay.turn_on.assert_not_called()

    freezer.tick(1)
    await scheduler._tick()
    relay.turn_on.assert_called_once()
    assert scheduler._states[0].off_t == pytest.approx(570 + ON_TIME, abs=1)


@patch("custom_components.versatile_thermostat.cycle_scheduler.async_call_later", return_value=MagicMock())
async def test_off_commanded_by_vtherm_honours_min_deactivation(mock_call_later, freezer, hass):
    """A relay switched off through the VTherm OFF command keeps the OFF delay on the next cycle."""
    freezer.move_to("2026-11-06T07:34:10Z")
    relay = await make_relay(hass, 3600)
    scheduler = make_scheduler(relay)
    await scheduler.start_cycle(VThermHvacMode_HEAT, ON_PERCENT, force=True)
    relay.turn_on.assert_called_once()

    freezer.tick(ON_TIME)
    await scheduler._tick()
    relay.turn_off.assert_called_once()
    assert relay.is_device_active is False

    relay.turn_on.reset_mock()
    freezer.tick(60)
    await scheduler.start_cycle(VThermHvacMode_HEAT, ON_PERCENT, force=True)
    relay.turn_on.assert_not_called()
    assert scheduler._states[0].on_t == pytest.approx(MIN_DEACTIVATION - 60)

    freezer.tick(MIN_DEACTIVATION - 60)
    await scheduler._tick()
    relay.turn_on.assert_called_once()


@pytest.mark.parametrize(
    ("on_t", "off_t", "new_on_t", "penalty_in", "expected"),
    [
        (0, 1224, 561, 561, (1785, 0)),
        (0, 3200, 600, 600, (3600, 200)),
        (2700, 900, 2800, 100, (900, 100)),
        (0, 1224, 3600, 1224, (1224, 1224)),
    ],
    ids=["shifted", "clipped_at_cycle_end", "wrapped_window", "start_outside_cycle"],
)
def test_keep_on_duration_when_postponed(on_t, off_t, new_on_t, penalty_in, expected):
    """The end of the ON window follows its start, within the cycle."""
    assert keep_on_duration_when_postponed(on_t, off_t, new_on_t, CYCLE_SEC, penalty_in) == pytest.approx(expected)

import datetime
import zoneinfo

import pytest
from gpiozero import Device, PWMLED
from gpiozero.pins.mock import MockFactory, MockPWMPin

import ir_light_daemon


@pytest.fixture(autouse=True)
def mock_gpio():
    Device.pin_factory = MockFactory(pin_class=MockPWMPin)
    yield
    Device.pin_factory.reset()


JST = zoneinfo.ZoneInfo("Asia/Tokyo")


def _dt(hour, minute=0):
    return datetime.datetime(2026, 6, 1, hour, minute, tzinfo=JST)


@pytest.mark.parametrize(
    "now,expected",
    [
        (_dt(3, 0), 0.3),
        (_dt(12, 0), 0.0),
        (_dt(23, 0), 0.3),
    ],
)
def test_compute_brightness(now, expected):
    sunrise = _dt(5, 0)
    sunset = _dt(19, 0)
    assert (
        ir_light_daemon.compute_brightness(now, sunrise, sunset, night_brightness=0.3)
        == expected
    )


def test_compute_brightness_boundary_is_night():
    sunrise = _dt(5, 0)
    sunset = _dt(19, 0)
    assert (
        ir_light_daemon.compute_brightness(sunset, sunrise, sunset, night_brightness=0.3)
        == 0.3
    )
    assert (
        ir_light_daemon.compute_brightness(sunrise, sunrise, sunset, night_brightness=0.3)
        == 0.0
    )


def test_pwmled_value_reflects_set_value():
    led = PWMLED(18)
    led.value = 0.3
    assert led.value == pytest.approx(0.3)
    led.value = 0.0
    assert led.value == 0.0
    led.close()

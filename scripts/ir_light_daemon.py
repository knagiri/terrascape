#!/usr/bin/env python3
"""IR ライトを日没〜日の出の間だけ PWM 点灯させる常駐デーモン。"""

import datetime
import os
import time
import zoneinfo

from astral import LocationInfo
from astral.sun import sun
from gpiozero import PWMLED

POLL_INTERVAL_SECONDS = 60


def load_config():
    return {
        "gpio_pin": int(os.environ["IR_LIGHT_GPIO_PIN"]),
        "latitude": float(os.environ["IR_LIGHT_LATITUDE"]),
        "longitude": float(os.environ["IR_LIGHT_LONGITUDE"]),
        "timezone": os.environ["IR_LIGHT_TIMEZONE"],
        "brightness": float(os.environ["IR_LIGHT_BRIGHTNESS"]),
    }


def compute_brightness(now, sunrise, sunset, night_brightness):
    """now が日没〜日の出の間なら night_brightness、それ以外は 0.0 を返す。"""
    is_night = now < sunrise or now >= sunset
    return night_brightness if is_night else 0.0


def today_sun_times(latitude, longitude, tz):
    location = LocationInfo(latitude=latitude, longitude=longitude, timezone=str(tz))
    s = sun(location.observer, date=datetime.datetime.now(tz).date(), tzinfo=tz)
    return s["sunrise"], s["sunset"]


def main():
    config = load_config()
    tz = zoneinfo.ZoneInfo(config["timezone"])
    led = PWMLED(config["gpio_pin"])

    try:
        while True:
            now = datetime.datetime.now(tz)
            sunrise, sunset = today_sun_times(config["latitude"], config["longitude"], tz)
            led.value = compute_brightness(now, sunrise, sunset, config["brightness"])
            time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        led.close()


if __name__ == "__main__":
    main()

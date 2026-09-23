import datetime
import signal
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


def test_main_turns_led_off_and_closes_on_sigterm(monkeypatch):
    """systemctl stop 相当の SIGTERM でも main() の finally (led off + close) が走ることを確認する。

    time.sleep を、その時点で SIGTERM に登録されているハンドラを呼び出す差し替えにして
    main() のループを1周だけ回し、signal 到来を模す。main() が
    signal.signal(SIGTERM, ...) を登録していなければ、signal.getsignal(SIGTERM) は
    callable でない既定値 (SIG_DFL) を返すため呼び出しが TypeError になり、
    このテストはガード無しでは落ちる（判別力の確認）。

    compute_brightness を昼夜に関わらず常に 0.3 (点灯) を返すよう monkeypatch する。
    実時刻依存のままだと、テストを昼間に実行した場合 compute_brightness が 0.0 を返し、
    led.value は最初から 0.0 のままになる。その場合 finally から led.off() を削除しても
    アサーションが偶然通ってしまい、実行時刻次第でガードが空振りする
    （実測: led.off() を外した変異でテストが落ちることを確認済み）。
    """
    monkeypatch.setenv("IR_LIGHT_GPIO_PIN", "18")
    monkeypatch.setenv("IR_LIGHT_LATITUDE", "35.0")
    monkeypatch.setenv("IR_LIGHT_LONGITUDE", "139.0")
    monkeypatch.setenv("IR_LIGHT_TIMEZONE", "Asia/Tokyo")
    monkeypatch.setenv("IR_LIGHT_BRIGHTNESS", "0.3")

    original_sigterm_handler = signal.getsignal(signal.SIGTERM)
    state = {}
    original_pwmled = ir_light_daemon.PWMLED

    def _tracking_pwmled(pin):
        led = original_pwmled(pin)
        state["led"] = led
        original_close = led.close

        def _spy_close():
            # __del__ が GC 時に close() を再度呼ぶことがあるため、既に閉じていれば
            # value 読み出しをスキップする（閉じた後の value アクセスは例外になる）。
            if not led.closed:
                state["value_before_close"] = led.value
            original_close()

        led.close = _spy_close
        return led

    def _deliver_sigterm(_seconds):
        handler = signal.getsignal(signal.SIGTERM)
        handler(signal.SIGTERM, None)

    monkeypatch.setattr(ir_light_daemon, "PWMLED", _tracking_pwmled)
    monkeypatch.setattr(ir_light_daemon.time, "sleep", _deliver_sigterm)
    # 実行時刻に関わらず点灯状態を強制する（上のテスト docstring 参照）。
    monkeypatch.setattr(ir_light_daemon, "compute_brightness", lambda *a, **k: 0.3)

    try:
        with pytest.raises(SystemExit):
            ir_light_daemon.main()
    finally:
        signal.signal(signal.SIGTERM, original_sigterm_handler)

    led = state["led"]
    # led.off() が close() の前に効いて 0.0 に戻っていることを検証する
    # （compute_brightness を 0.3 固定にしたので、off() が無ければここは 0.3 のまま）。
    assert state["value_before_close"] == 0.0
    assert led.closed

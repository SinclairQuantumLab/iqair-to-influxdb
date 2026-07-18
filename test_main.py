from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from iqair_client import IQAirDevice, IQAirSample
import main as main_module
from main import (
    AppSettings,
    DryRunWriter,
    EmptyIQAirSampleError,
    IQAirInfluxCollector,
    build_influx_record,
    load_influx_config,
    load_settings,
)


def make_sample(
    *,
    fan_rpm: int | None = 805,
    pm1: int | None = 1,
    pm25: int | None = 2,
    pm10: int | None = 3,
    serial_number: str | None = "050S-B009-T080-1",
) -> IQAirSample:
    return IQAirSample(
        observed_at=datetime(2026, 7, 17, 12, 30, tzinfo=timezone.utc),
        mac_address="10:97:BD:09:3A:D2",
        serial_number=serial_number,
        fan_rpm=fan_rpm,
        pm1_ugm3=pm1,
        pm25_ugm3=pm25,
        pm10_ugm3=pm10,
    )


class FakeIQAirClient:
    def __init__(
        self,
        actions: list[IQAirSample | Exception],
        *,
        connected: bool,
    ) -> None:
        self.identity = IQAirDevice(
            mac_address="10:97:BD:09:3A:D2",
            serial_number="050S-B009-T080-1",
            product_name="IQAir HealthPro Plus XE",
            verified=True,
        )
        self._device = self.identity if connected else None
        self._connected = connected
        self.actions = list(actions)
        self.connect_count = 0
        self.reconnect_count = 0
        self.close_count = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def device(self) -> IQAirDevice | None:
        return self._device

    async def connect(self) -> None:
        self.connect_count += 1
        self._connected = True
        self._device = self.identity

    async def reconnect(self, *, rediscover_on_failure: bool = True) -> None:
        assert rediscover_on_failure
        self.reconnect_count += 1
        self._connected = True
        self._device = self.identity

    async def read_measurements(self) -> IQAirSample:
        if not self.actions:
            raise AssertionError("no fake measurement action remains")
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    async def close(self) -> None:
        self.close_count += 1
        self._connected = False


class FakeWriter:
    def __init__(self, *, write_error: Exception | None = None) -> None:
        self.records: list[dict[str, Any]] = []
        self.write_error = write_error
        self.close_count = 0

    async def write(self, record: dict[str, Any]) -> bool:
        if self.write_error is not None:
            raise self.write_error
        self.records.append(record)
        return True

    async def close(self) -> None:
        self.close_count += 1


async def no_sleep(_delay: float) -> None:
    return None


def test_load_settings_applies_defaults_and_relative_auth_path(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(
        'device_identifier = "050S-B009-T080-1"\n',
        encoding="utf-8",
    )

    settings = load_settings(settings_path)

    assert settings.interval_s == 30.0
    assert settings.reconnect_delay_s == 10.0
    assert settings.exception_threshold == 3
    assert settings.measurement == "IQAir"
    assert settings.pair_on_startup is True
    assert settings.auth_path == tmp_path / "imaq_config" / "auth.toml"


def test_load_settings_rejects_invalid_threshold(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(
        'device_identifier = "device"\nexception_threshold = 0\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exception_threshold must be positive"):
        load_settings(settings_path)


def test_load_influx_config_validates_and_separates_bucket(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.toml"
    auth_path.write_text(
        """[influxdb]
url = "http://localhost:8086"
token = "secret"
org = "lab"
bucket = "sensors"
timeout = 15000
""",
        encoding="utf-8",
    )

    config = load_influx_config(auth_path)

    assert config.org == "lab"
    assert config.bucket == "sensors"
    assert config.client_options["url"] == "http://localhost:8086"
    assert config.client_options["timeout"] == 15000
    assert "bucket" not in config.client_options


def test_build_influx_record_uses_exact_human_readable_schema() -> None:
    device = IQAirDevice(
        mac_address="10:97:BD:09:3A:D2",
        serial_number="050S-B009-T080-1",
        product_name="IQAir HealthPro Plus XE",
        verified=True,
    )

    record = build_influx_record(
        make_sample(serial_number=None),
        measurement="IQAir",
        device=device,
    )

    assert record == {
        "measurement": "IQAir",
        "tags": {
            "Serial number": "050S-B009-T080-1",
            "Product name": "IQAir HealthPro Plus XE",
            "MAC address": "10:97:BD:09:3A:D2",
            "Connection": "Bluetooth LE",
        },
        "fields": {
            "Fan speed [rpm]": 805,
            "PM1 [ug/m^3]": 1,
            "PM2.5 [ug/m^3]": 2,
            "PM10 [ug/m^3]": 3,
        },
        "time": datetime(2026, 7, 17, 12, 30, tzinfo=timezone.utc),
    }
    assert "source" not in record["tags"]
    assert "BLE MAC address" not in record["tags"]


def test_build_influx_record_omits_missing_values() -> None:
    record = build_influx_record(
        make_sample(pm10=None, serial_number=None),
        measurement="IQAir",
    )

    assert "Serial number" not in record["tags"]
    assert "Product name" not in record["tags"]
    assert "PM10 [ug/m^3]" not in record["fields"]
    assert record["tags"]["Connection"] == "Bluetooth LE"


def test_build_influx_record_rejects_empty_sample() -> None:
    with pytest.raises(EmptyIQAirSampleError):
        build_influx_record(
            make_sample(fan_rpm=None, pm1=None, pm25=None, pm10=None),
            measurement="IQAir",
        )


def test_polling_reuses_one_persistent_ble_session() -> None:
    async def run() -> None:
        client = FakeIQAirClient([make_sample(), make_sample()], connected=False)
        writer = FakeWriter()
        collector = IQAirInfluxCollector(
            AppSettings(device_identifier="device", reconnect_delay_s=0),
            client,
            writer,
            sleep=no_sleep,
        )

        await collector.poll_once(1)
        await collector.poll_once(2)

        assert client.connect_count == 1
        assert client.reconnect_count == 0
        assert len(writer.records) == 2

    asyncio.run(run())


def test_measurement_failure_reconnects_once_without_counting_recovered_error() -> None:
    async def run() -> None:
        client = FakeIQAirClient(
            [OSError("connection lost"), make_sample()],
            connected=True,
        )
        writer = FakeWriter()
        collector = IQAirInfluxCollector(
            AppSettings(device_identifier="device", reconnect_delay_s=0),
            client,
            writer,
            sleep=no_sleep,
        )

        await collector.poll_once()

        assert client.reconnect_count == 1
        assert collector.lifetime_exception_count == 0
        assert len(writer.records) == 1

    asyncio.run(run())


def test_influx_failure_does_not_reconnect_ble() -> None:
    async def run() -> None:
        client = FakeIQAirClient([make_sample()], connected=True)
        writer = FakeWriter(write_error=OSError("InfluxDB unavailable"))
        collector = IQAirInfluxCollector(
            AppSettings(device_identifier="device", reconnect_delay_s=0),
            client,
            writer,
            sleep=no_sleep,
        )

        assert not await collector.run_cycle(1)

        assert client.reconnect_count == 0
        assert client.is_connected
        assert collector.lifetime_exception_count == 1

    asyncio.run(run())


def test_lifetime_counter_survives_success_between_failed_cycles() -> None:
    async def run() -> None:
        client = FakeIQAirClient(
            [
                OSError("failure 1a"),
                OSError("failure 1b"),
                make_sample(),
                OSError("failure 2a"),
                OSError("failure 2b"),
                OSError("failure 3a"),
                OSError("failure 3b"),
            ],
            connected=True,
        )
        writer = FakeWriter()
        collector = IQAirInfluxCollector(
            AppSettings(
                device_identifier="device",
                reconnect_delay_s=0,
                exception_threshold=3,
            ),
            client,
            writer,
            sleep=no_sleep,
        )

        assert not await collector.run_cycle(1)
        assert collector.lifetime_exception_count == 1
        assert await collector.run_cycle(2)
        assert collector.lifetime_exception_count == 1
        assert not await collector.run_cycle(3)
        assert collector.lifetime_exception_count == 2
        with pytest.raises(OSError, match="failure 3b"):
            await collector.run_cycle(4)

        assert collector.lifetime_exception_count == 3
        assert client.reconnect_count == 3
        assert len(writer.records) == 1

    asyncio.run(run())


def test_once_mode_closes_ble_and_influx_resources() -> None:
    async def run() -> None:
        client = FakeIQAirClient([make_sample()], connected=True)
        writer = FakeWriter()
        collector = IQAirInfluxCollector(
            AppSettings(device_identifier="device"),
            client,
            writer,
        )

        await collector.run(once=True)

        assert client.close_count == 1
        assert writer.close_count == 1

    asyncio.run(run())


def test_fatal_threshold_closes_ble_and_influx_resources() -> None:
    async def run() -> None:
        client = FakeIQAirClient(
            [OSError("measurement failed"), OSError("retry failed")],
            connected=True,
        )
        writer = FakeWriter()
        collector = IQAirInfluxCollector(
            AppSettings(
                device_identifier="device",
                reconnect_delay_s=0,
                exception_threshold=1,
            ),
            client,
            writer,
            sleep=no_sleep,
        )

        with pytest.raises(OSError, match="retry failed"):
            await collector.run()

        assert collector.lifetime_exception_count == 1
        assert client.close_count == 1
        assert writer.close_count == 1

    asyncio.run(run())


def test_dry_run_writer_skips_upload_and_reports_record(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def run() -> None:
        client = FakeIQAirClient([make_sample()], connected=True)
        collector = IQAirInfluxCollector(
            AppSettings(device_identifier="device"),
            client,
            DryRunWriter(),
        )

        await collector.run(once=True)

    asyncio.run(run())

    output = capsys.readouterr()
    assert "dry-run record, not uploaded" in output.out
    assert "'Connection': 'Bluetooth LE'" in output.out
    assert "PM2.5 [ug/m^3]" in output.out


def test_dry_run_cli_does_not_load_influx_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(device_identifier="device")
    received: dict[str, Any] = {}

    monkeypatch.setattr(main_module, "load_settings", lambda _path: settings)

    def unexpected_load(_path: Path) -> None:
        raise AssertionError("dry-run must not load InfluxDB credentials")

    async def fake_run_application(
        app_settings: AppSettings,
        influx_config: object,
        *,
        once: bool,
        dry_run: bool,
    ) -> int:
        received.update(
            settings=app_settings,
            influx_config=influx_config,
            once=once,
            dry_run=dry_run,
        )
        return 0

    monkeypatch.setattr(main_module, "load_influx_config", unexpected_load)
    monkeypatch.setattr(main_module, "run_application", fake_run_application)

    assert main_module.main(["--once", "--dry-run"]) == 0
    assert received == {
        "settings": settings,
        "influx_config": None,
        "once": True,
        "dry_run": True,
    }

"""Continuously relay IQAir purifier measurements from BLE to InfluxDB.

The collector owns one persistent :class:`iqair_client.IQAirClient` session. It
reconnects only after a lost or unhealthy BLE session, retries one measurement,
and lets a process supervisor recover the app after a configured number of
lifetime polling failures.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import tomllib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

from iqair_client import IQAirClient, IQAirDevice, IQAirSample
from supervisor.supervisor_helper import log, log_error, log_warn


DEFAULT_SETTINGS_PATH = Path("settings.toml")
DEFAULT_AUTH_PATH = Path("imaq_config/auth.toml")
DEFAULT_MEASUREMENT = "IQAir"
CONNECTION_TAG_VALUE = "Bluetooth LE"
SOURCE_TAG_VALUE = "IQAir_Device"

INFLUX_FIELD_NAMES = (
    ("fan_rpm", "FanSpeed[rpm]"),
    ("pm1_ugm3", "PM1[ug/m^3]"),
    ("pm25_ugm3", "PM2.5[ug/m^3]"),
    ("pm10_ugm3", "PM10[ug/m^3]"),
)


class EmptyIQAirSampleError(ValueError):
    """Raised when an IQAir sample contains no writable measurement fields."""


@dataclass(frozen=True)
class AppSettings:
    """Validated application and BLE polling configuration."""

    device_identifier: str
    interval_s: float = 30.0
    reconnect_delay_s: float = 10.0
    exception_threshold: int = 3
    measurement: str = DEFAULT_MEASUREMENT
    pair_on_startup: bool = True
    scan_seconds: float = 10.0
    connect_timeout_s: float = 20.0
    response_timeout_s: float = 6.0
    auth_path: Path = DEFAULT_AUTH_PATH


@dataclass(frozen=True)
class InfluxConfig:
    """Validated InfluxDB client options and write destination."""

    client_options: dict[str, Any]
    org: str
    bucket: str


class IQAirConnection(Protocol):
    """Connection operations required by the polling application."""

    @property
    def is_connected(self) -> bool:
        """Whether the BLE session is currently connected."""

    @property
    def device(self) -> IQAirDevice | None:
        """Best-known connected device identity."""

    async def connect(self) -> None:
        """Open and initialize the first BLE session."""

    async def reconnect(self, *, rediscover_on_failure: bool = True) -> None:
        """Replace a stale BLE session."""

    async def read_measurements(self) -> IQAirSample:
        """Read one normalized purifier sample."""

    async def close(self) -> None:
        """Release the BLE session."""


class RecordWriter(Protocol):
    """Asynchronous record writer owned by the collector."""

    async def write(self, record: Mapping[str, Any]) -> bool:
        """Handle one record and return whether it was uploaded."""

    async def close(self) -> None:
        """Release writer resources."""


def _required_string(
    values: Mapping[str, Any],
    key: str,
    *,
    location: str,
    default: str | None = None,
) -> str:
    """Return one nonempty string setting or raise a descriptive error."""

    value = values.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.{key} must be a nonempty string")
    return value.strip()


def _number_setting(values: Mapping[str, Any], key: str, default: float) -> float:
    """Return one finite numeric setting as a float."""

    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"settings.{key} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"settings.{key} must be finite")
    return number


def _integer_setting(values: Mapping[str, Any], key: str, default: int) -> int:
    """Return one integer setting without accepting booleans."""

    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"settings.{key} must be an integer")
    return value


def _boolean_setting(values: Mapping[str, Any], key: str, default: bool) -> bool:
    """Return one strict TOML boolean setting."""

    value = values.get(key, default)
    if not isinstance(value, bool):
        raise TypeError(f"settings.{key} must be a boolean")
    return value


def load_settings(path: str | Path) -> AppSettings:
    """Load and validate collector settings from a TOML file."""

    settings_path = Path(path).expanduser()
    with settings_path.open("rb") as file:
        values = tomllib.load(file)

    device_identifier = _required_string(
        values,
        "device_identifier",
        location="settings",
    )
    measurement = _required_string(
        values,
        "measurement",
        location="settings",
        default=DEFAULT_MEASUREMENT,
    )
    interval_s = _number_setting(values, "interval_s", 30.0)
    reconnect_delay_s = _number_setting(values, "reconnect_delay_s", 10.0)
    exception_threshold = _integer_setting(values, "exception_threshold", 3)
    scan_seconds = _number_setting(values, "scan_seconds", 10.0)
    connect_timeout_s = _number_setting(values, "connect_timeout_s", 20.0)
    response_timeout_s = _number_setting(values, "response_timeout_s", 6.0)
    pair_on_startup = _boolean_setting(values, "pair_on_startup", True)

    if interval_s <= 0:
        raise ValueError("settings.interval_s must be positive")
    if reconnect_delay_s < 0:
        raise ValueError("settings.reconnect_delay_s must not be negative")
    if exception_threshold <= 0:
        raise ValueError("settings.exception_threshold must be positive")
    for key, value in (
        ("scan_seconds", scan_seconds),
        ("connect_timeout_s", connect_timeout_s),
        ("response_timeout_s", response_timeout_s),
    ):
        if value <= 0:
            raise ValueError(f"settings.{key} must be positive")

    auth_value = values.get("auth_path", str(DEFAULT_AUTH_PATH))
    if not isinstance(auth_value, str) or not auth_value.strip():
        raise ValueError("settings.auth_path must be a nonempty path string")
    auth_path = Path(auth_value).expanduser()
    if not auth_path.is_absolute():
        auth_path = settings_path.parent / auth_path

    return AppSettings(
        device_identifier=device_identifier,
        interval_s=interval_s,
        reconnect_delay_s=reconnect_delay_s,
        exception_threshold=exception_threshold,
        measurement=measurement,
        pair_on_startup=pair_on_startup,
        scan_seconds=scan_seconds,
        connect_timeout_s=connect_timeout_s,
        response_timeout_s=response_timeout_s,
        auth_path=auth_path,
    )


def load_influx_config(path: str | Path) -> InfluxConfig:
    """Load and validate the ``[influxdb]`` authentication table."""

    auth_path = Path(path).expanduser()
    with auth_path.open("rb") as file:
        values = tomllib.load(file)
    table = values.get("influxdb")
    if not isinstance(table, dict):
        raise ValueError(f"{auth_path} must contain an [influxdb] table")

    url = _required_string(table, "url", location="influxdb")
    token = _required_string(table, "token", location="influxdb")
    org = _required_string(table, "org", location="influxdb")
    bucket = _required_string(table, "bucket", location="influxdb")

    client_options = dict(table)
    client_options.update(url=url, token=token, org=org)
    client_options.pop("bucket", None)
    return InfluxConfig(client_options=client_options, org=org, bucket=bucket)


def build_influx_record(
    sample: IQAirSample,
    *,
    measurement: str,
    device: IQAirDevice | None = None,
) -> dict[str, Any]:
    """Convert one normalized sample to the stable human-readable schema."""

    fields = {
        influx_name: value
        for attribute, influx_name in INFLUX_FIELD_NAMES
        if (value := getattr(sample, attribute)) is not None
    }
    if not fields:
        raise EmptyIQAirSampleError("IQAir sample contains no measurement values")
    if not sample.mac_address:
        raise ValueError("IQAir sample is missing its MAC address")

    serial_number = sample.serial_number or (device.serial_number if device else None)
    product_name = device.product_name if device else None
    tags: dict[str, str] = {}
    if serial_number:
        tags["Serial number"] = serial_number
    if product_name:
        tags["Product name"] = product_name
    tags["MAC address"] = sample.mac_address
    tags["Connection"] = CONNECTION_TAG_VALUE
    tags["source"] = SOURCE_TAG_VALUE

    return {
        "measurement": measurement,
        "tags": tags,
        "fields": fields,
        "time": sample.observed_at,
    }


def missing_influx_fields(sample: IQAirSample) -> list[str]:
    """Return display names for measurement values absent from a sample."""

    return [
        influx_name
        for attribute, influx_name in INFLUX_FIELD_NAMES
        if getattr(sample, attribute) is None
    ]


class InfluxDBWriter:
    """Own an InfluxDB client and expose nonblocking async writes."""

    def __init__(self, config: InfluxConfig) -> None:
        """Create a synchronous write API from validated client options."""

        self.org = config.org
        self.bucket = config.bucket
        self._client = influxdb_client.InfluxDBClient(**config.client_options)
        self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
        self._closed = False

    async def write(self, record: Mapping[str, Any]) -> bool:
        """Write one record in a worker thread so BLE callbacks remain responsive."""

        await asyncio.to_thread(
            self._write_api.write,
            bucket=self.bucket,
            org=self.org,
            record=record,
        )
        return True

    async def close(self) -> None:
        """Close the underlying InfluxDB client once."""

        if self._closed:
            return
        self._closed = True
        await asyncio.to_thread(self._client.close)


class DryRunWriter:
    """Accept records without constructing a client or contacting InfluxDB."""

    async def write(self, record: Mapping[str, Any]) -> bool:
        """Deliberately skip the network write and report dry-run status."""

        return False

    async def close(self) -> None:
        """Release no resources because dry-run mode owns no InfluxDB client."""


class IQAirInfluxCollector:
    """Poll one persistent IQAir BLE session and relay samples to InfluxDB."""

    def __init__(
        self,
        settings: AppSettings,
        client: IQAirConnection,
        writer: RecordWriter,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Initialize collector state without connecting to external resources."""

        self.settings = settings
        self.client = client
        self.writer = writer
        self.lifetime_exception_count = 0
        self._sleep = sleep

    async def _ensure_connected(self) -> None:
        """Open an initial session or recover a previously resolved target."""

        if self.client.is_connected:
            return
        if self.client.device is None:
            log(f"Connecting to IQAir device {self.settings.device_identifier!r}...")
            await self.client.connect()
        else:
            log_warn("BLE session is disconnected; reconnecting cached IQAir device.")
            await self.client.reconnect()
        log(f"IQAir BLE session connected: {self.client.device}")

    async def _read_with_recovery(self) -> IQAirSample:
        """Read one sample, replacing the BLE session and retrying once on error."""

        first_error: Exception
        try:
            await self._ensure_connected()
            return await self.client.read_measurements()
        except Exception as error:
            first_error = error
            log_warn(
                "IQAir measurement failed; reconnecting before one retry: "
                f"{type(first_error).__name__}: {first_error}"
            )

        if self.settings.reconnect_delay_s:
            await self._sleep(self.settings.reconnect_delay_s)

        try:
            await self.client.reconnect()
            sample = await self.client.read_measurements()
        except Exception as retry_error:
            retry_error.add_note(
                "Initial IQAir measurement failure: "
                f"{type(first_error).__name__}: {first_error}"
            )
            raise

        log("IQAir BLE session recovered and measurement retry succeeded.")
        return sample

    async def poll_once(self, iteration: int = 1) -> dict[str, Any]:
        """Read, validate, upload, and log one polling iteration."""

        sample = await self._read_with_recovery()
        missing = missing_influx_fields(sample)
        record = build_influx_record(
            sample,
            measurement=self.settings.measurement,
            device=self.client.device,
        )
        if missing:
            log_warn(
                f"Iteration {iteration}: missing {', '.join(missing)}; "
                "uploading the remaining fields."
            )

        uploaded = await self.writer.write(record)
        field_summary = ", ".join(
            f"{name}={value}" for name, value in record["fields"].items()
        )
        if uploaded:
            log(f"Iteration {iteration}: uploaded {field_summary}")
        else:
            log(
                f"Iteration {iteration}: dry-run record, not uploaded: "
                f"measurement={record['measurement']!r}, tags={record['tags']!r}, "
                f"fields={record['fields']!r}, time={record['time'].isoformat()}"
            )
        return record

    async def run_cycle(self, iteration: int) -> bool:
        """Run one guarded cycle and update the lifetime failure counter."""

        try:
            await self.poll_once(iteration)
            return True
        except Exception as error:
            self.lifetime_exception_count += 1
            count = self.lifetime_exception_count
            threshold = self.settings.exception_threshold
            log_error(
                f"Iteration {iteration}: measurement/upload failed "
                f"({count}/{threshold} lifetime exceptions): "
                f"{type(error).__name__}: {error}"
            )
            if count >= threshold:
                log_error("Lifetime exception threshold reached; raising to supervisor.")
                raise
            return False

    async def run(
        self,
        *,
        once: bool = False,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Run once or poll until stopped, always releasing owned resources."""

        event = stop_event or asyncio.Event()
        try:
            if once:
                await self.poll_once()
                return

            loop = asyncio.get_running_loop()
            next_poll = loop.time()
            iteration = 1
            while not event.is_set():
                await self.run_cycle(iteration)
                iteration += 1

                next_poll += self.settings.interval_s
                now = loop.time()
                if next_poll <= now:
                    next_poll = now + self.settings.interval_s
                if await _wait_for_stop(event, next_poll - now):
                    break
        finally:
            await self.close()

    async def close(self) -> None:
        """Release BLE and InfluxDB resources without masking the main failure."""

        log("Shutting down IQAir collector resources.")
        try:
            await self.client.close()
        except Exception as error:
            log_warn(f"BLE cleanup failed: {type(error).__name__}: {error}")
        try:
            await self.writer.close()
        except Exception as error:
            log_warn(f"InfluxDB cleanup failed: {type(error).__name__}: {error}")


async def _wait_for_stop(event: asyncio.Event, timeout: float) -> bool:
    """Wait for a stop request until timeout and return whether it arrived."""

    try:
        await asyncio.wait_for(event.wait(), timeout=max(0.0, timeout))
    except TimeoutError:
        return False
    return True


def _install_signal_handlers(stop_event: asyncio.Event) -> list[signal.Signals]:
    """Install supported asyncio SIGINT/SIGTERM handlers for graceful shutdown."""

    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_number, stop_event.set)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(signal_number)
    return installed


def _remove_signal_handlers(installed: Sequence[signal.Signals]) -> None:
    """Remove signal handlers previously installed on the running event loop."""

    loop = asyncio.get_running_loop()
    for signal_number in installed:
        loop.remove_signal_handler(signal_number)


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the collector command-line parser without external side effects."""

    parser = argparse.ArgumentParser(
        description="Poll one IQAir purifier over BLE and write samples to InfluxDB."
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=DEFAULT_SETTINGS_PATH,
        help="TOML settings path (default: settings.toml).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one sample and exit instead of polling continuously.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Read BLE and build records without loading InfluxDB credentials, "
            "creating an InfluxDB client, or uploading data."
        ),
    )
    return parser


async def run_application(
    settings: AppSettings,
    influx_config: InfluxConfig | None,
    *,
    once: bool,
    dry_run: bool,
) -> int:
    """Construct runtime clients and run the configured collector."""

    if dry_run:
        writer: RecordWriter = DryRunWriter()
    else:
        if influx_config is None:
            raise ValueError("InfluxDB configuration is required outside dry-run mode")
        writer = InfluxDBWriter(influx_config)
    client = IQAirClient(
        settings.device_identifier,
        pair=settings.pair_on_startup,
        scan_seconds=settings.scan_seconds,
        connect_timeout=settings.connect_timeout_s,
        response_timeout=settings.response_timeout_s,
        query_identity_on_connect=True,
    )
    collector = IQAirInfluxCollector(settings, client, writer)
    stop_event = asyncio.Event()
    installed_signals = _install_signal_handlers(stop_event)

    log(
        f"Starting IQAir collector for {settings.device_identifier!r}: "
        f"measurement={settings.measurement!r}, interval={settings.interval_s:g} s, "
        f"lifetime exception threshold={settings.exception_threshold}."
    )
    if dry_run:
        log_warn(
            "Dry-run mode: InfluxDB credentials will not be loaded and records "
            "will not be uploaded."
        )
    elif influx_config is not None:
        log(
            f"InfluxDB destination: org={influx_config.org!r}, "
            f"bucket={influx_config.bucket!r}."
        )
    try:
        await collector.run(once=once, stop_event=stop_event)
    finally:
        _remove_signal_handlers(installed_signals)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Load configuration and run the foreground or one-shot collector."""

    args = build_argument_parser().parse_args(argv)
    try:
        settings = load_settings(args.settings)
        influx_config = None if args.dry_run else load_influx_config(settings.auth_path)
    except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
        log_error(f"Configuration error: {type(error).__name__}: {error}")
        return 2

    try:
        return asyncio.run(
            run_application(
                settings,
                influx_config,
                once=args.once,
                dry_run=args.dry_run,
            )
        )
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        log_error(f"Fatal collector error: {type(error).__name__}: {error}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())

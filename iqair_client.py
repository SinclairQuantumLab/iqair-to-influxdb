"""Reusable asynchronous BLE client for IQAir HealthPro Plus XE purifiers.

Typical use::

    devices = await IQAirClient.discover_devices()
    for device in devices:
        print(device)

    async with IQAirClient("serial-number-or-BLE-MAC") as purifier:
        sample = await purifier.read_measurements()
        print(sample.fields)

BLE operations are asynchronous, so this client uses ``async with`` rather than a
regular synchronous context manager.

The module also provides an opt-in command-line demonstration::

    uv run iqair_client.py scan
    uv run iqair_client.py discover --pair
    uv run iqair_client.py sample SERIAL-OR-BLE-MAC --pair

Running the module without a subcommand only prints command help; it never starts
Bluetooth discovery or pairing implicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Iterable, Sequence

from bleak import BleakClient, BleakScanner


IQAIR_COMPANY_ID = 0x060A
IQAIR_SERVICE_UUID = "1b5ae7e4-f469-440f-a0b4-aed74acd94f8"
WRITE_UUID = "55340670-4e1c-471a-bd05-1891775a1f64"
NOTIFY_UUID = "6f5e9f58-ed60-47a2-bbe4-ec93545b94b6"

CONN_REQUEST = 0x01
CONN_RESPONSE = 0x81
DPRL_REQUEST = 0x13
DPRL_RESPONSE = 0x93

STANDARD_CHARACTERISTICS = {
    "00002a00-0000-1000-8000-00805f9b34fb": ("device_name", "text"),
    "00002a23-0000-1000-8000-00805f9b34fb": ("system_id", "hex"),
    "00002a24-0000-1000-8000-00805f9b34fb": ("model_number", "text"),
    "00002a25-0000-1000-8000-00805f9b34fb": ("serial_number", "text"),
    "00002a26-0000-1000-8000-00805f9b34fb": ("firmware_revision", "text"),
    "00002a27-0000-1000-8000-00805f9b34fb": ("hardware_revision", "text"),
    "00002a28-0000-1000-8000-00805f9b34fb": ("software_revision", "text"),
    "00002a29-0000-1000-8000-00805f9b34fb": ("manufacturer_name", "text"),
    "00002a50-0000-1000-8000-00805f9b34fb": ("pnp_id", "pnp_id"),
}


class IQAirError(RuntimeError):
    """Base exception for IQAir discovery, connection, and protocol failures."""


class IQAirNotConnectedError(IQAirError):
    """Raised when a command requires an active BLE connection."""


class IQAirDeviceNotFoundError(IQAirError):
    """Raised when no IQAir purifier matches a serial number or MAC address."""


class IQAirAmbiguousDeviceError(IQAirError):
    """Raised when a selector matches more than one purifier."""


class IQAirProtocolError(IQAirError):
    """Raised for malformed, rejected, or timed-out IQAir protocol responses."""


@dataclass(frozen=True)
class ParameterSpec:
    """Describe how to name and decode one known read-only IQAir parameter.

    Attributes:
        code: Numeric parameter identifier sent through the IQAir DPRL protocol.
        key: Stable Python/JSON key used for the decoded value.
        kind: Decoder name understood by :func:`decode_parameter`.
    """

    code: int
    key: str
    kind: str


# Read-only identity opcodes recovered from the AirVisual Android app. The Wi-Fi
# password opcode (4102) and all write/configuration opcodes are excluded.
IDENTITY_PARAMETERS = (
    ParameterSpec(1000, "serial_number", "reverse_text"),
    ParameterSpec(1002, "product_name", "reverse_text"),
    ParameterSpec(1003, "purifier_color", "uint"),
    ParameterSpec(1005, "application_firmware_version", "version"),
    ParameterSpec(1007, "application_firmware_crc", "hex_uint"),
    ParameterSpec(1011, "hardware_version", "version"),
    ParameterSpec(1012, "bootloader_firmware_version", "version"),
    ParameterSpec(1013, "product_type", "uint"),
    ParameterSpec(1014, "product_variation", "uint"),
    ParameterSpec(1015, "product_technical_revision", "version"),
    ParameterSpec(1022, "communication_chip_firmware_version", "version"),
    ParameterSpec(1023, "application_firmware_nvm_version", "version"),
    ParameterSpec(1024, "communication_chip_firmware_nvm_version", "version"),
    ParameterSpec(1025, "certificate_version", "version"),
    ParameterSpec(1026, "certificate_nvm_version", "version"),
    ParameterSpec(1030, "ethernet_supported", "bool_uint"),
    ParameterSpec(1040, "registration_number", "reverse_text"),
    ParameterSpec(1100, "network_ip", "reverse_ipv4"),
    ParameterSpec(1101, "network_netmask", "reverse_ipv4"),
    ParameterSpec(1102, "network_gateway", "reverse_ipv4"),
    ParameterSpec(1103, "network_interface", "uint"),
    ParameterSpec(1104, "network_interface_enabled", "bool_uint"),
    ParameterSpec(4060, "feature_set", "bitset"),
    ParameterSpec(4104, "wifi_mac_address", "mac"),
    ParameterSpec(4108, "wifi_access_point_ssid", "reverse_text"),
    ParameterSpec(4109, "wifi_access_point_mac", "mac"),
    ParameterSpec(4110, "wifi_rssi_dbm", "sint"),
    ParameterSpec(4120, "ethernet_mac_address", "mac"),
)

MEASUREMENT_PARAMETERS = (
    ParameterSpec(3013, "fan_rpm", "uint"),
    ParameterSpec(3023, "pm25_ugm3", "uint"),
    ParameterSpec(3024, "pm1_ugm3", "uint"),
    ParameterSpec(3025, "pm10_ugm3", "uint"),
)

PARAMETERS_BY_CODE = {
    parameter.code: parameter for parameter in (*IDENTITY_PARAMETERS, *MEASUREMENT_PARAMETERS)
}


@dataclass(frozen=True)
class IQAirParameterValue:
    """Represent one parameter returned by a DPR/DPRL response.

    Attributes:
        code: Numeric IQAir parameter identifier.
        raw_value: Unmodified value bytes from the protocol response.
        key: Known semantic name, or ``unknown_<code>`` for an unknown parameter.
        value: Best-effort decoded Python value.
    """

    code: int
    raw_value: bytes
    key: str
    value: Any

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation including the raw bytes."""

        return {
            "code": self.code,
            "key": self.key,
            "value": self.value,
            "raw_hex": self.raw_value.hex(),
        }


@dataclass(frozen=True)
class IQAirFrame:
    """Represent a parsed IQAir application-protocol frame.

    ``valid`` requires a matching declared length, a valid CRC, and no payload
    parsing error. A valid response may still contain a nonzero device status.
    """

    raw: bytes
    message_code: int | None
    payload: bytes
    declared_tail_length: int | None
    crc_ok: bool
    length_ok: bool
    status: int | None = None
    parameters: tuple[IQAirParameterValue, ...] = ()
    error: str | None = None

    @property
    def valid(self) -> bool:
        """Whether framing, CRC validation, and payload parsing all succeeded."""

        return self.crc_ok and self.length_ok and self.error is None

    def to_dict(self) -> dict[str, Any]:
        """Return all decoded frame details in a JSON-serializable mapping."""

        return {
            "raw_hex": self.raw.hex(),
            "message_code": self.message_code,
            "payload_hex": self.payload.hex(),
            "declared_tail_length": self.declared_tail_length,
            "crc_ok": self.crc_ok,
            "length_ok": self.length_ok,
            "valid": self.valid,
            "status": self.status,
            "parameters": [parameter.to_dict() for parameter in self.parameters],
            "error": self.error,
        }


@dataclass(frozen=True)
class IQAirDevice:
    """Describe one discovered IQAir candidate and its best-known identity.

    Advertisement-only instances are not necessarily verified. ``verified`` is
    set only after the custom service, characteristics, and IQAir handshake have
    succeeded. Identity dictionaries may be incomplete when optional reads fail.
    """

    mac_address: str
    serial_number: str | None = None
    product_name: str | None = None
    advertised_name: str | None = None
    rssi_dbm: int | None = None
    manufacturer_company_id: int | None = None
    manufacturer_data_hex: str | None = None
    advertised_service_uuids: tuple[str, ...] = ()
    verified: bool = False
    standard_information: dict[str, Any] = field(default_factory=dict)
    iqair_information: dict[str, Any] = field(default_factory=dict)
    raw_parameters: dict[int, str] = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    _backend_device: Any = field(default=None, repr=False, compare=False)

    @property
    def display_name(self) -> str:
        """Return the most descriptive available human-readable device name."""

        return self.product_name or self.advertised_name or "IQAir device"

    def matches(self, selector: str) -> bool:
        """Return whether ``selector`` equals this device's BLE MAC or serial."""

        if looks_like_mac(selector):
            return self.mac_address == normalize_mac(selector)
        return self.serial_number is not None and self.serial_number.casefold() == selector.strip().casefold()

    def to_dict(self) -> dict[str, Any]:
        """Return discovery, identity, verification, and error details as data."""

        return {
            "mac_address": self.mac_address,
            "serial_number": self.serial_number,
            "product_name": self.product_name,
            "advertised_name": self.advertised_name,
            "rssi_dbm": self.rssi_dbm,
            "manufacturer_company_id": self.manufacturer_company_id,
            "manufacturer_company_id_hex": (
                f"0x{self.manufacturer_company_id:04X}"
                if self.manufacturer_company_id is not None
                else None
            ),
            "manufacturer_data_hex": self.manufacturer_data_hex,
            "advertised_service_uuids": list(self.advertised_service_uuids),
            "verified": self.verified,
            "standard_information": self.standard_information,
            "iqair_information": self.iqair_information,
            "raw_parameters": {str(code): value for code, value in self.raw_parameters.items()},
            "errors": list(self.errors),
        }

    def __str__(self) -> str:
        """Return a concise human-readable identity and connection summary."""

        identity = self.serial_number or "serial unknown"
        return (
            f"{self.display_name} (SN={identity}, MAC={self.mac_address}, "
            f"RSSI={self.rssi_dbm}, verified={self.verified})"
        )


@dataclass(frozen=True)
class IQAirSample:
    """Represent one normalized purifier measurement sample.

    Missing protocol values remain ``None`` and are omitted from :attr:`fields`.
    ``raw_frames`` is retained for diagnostics but is excluded from normal output.
    """

    observed_at: datetime
    mac_address: str
    serial_number: str | None
    fan_rpm: int | None
    pm1_ugm3: int | None
    pm25_ugm3: int | None
    pm10_ugm3: int | None
    raw_frames: tuple[IQAirFrame, ...] = field(default=(), repr=False)

    @property
    def fields(self) -> dict[str, int]:
        """Return present numeric fields in an InfluxDB-friendly mapping."""

        values = {
            "fan_rpm": self.fan_rpm,
            "pm1_ugm3": self.pm1_ugm3,
            "pm25_ugm3": self.pm25_ugm3,
            "pm10_ugm3": self.pm10_ugm3,
        }
        return {key: value for key, value in values.items() if value is not None}

    def to_dict(self, include_raw_frames: bool = False) -> dict[str, Any]:
        """Return a JSON-serializable sample.

        Args:
            include_raw_frames: Include diagnostic protocol frames when true.
        """

        result: dict[str, Any] = {
            "source": "iqair-healthpro-plus-xe-ble",
            "observed_at": self.observed_at.isoformat(),
            "device_ble_address": self.mac_address,
            "serial_number": self.serial_number,
            "fields": self.fields,
        }
        if include_raw_frames:
            result["raw_frames"] = [frame.to_dict() for frame in self.raw_frames]
        return result


def now_utc() -> datetime:
    """Return the current timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


def looks_like_mac(value: str) -> bool:
    """Return whether ``value`` has a supported 48-bit MAC-address format."""

    value = value.strip()
    return bool(
        re.fullmatch(r"[0-9A-Fa-f]{12}", value)
        or re.fullmatch(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", value)
    )


def normalize_mac(value: str) -> str:
    """Normalize a 48-bit MAC address to uppercase colon-separated notation.

    Raises:
        ValueError: If ``value`` is not a supported 12-hex-digit MAC address.
    """

    if not looks_like_mac(value):
        raise ValueError(f"expected a 6-byte MAC address, got {value!r}")
    compact = value.replace(":", "").replace("-", "").upper()
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))


def increment_mac(value: str, amount: int) -> str:
    """Return a MAC address offset by ``amount`` in the 48-bit address space."""

    number = int(normalize_mac(value).replace(":", ""), 16)
    number = (number + amount) & ((1 << 48) - 1)
    return normalize_mac(f"{number:012X}")


def crc16_ccitt_iqair(data: bytes) -> int:
    """Calculate the IQAir CRC-16/CCITT value for ``data``.

    The protocol uses initial value ``0xFFFF`` and polynomial ``0x1021``.
    """

    crc = 0xFFFF
    for byte in data:
        for bit in range(8):
            data_bit = (byte >> (7 - bit)) & 1
            crc_bit = (crc >> 15) & 1
            crc = (crc << 1) & 0xFFFF
            if data_bit ^ crc_bit:
                crc ^= 0x1021
    return crc


def build_frame(message_code: int, payload_on_wire: bytes = b"") -> bytes:
    """Build one CRC-protected IQAir frame from an on-wire payload."""

    tail_length = len(payload_on_wire) + 2
    body = bytes([message_code]) + tail_length.to_bytes(2, "little") + payload_on_wire
    return body + crc16_ccitt_iqair(body).to_bytes(2, "little")


def build_conn_request() -> bytes:
    """Build a connection-handshake request containing the current Unix time."""

    fields = (
        (2).to_bytes(1, "big"),
        (8192).to_bytes(2, "big"),
        (0).to_bytes(1, "big"),
        int(time.time()).to_bytes(4, "big"),
    )
    return build_frame(CONN_REQUEST, b"".join(value[::-1] for value in fields))


def build_dprl_request(parameter_codes: Sequence[int]) -> bytes:
    """Build a DPRL request for the supplied numeric parameter codes."""

    payload = b"".join(int(code).to_bytes(2, "big") for code in parameter_codes)
    return build_frame(DPRL_REQUEST, payload[::-1])


def _clean_reverse_text(value: bytes) -> str:
    """Decode a reversed, null-padded UTF-8 protocol string."""

    return value[::-1].rstrip(b"\x00").decode("utf-8", errors="replace").strip()


def _printable_reverse_text(value: bytes) -> str | None:
    """Decode reversed ASCII only when every non-null byte is printable."""

    reversed_value = value[::-1].rstrip(b"\x00")
    if not reversed_value or not all(32 <= byte <= 126 for byte in reversed_value):
        return None
    return reversed_value.decode("ascii")


def decode_parameter(spec: ParameterSpec | None, value: bytes) -> Any:
    """Decode raw IQAir parameter bytes according to optional known metadata.

    Unknown parameters and unsupported decoder kinds are preserved as hexadecimal
    data so callers never lose the original value.
    """

    if spec is None:
        return {"raw_hex": value.hex()}
    if spec.kind == "reverse_text":
        return _clean_reverse_text(value)
    if spec.kind == "mac":
        return ":".join(f"{byte:02X}" for byte in value)
    if spec.kind == "reverse_ipv4":
        return ".".join(str(byte) for byte in value[::-1]) if len(value) == 4 else value.hex()
    if spec.kind == "sint":
        return int.from_bytes(value, "little", signed=True) if value else None
    if spec.kind == "uint":
        return int.from_bytes(value, "little") if value else None
    if spec.kind == "bool_uint":
        number = int.from_bytes(value, "little") if value else 0
        return {"enabled": bool(number), "value": number}
    if spec.kind == "bitset":
        number = int.from_bytes(value, "little") if value else 0
        return {"raw_hex": value.hex(), "bits": f"{number:0{len(value) * 8}b}"}
    if spec.kind == "hex_uint":
        return {"raw_hex": value.hex(), "value": int.from_bytes(value, "little") if value else None}
    if spec.kind == "version":
        decoded: dict[str, Any] = {
            "raw_hex": value.hex(),
            "value": int.from_bytes(value, "little") if value else None,
        }
        text = _printable_reverse_text(value)
        if text is not None:
            decoded["text"] = text
        elif value:
            decoded["bytes_little_endian"] = list(value)
        return decoded
    return {"raw_hex": value.hex()}


def _parse_parameter_payload(
    payload: bytes,
) -> tuple[tuple[IQAirParameterValue, ...], str | None]:
    """Parse a DPRL status/payload and return values plus any structural error."""

    if not payload:
        return (), "DPRL response is missing its status byte"
    cursor = 1
    parameters: list[IQAirParameterValue] = []
    while cursor + 4 <= len(payload):
        code = int.from_bytes(payload[cursor : cursor + 2], "little")
        size = int.from_bytes(payload[cursor + 2 : cursor + 4], "little")
        cursor += 4
        if cursor + size > len(payload):
            return tuple(parameters), f"parameter {code} extends past the response payload"
        raw_value = payload[cursor : cursor + size]
        cursor += size
        spec = PARAMETERS_BY_CODE.get(code)
        parameters.append(
            IQAirParameterValue(
                code=code,
                raw_value=raw_value,
                key=spec.key if spec else f"unknown_{code}",
                value=decode_parameter(spec, raw_value),
            )
        )
    if cursor != len(payload):
        return tuple(parameters), f"{len(payload) - cursor} trailing payload byte(s)"
    return tuple(parameters), None


def parse_frame(raw: bytes) -> IQAirFrame:
    """Parse arbitrary bytes into an :class:`IQAirFrame` without raising.

    Short frames, CRC failures, declared-length mismatches, and malformed DPRL
    payloads are represented by validity fields and ``error`` on the result.
    """

    if len(raw) < 5:
        return IQAirFrame(
            raw=raw,
            message_code=raw[0] if raw else None,
            payload=b"",
            declared_tail_length=None,
            crc_ok=False,
            length_ok=False,
            error="frame is shorter than five bytes",
        )

    declared_tail_length = int.from_bytes(raw[1:3], "little")
    payload = raw[3:-2]
    crc_ok = crc16_ccitt_iqair(raw[:-2]).to_bytes(2, "little") == raw[-2:]
    length_ok = len(raw) == declared_tail_length + 3
    status = payload[0] if raw[0] in (CONN_RESPONSE, DPRL_RESPONSE) and payload else None
    parameters: tuple[IQAirParameterValue, ...] = ()
    parameter_error: str | None = None
    if raw[0] == DPRL_RESPONSE and crc_ok and length_ok:
        parameters, parameter_error = _parse_parameter_payload(payload)
    return IQAirFrame(
        raw=raw,
        message_code=raw[0],
        payload=payload,
        declared_tail_length=declared_tail_length,
        crc_ok=crc_ok,
        length_ok=length_ok,
        status=status,
        parameters=parameters,
        error=parameter_error,
    )


class _FrameStream:
    """Reassemble fragmented BLE notifications and route complete frames."""

    def __init__(self) -> None:
        """Initialize an empty byte buffer and response notification event."""

        self.buffer = bytearray()
        self.frames: list[IQAirFrame] = []
        self.changed = asyncio.Event()

    def feed(self, data: bytes | bytearray) -> None:
        """Append one BLE notification and parse every newly complete frame."""

        self.buffer.extend(bytes(data))
        while len(self.buffer) >= 3:
            expected_length = int.from_bytes(self.buffer[1:3], "little") + 3
            if expected_length < 5 or expected_length > 8192:
                del self.buffer[0]
                continue
            if len(self.buffer) < expected_length:
                break
            raw = bytes(self.buffer[:expected_length])
            del self.buffer[:expected_length]
            self.frames.append(parse_frame(raw))
            self.changed.set()

    async def wait_for(self, message_code: int, start_index: int, timeout: float) -> IQAirFrame:
        """Wait for a matching frame received at or after ``start_index``.

        Raises:
            IQAirProtocolError: If no matching frame arrives before ``timeout``.
        """

        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            for frame in self.frames[start_index:]:
                if frame.message_code == message_code:
                    return frame

            self.changed.clear()
            # Recheck after clear so a notification arriving at the boundary is not lost.
            for frame in self.frames[start_index:]:
                if frame.message_code == message_code:
                    return frame

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise IQAirProtocolError(
                    f"no 0x{message_code:02X} response within {timeout:g} seconds"
                )
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=remaining)
            except TimeoutError as exc:
                raise IQAirProtocolError(
                    f"no 0x{message_code:02X} response within {timeout:g} seconds"
                ) from exc


def _decode_standard_value(kind: str, value: bytes) -> Any:
    """Decode one standard Bluetooth Device Information characteristic."""

    if kind == "text":
        return value.rstrip(b"\x00").decode("utf-8", errors="replace").strip()
    if kind == "pnp_id" and len(value) == 7:
        return {
            "vendor_id_source": value[0],
            "vendor_id": int.from_bytes(value[1:3], "little"),
            "product_id": int.from_bytes(value[3:5], "little"),
            "product_version": int.from_bytes(value[5:7], "little"),
            "raw_hex": value.hex(),
        }
    return value.hex()


def _chunks(values: Sequence[int], size: int) -> Iterable[Sequence[int]]:
    """Yield consecutive slices of ``values`` containing at most ``size`` items."""

    for index in range(0, len(values), size):
        yield values[index : index + size]


def _advertisement_device(device: Any, advertisement: Any | None) -> IQAirDevice:
    """Convert a Bleak discovery result into an advertisement-only device model."""

    address = normalize_mac(device.address if hasattr(device, "address") else str(device))
    manufacturer_data = advertisement.manufacturer_data if advertisement is not None else {}
    company_id = IQAIR_COMPANY_ID if IQAIR_COMPANY_ID in manufacturer_data else None
    return IQAirDevice(
        mac_address=address,
        advertised_name=(
            (device.name if hasattr(device, "name") else None)
            or (advertisement.local_name if advertisement is not None else None)
        ),
        rssi_dbm=advertisement.rssi if advertisement is not None else None,
        manufacturer_company_id=company_id,
        manufacturer_data_hex=(
            manufacturer_data[IQAIR_COMPANY_ID].hex() if company_id is not None else None
        ),
        advertised_service_uuids=(
            tuple(advertisement.service_uuids) if advertisement is not None else ()
        ),
        _backend_device=device,
    )


class IQAirClient:
    """Manage discovery, identity, and one persistent IQAir BLE connection.

    ``selector`` may be an :class:`IQAirDevice`, a serial number, a BLE MAC
    address, or ``None``. A ``None`` selector is accepted only when discovery finds
    exactly one verified purifier.

    Instances are reusable after :meth:`close`; each later :meth:`connect` creates
    a fresh Bleak handle and protocol state. Commands on one connection are
    serialized because the IQAir request/response protocol has no transaction ID.
    """

    def __init__(
        self,
        selector: str | IQAirDevice | None = None,
        *,
        pair: bool = True,
        scan_seconds: float = 10.0,
        connect_timeout: float = 20.0,
        response_timeout: float = 6.0,
        query_identity_on_connect: bool = True,
    ) -> None:
        """Configure a client without opening Bluetooth resources.

        Args:
            selector: Device object, serial number, BLE MAC, or ``None`` for a
                single-device auto-selection.
            pair: Ask Bleak/the operating system to pair while connecting.
            scan_seconds: Advertisement scan duration used to resolve a selector.
            connect_timeout: Bleak connection timeout in seconds.
            response_timeout: Per-request IQAir protocol timeout in seconds.
            query_identity_on_connect: Read optional standard and proprietary
                identity fields after a successful handshake.
        """

        self.selector = selector
        self.pair = pair
        self.scan_seconds = scan_seconds
        self.connect_timeout = connect_timeout
        self.response_timeout = response_timeout
        self.query_identity_on_connect = query_identity_on_connect

        self._client: BleakClient | None = None
        self._stream: _FrameStream | None = None
        self._command_lock: asyncio.Lock | None = None
        self._notify_started = False
        self._device: IQAirDevice | None = selector if isinstance(selector, IQAirDevice) else None

    @property
    def handle(self) -> BleakClient | None:
        """Underlying Bleak connection handle, or ``None`` when disconnected."""

        return self._client

    @property
    def is_connected(self) -> bool:
        """Whether the retained Bleak handle currently reports a connection."""

        return self._client is not None and self._client.is_connected

    @property
    def device(self) -> IQAirDevice | None:
        """Best-known identity for the selected or connected purifier."""

        return self._device

    @property
    def mac_address(self) -> str | None:
        """Selected purifier BLE MAC, or ``None`` before device resolution."""

        return self._device.mac_address if self._device else None

    @property
    def serial_number(self) -> str | None:
        """Best-known purifier serial number, which may be unavailable."""

        return self._device.serial_number if self._device else None

    async def __aenter__(self) -> IQAirClient:
        """Connect and return this client for an asynchronous context block."""

        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the BLE connection when leaving an asynchronous context."""

        await self.close()

    def __str__(self) -> str:
        """Return a concise selected-device and connection-state summary."""

        if self._device is None:
            return f"IQAirClient(selector={self.selector!r}, connected={self.is_connected})"
        return (
            f"IQAirClient(product={self._device.display_name!r}, "
            f"serial_number={self._device.serial_number!r}, "
            f"mac_address={self._device.mac_address!r}, connected={self.is_connected})"
        )

    @classmethod
    async def scan_devices(
        cls,
        *,
        scan_seconds: float = 10.0,
        addresses: Sequence[str] = (),
    ) -> list[IQAirDevice]:
        """Scan advertisements and return IQAir company-ID candidates.

        This method does not connect or pair. Explicit ``addresses`` are included
        even when their manufacturer advertisement is not seen.

        Args:
            scan_seconds: Time to listen for BLE advertisements.
            addresses: Known BLE MAC addresses to include as forced candidates.

        Returns:
            Candidates sorted by strongest RSSI and then BLE MAC.
        """

        discovered = await BleakScanner.discover(timeout=scan_seconds, return_adv=True)
        candidates: list[IQAirDevice] = []
        by_address: dict[str, IQAirDevice] = {}
        for device, advertisement in discovered.values():
            try:
                candidate = _advertisement_device(device, advertisement)
            except ValueError:
                continue
            by_address[candidate.mac_address] = candidate
            if IQAIR_COMPANY_ID in advertisement.manufacturer_data:
                candidates.append(candidate)

        candidate_addresses = {candidate.mac_address for candidate in candidates}
        for address in addresses:
            normalized = normalize_mac(address)
            if normalized in candidate_addresses:
                continue
            candidates.append(
                by_address.get(
                    normalized,
                    IQAirDevice(mac_address=normalized, _backend_device=normalized),
                )
            )

        candidates.sort(
            key=lambda candidate: (
                candidate.rssi_dbm if candidate.rssi_dbm is not None else -1000,
                candidate.mac_address,
            ),
            reverse=True,
        )
        return candidates

    @classmethod
    async def discover_devices(
        cls,
        *,
        scan_seconds: float = 10.0,
        pair: bool = True,
        connect_timeout: float = 20.0,
        response_timeout: float = 6.0,
        query_identity: bool = True,
        addresses: Sequence[str] = (),
    ) -> list[IQAirDevice]:
        """Scan IQAir candidates and optionally verify/query each candidate.

        ``query_identity=True`` connects to candidates sequentially and may request
        OS pairing. Use :meth:`scan_devices` for advertisement-only discovery.

        Args:
            scan_seconds: Time to listen for BLE advertisements.
            pair: Ask the operating system to pair each candidate when connecting.
            connect_timeout: Bleak connection timeout for each candidate.
            response_timeout: Per-request IQAir response timeout.
            query_identity: Connect, verify, and query identity when true.
            addresses: Known BLE MAC addresses to include as forced candidates.

        Returns:
            One result per candidate. Candidate-specific failures are recorded in
            :attr:`IQAirDevice.errors` instead of aborting the complete discovery.
        """

        candidates = await cls.scan_devices(scan_seconds=scan_seconds, addresses=addresses)
        if not query_identity:
            return candidates

        results: list[IQAirDevice] = []
        for candidate in candidates:
            client = cls(
                candidate,
                pair=pair,
                scan_seconds=scan_seconds,
                connect_timeout=connect_timeout,
                response_timeout=response_timeout,
                query_identity_on_connect=True,
            )
            try:
                await client.connect()
                results.append(client.device or candidate)
            except Exception as exc:
                results.append(
                    replace(
                        candidate,
                        errors=(*candidate.errors, f"{type(exc).__name__}: {exc}"),
                    )
                )
            finally:
                await client.close()
        return results

    @staticmethod
    def select_device(devices: Sequence[IQAirDevice], selector: str) -> IQAirDevice:
        """Return the unique device matching a serial number or BLE MAC.

        Raises:
            IQAirDeviceNotFoundError: If no device matches ``selector``.
            IQAirAmbiguousDeviceError: If multiple devices match ``selector``.
        """

        matches = [device for device in devices if device.matches(selector)]
        if not matches:
            raise IQAirDeviceNotFoundError(f"no IQAir purifier matches {selector!r}")
        if len(matches) > 1:
            raise IQAirAmbiguousDeviceError(
                f"{len(matches)} IQAir purifiers match {selector!r}"
            )
        return matches[0]

    async def _resolve_selector(self) -> IQAirDevice:
        """Resolve the configured selector to exactly one candidate device."""

        if isinstance(self.selector, IQAirDevice):
            return self.selector

        if isinstance(self.selector, str) and looks_like_mac(self.selector):
            normalized = normalize_mac(self.selector)
            devices = await self.scan_devices(
                scan_seconds=self.scan_seconds,
                addresses=(normalized,),
            )
            return self.select_device(devices, normalized)

        devices = await self.discover_devices(
            scan_seconds=self.scan_seconds,
            pair=self.pair,
            connect_timeout=self.connect_timeout,
            response_timeout=self.response_timeout,
            query_identity=True,
        )
        verified = [device for device in devices if device.verified]
        if isinstance(self.selector, str):
            return self.select_device(verified, self.selector)
        if not verified:
            raise IQAirDeviceNotFoundError("no verified IQAir purifier was found")
        if len(verified) > 1:
            raise IQAirAmbiguousDeviceError(
                "multiple IQAir purifiers were found; select one by serial number or MAC"
            )
        return verified[0]

    def _on_disconnect(self, _client: BleakClient) -> None:
        """Update local notification state after a Bleak disconnect callback."""

        self._notify_started = False

    async def connect(self) -> None:
        """Resolve the selector, connect, validate GATT, and perform handshake.

        Calling this method while already connected is a no-op. If setup fails,
        partially acquired Bluetooth resources are released before the exception is
        propagated.

        Raises:
            IQAirDeviceNotFoundError: If selector resolution finds no purifier.
            IQAirAmbiguousDeviceError: If selector resolution is not unique.
            IQAirProtocolError: If GATT validation or the handshake fails.
        """

        if self.is_connected:
            return

        device = await self._resolve_selector()
        target = device._backend_device or device.mac_address
        client = BleakClient(
            target,
            disconnected_callback=self._on_disconnect,
            pair=self.pair,
            timeout=self.connect_timeout,
        )
        self._client = client
        self._device = device
        self._stream = _FrameStream()
        self._command_lock = asyncio.Lock()

        try:
            await client.connect()
            service_uuids = {service.uuid.lower() for service in client.services}
            if IQAIR_SERVICE_UUID not in service_uuids:
                raise IQAirProtocolError("IQAir custom GATT service was not found")
            if client.services.get_characteristic(WRITE_UUID) is None:
                raise IQAirProtocolError("IQAir write characteristic was not found")
            if client.services.get_characteristic(NOTIFY_UUID) is None:
                raise IQAirProtocolError("IQAir notify characteristic was not found")

            await client.start_notify(NOTIFY_UUID, self._on_notification)
            self._notify_started = True
            await self._request(CONN_REQUEST, build_conn_request(), CONN_RESPONSE)
            self._device = replace(device, verified=True)
            if self.query_identity_on_connect:
                await self.read_device_information()
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        """Stop notifications, disconnect, and release the BLE handle.

        The operation is idempotent and suppresses cleanup-only Bleak failures so
        it is safe to call from ``finally`` blocks.
        """

        client = self._client
        self._client = None
        if client is not None:
            if client.is_connected and self._notify_started:
                try:
                    await client.stop_notify(NOTIFY_UUID)
                except Exception:
                    pass
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass
        self._notify_started = False
        self._stream = None
        self._command_lock = None

    def _on_notification(self, _sender: Any, data: bytearray) -> None:
        """Forward one Bleak notification fragment to the frame reassembler."""

        if self._stream is not None:
            self._stream.feed(data)

    def _require_connected(self) -> tuple[BleakClient, _FrameStream, asyncio.Lock]:
        """Return active protocol state or raise a not-connected error."""

        if not self.is_connected or self._client is None:
            raise IQAirNotConnectedError("not connected; call connect() first")
        if self._stream is None or self._command_lock is None:
            raise IQAirNotConnectedError("connection protocol state is unavailable")
        return self._client, self._stream, self._command_lock

    async def _request(
        self,
        request_code: int,
        request_frame: bytes,
        response_code: int,
    ) -> IQAirFrame:
        """Serialize one write/response exchange and validate its response."""

        client, stream, lock = self._require_connected()
        async with lock:
            start_index = len(stream.frames)
            await client.write_gatt_char(WRITE_UUID, request_frame, response=True)
            response = await stream.wait_for(
                response_code,
                start_index,
                self.response_timeout,
            )
        if not response.valid:
            raise IQAirProtocolError(
                f"invalid response to 0x{request_code:02X}: {response.to_dict()}"
            )
        if response.status != 0:
            raise IQAirProtocolError(
                f"IQAir rejected 0x{request_code:02X} with status {response.status}"
            )
        return response

    async def _read_parameters_with_frames(
        self,
        parameter_codes: Sequence[int],
        *,
        chunk_size: int = 12,
    ) -> tuple[dict[int, IQAirParameterValue], tuple[IQAirFrame, ...]]:
        """Read deduplicated parameter codes and retain diagnostic response frames."""

        if not parameter_codes:
            return {}, ()
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")

        values: dict[int, IQAirParameterValue] = {}
        frames: list[IQAirFrame] = []
        for codes in _chunks(tuple(dict.fromkeys(int(code) for code in parameter_codes)), chunk_size):
            response = await self._request(
                DPRL_REQUEST,
                build_dprl_request(codes),
                DPRL_RESPONSE,
            )
            frames.append(response)
            for parameter in response.parameters:
                values[parameter.code] = parameter
        return values, tuple(frames)

    async def read_parameters(
        self,
        parameter_codes: Sequence[int],
        *,
        chunk_size: int = 12,
    ) -> dict[int, IQAirParameterValue]:
        """Read arbitrary parameter codes through the proven DPRL read path.

        Args:
            parameter_codes: Numeric read-only parameter identifiers.
            chunk_size: Maximum codes included in each request frame.

        Returns:
            Mapping keyed by parameter code. Unsupported or omitted device values
            are simply absent.

        Raises:
            IQAirNotConnectedError: If no active connection exists.
            IQAirProtocolError: If a request is rejected, malformed, or times out.
            ValueError: If ``chunk_size`` is not positive.
        """

        values, _frames = await self._read_parameters_with_frames(
            parameter_codes,
            chunk_size=chunk_size,
        )
        return values

    async def _read_standard_information(self) -> tuple[dict[str, Any], list[str]]:
        """Best-effort read of standard GATT Device Information fields."""

        client, _stream, _lock = self._require_connected()
        information: dict[str, Any] = {}
        errors: list[str] = []
        for uuid, (key, kind) in STANDARD_CHARACTERISTICS.items():
            characteristic = client.services.get_characteristic(uuid)
            if characteristic is None or "read" not in characteristic.properties:
                continue
            try:
                value = bytes(await client.read_gatt_char(characteristic))
                information[key] = _decode_standard_value(kind, value)
            except Exception as exc:
                errors.append(f"standard {key}: {type(exc).__name__}: {exc}")
        return information, errors

    async def read_device_information(self) -> IQAirDevice:
        """Refresh standard and IQAir identity fields for the connected purifier.

        Optional characteristic and identity-read failures are appended to the
        returned device's ``errors`` tuple. A successful handshake remains verified
        even when some metadata is unavailable.

        Returns:
            Updated immutable device model, also cached on :attr:`device`.

        Raises:
            IQAirNotConnectedError: If no active purifier connection exists.
        """

        self._require_connected()
        base = self._device
        if base is None:
            raise IQAirNotConnectedError("connected device identity is unavailable")

        standard_information, errors = await self._read_standard_information()
        values: dict[int, IQAirParameterValue] = {}
        for codes in _chunks([parameter.code for parameter in IDENTITY_PARAMETERS], 12):
            try:
                group_values, _frames = await self._read_parameters_with_frames(codes)
                values.update(group_values)
            except IQAirError as exc:
                errors.append(f"identity parameters {list(codes)}: {exc}")
                break

        iqair_information = {parameter.key: parameter.value for parameter in values.values()}
        raw_parameters = {code: parameter.raw_value.hex() for code, parameter in values.items()}
        serial_number = (
            iqair_information.get("serial_number")
            or standard_information.get("serial_number")
            or base.serial_number
        )
        product_name = (
            iqair_information.get("product_name")
            or standard_information.get("device_name")
            or standard_information.get("model_number")
            or base.product_name
        )
        self._device = replace(
            base,
            serial_number=str(serial_number) if serial_number else None,
            product_name=str(product_name) if product_name else None,
            verified=True,
            standard_information=standard_information,
            iqair_information=iqair_information,
            raw_parameters=raw_parameters,
            errors=(*base.errors, *errors),
        )
        return self._device

    async def read_measurements(self) -> IQAirSample:
        """Read PM1, PM2.5, PM10, and fan RPM over the active connection.

        Returns:
            Timestamped normalized sample with diagnostic response frames.

        Raises:
            IQAirNotConnectedError: If no active purifier connection exists.
            IQAirProtocolError: If the measurement response is invalid or absent.
        """

        values, frames = await self._read_parameters_with_frames(
            [parameter.code for parameter in MEASUREMENT_PARAMETERS]
        )
        decoded = {parameter.key: parameter.value for parameter in values.values()}
        if self._device is None:
            raise IQAirNotConnectedError("connected device identity is unavailable")
        return IQAirSample(
            observed_at=now_utc(),
            mac_address=self._device.mac_address,
            serial_number=self._device.serial_number,
            fan_rpm=decoded.get("fan_rpm"),
            pm1_ugm3=decoded.get("pm1_ugm3"),
            pm25_ugm3=decoded.get("pm25_ugm3"),
            pm10_ugm3=decoded.get("pm10_ugm3"),
            raw_frames=frames,
        )


__all__ = [
    "IQAIR_COMPANY_ID",
    "IQAIR_SERVICE_UUID",
    "IQAirAmbiguousDeviceError",
    "IQAirClient",
    "IQAirDevice",
    "IQAirDeviceNotFoundError",
    "IQAirError",
    "IQAirFrame",
    "IQAirNotConnectedError",
    "IQAirParameterValue",
    "IQAirProtocolError",
    "IQAirSample",
    "build_conn_request",
    "build_dprl_request",
    "build_frame",
    "crc16_ccitt_iqair",
    "increment_mac",
    "looks_like_mac",
    "normalize_mac",
    "parse_frame",
]


def _build_demo_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the opt-in module demonstration."""

    parser = argparse.ArgumentParser(
        description=(
            "Demonstrate IQAirClient discovery and one-shot BLE reads. "
            "No Bluetooth operation runs without a command."
        )
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    scan = commands.add_parser(
        "scan",
        help="List IQAir manufacturer advertisements without connecting.",
    )
    scan.add_argument("--scan-seconds", type=float, default=10.0)
    scan.add_argument(
        "--address",
        action="append",
        default=[],
        metavar="MAC",
        help="Include a known BLE MAC even when no advertisement is seen.",
    )

    discover = commands.add_parser(
        "discover",
        help="Connect to candidates, verify the protocol, and read identity.",
    )
    discover.add_argument("--scan-seconds", type=float, default=10.0)
    discover.add_argument("--connect-timeout", type=float, default=20.0)
    discover.add_argument("--response-timeout", type=float, default=6.0)
    discover.add_argument(
        "--address",
        action="append",
        default=[],
        metavar="MAC",
        help="Also try a known BLE MAC even when no advertisement is seen.",
    )
    discover.add_argument(
        "--pair",
        action="store_true",
        help="Allow the operating system to pair while connecting.",
    )

    sample = commands.add_parser(
        "sample",
        help="Connect to one serial number or BLE MAC and read one sample.",
    )
    sample.add_argument("identifier", help="Purifier serial number or BLE MAC.")
    sample.add_argument("--scan-seconds", type=float, default=10.0)
    sample.add_argument("--connect-timeout", type=float, default=20.0)
    sample.add_argument("--response-timeout", type=float, default=6.0)
    sample.add_argument(
        "--pair",
        action="store_true",
        help="Allow the operating system to pair while connecting.",
    )
    sample.add_argument(
        "--skip-device-info",
        action="store_true",
        help="Skip optional identity reads after the protocol handshake.",
    )
    sample.add_argument(
        "--include-raw-frames",
        action="store_true",
        help="Include diagnostic response frames in the JSON sample.",
    )
    return parser


async def _run_demo(args: argparse.Namespace) -> int:
    """Execute one parsed demo command and print JSON output."""

    if args.command == "scan":
        devices = await IQAirClient.scan_devices(
            scan_seconds=args.scan_seconds,
            addresses=args.address,
        )
        report = {
            "command": "scan",
            "observed_at": now_utc().isoformat(),
            "candidate_count": len(devices),
            "devices": [device.to_dict() for device in devices],
        }
    elif args.command == "discover":
        devices = await IQAirClient.discover_devices(
            scan_seconds=args.scan_seconds,
            pair=args.pair,
            connect_timeout=args.connect_timeout,
            response_timeout=args.response_timeout,
            query_identity=True,
            addresses=args.address,
        )
        report = {
            "command": "discover",
            "observed_at": now_utc().isoformat(),
            "candidate_count": len(devices),
            "verified_iqair_count": sum(device.verified for device in devices),
            "devices": [device.to_dict() for device in devices],
        }
    elif args.command == "sample":
        async with IQAirClient(
            args.identifier,
            pair=args.pair,
            scan_seconds=args.scan_seconds,
            connect_timeout=args.connect_timeout,
            response_timeout=args.response_timeout,
            query_identity_on_connect=not args.skip_device_info,
        ) as client:
            measured = await client.read_measurements()
            report = {
                "command": "sample",
                "device": client.device.to_dict() if client.device else None,
                "sample": measured.to_dict(include_raw_frames=args.include_raw_frames),
            }
    else:
        raise ValueError(f"unsupported demo command: {args.command!r}")

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the opt-in command-line demonstration.

    Args:
        argv: Arguments excluding the executable name. ``None`` reads the process
            command line.

    Returns:
        Process-style status code. Keyboard interruption returns ``130``.
    """

    parser = _build_demo_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    for name in ("scan_seconds", "connect_timeout", "response_timeout"):
        value = getattr(args, name, None)
        if value is not None and value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")

    try:
        return asyncio.run(_run_demo(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

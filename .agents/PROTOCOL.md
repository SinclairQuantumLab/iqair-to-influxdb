# IQAir BLE Protocol Notes

This document separates live observations from static AirVisual app analysis.

## Discovery and Identity

### Live verified

- Advertisement company ID: `0x060A` (`1546`), assigned to IQAir AG.
- Advertisement payload observed: `050008`.
- Advertisement name observed: `ONHN5OCNNIU6NIFNP`.
- BLE address observed on Windows: `10:97:BD:09:3A:D2`.
- Paired GATT device name: `IQAir HealthPro Plus XE B009-T`.

The advertisement name has no known public prefix or model encoding. Use company
ID for candidate selection and the custom protocol handshake for verification.

## GATT Layout

### Live verified

- Custom service: `1b5ae7e4-f469-440f-a0b4-aed74acd94f8`
- Write characteristic: `55340670-4e1c-471a-bd05-1891775a1f64`
- Read/notify characteristic: `6f5e9f58-ed60-47a2-bbe4-ec93545b94b6`

The notify characteristic required authentication before pairing. A passive notify
subscription produced no measurements; the device responds after a request is
written.

### Live-verified write limit

On the known purifier, the write characteristic accepts at most a 20-byte
attribute value even though Bleak reports an MTU of 517. A seven-code DPRL frame is
19 bytes and succeeds; an eight-code frame is 21 bytes and is rejected with ATT
error `0x0D` (`Invalid Attribute Value Length`). Production requests therefore use
at most seven parameter codes per DPRL frame.

## Frame Format

### Live and offline verified

```text
byte 0       message code
bytes 1..2   little-endian tail length (payload length + two CRC bytes)
bytes 3..N   payload
last 2       CRC16, little-endian
```

CRC parameters:

- Initial value: `0xFFFF`
- Polynomial: `0x1021`
- CRC bytes on wire: little-endian
- Total frame size: declared tail length plus 3

BLE notifications may split a protocol frame. Reassemble using the declared tail
length before validating CRC or parsing payload.

## Message Codes

| Request | Code | Response | Code |
| --- | ---: | --- | ---: |
| Connection | `0x01` | Connection | `0x81` |
| Single parameter (`DPR`) | `0x12` | Single parameter | `0x92` |
| Parameter list (`DPRL`) | `0x13` | Parameter list | `0x93` |

App analysis also found `PINF` (`0x03`/`0x83`), but current readers use the proven
`DPRL` path for product information.

## Handshake

The working flow is:

1. Subscribe to the notify characteristic.
2. Write `CONN_REQUEST`.
3. Require a valid `CONN_RESPONSE` with status byte `0`.
4. Write one or more `DPRL_REQUEST` frames.
5. Parse valid `DPRL_RESPONSE` frames with status byte `0`.

Known response:

```text
810700000214200124f6
```

It has valid CRC and status `0`.

## Parameter List Payload

A response payload starts with a one-byte status followed by repeated items:

```text
uint16_le parameter code
uint16_le value length
value bytes
```

The app reverses the complete parameter-code region when building a list request.
Live responses showed that value bytes themselves are already in display/network
order: product name, serial number, registration number, and SSID are normal text,
while IPv4 values use normal network byte order. They must not be reversed while
decoding a response.

## Measurement Parameters

| Code | App name | Collector field | Status |
| ---: | --- | --- | --- |
| `3013` | `SENSOR_FANRPM` | `fan_rpm` | Live verified |
| `3023` | `SENSOR_PM25` | `pm25_ugm3` | Live verified |
| `3024` | `SENSOR_PM1` | `pm1_ugm3` | Live verified |
| `3025` | `SENSOR_PM10` | `pm10_ugm3` | Live verified |
| `3000` | `SENSOR_AMBIENTTEMPERATURE` | not exposed | App analysis only; no response observed |
| `3001` | `SENSOR_AMBIENTHUMIDITY` | not exposed | App analysis only; no response observed |

Known live measurement frame:

```text
931b0000c50b0200f50ad10b02002b00d00b02002b00cf0b02002b00bc59
```

Decoded values were fan RPM `2805` and PM1/PM2.5/PM10 `43`.

The refactored `IQAirClient` was live verified again on 2026-07-17 and returned fan
RPM `807` with PM1/PM2.5/PM10 all equal to `1`.

## Identification Parameters

These codes came from static AirVisual app analysis. `iqair_client.py` requests
them through its generic read-only DPRL method. On 2026-07-17 the known purifier
live-returned codes `1000`, `1002`, `1003`, `1007`, `1012`, `1013`, `1014`, `1015`,
`1040`, `1100`, `1101`, `1102`, `1103`, `4060`, `4104`, `4108`, `4109`, and `4110`.
Codes absent from that response remain unverified.

| Code | App parameter | Output field |
| ---: | --- | --- |
| `1000` | `PURIFIER_SERIALNUMBER` | `serial_number` |
| `1002` | `PURIFIER_PRODUCTNAME` | `product_name` |
| `1003` | `PURIFIER_PURIFIERCOLOR` | `purifier_color` |
| `1005` | `PURIFIER_APPLFWVER` | `application_firmware_version` |
| `1007` | `PURIFIER_APPLFWCRC` | `application_firmware_crc` |
| `1011` | `PURIFIER_HWVER` | `hardware_version` |
| `1012` | `PURIFIER_BOOTLOADERFWVER` | `bootloader_firmware_version` |
| `1013` | `PURIFIER_PRODUCTTYPE` | `product_type` |
| `1014` | `PURIFIER_PRODUCTVARIATION` | `product_variation` |
| `1015` | `PURIFIER_PRODUCTTECHREV` | `product_technical_revision` |
| `1022` | `PURIFIER_COMMCHIPFWVER` | `communication_chip_firmware_version` |
| `1023` | `PURIFIER_APPLFWNVM` | `application_firmware_nvm_version` |
| `1024` | `PURIFIER_COMMCHIPFWNVM` | `communication_chip_firmware_nvm_version` |
| `1025` | `PURIFIER_CERTIFICATEVER` | `certificate_version` |
| `1026` | `PURIFIER_CERTIFICATEVERNVM` | `certificate_nvm_version` |
| `1030` | `PURIFIER_ETHERNETSUPPORTED` | `ethernet_supported` |
| `1040` | `PURIFIER_REGISTRNR` | `registration_number` |
| `1100` | `PURIFIER_NETWIP` | `network_ip` |
| `1101` | `PURIFIER_NETWNETMASK` | `network_netmask` |
| `1102` | `PURIFIER_NETWGATEWAY` | `network_gateway` |
| `1103` | `PURIFIER_NETWINTERFACE` | `network_interface` |
| `1104` | `SETTINGS_NETWINTERFACEENABLED` | `network_interface_enabled` |
| `4060` | `PURIFIER_FEATURESET` | `feature_set` |
| `4104` | `WIFI_WIFIMACADDRESS` | `wifi_mac_address` |
| `4108` | `WIFI_WIFIAPSSID` | `wifi_access_point_ssid` |
| `4109` | `WIFI_WIFIAPMAC` | `wifi_access_point_mac` |
| `4110` | `WIFI_WIFIRSSI` | `wifi_rssi_dbm` |
| `4120` | `PURIFIER_ETHMACADDRESS` | `ethernet_mac_address` |

Do not add or query `WIFI_WIFIPSW` (`4102`).

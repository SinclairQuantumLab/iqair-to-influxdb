from __future__ import annotations

import argparse
import json
import struct
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


NO_INDEX = 0xFFFFFFFF


def emit(event: str, **fields: object) -> None:
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, sort_keys=True))


def read_uleb(data: bytes, off: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        byte = data[off]
        off += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, off
        shift += 7


def read_string(data: bytes, off: int) -> str:
    _utf16_len, pos = read_uleb(data, off)
    end = data.index(0, pos)
    return data[pos:end].decode("utf-8", errors="replace")


@dataclass
class Dex:
    name: str
    data: bytes
    strings: list[str]
    types: list[str]
    methods: list[dict[str, object]]
    class_defs: list[dict[str, object]]


def parse_dex(name: str, data: bytes) -> Dex:
    string_ids_size = struct.unpack_from("<I", data, 0x38)[0]
    string_ids_off = struct.unpack_from("<I", data, 0x3C)[0]
    type_ids_size = struct.unpack_from("<I", data, 0x40)[0]
    type_ids_off = struct.unpack_from("<I", data, 0x44)[0]
    method_ids_size = struct.unpack_from("<I", data, 0x58)[0]
    method_ids_off = struct.unpack_from("<I", data, 0x5C)[0]
    class_defs_size = struct.unpack_from("<I", data, 0x60)[0]
    class_defs_off = struct.unpack_from("<I", data, 0x64)[0]

    strings = []
    for index in range(string_ids_size):
        string_off = struct.unpack_from("<I", data, string_ids_off + index * 4)[0]
        strings.append(read_string(data, string_off))

    types = []
    for index in range(type_ids_size):
        descriptor_idx = struct.unpack_from("<I", data, type_ids_off + index * 4)[0]
        types.append(strings[descriptor_idx])

    methods = []
    for index in range(method_ids_size):
        class_idx, proto_idx, name_idx = struct.unpack_from("<HHI", data, method_ids_off + index * 8)
        methods.append({"index": index, "class": types[class_idx], "name": strings[name_idx], "proto_idx": proto_idx})

    class_defs = []
    for index in range(class_defs_size):
        off = class_defs_off + index * 32
        class_idx, access_flags, superclass_idx, interfaces_off, source_file_idx, annotations_off, class_data_off, static_values_off = struct.unpack_from(
            "<IIIIIIII", data, off
        )
        class_defs.append(
            {
                "class": types[class_idx],
                "access_flags": access_flags,
                "superclass": types[superclass_idx] if superclass_idx != NO_INDEX else None,
                "source_file": strings[source_file_idx] if source_file_idx != NO_INDEX else None,
                "class_data_off": class_data_off,
            }
        )

    return Dex(name=name, data=data, strings=strings, types=types, methods=methods, class_defs=class_defs)


def iter_encoded_methods(dex: Dex):
    for class_def in dex.class_defs:
        off = int(class_def["class_data_off"])
        if off == 0:
            continue
        static_fields_size, off = read_uleb(dex.data, off)
        instance_fields_size, off = read_uleb(dex.data, off)
        direct_methods_size, off = read_uleb(dex.data, off)
        virtual_methods_size, off = read_uleb(dex.data, off)

        for _ in range(static_fields_size + instance_fields_size):
            _field_idx_diff, off = read_uleb(dex.data, off)
            _access_flags, off = read_uleb(dex.data, off)

        for method_kind, count in (("direct", direct_methods_size), ("virtual", virtual_methods_size)):
            method_idx = 0
            for _ in range(count):
                method_idx_diff, off = read_uleb(dex.data, off)
                method_idx += method_idx_diff
                access_flags, off = read_uleb(dex.data, off)
                code_off, off = read_uleb(dex.data, off)
                if code_off:
                    method = dict(dex.methods[method_idx])
                    method.update({"method_kind": method_kind, "access_flags": access_flags, "code_off": code_off, "declaring_class": class_def["class"]})
                    yield method


def const_string_refs(data: bytes, code_off: int) -> list[int]:
    insns_size = struct.unpack_from("<I", data, code_off + 12)[0]
    insns_off = code_off + 16
    refs: list[int] = []
    cursor = 0
    while cursor < insns_size:
        unit = struct.unpack_from("<H", data, insns_off + cursor * 2)[0]
        opcode = unit & 0xFF
        aa = (unit >> 8) & 0xFF
        if opcode == 0x1A and cursor + 1 < insns_size:
            string_idx = struct.unpack_from("<H", data, insns_off + (cursor + 1) * 2)[0]
            refs.append(string_idx)
            cursor += 2
        elif opcode == 0x1B and cursor + 2 < insns_size:
            string_idx = struct.unpack_from("<I", data, insns_off + (cursor + 1) * 2)[0]
            refs.append(string_idx)
            cursor += 3
        else:
            # We do not need exact instruction lengths for xref discovery. Advancing
            # one code unit may inspect payload bytes too, but const-string false
            # positives are rare and easier to audit than a full disassembler.
            cursor += 1
    return refs


def load_dex_files(apk_path: Path) -> list[Dex]:
    dexes = []
    with zipfile.ZipFile(apk_path) as archive:
        for name in archive.namelist():
            if name.startswith("classes") and name.endswith(".dex"):
                dexes.append(parse_dex(name, archive.read(name)))
    return dexes


def main() -> int:
    parser = argparse.ArgumentParser(description="Find dex methods that reference matching string constants.")
    parser.add_argument("apk")
    parser.add_argument("pattern", nargs="+")
    parser.add_argument("--context", type=int, default=30)
    args = parser.parse_args()

    patterns = [item.lower() for item in args.pattern]
    for dex in load_dex_files(Path(args.apk)):
        target_indexes = {
            index
            for index, value in enumerate(dex.strings)
            if any(pattern in value.lower() for pattern in patterns)
        }
        emit("dex_targets", dex=dex.name, count=len(target_indexes), strings=[dex.strings[index] for index in sorted(target_indexes)[: args.context]])
        if not target_indexes:
            continue

        for method in iter_encoded_methods(dex):
            refs = const_string_refs(dex.data, int(method["code_off"]))
            if not target_indexes.intersection(refs):
                continue
            method_strings = [dex.strings[index] for index in refs if 0 <= index < len(dex.strings)]
            emit(
                "xref",
                dex=dex.name,
                method_class=method["class"],
                declaring_class=method["declaring_class"],
                method_name=method["name"],
                code_off=method["code_off"],
                matched=[dex.strings[index] for index in sorted(target_indexes.intersection(refs))],
                method_strings=method_strings[: args.context],
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

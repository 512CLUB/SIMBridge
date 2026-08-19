#!/usr/bin/env python3
import argparse
import csv
import json
import mimetypes
import os
import plistlib
import re
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import usb.core
import usb.util
from usb.backend import libusb1

from forwarding import ForwardingService
from mobile_access import MobileAccess, is_loopback
from storage import ArchiveSyncService, MessageArchive


def resource_root_candidates():
    candidates = [Path(__file__).resolve().parent]
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS))
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        if sys.platform == "darwin":
            contents = executable.parent.parent
            candidates.extend([contents / "Resources", contents / "Frameworks"])
        else:
            candidates.extend([executable.parent, executable.parent / "_internal"])
    return candidates


def find_resource_root():
    for candidate in resource_root_candidates():
        if (candidate / "static").exists():
            return candidate
    return resource_root_candidates()[0]


ROOT = find_resource_root()
STATIC_ROOT = ROOT / "static"
TARGETS = [(0x2CA3, 0x4006), (0x2C7C, 0x0125)]
USB_LOCK = threading.Lock()
LAUNCH_AGENT_LABEL = "com.wangquanrun.simbridge.login"
LAUNCH_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"

GSM7 = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ"
    "\x1bÆæßÉ !\"#¤%&'()*+,-./"
    "0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXT = {
    10: "\f",
    20: "^",
    40: "{",
    41: "}",
    47: "\\",
    60: "[",
    61: "~",
    62: "]",
    64: "|",
    101: "€",
}


class ModemError(RuntimeError):
    pass


def json_bytes(value, status=200):
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return status, "application/json; charset=utf-8", data


def libusb_candidates():
    if sys.platform == "win32":
        candidates = [
            ROOT / "libusb-1.0.dll",
            ROOT / "lib" / "libusb-1.0.dll",
        ]
        if getattr(sys, "frozen", False):
            executable_dir = Path(sys.executable).resolve().parent
            candidates.extend([
                executable_dir / "libusb-1.0.dll",
                executable_dir / "lib" / "libusb-1.0.dll",
                executable_dir / "_internal" / "libusb-1.0.dll",
                executable_dir / "_internal" / "lib" / "libusb-1.0.dll",
            ])
        return candidates

    candidates = [
        ROOT / "libusb-1.0.dylib",
        ROOT / "lib" / "libusb-1.0.dylib",
        Path("/opt/homebrew/lib/libusb-1.0.dylib"),
        Path("/opt/homebrew/opt/libusb/lib/libusb-1.0.dylib"),
        Path("/usr/local/lib/libusb-1.0.dylib"),
        Path("/usr/local/opt/libusb/lib/libusb-1.0.dylib"),
    ]
    if getattr(sys, "frozen", False):
        contents = Path(sys.executable).resolve().parent.parent
        candidates.extend([
            contents / "Resources" / "libusb-1.0.dylib",
            contents / "Resources" / "lib" / "libusb-1.0.dylib",
            contents / "Frameworks" / "libusb-1.0.dylib",
            contents / "Frameworks" / "lib" / "libusb-1.0.dylib",
        ])
    return candidates


def find_libusb_path():
    for candidate in libusb_candidates():
        if candidate.exists():
            return candidate
    return None


def find_target():
    libusb_path = find_libusb_path()
    if libusb_path:
        backend = libusb1.get_backend(find_library=lambda _: str(libusb_path))
    else:
        backend = libusb1.get_backend()
    if backend is None:
        searched = "、".join(str(path) for path in libusb_candidates())
        raise ModemError(f"没有找到 libusb，已搜索：{searched}")
    for vendor, product in TARGETS:
        dev = usb.core.find(idVendor=vendor, idProduct=product, backend=backend)
        if dev:
            return dev
    raise ModemError("没有找到 Baiwang/DJI/Quectel 4G 模块")


def read_until(dev, ep_in, patterns=(b"\r\nOK\r\n", b"\r\nERROR\r\n"), timeout_ms=3000):
    chunks = []
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            chunk = bytes(dev.read(ep_in.bEndpointAddress, ep_in.wMaxPacketSize, timeout=150))
            if chunk:
                chunks.append(chunk)
                data = b"".join(chunks)
                if any(pattern in data for pattern in patterns):
                    return data
        except usb.core.USBTimeoutError:
            pass
    return b"".join(chunks)


class Modem:
    def __init__(self):
        self.dev = find_target()
        self.interface = None
        self.ep_out = None
        self.ep_in = None
        self._open_at_interface()

    def _open_at_interface(self):
        try:
            self.dev.set_configuration()
        except Exception:
            pass

        cfg = self.dev.get_active_configuration()
        candidates = []
        for intf in cfg:
            eps = list(intf)
            out_eps = [
                ep for ep in eps
                if usb.util.endpoint_type(ep.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
                and usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT
            ]
            in_eps = [
                ep for ep in eps
                if usb.util.endpoint_type(ep.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
                and usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN
            ]
            if out_eps and in_eps:
                candidates.append((intf.bInterfaceNumber, out_eps[0], in_eps[0]))

        for number, ep_out, ep_in in candidates:
            try:
                usb.util.claim_interface(self.dev, number)
                self.dev.write(ep_out.bEndpointAddress, b"AT\r\n", timeout=500)
                response = read_until(self.dev, ep_in, timeout_ms=1200)
                if b"OK" in response:
                    self.interface = number
                    self.ep_out = ep_out
                    self.ep_in = ep_in
                    return
                usb.util.release_interface(self.dev, number)
            except Exception:
                try:
                    usb.util.release_interface(self.dev, number)
                except Exception:
                    pass
        raise ModemError("没有找到可用 AT 接口")

    def close(self):
        try:
            usb.util.release_interface(self.dev, self.interface)
        except Exception:
            pass
        usb.util.dispose_resources(self.dev)

    def at(self, command, timeout_ms=3000):
        self.dev.write(self.ep_out.bEndpointAddress, command.encode("ascii") + b"\r\n", timeout=800)
        data = read_until(self.dev, self.ep_in, timeout_ms=timeout_ms)
        return data.decode("ascii", "replace")

    def send_pdu(self, pdu_hex, timeout_ms=60000):
        tpdu_len = len(pdu_hex) // 2 - 1
        self.dev.write(self.ep_out.bEndpointAddress, f"AT+CMGS={tpdu_len}\r".encode("ascii"), timeout=800)
        prompt = read_until(self.dev, self.ep_in, patterns=(b">", b"ERROR"), timeout_ms=5000)
        if b">" not in prompt:
            raise ModemError(prompt.decode("ascii", "replace").strip() or "模块没有进入短信发送提示符")
        self.dev.write(self.ep_out.bEndpointAddress, pdu_hex.encode("ascii") + b"\x1a", timeout=1000)
        return read_until(self.dev, self.ep_in, timeout_ms=timeout_ms).decode("ascii", "replace")


def with_modem(fn):
    with USB_LOCK:
        modem = Modem()
        try:
            return fn(modem)
        finally:
            modem.close()


def clean_response(response):
    return [line.strip() for line in response.replace("\r", "").split("\n") if line.strip()]


def parse_value_line(response, prefix):
    for line in clean_response(response):
        if line.startswith(prefix):
            return line
    return ""


def parse_csv_payload(line, prefix):
    if not line.startswith(prefix):
        return []
    payload = line[len(prefix):].strip()
    if payload.startswith(":"):
        payload = payload[1:].strip()
    try:
        return next(csv.reader([payload], skipinitialspace=True))
    except Exception:
        return []


def status_payload():
    commands = {
        "sim": "AT+CPIN?",
        "signal": "AT+CSQ",
        "operator": "AT+COPS?",
        "mode": "AT+CMGF?",
        "storage": "AT+CPMS?",
        "smsc": "AT+CSCA?",
        "notify": "AT+CNMI?",
        "network": 'AT+QNWINFO',
    }

    def run(modem):
        raw = {key: modem.at(command) for key, command in commands.items()}
        return {
            "ok": True,
            "updatedAt": datetime.now().isoformat(timespec="seconds"),
            "raw": {key: clean_response(value) for key, value in raw.items()},
            "sim": parse_value_line(raw["sim"], "+CPIN:").replace("+CPIN:", "").strip(),
            "signal": parse_signal(parse_value_line(raw["signal"], "+CSQ:")),
            "operator": parse_operator(parse_value_line(raw["operator"], "+COPS:")),
            "storage": parse_storage(parse_value_line(raw["storage"], "+CPMS:")),
            "smsc": parse_smsc(parse_value_line(raw["smsc"], "+CSCA:")),
            "network": parse_value_line(raw["network"], "+QNWINFO:"),
            "mode": "PDU" if "+CMGF: 0" in raw["mode"] else "Text",
            "notify": parse_value_line(raw["notify"], "+CNMI:"),
        }

    return with_modem(run)


def radio_payload():
    commands = {
        "servingCell": 'AT+QENG="servingcell"',
        "network": "AT+QNWINFO",
        "operator": "AT+COPS?",
        "spn": "AT+QSPN",
        "signal": "AT+CSQ",
    }

    def run(modem):
        raw = {key: modem.at(command, timeout_ms=5000) for key, command in commands.items()}
        serving_cell = parse_serving_cell(parse_value_line(raw["servingCell"], "+QENG:"))
        network = parse_network_info(parse_value_line(raw["network"], "+QNWINFO:"))
        spn = parse_spn(parse_value_line(raw["spn"], "+QSPN:"))
        operator = parse_operator(parse_value_line(raw["operator"], "+COPS:"))
        signal = parse_signal(parse_value_line(raw["signal"], "+CSQ:"))
        plmn = serving_cell.get("plmn") or network.get("plmn") or spn.get("plmn")
        mcc = serving_cell.get("mcc") or network.get("mcc")
        mnc = serving_cell.get("mnc") or network.get("mnc")

        band_label = serving_cell.get("bandLabel") or network.get("band")
        payload = {
            "ok": True,
            "updatedAt": datetime.now().isoformat(timespec="seconds"),
            "operator": {
                "name": operator.get("name") or spn.get("shortName") or spn.get("fullName"),
                "access": operator.get("access") or serving_cell.get("rat"),
                "spn": spn,
                "raw": operator.get("raw"),
            },
            "plmn": plmn,
            "mcc": mcc,
            "mnc": mnc,
            "network": network,
            "servingCell": serving_cell,
            "signal": signal,
            "raw": {key: clean_response(value) for key, value in raw.items()},
        }
        payload.update({
            "rat": serving_cell.get("rat") or network.get("access"),
            "duplex": serving_cell.get("duplex"),
            "band": band_label,
            "bandNumber": serving_cell.get("band") or network.get("bandNumber"),
            "earfcn": serving_cell.get("earfcn") or network.get("earfcn"),
            "cellId": serving_cell.get("cellId"),
            "cellIdHex": serving_cell.get("cellIdHex"),
            "eNodeB": serving_cell.get("eNodeB"),
            "enodeb": serving_cell.get("eNodeB"),
            "sector": serving_cell.get("sector"),
            "pci": serving_cell.get("pci"),
            "tac": serving_cell.get("tac"),
            "rsrp": serving_cell.get("rsrp"),
            "rsrq": serving_cell.get("rsrq"),
            "rssi": serving_cell.get("rssi"),
            "sinr": serving_cell.get("sinr"),
            "srxlev": serving_cell.get("srxlev"),
        })
        return payload

    return with_modem(run)


def parse_signal(line):
    match = re.search(r"\+CSQ:\s*(\d+),(\d+)", line)
    if not match:
        return {"raw": line}
    rssi = int(match.group(1))
    ber = int(match.group(2))
    dbm = None if rssi == 99 else -113 + 2 * rssi
    if rssi == 99:
        quality = "未知"
    elif rssi >= 24:
        quality = "很好"
    elif rssi >= 16:
        quality = "可用"
    elif rssi >= 10:
        quality = "较弱"
    else:
        quality = "很弱"
    return {"rssi": rssi, "ber": ber, "dbm": dbm, "quality": quality, "raw": line}


def maybe_int(value, base=10):
    try:
        return int(str(value), base)
    except (TypeError, ValueError):
        return None


def decode_ucs2_hex(value):
    if re.fullmatch(r"[0-9A-Fa-f]+", value or "") and len(value) % 4 == 0:
        try:
            decoded = bytes.fromhex(value).decode("utf-16-be")
            if decoded:
                return decoded
        except UnicodeDecodeError:
            pass
    return value


def parse_network_info(line):
    fields = parse_csv_payload(line, "+QNWINFO")
    if len(fields) < 4:
        return {"raw": line}
    plmn = fields[1]
    mcc = plmn[:3] if len(plmn) >= 5 else ""
    mnc = plmn[3:] if len(plmn) >= 5 else ""
    band_match = re.search(r"BAND\s+(\d+)", fields[2], re.I)
    return {
        "access": fields[0],
        "plmn": plmn,
        "mcc": mcc,
        "mnc": mnc,
        "band": fields[2],
        "bandNumber": maybe_int(band_match.group(1)) if band_match else None,
        "earfcn": maybe_int(fields[3]),
        "raw": line,
    }


def parse_spn(line):
    fields = parse_csv_payload(line, "+QSPN")
    if len(fields) < 5:
        return {"raw": line}
    return {
        "fullName": decode_ucs2_hex(fields[0]),
        "shortName": decode_ucs2_hex(fields[1]),
        "providerName": decode_ucs2_hex(fields[2]),
        "displayCondition": maybe_int(fields[3]),
        "plmn": fields[4],
        "raw": line,
    }


def parse_serving_cell(line):
    fields = parse_csv_payload(line, "+QENG")
    if len(fields) < 18 or fields[0] != "servingcell":
        return {"raw": line}

    cell_id_raw = fields[6]
    cell_id = maybe_int(cell_id_raw, 16)
    mcc = fields[4]
    mnc = fields[5]
    band = maybe_int(fields[9])
    result = {
        "state": fields[1],
        "rat": fields[2],
        "duplex": fields[3],
        "mcc": mcc,
        "mnc": mnc,
        "plmn": f"{mcc}{mnc}",
        "cellId": cell_id,
        "cellIdHex": cell_id_raw,
        "eNodeB": cell_id // 256 if cell_id is not None else None,
        "sector": cell_id % 256 if cell_id is not None else None,
        "pci": maybe_int(fields[7]),
        "earfcn": maybe_int(fields[8]),
        "band": band,
        "bandLabel": f"LTE BAND {band}" if band is not None else "",
        "uplinkBandwidth": maybe_int(fields[10]),
        "downlinkBandwidth": maybe_int(fields[11]),
        "tac": maybe_int(fields[12]),
        "rsrp": maybe_int(fields[13]),
        "rsrq": maybe_int(fields[14]),
        "rssi": maybe_int(fields[15]),
        "sinr": maybe_int(fields[16]),
        "srxlev": maybe_int(fields[17]),
        "raw": line,
    }
    return result


OPERATOR_CHINESE_MAP = {
    "CHN-CT": "中国电信",
    "CTCC": "中国电信",
    "CHINA TELECOM": "中国电信",
    "TELECOM": "中国电信",
    "CHN-UNICOM": "中国联通",
    "UNICOM": "中国联通",
    "CHINA UNICOM": "中国联通",
    "CMCC": "中国移动",
    "CHN-CMCC": "中国移动",
    "CHINA MOBILE": "中国移动",
    "CHN-CB": "中国广电",
    "CBN": "中国广电",
    "CHINA BROADNET": "中国广电",
}

PLMN_CHINESE_MAP = {
    "46003": "中国电信",
    "46005": "中国电信",
    "46011": "中国电信",
    "46001": "中国联通",
    "46006": "中国联通",
    "46009": "中国联通",
    "46000": "中国移动",
    "46002": "中国移动",
    "46004": "中国移动",
    "46007": "中国移动",
    "46008": "中国移动",
    "46015": "中国广电",
}


def map_operator_chinese(name, plmn=None):
    if plmn and str(plmn) in PLMN_CHINESE_MAP:
        return PLMN_CHINESE_MAP[str(plmn)]
    if not name:
        return ""
    name_str = str(name).strip().upper()
    for key, val in OPERATOR_CHINESE_MAP.items():
        if key in name_str:
            return val
    return name


def parse_operator(line):
    match = re.search(r'"([^"]+)"\s*,\s*(\d+)\s*$', line)
    if not match:
        return {"name": "", "chineseName": "", "access": "", "raw": line}
    raw_name = match.group(1)
    access = {"0": "GSM", "2": "UTRAN", "7": "LTE"}.get(match.group(2), match.group(2))
    chinese_name = map_operator_chinese(raw_name)
    return {"name": chinese_name or raw_name, "rawName": raw_name, "chineseName": chinese_name, "access": access, "raw": line}



def parse_storage(line):
    match = re.search(r'"([^"]+)",(\d+),(\d+)', line)
    if not match:
        return {"raw": line}
    return {"name": match.group(1), "used": int(match.group(2)), "total": int(match.group(3)), "raw": line}


def parse_smsc(line):
    match = re.search(r'"([^"]+)"', line)
    return {"number": match.group(1) if match else "", "raw": line}


def semi_octets(number):
    digits = re.sub(r"\D", "", number)
    if len(digits) % 2:
        digits += "F"
    return "".join(digits[i + 1] + digits[i] for i in range(0, len(digits), 2))


def decode_semi_octets(hexstr, digits):
    out = []
    for i in range(0, len(hexstr), 2):
        pair = hexstr[i:i + 2]
        if len(pair) < 2:
            break
        out.extend([pair[1], pair[0]])
    return "".join(out)[:digits].replace("F", "")


def is_alphanumeric_address(type_hex):
    try:
        type_of_address = int(type_hex, 16)
    except ValueError:
        return False
    return (type_of_address & 0x70) == 0x50


def address_field_hex_length(address_len, type_hex):
    if is_alphanumeric_address(type_hex):
        return ((address_len * 7 + 7) // 8) * 2
    return ((address_len + 1) // 2) * 2


def decode_address(hexstr, address_len, type_hex):
    if is_alphanumeric_address(type_hex):
        return decode_gsm7(hexstr, address_len)
    value = decode_semi_octets(hexstr, address_len)
    if type_hex == "91" and value:
        return "+" + value
    return value


def normalize_recipient_number(number):
    compact = re.sub(r"[\s\-()]", "", number.strip())
    digits = re.sub(r"\D", "", compact)
    if not digits:
        raise ModemError("号码不能为空")
    if compact.startswith("+"):
        return "+" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+86" + digits
    if len(digits) == 13 and digits.startswith("86"):
        return "+" + digits
    return digits


def build_unicode_submit_pdu(number, text):
    number = normalize_recipient_number(number)
    digits = re.sub(r"\D", "", number)
    encoded = text.encode("utf-16-be")
    if len(encoded) > 140:
        raise ModemError("当前版本只支持单条 UCS2 短信，内容请控制在 70 个中文字符以内")
    toa = "91" if number.strip().startswith("+") else "81"
    return (
        "00"
        + "11"
        + "00"
        + f"{len(digits):02X}"
        + toa
        + semi_octets(number)
        + "00"
        + "08"
        + "AA"
        + f"{len(encoded):02X}"
        + encoded.hex().upper()
    )


def decode_gsm7(user_data_hex, septet_count, bit_offset=0):
    data = bytes.fromhex(user_data_hex)
    bits = []
    for byte in data:
        bits.extend((byte >> bit) & 1 for bit in range(8))
    chars = []
    escaped = False
    for i in range(septet_count):
        value = 0
        for bit in range(7):
            pos = bit_offset + i * 7 + bit
            if pos < len(bits):
                value |= bits[pos] << bit
        if escaped:
            chars.append(GSM7_EXT.get(value, ""))
            escaped = False
        elif value == 27:
            escaped = True
        else:
            chars.append(GSM7[value] if value < len(GSM7) else "")
    return "".join(chars)


def alphabet_from_dcs(dcs):
    if (dcs & 0xC0) == 0x00:
        return {
            0x00: "gsm7",
            0x04: "8bit",
            0x08: "ucs2",
        }.get(dcs & 0x0C, "gsm7")
    if (dcs & 0xF0) in (0xC0, 0xD0):
        return "gsm7"
    if (dcs & 0xF0) == 0xE0:
        return "ucs2"
    if (dcs & 0xF0) == 0xF0:
        return "8bit" if dcs & 0x04 else "gsm7"
    return "gsm7"


def decode_text_octets(data):
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if text and "\ufffd" not in text:
            return text
    return data.decode("latin-1", "replace")


def udh_offset(user_data):
    if not user_data:
        return 0
    return min(len(user_data), user_data[0] + 1)


def decode_user_data(dcs, udl, user_data_hex, udhi=False):
    data = bytes.fromhex(user_data_hex)
    alphabet = alphabet_from_dcs(dcs)
    offset = udh_offset(data) if udhi else 0

    if alphabet == "ucs2":
        payload = data[offset:udl]
        return payload.decode("utf-16-be", "replace")
    if alphabet == "gsm7":
        header_septets = ((offset * 8) + 6) // 7 if offset else 0
        payload_septets = max(0, udl - header_septets)
        bit_offset = header_septets * 7
        return decode_gsm7(user_data_hex, payload_septets, bit_offset=bit_offset)
    if alphabet == "8bit":
        return decode_text_octets(data[offset:udl])
    return decode_text_octets(data[offset:])


def decode_scts(hexstr):
    parts = [decode_semi_octets(hexstr[i:i + 2], 2) for i in range(0, 14, 2)]
    if len(parts) < 7:
        return ""
    year, month, day, hour, minute, second, _tz = parts
    return f"20{year}-{month}-{day} {hour}:{minute}:{second}"


def decode_pdu(pdu):
    try:
        pos = 0
        smsc_len = int(pdu[pos:pos + 2], 16)
        pos += 2 + smsc_len * 2
        first = int(pdu[pos:pos + 2], 16)
        pos += 2
        mti = first & 0x03
        udhi = bool(first & 0x40)
        if mti == 0:
            sender_len = int(pdu[pos:pos + 2], 16)
            pos += 2
            sender_type = pdu[pos:pos + 2]
            pos += 2
            sender_octets = address_field_hex_length(sender_len, sender_type)
            sender = decode_address(pdu[pos:pos + sender_octets], sender_len, sender_type)
            pos += sender_octets
            pid = int(pdu[pos:pos + 2], 16)
            pos += 2
            dcs = int(pdu[pos:pos + 2], 16)
            pos += 2
            timestamp = decode_scts(pdu[pos:pos + 14])
            pos += 14
            udl = int(pdu[pos:pos + 2], 16)
            pos += 2
            text = decode_user_data(dcs, udl, pdu[pos:], udhi=udhi)
            return {
                "kind": "received",
                "peer": sender,
                "timestamp": timestamp,
                "dcs": f"0x{dcs:02X}",
                "pid": f"0x{pid:02X}",
                "udhi": udhi,
                "text": text,
            }
        if mti == 1:
            pos += 2
            dest_len = int(pdu[pos:pos + 2], 16)
            pos += 2
            dest_type = pdu[pos:pos + 2]
            pos += 2
            dest_octets = address_field_hex_length(dest_len, dest_type)
            dest = decode_address(pdu[pos:pos + dest_octets], dest_len, dest_type)
            pos += dest_octets
            pos += 2
            dcs = int(pdu[pos:pos + 2], 16)
            pos += 2
            if first & 0x18:
                pos += 2
            udl = int(pdu[pos:pos + 2], 16)
            pos += 2
            return {
                "kind": "sent",
                "peer": dest,
                "timestamp": "",
                "dcs": f"0x{dcs:02X}",
                "udhi": udhi,
                "text": decode_user_data(dcs, udl, pdu[pos:], udhi=udhi),
            }
        return {"kind": "unknown", "text": "", "peer": "", "timestamp": "", "dcs": "", "error": f"Unsupported MTI {mti}"}
    except Exception as exc:
        return {"kind": "unknown", "text": "", "peer": "", "timestamp": "", "dcs": "", "error": str(exc)}


def parse_cmgl(response):
    lines = clean_response(response)
    messages = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("+CMGL:") and i + 1 < len(lines):
            parts = [part.strip() for part in line.replace("+CMGL:", "", 1).split(",")]
            pdu = lines[i + 1]
            decoded = decode_pdu(pdu)
            messages.append({
                "index": int(parts[0]) if parts and parts[0].isdigit() else None,
                "status": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None,
                "length": int(parts[-1]) if parts and parts[-1].isdigit() else None,
                "pdu": pdu,
                "decoded": decoded,
            })
            i += 2
        else:
            i += 1
    return messages


def list_messages(box="all"):
    cmgl_status = {
        "all": 4,
        "unread": 0,
        "sent": 3,
    }

    def run(modem):
        modem.at("AT+CMGF=0")
        if box == "inbox":
            response = modem.at("AT+CMGL=4", timeout_ms=9000)
            messages = [message for message in parse_cmgl(response) if message.get("status") in (0, 1)]
        else:
            status = cmgl_status.get(box)
            if status is None:
                raise ModemError(f"未知短信筛选：{box}")
            response = modem.at(f"AT+CMGL={status}", timeout_ms=9000)
            messages = parse_cmgl(response)
        return {"ok": True, "box": box, "messages": messages, "raw": clean_response(response)}

    return with_modem(run)


def read_message(index):
    def run(modem):
        modem.at("AT+CMGF=0")
        response = modem.at(f"AT+CMGR={index}", timeout_ms=9000)
        lines = clean_response(response)
        pdu = ""
        for line in lines:
            if re.fullmatch(r"[0-9A-Fa-f]+", line):
                pdu = line
                break
        return {"ok": True, "index": index, "pdu": pdu, "decoded": decode_pdu(pdu) if pdu else {}, "raw": lines}

    return with_modem(run)


def send_message(number, text):
    text = text.strip()
    if not text:
        raise ModemError("短信内容不能为空")
    normalized_to = normalize_recipient_number(number)
    pdu = build_unicode_submit_pdu(normalized_to, text)

    def run(modem):
        modem.at("AT+CMGF=0")
        response = modem.send_pdu(pdu)
        ok = "+CMGS:" in response and "OK" in response
        if ok:
            archive = globals().get("ARCHIVE")
            if archive:
                archive.record_sent(normalized_to, text, pdu)
        return {"ok": ok, "response": clean_response(response), "pduLength": len(pdu) // 2, "to": normalized_to}

    return with_modem(run)


def delete_messages(indices):
    def run(modem):
        results = []
        for idx in indices:
            response = modem.at(f"AT+CMGD={idx}", timeout_ms=6000)
            results.append({"index": idx, "ok": "OK" in response})
        all_ok = all(r["ok"] for r in results) if results else True
        deleted_count = sum(1 for r in results if r["ok"])
        if deleted_count:
            archive = globals().get("ARCHIVE")
            if archive:
                archive.detach_modem_indices([item["index"] for item in results if item["ok"]])
        return {"ok": all_ok, "results": results, "deletedCount": deleted_count}

    return with_modem(run)



def current_app_bundle_path():
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    if sys.platform == "win32":
        return executable
    for candidate in (executable, *executable.parents):
        if candidate.suffix == ".app":
            return candidate
    return None


def path_is_under(path, root):
    try:
        path.resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def read_launch_agent():
    if not LAUNCH_AGENT_PATH.exists():
        return {}
    try:
        with LAUNCH_AGENT_PATH.open("rb") as handle:
            return plistlib.load(handle)
    except Exception:
        return {}


def configured_launch_app_path(plist):
    args = plist.get("ProgramArguments") or []
    for arg in reversed(args):
        if isinstance(arg, str) and arg.endswith(".app"):
            return arg
    return ""


def autostart_status():
    if sys.platform == "win32":
        return windows_autostart_status()

    app_path = current_app_bundle_path()
    plist = read_launch_agent()
    configured_path = configured_launch_app_path(plist)
    app_path_text = str(app_path) if app_path else ""
    enabled = bool(app_path_text and configured_path == app_path_text and LAUNCH_AGENT_PATH.exists())
    on_readonly_volume = bool(app_path and path_is_under(app_path, "/Volumes"))
    installed = bool(app_path and (path_is_under(app_path, "/Applications") or path_is_under(app_path, Path.home() / "Applications")))
    available = bool(app_path and not on_readonly_volume)

    if not app_path:
        message = "请在打包后的 macOS App 中设置开机自启动"
    elif on_readonly_volume:
        message = "请先把 App 拖到“应用程序”里，再开启开机自启动"
    elif enabled:
        message = "已开启，登录 macOS 后会自动启动"
    elif configured_path and configured_path != app_path_text:
        message = "检测到旧的自启动配置，可重新开启以更新路径"
    elif installed:
        message = "未开启"
    else:
        message = "未开启，建议先把 App 放到“应用程序”里"

    return {
        "ok": True,
        "enabled": enabled,
        "available": available,
        "installed": installed,
        "appPath": app_path_text,
        "configuredPath": configured_path,
        "plistPath": str(LAUNCH_AGENT_PATH),
        "message": message,
    }


def set_autostart(enabled):
    if sys.platform == "win32":
        return set_windows_autostart(enabled)

    app_path = current_app_bundle_path()
    if not app_path:
        raise ModemError("请在打包后的 macOS App 中设置开机自启动")
    if path_is_under(app_path, "/Volumes"):
        raise ModemError("请先把 App 拖到“应用程序”里，再开启开机自启动")

    if enabled:
        LAUNCH_AGENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        plist = {
            "Label": LAUNCH_AGENT_LABEL,
            "ProgramArguments": ["/usr/bin/open", str(app_path)],
            "RunAtLoad": True,
        }
        with LAUNCH_AGENT_PATH.open("wb") as handle:
            plistlib.dump(plist, handle, sort_keys=False)
    elif LAUNCH_AGENT_PATH.exists():
        LAUNCH_AGENT_PATH.unlink()

    return autostart_status()


def windows_autostart_status():
    app_path = current_app_bundle_path()
    app_path_text = str(app_path) if app_path else ""
    configured_path = ""
    if app_path:
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            ) as key:
                configured_path = winreg.QueryValueEx(key, "SIMBridge")[0]
        except (FileNotFoundError, OSError):
            pass

    expected = f'"{app_path_text}"' if app_path_text else ""
    enabled = bool(expected and configured_path == expected)
    if not app_path:
        message = "请在打包后的 Windows 程序中设置开机自启动"
    elif enabled:
        message = "已开启，登录 Windows 后会自动启动"
    elif configured_path:
        message = "检测到旧的自启动配置，可重新开启以更新路径"
    else:
        message = "未开启"
    return {
        "ok": True,
        "enabled": enabled,
        "available": bool(app_path),
        "installed": bool(app_path),
        "appPath": app_path_text,
        "configuredPath": configured_path,
        "plistPath": "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run",
        "message": message,
    }


def set_windows_autostart(enabled):
    app_path = current_app_bundle_path()
    if not app_path:
        raise ModemError("请在打包后的 Windows 程序中设置开机自启动")
    try:
        import winreg
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(key, "SIMBridge", 0, winreg.REG_SZ, f'"{app_path}"')
            else:
                try:
                    winreg.DeleteValue(key, "SIMBridge")
                except FileNotFoundError:
                    pass
    except OSError as exc:
        raise ModemError(f"无法更新 Windows 开机自启动：{exc}") from exc
    return windows_autostart_status()


ARCHIVE = MessageArchive()
ARCHIVE_SYNC = ArchiveSyncService(ARCHIVE, list_messages)
FORWARDER = ForwardingService(list_messages)
MOBILE_ACCESS = MobileAccess()


class Handler(BaseHTTPRequestHandler):
    server_version = "SIMBridgePanel/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def send_body(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_headers_only(self, status, content_type, length):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def send_json(self, value, status=200):
        self.send_body(*json_bytes(value, status=status))

    def client_address_text(self):
        return self.client_address[0]

    def authorized(self):
        return MOBILE_ACCESS.authorized(
            self.headers.get("Authorization", ""), self.client_address_text()
        )

    def require_authorization(self):
        if self.authorized():
            return True
        self.send_json({"ok": False, "error": "请先验证账号和密码，再输入 Mac 上的配对码"}, status=401)
        return False

    def require_local(self):
        if is_loopback(self.client_address_text()):
            return True
        self.send_json({"ok": False, "error": "只能在 Mac 本机修改手机登录账号"}, status=403)
        return False

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/mobile":
                return self.send_json(
                    MOBILE_ACCESS.info(self.client_address_text(), self.server.server_port)
                )
            if parsed.path == "/api/mobile/account":
                if not self.require_local():
                    return
                return self.send_json(MOBILE_ACCESS.account_info())
            if parsed.path.startswith("/api/") and not self.require_authorization():
                return
            if parsed.path == "/api/status":
                return self.send_json(status_payload())
            if parsed.path == "/api/radio":
                return self.send_json(radio_payload())
            if parsed.path == "/api/messages":
                qs = parse_qs(parsed.query)
                return self.send_json(
                    ARCHIVE.list(
                        box=qs.get("box", ["all"])[0],
                        query=qs.get("query", [""])[0],
                        limit=qs.get("limit", ["500"])[0],
                        offset=qs.get("offset", ["0"])[0],
                    )
                )
            if parsed.path == "/api/message":
                qs = parse_qs(parsed.query)
                index = int(qs.get("index", ["0"])[0])
                return self.send_json(read_message(index))
            if parsed.path == "/api/autostart":
                return self.send_json(autostart_status())
            if parsed.path == "/api/forwarding":
                return self.send_json(FORWARDER.settings())
            if parsed.path == "/api/archive/status":
                return self.send_json(ARCHIVE_SYNC.status())
            return self.serve_static(parsed.path)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=500)

    def do_HEAD(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
                return self.send_headers_only(200, "application/json; charset=utf-8", len(body))
            return self.serve_static(parsed.path, headers_only=True)
        except Exception:
            self.send_headers_only(500, "application/json; charset=utf-8", 0)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 65536:
                return self.send_json({"ok": False, "error": "Request body too large"}, status=413)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            path = urlparse(self.path).path
            if path == "/api/login":
                try:
                    challenge = MOBILE_ACCESS.login(
                        payload.get("username", ""),
                        payload.get("password", ""),
                        self.client_address_text(),
                    )
                    return self.send_json({"ok": True, "challenge": challenge, "expiresIn": 300})
                except PermissionError as exc:
                    return self.send_json({"ok": False, "error": str(exc)}, status=403)
            if path == "/api/pair":
                try:
                    token = MOBILE_ACCESS.pair(
                        payload.get("code", ""),
                        payload.get("challenge", ""),
                        self.client_address_text(),
                    )
                    return self.send_json({"ok": True, "token": token})
                except PermissionError as exc:
                    return self.send_json({"ok": False, "error": str(exc)}, status=403)
            if path == "/api/mobile/account":
                if not self.require_local():
                    return
                try:
                    return self.send_json(
                        MOBILE_ACCESS.configure(payload.get("username", ""), payload.get("password", ""))
                    )
                except ValueError as exc:
                    return self.send_json({"ok": False, "error": str(exc)}, status=400)
            if path.startswith("/api/") and not self.require_authorization():
                return
            if path == "/api/send":
                return self.send_json(send_message(payload.get("to", ""), payload.get("text", "")))
            if path == "/api/delete":
                raw_indices = payload.get("indices")
                if raw_indices is not None:
                    if not isinstance(raw_indices, list):
                        raw_indices = [raw_indices]
                    indices = [int(x) for x in raw_indices]
                else:
                    indices = [int(payload.get("index", 0))]
                return self.send_json(delete_messages(indices))
            if path == "/api/autostart":
                return self.send_json(set_autostart(bool(payload.get("enabled"))))
            if path == "/api/forwarding":
                return self.send_json(FORWARDER.update(payload))
            if path == "/api/forwarding/test":
                return self.send_json(FORWARDER.send_test(payload))
            if path == "/api/archive/update":
                return self.send_json(
                    ARCHIVE.update(
                        str(payload.get("id", "")),
                        note=payload.get("note") if "note" in payload else None,
                        starred=payload.get("starred") if "starred" in payload else None,
                    )
                )
            if path == "/api/archive/delete":
                return self.send_json(ARCHIVE.delete(str(payload.get("id", ""))))
            if path == "/api/archive/sync":
                count = ARCHIVE_SYNC.sync_once(wait=True)
                return self.send_json({**ARCHIVE_SYNC.status(), "synced": count})
            self.send_json({"ok": False, "error": "Unknown endpoint"}, status=404)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=500)

    def serve_static(self, path, headers_only=False):
        if path == "/":
            path = "/index.html"
        safe = os.path.normpath(path.lstrip("/"))
        target = STATIC_ROOT / safe
        if not str(target.resolve()).startswith(str(STATIC_ROOT.resolve())):
            return self.send_json({"ok": False, "error": "Forbidden"}, status=403)
        if not target.exists() or not target.is_file():
            return self.send_json({"ok": False, "error": "Not found"}, status=404)
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if headers_only:
            return self.send_headers_only(200, content_type, target.stat().st_size)
        self.send_body(200, content_type, target.read_bytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"SIMBridge panel listening on http://{args.host}:{args.port}")
    FORWARDER.start()
    ARCHIVE_SYNC.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        FORWARDER.stop()
        ARCHIVE_SYNC.stop()
        server.server_close()


if __name__ == "__main__":
    main()

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import re
import zlib
import cv2
from pyzbar.pyzbar import decode as zbar_decode
import defusedxml.ElementTree as ET


# ── Field order for each known Secure QR version ────────────────────────
_VERSIONED_FIELDS = [
    "version", "email_mobile_status", "referenceid", "name", "dob",
    "gender", "careof", "district", "landmark", "house", "location",
    "pincode", "postoffice", "state", "street", "subdistrict", "vtc",
    "last_4_digits_mobile_no",
]

_UNVERSIONED_FIELDS = [
    "email_mobile_status", "referenceid", "name", "dob", "gender",
    "careof", "district", "landmark", "house", "location", "pincode",
    "postoffice", "state", "street", "subdistrict", "vtc",
]


@dataclass
class QrResult:
    decoded: bool
    fields: dict


def _locate_qr_payload(image_path: str) -> Optional[str]:
    img = cv2.imread(image_path)
    if img is None:
        return None

    results = zbar_decode(img)
    if not results:
        # Retry with cubic upscaled grayscale for dense/small QR codes
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)
        results = zbar_decode(gray)

    if not results:
        # Fall back to OpenCV's detector
        detector = cv2.QRCodeDetector()
        data, _, _ = detector.detectAndDecode(img)
        return data or None

    return results[0].data.decode("utf-8", errors="ignore")


def _qr_format(payload: str) -> str:
    """Classify a raw QR payload string."""
    s = payload.strip()
    if s.isdigit():
        return "secure"
    if s.startswith("<"):
        return "old_xml"
    return "unknown"


_OLD_XML_ROOT = "PrintLetterBarcodeData"

_XML_ATTR_MAP = {
    "name":         "name",
    "gender":       "gender",
    "dob":          "dob",
    "yob":          "yob",
    "careOf":       "careof",
    "co":           "careof",
    "house":        "house",
    "street":       "street",
    "lm":           "landmark",
    "loc":          "location",
    "vtcName":      "vtc",
    "vtc":          "vtc",
    "po":           "postoffice",
    "dist":         "district",
    "districtName": "district",
    "subdist":      "subdistrict",
    "stateName":    "state",
    "state":        "state",
    "pc":           "pincode",
    "pincode":      "pincode",
    "locality":     "location",
}


def _parse_old_xml_qr(payload: str) -> dict:
    """Parse an old-format Aadhaar XML QR (PrintLetterBarcodeData)."""
    try:
        root = ET.fromstring(payload.strip())
    except Exception as exc:
        raise ValueError(f"XML parse error: {exc}") from exc

    tag = root.tag.split("}")[-1]
    if tag != _OLD_XML_ROOT:
        raise ValueError(f"Unexpected XML root tag '{tag}'")

    raw_uid = root.get("uid", "")
    uid_digits = re.sub(r"\D", "", raw_uid)
    aadhaar_last_4 = uid_digits[-4:] if len(uid_digits) >= 4 else uid_digits

    data: dict = {"aadhaar_last_4_digit": aadhaar_last_4}
    for xml_attr, field_name in _XML_ATTR_MAP.items():
        val = root.get(xml_attr)
        if val is not None and field_name not in data:
            data[field_name] = val.strip()

    if "dob" not in data and "yob" in data:
        data["dob"] = data["yob"]

    return data


def _decode_secure_qr(numeric_payload: str) -> dict:
    """Decode a UIDAI Secure QR (numeric big-int string) into a demographic dictionary."""
    qr_int = int(numeric_payload)
    raw_bytes = qr_int.to_bytes((qr_int.bit_length() + 7) // 8, "big")

    decompressed = zlib.decompress(raw_bytes, 16 + zlib.MAX_WBITS)

    prefix = decompressed[:2].decode("ISO-8859-1", errors="replace")
    is_versioned = len(prefix) == 2 and prefix[0] == "V" and prefix[1].isdigit()
    field_names = list(_VERSIONED_FIELDS if is_versioned else _UNVERSIONED_FIELDS)

    delimiters = [-1]
    for i in range(len(decompressed)):
        if decompressed[i] == 255:
            delimiters.append(i)

    data: dict = {}
    n = min(len(field_names), len(delimiters) - 1)
    for i in range(n):
        start = delimiters[i] + 1
        end = delimiters[i + 1]
        data[field_names[i]] = decompressed[start:end].decode("ISO-8859-1", errors="replace")

    ref = data.get("referenceid", "")
    data["aadhaar_last_4_digit"] = ref[:4] if len(ref) >= 4 else ref

    try:
        status = int(data.get("email_mobile_status", "0"))
    except ValueError:
        status = 0
    data["email"] = status in {1, 3}
    data["mobile"] = status in {2, 3}

    return data


def check_qr(image_path: str) -> QrResult:
    """Detect and decode UIDAI QR demographics from a rendered document page."""
    qr_payload = _locate_qr_payload(image_path)
    if not qr_payload:
        return QrResult(decoded=False, fields={})

    fmt = _qr_format(qr_payload)

    if fmt == "old_xml":
        try:
            xml_fields = _parse_old_xml_qr(qr_payload)
            return QrResult(decoded=True, fields=xml_fields)
        except ValueError:
            return QrResult(decoded=False, fields={})

    if fmt != "secure":
        return QrResult(decoded=False, fields={})

    try:
        fields = _decode_secure_qr(qr_payload)
        safe_fields = {k: v for k, v in fields.items() if k != "referenceid"}
        return QrResult(decoded=True, fields=safe_fields)
    except Exception:
        return QrResult(decoded=False, fields={})

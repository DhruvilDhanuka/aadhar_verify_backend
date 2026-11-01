"""
OCR text extraction and pattern matching for Aadhaar card PDF pages.

Extracts printed text, card numbers, dates of birth, and names from Aadhaar cards,
and provides strict verification against registration data.
"""
from __future__ import annotations

import re
from datetime import date
from dataclasses import dataclass
import numpy as np


DOB_RE_f1 = re.compile(r"\b(\d{2}[/\-.]\d{2}[/\-.]\d{4})\b")
DOB_RE_f2 = re.compile(r"\b((?:19|20-)\d{2}[/\-.](?:0[1-9]|1[0-2])[/\-.](?:0[1-9]|[12]\d|3[01]))\b")
YOB_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

# DOB-specific prefixes (DOB, D.O.B, Date of Birth, जन्म, जम)
DOB_PREFIX_RE = re.compile(
    r"(?:DOB|D\.O\.B|Birth|\u091c\u0928\u094d\u092e|\u091c\u092e)[\s\S]{0,25}?(\d{2}[/\-.]\d{2}[/\-.]\d{4})",
    re.IGNORECASE
)
DOB_PREFIX_RE_Y = re.compile(
    r"(?:DOB|D\.O\.B|Birth|\u091c\u0928\u094d\u092e|\u091c\u092e)[\s\S]{0,25}?((?:19|20)\d{2}[/\-.](?:0[1-9]|1[0-2])[/\-.](?:0[1-9]|[12]\d|3[01]))",
    re.IGNORECASE
)


def normalize_dob_to_ddmmyyyy(raw_dob: str | None) -> str | None:
    """Normalize date of birth string (DD-MM-YYYY or YYYY-MM-DD) to canonical DD-MM-YYYY."""
    if not raw_dob:
        return None
    cleaned = raw_dob.strip().replace("/", "-").replace(".", "-")
    parts = cleaned.split("-")
    if len(parts) == 3:
        # YYYY-MM-DD -> DD-MM-YYYY
        if len(parts[0]) == 4 and len(parts[2]) in (1, 2):
            y, m, d = parts[0], parts[1].zfill(2), parts[2].zfill(2)
            return f"{d}-{m}-{y}"
        # DD-MM-YYYY -> zero-padded
        elif len(parts[2]) == 4 and len(parts[0]) in (1, 2):
            d, m, y = parts[0].zfill(2), parts[1].zfill(2), parts[2]
            return f"{d}-{m}-{y}"
    return cleaned


def dobs_match(dob1: str | None, dob2: str | None) -> bool:
    """Check if two DOB strings match, resolving differences between formats and 4-digit YOB."""
    if not dob1 or not dob2:
        return False
    norm1 = normalize_dob_to_ddmmyyyy(dob1)
    norm2 = normalize_dob_to_ddmmyyyy(dob2)
    if norm1 == norm2:
        return True
    s1, s2 = dob1.strip(), dob2.strip()
    # Check 4-digit Year of Birth
    if len(s1) == 4 and s1.isdigit() and norm2 and norm2.endswith(f"-{s1}"):
        return True
    if len(s2) == 4 and s2.isdigit() and norm1 and norm1.endswith(f"-{s2}"):
        return True
    return False


def calculate_age(dob_str: str | None) -> int | None:
    """Calculate age in years from DD-MM-YYYY, YYYY-MM-DD, or YYYY string relative to today."""
    if not dob_str:
        return None

    cleaned = dob_str.strip().replace("/", "-").replace(".", "-")
    parts = cleaned.split("-")
    today = date.today()

    try:
        if len(parts) == 3:
            # YYYY-MM-DD
            if len(parts[0]) == 4:
                birth_date = date(int(parts[0]), int(parts[1]), int(parts[2]))
            # DD-MM-YYYY
            elif len(parts[2]) == 4:
                birth_date = date(int(parts[2]), int(parts[1]), int(parts[0]))
            else:
                return None
            return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))

        # 4-digit Year of birth only (e.g. '2005')
        if len(parts) == 1 and len(parts[0]) == 4 and parts[0].isdigit():
            return today.year - int(parts[0])

    except (ValueError, TypeError):
        return None

    return None


def is_18_or_older(dob_str: str | None) -> tuple[bool, int | None]:
    """Check if DOB corresponds to someone who is at least 18 years old today."""
    age = calculate_age(dob_str)
    if age is None:
        return False, None
    return age >= 18, age


@dataclass
class OcrResult:
    text: str
    detected_dob: str | None

    def to_dict(self) -> dict:
        return {
            "detected_dob": self.detected_dob,
            "text_snippet": self.text[:200] if self.text else "",
        }


def extract_ocr(pdf_text: str | None = None, image: str | np.ndarray | None = None) -> OcrResult:
    """Extract demographic details from PDF-extracted text, ignoring metadata dates like Issue/Download."""
    raw_text = pdf_text or ""

    # Filter out lines that explicitly denote metadata dates (Issue Date, Download Date)
    clean_lines = [
        line for line in raw_text.splitlines()
        if not re.search(r"(?:issue|download)\s*date", line, re.IGNORECASE)
    ]
    filtered_text = "\n".join(clean_lines)

    # 1. Look for labeled DOB first (highest confidence)
    detected_dob = None
    pref_match = DOB_PREFIX_RE.search(raw_text) or DOB_PREFIX_RE_Y.search(raw_text)
    if pref_match:
        detected_dob = normalize_dob_to_ddmmyyyy(pref_match.group(1))
    else:
        # 2. Search filtered text (where Issue Date and Download Date have been stripped)
        dob_match = DOB_RE_f1.search(filtered_text) or DOB_RE_f2.search(filtered_text)
        if dob_match:
            detected_dob = normalize_dob_to_ddmmyyyy(dob_match.group(1))
        else:
            yob_match = YOB_RE.search(filtered_text)
            if yob_match:
                detected_dob = yob_match.group(1)

    return OcrResult(
        text=raw_text,
        detected_dob=detected_dob,
    )


def normalize_name(name: str) -> str:
    """Lowercase, strip honorific titles (Mr/Ms/etc.), and keep alphabetic words (no abbreviation expansion)."""
    if not name:
        return ""
    name = re.sub(r"\b(mr|ms|mrs|shri|smt|dr)\b\.?", "", name.lower())
    words = re.findall(r"[a-z]+", name)
    return " ".join(words)


def names_match(expected: str | None, actual: str | None) -> bool:
    """Strict name match: all words in the full name must match (allows first/last order differences)."""
    if not expected or not actual:
        return False
    exp_words = normalize_name(expected).split()
    act_words = normalize_name(actual).split()
    if not exp_words or not act_words:
        return False
    return exp_words == act_words or sorted(exp_words) == sorted(act_words)


def extract_name_from_ocr(ocr_text: str) -> str | None:
    """Extract candidate English name located immediately above the DOB line in Aadhaar text, including multi-line names."""
    lines = [line.strip() for line in (ocr_text or "").splitlines() if line.strip()]
    dob_idx = -1
    for i, line in enumerate(lines):
        if re.search(r"(?:DOB|Date of Birth|Birth|\u091c\u0928\u094d\u092e|\u092e\s*\u0924\u0925)", line, re.IGNORECASE):
            dob_idx = i
            break

    if dob_idx > 0:
        name_lines = []
        # Walk backwards from line above DOB, collecting consecutive lines with English letters
        for j in range(dob_idx - 1, -1, -1):
            line = lines[j]
            # Stop if reaching metadata lines (Issue Date, Download Date, VID, etc.)
            if re.search(r"(?:issue|download|date|vid|uid|details|aadhaar)", line, re.IGNORECASE):
                break
            # Collect English lines; stop once reaching a line without English (e.g. Hindi name)
            if re.search(r"[a-zA-Z]", line):
                name_lines.insert(0, line)
            else:
                break

        if name_lines:
            return " ".join(name_lines)
    return None


def match_name_in_ocr(expected_name: str | None, ocr_text: str) -> bool:
    """Strict name match against OCR text (checks full name above DOB first)."""
    if not expected_name or not ocr_text:
        return False

    exp_words = normalize_name(expected_name).split()
    if not exp_words:
        return False

    # 1. Primary Aadhaar layout: English name line(s) directly above the DOB line
    card_name = extract_name_from_ocr(ocr_text)
    if card_name:
        return names_match(expected_name, card_name)

    # 2. Fallback only if DOB line was not found: check if any line has all expected words
    for line in ocr_text.splitlines():
        line_words = normalize_name(line).split()
        if len(line_words) == len(exp_words) and sorted(line_words) == sorted(exp_words):
            return True

    return False


# Alias for backwards compatibility with main.py
fuzzy_match_name = match_name_in_ocr


def match_dob(expected_dob: str | None, ocr_text: str) -> bool:
    """Check if expected DOB matches text found in OCR, ignoring Issue/Download dates."""
    if not expected_dob or not ocr_text:
        return False

    clean_dob = expected_dob.strip()
    norm_ddmmyyyy = normalize_dob_to_ddmmyyyy(clean_dob)

    # Filter out Issue Date and Download Date lines
    clean_lines = [
        line for line in ocr_text.splitlines()
        if not re.search(r"(?:issue|download)\s*date", line, re.IGNORECASE)
    ]
    target_text = "\n".join(clean_lines).lower()

    variants = {
        clean_dob.lower(),
        clean_dob.lower().replace("-", "/"),
        clean_dob.lower().replace("-", "."),
    }

    if norm_ddmmyyyy:
        parts = norm_ddmmyyyy.split("-")
        if len(parts) == 3:
            d, m, y = parts[0], parts[1], parts[2]
            variants.add(f"{d}-{m}-{y}")
            variants.add(f"{d}/{m}/{y}")
            variants.add(f"{d}.{m}.{y}")
            variants.add(f"{y}-{m}-{d}")
            variants.add(f"{y}/{m}/{d}")
            variants.add(f"{y}.{m}.{d}")

    return any(v in target_text for v in variants)

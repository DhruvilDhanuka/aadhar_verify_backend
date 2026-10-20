"""
OCR text extraction and pattern matching for Aadhaar card PDF pages.

Extracts printed text, card numbers, dates of birth, and names from Aadhaar cards,
and provides fuzzy cross-matching against registration data.
"""
from __future__ import annotations

import re
from datetime import date
from dataclasses import dataclass
from difflib import SequenceMatcher
import numpy as np
from rapidfuzz import fuzz


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



NAME_ABBREVIATIONS = {
    "md": "mohammed", "mohd": "mohammed", "mohammad": "mohammed",
    "muhammad": "mohammed", "ku": "kumari", "km": "kumari",
}

def normalize_name(name: str) -> str:
    """Lowercase, strip titles, keep only letters, expand abbreviations."""
    name = re.sub(r"\b(mr|ms|mrs|shri|smt|dr)\b\.?", "", name.lower())
    words = [NAME_ABBREVIATIONS.get(w, w) for w in re.findall(r"[a-z]+", name) if len(w) >= 2]
    return " ".join(words)

def names_match(expected: str | None, actual: str | None, threshold: int = 88) -> bool:
    if not expected or not actual:
        return False
    a, b = normalize_name(expected), normalize_name(actual)
    if not a or not b:
        return False
    return fuzz.token_sort_ratio(a, b) >= threshold

def fuzzy_match_name(expected_name: str | None, ocr_text: str) -> bool:
    """Check if expected name matches text found in OCR using word boundaries and sliding window fuzzy ratio."""
    if not expected_name or not ocr_text:
        return False

    expected_clean = expected_name.strip().lower()
    text_clean = ocr_text.lower()

    if len(expected_clean) < 3:
        return False

    # Exact token / whole-word check
    if re.search(r"\b" + re.escape(expected_clean) + r"\b", text_clean):
        return True

    # Token match (e.g. "Rahul", "Kumar") with word boundaries
    tokens = [t for t in expected_clean.split() if len(t) > 2]
    if tokens and all(re.search(r"\b" + re.escape(t) + r"\b", text_clean) for t in tokens):
        return True

    # Sliding-window SequenceMatcher (handles minor character discrepancies)
    window = len(expected_clean)
    if len(text_clean) < window:
        return False

    best_ratio = 0.0
    for i in range(len(text_clean) - window + 1):
        # Must be bounded by non-alphabetic characters (word boundaries)
        if i > 0 and text_clean[i - 1].isalpha():
            continue
        if (i + window) < len(text_clean) and text_clean[i + window].isalpha():
            continue

        chunk = text_clean[i : i + window]
        ratio = SequenceMatcher(None, expected_clean, chunk).ratio()
        if ratio > best_ratio:
            best_ratio = ratio

    return best_ratio >= 0.88


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

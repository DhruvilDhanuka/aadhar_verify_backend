"""
Aadhaar Verification Pipeline (PDF-Only).

Architecture:
1. Registration Form Data (User Input)
2. UIDAI Secure QR Code Data (Cryptographic Truth from document)
3. Aadhaar Printed Card Text (OCR Data Fallback)
"""
from __future__ import annotations
import numpy as np

from .qr_verify import check_qr
from .ocr import (
    extract_ocr,
    names_match,
    fuzzy_match_name,
    match_dob,
    dobs_match,
    normalize_dob_to_ddmmyyyy,
    is_18_or_older,
)


def run_pipeline(
    image: str | np.ndarray | None = None,
    expected_name: str | None = None,
    expected_dob: str | None = None,
    image_path: str | None = None,
    pdf_text: str | None = None,
) -> dict:
    """Run the complete Aadhaar PDF verification pipeline with QR primary and text fallback. Accepts image array or path and extracted pdf_text."""
    target_image = image if image is not None else image_path
    if target_image is None:
        raise ValueError("Either 'image' (np.ndarray) or 'image_path' (str) must be provided.")
    exp_name = expected_name.strip() if expected_name else None
    exp_dob = normalize_dob_to_ddmmyyyy(expected_dob.strip()) if expected_dob else None

    # Step 1: Registration details are strictly mandatory
    if not exp_name or not exp_dob:
        return _build_response(
            verdict="RE-UPLOAD",
            verdict_reason=(
                "Both Registration Name and Date of Birth are strictly required "
                "to verify your identity against the official Aadhaar document."
            ),
            reupload_message="Please enter both your full Name and Date of Birth as registered, and submit again.",
            name=exp_name,
            dob=exp_dob,
        )

    # Step 2: Attempt to decode QR first
    qr_result = check_qr(target_image)
    ocr_result = None

    # Extract demographic fields if QR is decoded
    qr_name = None
    qr_dob = None
    if qr_result.decoded:
        qr_fields = qr_result.fields or {}
        raw_name = qr_fields.get("name")
        qr_name = raw_name.strip() if raw_name and raw_name.strip() else None

        raw_dob = qr_fields.get("dob") or qr_fields.get("yob")
        qr_dob = raw_dob.strip() if raw_dob and raw_dob.strip() else None

    # Run OCR if QR is unreadable, if name or DOB is missing, or if QR DOB only has 4-digit Year
    needs_ocr = (not qr_result.decoded) or (qr_name is None) or (qr_dob is None) or (qr_dob and len(qr_dob) == 4)
    if needs_ocr:
        ocr_result = extract_ocr(pdf_text=pdf_text, image=target_image)

    # If QR only had 4-digit Year of Birth (e.g. '2006'), find exact full date (DD-MM-YYYY) through OCR
    if qr_dob and len(qr_dob) == 4:
        full_ocr_dob = None
        if ocr_result and ocr_result.detected_dob and len(ocr_result.detected_dob.split("-")) == 3:
            if ocr_result.detected_dob.endswith(f"-{qr_dob}"):
                full_ocr_dob = ocr_result.detected_dob
        elif ocr_result and ocr_result.text and match_dob(exp_dob, ocr_result.text):
            if exp_dob.endswith(f"-{qr_dob}"):
                full_ocr_dob = exp_dob

        if full_ocr_dob:
            qr_dob = full_ocr_dob

    # Verification structure based on detected demographics
    if qr_name and qr_dob:
        return _evaluate_match_and_build_response(
            name_match=names_match(exp_name, qr_name),
            dob_match=dobs_match(exp_dob, qr_dob),
            name=qr_name,
            dob=qr_dob,
            exp_name=exp_name,
            exp_dob=exp_dob,
            success_reason="Verification successful: Identity and Age verified against official UIDAI QR code in signed document.",
        )

    elif qr_name:
        if not ocr_result:
            ocr_result = extract_ocr(pdf_text=pdf_text, image=target_image)

        detected_dob = None
        dob_match = False
        if ocr_result and ocr_result.detected_dob:
            detected_dob = ocr_result.detected_dob
            dob_match = dobs_match(exp_dob, detected_dob)

        # If detected_dob from OCR didn't match, check if expected DOB matches text in OCR
        if not dob_match and ocr_result and ocr_result.text and match_dob(exp_dob, ocr_result.text):
            detected_dob = exp_dob
            dob_match = True

        return _evaluate_match_and_build_response(
            name_match=names_match(exp_name, qr_name),
            dob_match=dob_match,
            name=qr_name,
            dob=detected_dob or exp_dob,
            exp_name=exp_name,
            exp_dob=exp_dob,
            success_reason="Verification successful: Name verified via QR code and Date of Birth verified via printed card text.",
        )

    elif qr_dob:
        if not ocr_result:
            ocr_result = extract_ocr(pdf_text=pdf_text, image=target_image)

        name_match = bool(ocr_result and ocr_result.text and fuzzy_match_name(exp_name, ocr_result.text))
        return _evaluate_match_and_build_response(
            name_match=name_match,
            dob_match=dobs_match(exp_dob, qr_dob),
            name=exp_name if name_match else None,
            dob=qr_dob,
            exp_name=exp_name,
            exp_dob=exp_dob,
            success_reason="Verification successful: Date of Birth verified via QR code and Name verified via printed card text.",
        )

    else:
        # Fallback: QR code could not be read; verify both Name and Date of Birth from card text via OCR
        if not ocr_result:
            ocr_result = extract_ocr(pdf_text=pdf_text, image=target_image)

        if not ocr_result or not ocr_result.text:
            fallback_is_adult, fallback_age = is_18_or_older(exp_dob) if exp_dob else (False, None)
            return _build_response(
                verdict="FAIL",
                verdict_reason="Unable to read the QR code or printed text from the document. The file may be corrupt or unreadable.",
                reupload_message="Please upload your own Aadhaar card that matches your registration details.",
                name=None,
                dob=None,
                age=fallback_age,
                is_adult=fallback_is_adult,
            )

        name_match = bool(ocr_result.text and fuzzy_match_name(exp_name, ocr_result.text))

        detected_dob = None
        dob_match = False
        if ocr_result.detected_dob:
            detected_dob = ocr_result.detected_dob
            dob_match = dobs_match(exp_dob, detected_dob)

        if not dob_match and match_dob(exp_dob, ocr_result.text):
            detected_dob = exp_dob
            dob_match = True

        return _evaluate_match_and_build_response(
            name_match=name_match,
            dob_match=dob_match,
            name=exp_name if name_match else None,
            dob=detected_dob,
            exp_name=exp_name,
            exp_dob=exp_dob,
            success_reason="Verification successful: Identity and Age verified via printed card text.",
        )


def _evaluate_match_and_build_response(
    name_match: bool,
    dob_match: bool,
    name: str | None,
    dob: str | None,
    exp_name: str,
    exp_dob: str,
    success_reason: str,
) -> dict:
    """Helper function to evaluate identity matching and 18+ age criteria, returning a unified response."""
    verified_dob = dob if (dob and len(dob.split("-")) == 3) else exp_dob
    is_adult, age = is_18_or_older(verified_dob)

    if name_match and dob_match:
        if not is_adult:
            age_str = f"{age} years old" if age is not None else "under 18"
            return _build_response(
                verdict="FAIL",
                verdict_reason=f"Age verification failed: Attendee is {age_str}.",
                reupload_message="Attendees under 18 years of age are not eligible to attend.",
                name=name,
                dob=dob,
                age=age,
                is_adult=is_adult,
            )
        return _build_response(
            verdict="PASS",
            verdict_reason=success_reason,
            name=name,
            dob=dob,
            age=age,
            is_adult=is_adult,
        )
    else:
        mismatches = []
        if not name_match:
            mismatches.append("Name")
        if not dob_match:
            mismatches.append("Date of Birth")
        return _build_response(
            verdict="FAIL",
            verdict_reason=(
                f"Identity mismatch on: {', '.join(mismatches)}. "
                f"The registration details do not match the document details "
                f"(Registered: {exp_name}, {exp_dob} | Document: {name or 'N/A'}, {dob or 'N/A'})."
            ),
            reupload_message="Please upload your own Aadhaar card that matches your registration details.",
            name=name,
            dob=dob,
            age=age,
            is_adult=is_adult,
        )


def _build_response(
    verdict: str,
    verdict_reason: str,
    name: str | None = None,
    dob: str | None = None,
    age: int | None = None,
    is_adult: bool | None = None,
    reupload_message: str | None = None,
) -> dict:
    """Build a unified, lightweight JSON response for the frontend."""
    return {
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "reupload_message": reupload_message,
        "name": name,
        "dob": dob,
        "age": age,
        "is_adult": is_adult,
    }

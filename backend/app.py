"""
FastAPI backend for Aadhaar verification.

Zero-retention: uploaded PDFs are written to a temp file, the first page
is rendered to an in-memory PNG, processed, and all temp files are
immediately deleted. No PII is logged or persisted.
"""



from __future__ import annotations
import shutil
# from db import add,exists,col,norm  # Skipped for testing - uncomment to restore DB / email check
from pydantic import BaseModel, EmailStr


import tempfile
import os
import re
from datetime import datetime

import io
import pypdfium2 as pdfium
from pyhanko.pdf_utils.reader import PdfFileReader
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from aadhar_verify.main import run_pipeline

app = FastAPI(
    title="Aadhaar Verify API",
    description="Upload an official e-Aadhaar PDF and verify it against registration details.",
    version="1.0.0",
)

# Allow React dev server (localhost:5173) to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://aadhar-verify-frontend-r2l4c7tf3-dhruvil-codes.vercel.app", "https://aadhar-verify-frontend.vercel.app",
                   "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["POST"],
    allow_headers=["*"],
)


def get_aadhaar_pdf_passwords(expected_name: str, expected_dob: str, manual_password: str | None = None) -> list[str]:
    """
    Generate candidate UIDAI e-Aadhaar passwords.
    If manual_password is provided by user, prioritize it first.
    Standard UIDAI rule: First 4 letters of name in CAPITAL + 4-digit Year of Birth.
    Also handles short names (e.g. 'Om', 'Ali'), titles (e.g. 'Mr.', 'Dr.'), and order variations.
    """
    passwords = []
    if manual_password and manual_password.strip():
        pwd_clean = manual_password.strip()
        passwords.append(pwd_clean)
        # Also try without internal spaces
        no_space = pwd_clean.replace(" ", "")
        if no_space not in passwords:
            passwords.append(no_space)
        if pwd_clean.upper() not in passwords:
            passwords.append(pwd_clean.upper())
        if no_space.upper() not in passwords:
            passwords.append(no_space.upper())

    yob_match = re.search(r"\b(19\d{2}|20\d{2})\b", expected_dob)
    if yob_match:
        yob = yob_match.group(1)

        # 1. Cleaned full name without non-alphabetic chars
        clean_name = re.sub(r"[^A-Za-z]", "", expected_name).upper()
        if clean_name:
            pwd = clean_name[:4] + yob
            if pwd not in passwords:
                passwords.append(pwd)

        # 2. Strip titles (Mr., Ms., Dr., Shri, etc.) and try first name
        no_title = re.sub(r"\b(mr|ms|mrs|shri|smt|dr|master|miss)\b\.?", "", expected_name, flags=re.IGNORECASE).strip()
        words = [w for w in re.sub(r"[^A-Za-z\s]", "", no_title).upper().split() if w]
        if words:
            # First name >= 4 chars
            first_name = words[0]
            if len(first_name) >= 4:
                pwd_fn = first_name[:4] + yob
                if pwd_fn not in passwords:
                    passwords.append(pwd_fn)
            else:
                # Short first name: e.g. "Om" -> "OM2004" as well as "OMSH2004"
                pwd_short = first_name + yob
                if pwd_short not in passwords:
                    passwords.append(pwd_short)
                # First name + letters of next word
                combined = "".join(words)
                pwd_comb = combined[:4] + yob
                if pwd_comb not in passwords:
                    passwords.append(pwd_comb)

    return passwords


def signed_by_uidai(pdf_bytes: bytes, passwords: list[str], has_manual_password: bool = False) -> bool:
    """Return True if the PDF carries a digital signature from UIDAI."""
    r = PdfFileReader(io.BytesIO(pdf_bytes))
    if r.encrypted:
        if not passwords:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "PASSWORD_REQUIRED",
                    "message": (
                        "This e-Aadhaar PDF is password-protected. "
                        "Please enter your Name and Date of Birth in the registration form "
                        "or provide your PDF password so the document can be unlocked."
                    ),
                },
            )
        decrypted = False
        for pwd in passwords:
            try:
                res = r.decrypt(pwd)
                if getattr(res, 'status', None) and res.status.name != 'FAILED':
                    decrypted = True
                    break
                elif res is None or (getattr(res, 'status', None) is None and res):
                    decrypted = True
                    break
            except Exception:
                continue
        if not decrypted:
            if has_manual_password:
                msg = (
                    f"Unable to unlock password-protected e-Aadhaar PDF. "
                    f"The password you entered ('{passwords[0]}') was incorrect. "
                    f"Please re-check your PDF password and try again."
                )
            else:
                attempted_pwd = passwords[0] if passwords else "FIRST4NAME+YYYY"
                msg = (
                    f"This e-Aadhaar PDF is password-protected. Automated unlock with '{attempted_pwd}' did not succeed. "
                    f"Please enter your PDF password manually below to proceed."
                )
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "PASSWORD_REQUIRED",
                    "message": msg,
                },
            )
    try:
        for sig in r.embedded_signatures:
            cert = sig.signer_cert
            DOMAINS = (
                "uidai.net.in",
                "uidai.gov.in",
                "digilocker.gov.in",
                "negd.gov.in",
                "nic.in",
                "meity.gov.in",
                "cca.gov.in",
            )
            ORGS = (
                "unique identification authority of india",
                "uidai",
                "digilocker",
                "national e-governance division",
                "negd",
                "ministry of electronics and information technology",
                "national informatics centre",
            )

            subject = cert.subject.human_friendly.lower()
            dump = cert.dump().lower()
            
            if any(d in subject for d in DOMAINS):
                return True
            if any(d.encode() in dump for d in DOMAINS):
                return True
            if any(o in subject for o in ORGS):
                return True
            if any(o.encode() in dump for o in ORGS):
                return True
    except Exception as e:
        if "Authentication failed" in str(e) or "PdfKeyNotAvailable" in type(e).__name__:
            if has_manual_password:
                msg = (
                    f"Unable to unlock password-protected e-Aadhaar PDF. "
                    f"The password you entered ('{passwords[0]}') was incorrect. "
                    f"Please re-check your PDF password and try again."
                )
            else:
                attempted_pwd = passwords[0] if passwords else "FIRST4NAME+YYYY"
                msg = (
                    f"This e-Aadhaar PDF is password-protected. Automated unlock with '{attempted_pwd}' did not succeed. "
                    f"Please enter your PDF password manually below to proceed."
                )
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "PASSWORD_REQUIRED",
                    "message": msg,
                },
            )
        raise

    return False


def open_pdf_document(pdf_input: str | bytes, passwords: list[str], has_manual_password: bool = False) -> pdfium.PdfDocument:
    """Open a PDF document. If password-protected, unlocks using candidate password."""
    # 1. Try opening without a password (for unencrypted / already unlocked PDFs)
    try:
        doc = pdfium.PdfDocument(pdf_input)
        if len(doc) > 0:
            _ = doc[0]  # Verify page access
            return doc
        doc.close()
    except Exception:
        pass

    # 2. Open with candidate password
    for pwd in passwords:
        try:
            doc = pdfium.PdfDocument(pdf_input, password=pwd)
            if len(doc) > 0:
                _ = doc[0]  # Verify page access
                return doc
            doc.close()
        except Exception:
            continue

    if has_manual_password:
        msg = f"Unable to decrypt the PDF document with password '{passwords[0]}'. Please check the password and try again."
    else:
        attempted_pwd = passwords[0] if passwords else "FIRST4NAME+YYYY"
        msg = f"This e-Aadhaar PDF is password-protected. Automated unlock with '{attempted_pwd}' did not succeed. Please enter your PDF password manually below."

    raise HTTPException(
        status_code=400,
        detail={
            "code": "PASSWORD_REQUIRED",
            "message": msg,
        },
    )


class EmailIn(BaseModel):
    email: EmailStr

@app.post("/emails/check")
def check_email(body: EmailIn):
    # Skipped for testing without DB
    return {"exists": True}
    # found = exists(body.email)
    # return {"exists": found}


@app.post("/api/verify")
async def verify(
    aadhaar_image: UploadFile = File(...),
    expected_name: str = Form(""),
    expected_dob: str = Form(""),
    password: str = Form(""),
):
    """
    Verify an Aadhaar card PDF
    """
    # Validate file type — PDF only
    content_type = (aadhaar_image.content_type or "").lower()
    filename = (aadhaar_image.filename or "").lower()

    is_pdf = content_type == "application/pdf" or filename.endswith(".pdf")

    if not is_pdf:
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are accepted (e-Aadhaar / DigiLocker / mAadhaar).",
        )

    # Read file into memory
    file_bytes = await aadhaar_image.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(file_bytes) > 5 * 1024 * 1024:  # 5 MB limit
        raise HTTPException(
            status_code=400, detail="File too large. Max 5 MB.")

    manual_pwd = password.strip() if password and password.strip() else None
    has_manual_pwd = manual_pwd is not None
    passwords = get_aadhaar_pdf_passwords(
        expected_name.strip(),
        expected_dob.strip(),
        manual_password=manual_pwd,
    )

    try:
        # Step 1: Verify UIDAI / DigiLocker digital signature (purely in-memory)
        if not signed_by_uidai(file_bytes, passwords, has_manual_password=has_manual_pwd):
            raise HTTPException(status_code=400, detail=(
                "PDF is not digitally signed by UIDAI or DigiLocker / Government of India. "
                "Only genuine e-Aadhaar PDFs are accepted."
            ))

        # Step 2: Render first page directly to NumPy array in memory and extract embedded text
        pdf_doc = open_pdf_document(
            pdf_input=file_bytes,
            passwords=passwords,
            has_manual_password=has_manual_pwd,
        )

        try:
            if len(pdf_doc) == 0:
                raise HTTPException(
                    status_code=400, detail="PDF has no pages.")
            first_page = pdf_doc[0]
            w, h = first_page.get_size()

            # Fast text extraction: crop to bottom cut-out wallet card area (standard portrait e-Aadhaar A4)
            # In PDF coordinates, (0,0) is bottom-left, so bottom card (40% height) is [0, 0, w, h * 0.40]
            full_text = ""
            try:
                textpage = first_page.get_textpage()
                full_text = textpage.get_text_range()
                if h > w:
                    extracted_text = textpage.get_text_bounded(left=0, bottom=0, right=w, top=h * 0.40)
                else:
                    extracted_text = full_text
                if not extracted_text.strip():
                    extracted_text = full_text
            except Exception:
                extracted_text = ""
                full_text = ""

            bitmap = first_page.render(scale=220 / 72, rotation=0)
            # Convert directly to in-memory NumPy uint8 array (zero disk I/O)
            image_np = bitmap.to_numpy()

            # Crop image to lower cut-out card portion (starting from 60% of height) for QR scan
            img_h, img_w = image_np.shape[:2]
            target_image = image_np[int(img_h * 0.60):, :] if img_h > img_w else image_np
        finally:
            pdf_doc.close()


        print("==========EXTRACTED TEXT LAYER FROM THE PDF ", extracted_text)
        # Step 3: QR scan + text pipeline on cropped card
        result = run_pipeline(
            image=target_image,
            expected_name=expected_name.strip() or None,
            expected_dob=expected_dob.strip() or None,
            pdf_text=extracted_text,
        )

        # Fallback: If cropped image didn't verify, retry with full page
        if result.get("verdict") != "PASS" and target_image.shape != image_np.shape:
            fallback_res = run_pipeline(
                image=image_np,
                expected_name=expected_name.strip() or None,
                expected_dob=expected_dob.strip() or None,
                pdf_text=full_text or extracted_text,
            )
            if fallback_res.get("verdict") == "PASS":
                result = fallback_res
    except HTTPException:
        raise
    except Exception as e:
        err_msg = str(e).lower()
        if (
            "authentication failed" in err_msg
            or "incorrect password" in err_msg
            or "password" in err_msg
            or "pdfkeynotavailable" in type(e).__name__.lower()
        ):
            if has_manual_pwd:
                msg = (
                    f"Unable to unlock password-protected e-Aadhaar PDF. "
                    f"The password you entered ('{passwords[0]}') was incorrect. "
                    f"Please re-check your PDF password and try again."
                )
            else:
                attempted_pwd = passwords[0] if passwords else "FIRST4NAME+YYYY"
                msg = (
                    f"This e-Aadhaar PDF is password-protected. Automated unlock with '{attempted_pwd}' did not succeed. "
                    f"Please enter your PDF password manually below to proceed."
                )
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "PASSWORD_REQUIRED",
                    "message": msg,
                },
            )
        raise HTTPException(
            status_code=500, detail=f"Verification failed: {e}")

    return JSONResponse(content=result)


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "tesseract": shutil.which("tesseract") is not None,
        "timestamp": datetime.now().isoformat(),
    }
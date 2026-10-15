"""
FastAPI backend for Aadhaar verification.

Zero-retention: uploaded PDFs are written to a temp file, the first page
is rendered to an in-memory PNG, processed, and all temp files are
immediately deleted. No PII is logged or persisted.
"""
from __future__ import annotations
import shutil


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


def get_aadhaar_pdf_passwords(expected_name: str, expected_dob: str) -> list[str]:
    """
    Generate candidate UIDAI e-Aadhaar password.
    Standard UIDAI rule: First 4 letters of name in CAPITAL + 4-digit Year of Birth.
    Spaces and non-letters are stripped (e.g. 'Om Prakash' -> 'OMPR' + '2000' -> 'OMPR2000').
    """
    clean_name = re.sub(r"[^A-Za-z]", "", expected_name).upper()
    yob_match = re.search(r"\b(19\d{2}|20\d{2})\b", expected_dob)
    if not clean_name or not yob_match:
        return []

    year = yob_match.group(1)
    return [clean_name[:4] + year]


def signed_by_uidai(pdf_bytes: bytes, passwords: list[str]) -> bool:
    """Return True if the PDF carries a digital signature from UIDAI."""
    r = PdfFileReader(io.BytesIO(pdf_bytes))
    if r.encrypted:
        if not passwords:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This e-Aadhaar PDF is password-protected. "
                    "Please enter your Name and Date of Birth in the registration form "
                    "so the document can be unlocked."
                ),
            )
        decrypted = False
        for pwd in passwords:
            try:
                r.decrypt(pwd)
                decrypted = True
                break
            except Exception:
                continue
        if not decrypted:
            attempted_pwd = passwords[0]
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unable to unlock password-protected e-Aadhaar PDF. "
                    f"UIDAI e-Aadhaar password is the first 4 letters of your name in CAPITAL and your 4-digit Year of Birth (e.g., '{attempted_pwd}'). "
                    f"The registration details you entered did not unlock this file. "
                    f"Please verify your registration Name and Date of Birth match your official Aadhaar card and submit again."
                ),
            )
    for sig in r.embedded_signatures:
        cert = sig.signer_cert
        if "uidai.net.in" in cert.subject.human_friendly.lower():
            return True
        if b"uidai.net.in" in cert.dump().lower():   # fallback: email not in subject
            return True
    return False


def open_pdf_document(pdf_path: str, passwords: list[str]) -> pdfium.PdfDocument:
    """Open a PDF document. If password-protected, unlocks using candidate password."""
    # 1. Try opening without a password (for unencrypted / already unlocked PDFs)
    try:
        doc = pdfium.PdfDocument(pdf_path)
        if len(doc) > 0:
            _ = doc[0]  # Verify page access
            return doc
        doc.close()
    except Exception:
        pass

    # 2. Open with candidate password
    for pwd in passwords:
        try:
            doc = pdfium.PdfDocument(pdf_path, password=pwd)
            if len(doc) > 0:
                _ = doc[0]  # Verify page access
                return doc
            doc.close()
        except Exception:
            continue

    raise HTTPException(
        status_code=400, detail="Unable to open or decrypt the PDF document.")


@app.post("/api/verify")
async def verify(
    aadhaar_image: UploadFile = File(...),
    expected_name: str = Form(""),
    expected_dob: str = Form(""),
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

    tmp_pdf = None
    tmp_img = None
    try:
        tmp_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp_img = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp_img.close()

        tmp_pdf.write(file_bytes)
        tmp_pdf.flush()
        tmp_pdf.close()

        # ── Step 1: Verify UIDAI digital signature ────────────────────────
        passwords = get_aadhaar_pdf_passwords(
            expected_name.strip(), expected_dob.strip())
        if not signed_by_uidai(file_bytes, passwords):
            raise HTTPException(status_code=400, detail=(
                "PDF is not signed by UIDAI. Only genuine Aadhaar PDFs from "
                "UIDAI / DigiLocker / mAadhaar are accepted."
            ))

        # ── Step 2: Render first page → image for QR + OCR ───────────────
        pdf_doc = open_pdf_document(
            pdf_path=tmp_pdf.name,
            passwords=passwords,
        )

        try:
            if len(pdf_doc) == 0:
                raise HTTPException(
                    status_code=400, detail="PDF has no pages.")
            bitmap = pdf_doc[0].render(scale=200 / 72, rotation=0)
            pil_image = bitmap.to_pil()
            pil_image.save(tmp_img.name, format="PNG")
        finally:
            pdf_doc.close()

        # ── Step 3: QR scan + OCR pipeline ───────────────────────────────
        result = run_pipeline(
            image_path=tmp_img.name,
            expected_name=expected_name.strip() or None,
            expected_dob=expected_dob.strip() or None,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Verification failed: {e}")
    finally:
        # Zero-retention: delete temporary files
        for f in (tmp_pdf, tmp_img):
            if f and os.path.exists(f.name):
                try:
                    os.unlink(f.name)
                except OSError:
                    pass

    return JSONResponse(content=result)


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "tesseract": shutil.which("tesseract") is not None,
        "timestamp": datetime.now().isoformat(),
    }
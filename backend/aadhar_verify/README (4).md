# Aadhaar PDF Verifier

Verifies an uploaded e-Aadhaar PDF: checks the UIDAI digital signature on the PDF, reads the QR code, and compares the name and date of birth with what the user typed at registration.

The app reads the text directly from the PDF


Install Tesseract (system program, not a pip package)


## 1. Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Check it works: open http://localhost:8000/api/health (should return `{"status": "ok", ...}`). Interactive API docs are at http://localhost:8000/docs.


## 2. Frontend

```bash
cd frontend
npm install
```

Create `frontend/.env`:

```
VITE_API_URL=http://localhost:8000
```

(Use `REACT_APP_API_URL` if the app uses Create React App.) Then run:

```bash
npm run dev
```

If the browser shows a CORS error, add the frontend's address (for example `http://localhost:5173`) to the allowed origins in the backend's CORS settings.


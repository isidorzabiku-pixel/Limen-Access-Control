# Inbound API

The code lives in `backend/routers/api.py` (`/api/v1` routes) and `backend/services/api_accesso.py` (login, tokens, attempt limiting).
API accounts are created in **System properties → API** (admin only): each account has **full access**, no permissions to manage.

- `limen-api.pdf` = documentation for integrators (English), the same one admins download from System properties.
- Edit **`backend/docs/api_docs.html`** and regenerate the PDF with `python backend/docs/genera_pdf.py` (requires Playwright with Chromium); then copy the PDF here.
- The API is used over **HTTPS** only (see `backend/avvia.sh`). FastAPI's automatic documentation (`/docs`, `/openapi.json`) is disabled.

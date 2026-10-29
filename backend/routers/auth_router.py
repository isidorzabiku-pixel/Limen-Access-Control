# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session

from database import get_db
from auth import get_user_by_username, verify_password, login_user, logout_user, get_current_user
from services.logger import registra_log

router = APIRouter()
from templating import templates
from services import lingua
from services.lingua import t


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_user_by_username(db, username)
    if not user or not verify_password(password, user.password_hash):
        registra_log(db, t("Tentativo di login fallito per '%(username)s'", username=username), livello="warning", categoria="auth",
                     codice="AUTH_LOGIN_KO", utente=username[:80], ip=request.client.host if request.client else None,
                     dettagli={"motivo": "utente inesistente" if not user else "password errata"})
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": t("Credenziali non valide")},
            status_code=401,
        )
    login_user(request, user)
    # Lingua: quella salvata dall'utente; se non ne ha, quella scelta nella pagina di login (cookie).
    scelta = lingua.valida(request.cookies.get(lingua.COOKIE))
    if lingua.valida(user.lingua):
        request.session["lingua"] = user.lingua
    elif scelta:
        user.lingua = scelta
        db.commit()
        request.session["lingua"] = scelta
    registra_log(db, t("Login effettuato: %(username)s", username=user.username), livello="info", categoria="auth",
                 codice="AUTH_LOGIN", utente=user.username, ip=request.client.host if request.client else None)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        registra_log(db, t("Logout: %(username)s", username=user.username), livello="info", categoria="auth",
                     codice="AUTH_LOGOUT", utente=user.username, ip=request.client.host if request.client else None)
    logout_user(request)
    return RedirectResponse("/login", status_code=303)


@router.get("/lingua/{codice}")
def scegli_lingua(request: Request, codice: str, next: str = "/", db: Session = Depends(get_db)):
    """Cambia la lingua dell'interfaccia: la ricorda il browser (cookie) e, se si e' dentro, anche il profilo dell'utente."""
    if lingua.valida(codice) is None:
        return RedirectResponse("/", status_code=303)
    destinazione = next if next.startswith("/") and not next.startswith("//") and "\\" not in next else "/"
    risposta = RedirectResponse(destinazione, status_code=303)
    risposta.set_cookie(lingua.COOKIE, codice, max_age=365 * 24 * 3600, samesite="lax", httponly=True,
                        secure=request.url.scheme == "https")
    user = get_current_user(request, db)
    if user:
        user.lingua = codice
        db.commit()
        request.session["lingua"] = codice
    return risposta


@router.get("/lingua/catalogo/{codice}.js")
def catalogo_js(codice: str):
    """Le traduzioni per i testi scritti negli script della pagina (window.I18N)."""
    import json
    from fastapi.responses import Response
    if lingua.valida(codice) is None:
        return Response("window.I18N = {};", media_type="application/javascript")
    return Response("window.I18N = " + json.dumps(lingua.catalogo(codice), ensure_ascii=False) + ";",
                    media_type="application/javascript", headers={"Cache-Control": "public, max-age=86400"})

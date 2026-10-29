# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Monitoraggio dei passaggi ai varchi: una pagina sola per «cosa sta succedendo ora»
(prima pagina, senza periodo: si aggiorna da sola) e «cosa e' successo» (periodo o
pagine successive: storico paginato). Stessi filtri, stessa tabella, export CSV.
/registro e' rimasto solo come indirizzo che porta qui (segnalibri, link vecchi).
"""
import csv
import io
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session, joinedload
from urllib.parse import urlencode

from database import get_db
from auth import require_login
from models import Evento, EsitoEvento, Persona, Varco, AccessMedia
from services import tempo
from templating import templates
from routers.admin import ESITO_INFO
from services.lingua import t

router = APIRouter()

PER_PAGINA = 100
MAX_RIGHE_CSV = 200_000


def leggi_filtri(params) -> dict:
    """Legge e normalizza i filtri dalla query string (valori non validi = filtro ignorato)."""
    def testo(nome):
        return (params.get(nome) or "").strip()

    def data(nome):
        try:
            return datetime.strptime(testo(nome), "%Y-%m-%d").date()
        except ValueError:
            return None

    try:
        varco_id = int(testo("varco")) if testo("varco") else None
    except ValueError:
        varco_id = None
    try:
        pagina = max(1, int(testo("pagina") or 1))
    except ValueError:
        pagina = 1
    # «Esito preciso» (select) prevale sui chip Tutti/Concessi/Negati/Allarmi.
    esito = testo("motivo") or testo("esito")
    if esito not in ("concessi", "negati", "allarmi") and esito not in EsitoEvento.__members__:
        esito = ""
    return {"da": data("da"), "a": data("a"), "varco": varco_id, "q": testo("q"), "esito": esito, "pagina": pagina}


def query_eventi(db: Session, f: dict):
    query = db.query(Evento).outerjoin(Persona, Evento.persona_id == Persona.id)
    if f["da"]:
        query = query.filter(Evento.timestamp >= tempo.locale_a_utc(datetime.combine(f["da"], datetime.min.time())))
    if f["a"]:
        fine = tempo.locale_a_utc(datetime.combine(f["a"] + timedelta(days=1), datetime.min.time()))
        query = query.filter(Evento.timestamp < fine)
    if f["varco"]:
        query = query.filter(Evento.varco_id == f["varco"])
    for parola in f["q"].split():
        like = f"%{parola}%"
        query = query.filter(or_(
            Persona.nome.ilike(like), Persona.cognome.ilike(like), Evento.media_codice.ilike(like),
        ))
    if f["esito"] == "concessi":
        query = query.filter(Evento.esito.in_([EsitoEvento.concesso, EsitoEvento.apertura_remota, EsitoEvento.apertura_comando_esterno]))
    elif f["esito"] == "negati":
        query = query.filter(Evento.esito.notin_([
            EsitoEvento.concesso, EsitoEvento.apertura_remota, EsitoEvento.apertura_comando_esterno,
            EsitoEvento.ingresso_attivato, EsitoEvento.uscita_azionata,
        ]))
    elif f["esito"] == "allarmi":
        query = query.filter(Evento.allarme == True)  # noqa: E712
    elif f["esito"]:
        query = query.filter(Evento.esito == EsitoEvento[f["esito"]])
    return query.order_by(Evento.timestamp.desc(), Evento.id.desc())


def _stringa_filtri(f: dict, **extra) -> str:
    """Query string che riproduce i filtri (senza pagina), per i link di paginazione ed export."""
    dati = {
        "da": f["da"].isoformat() if f["da"] else "", "a": f["a"].isoformat() if f["a"] else "",
        "varco": f["varco"] or "", "q": f["q"], "esito": f["esito"],
    }
    dati.update(extra)
    return urlencode({k: v for k, v in dati.items() if v not in ("", None)})


@router.get("/registro")
def registro_ora_nel_monitoraggio(request: Request):
    """Il Registro e' stato unito al Monitoraggio: i vecchi link (con i loro filtri) arrivano li'."""
    qs = request.url.query
    return RedirectResponse("/monitoraggio" + (f"?{qs}" if qs else ""), status_code=301)


@router.get("/registro/export.csv")
def registro_export_spostato(request: Request):
    qs = request.url.query
    return RedirectResponse("/monitoraggio/export.csv" + (f"?{qs}" if qs else ""), status_code=301)


def _cella_sicura(valore) -> str:
    """Neutralizza le formule: un testo che inizia con = + - @ verrebbe eseguito da Excel."""
    testo = "" if valore is None else str(valore)
    return "'" + testo if testo[:1] in ("=", "+", "-", "@") else testo


def genera_csv(eventi) -> str:
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    w.writerow([t("Data/ora"), t("Varco"), t("Persona"), t("Credenziale"), t("Esito"), t("Allarme"), t("Gestito da"), t("Gestito il")])
    for e in eventi:
        etichetta = ESITO_INFO.get(e.esito, (e.esito.value, ""))[0]
        w.writerow([
            tempo.formatta(e.timestamp),
            _cella_sicura(e.varco.nome if e.varco else ""),
            _cella_sicura(f"{e.persona.nome} {e.persona.cognome}" if e.persona else ""),
            _cella_sicura(e.media_codice),
            etichetta + (t(" · %(dettaglio)s", dettaglio=e.dettaglio) if e.dettaglio else ""),
            "si" if e.allarme else "",
            _cella_sicura(e.gestito_da),
            tempo.formatta(e.gestito_at) if e.gestito_at else "",
        ])
    return out.getvalue()


@router.get("/monitoraggio/export.csv")
def esporta_passaggi(request: Request, db: Session = Depends(get_db)):
    require_login(request, db)
    f = leggi_filtri(request.query_params)
    eventi = (
        query_eventi(db, f).options(joinedload(Evento.varco), joinedload(Evento.persona))
        .limit(MAX_RIGHE_CSV).all()
    )
    # BOM + punto e virgola: Excel italiano apre il file con gli accenti e le colonne giuste.
    contenuto = "﻿" + genera_csv(eventi)
    nome = f"passaggi-{tempo.adesso_locale().strftime('%Y%m%d-%H%M')}.csv"
    return Response(
        content=contenuto, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


# ---------- Pagina Monitoraggio ----------

MAX_ASSEGNA_LIVE = 5   # quante righe mostrano il modulo "Assegna a persona" (uno per credenziale)


def contesto_blocco(db: Session, f: dict) -> dict:
    """Tutto cio' che serve al blocco risultati (conteggio, tabella, pagine), con l'elenco
    persone solo se serve a collegare un badge nuovo."""
    query = query_eventi(db, f)
    totale = query.count()
    pagine = max(1, -(-totale // PER_PAGINA))
    pagina = min(f["pagina"], pagine)
    eventi = (
        query.options(joinedload(Evento.varco), joinedload(Evento.persona))
        .offset((pagina - 1) * PER_PAGINA).limit(PER_PAGINA).all()
    )
    candidati = {e.media_codice for e in eventi if e.esito == EsitoEvento.negato_credenziale_sconosciuta and e.media_codice}
    assegnabili: list[str] = []
    if candidati:
        gia = {cod for (cod,) in db.query(AccessMedia.codice).filter(AccessMedia.codice.in_(list(candidati))).all()}
        for e in eventi:   # dal piu' recente: una sola riga per credenziale
            if e.media_codice in candidati and e.media_codice not in gia and e.media_codice not in assegnabili:
                assegnabili.append(e.media_codice)
        assegnabili = assegnabili[:MAX_ASSEGNA_LIVE]
    persone = (
        [(p.id, f"{p.cognome} {p.nome}") for p in db.query(Persona).order_by(Persona.cognome, Persona.nome).all()]
        if assegnabili else []
    )
    # In diretta = la prima pagina senza periodo; altrimenti si sta sfogliando lo storico.
    storico = bool(f["da"] or f["a"] or pagina > 1)
    return {
        "eventi": eventi, "esito_info": ESITO_INFO, "assegnabili": assegnabili, "tutte_le_persone": persone,
        "f": f, "totale": totale, "pagina": pagina, "pagine": pagine, "per_pagina": PER_PAGINA,
        "qs": _stringa_filtri(f), "storico": storico,
        "filtri_attivi": any([f["da"], f["a"], f["varco"], f["q"], f["esito"]]),
        "max_righe_csv": MAX_RIGHE_CSV,
    }


@router.get("/monitoraggio", response_class=HTMLResponse)
def pagina_monitoraggio(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    f = leggi_filtri(request.query_params)
    ctx = contesto_blocco(db, f)
    ctx.update({
        "request": request, "user": user, "varchi": db.query(Varco).order_by(Varco.nome).all(),
        "esiti": [(e.name, ESITO_INFO.get(e, (e.value, ""))[0]) for e in EsitoEvento],
    })
    return templates.TemplateResponse("monitoraggio.html", ctx)


@router.get("/monitoraggio/righe", response_class=HTMLResponse)
def blocco_monitoraggio(request: Request, db: Session = Depends(get_db)):
    """Solo il blocco risultati (conteggio, tabella, pagine): risposta del polling htmx e dei filtri."""
    require_login(request, db)
    f = leggi_filtri(request.query_params)
    ctx = contesto_blocco(db, f)
    ctx["request"] = request
    return templates.TemplateResponse("_monitoraggio_blocco.html", ctx)

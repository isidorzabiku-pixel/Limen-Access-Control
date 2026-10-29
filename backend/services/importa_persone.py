# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Import di persone e badge da CSV (tipicamente un'esportazione dall'anagrafica
del personale o da un vecchio sistema). Pensato per essere ripetibile: lo stesso
file importato due volte non crea doppioni (le persone si riconoscono per
nome+cognome, i badge per codice).

Colonne (intestazione obbligatoria, ordine libero, separatore ; , o tab):
  nome, cognome            obbligatori
  email                    facoltativa (si compila solo se la persona non ne ha)
  codice                   codice del badge (UID letto dal lettore); vuoto = solo la persona
  gruppo                   uno o piu' gruppi separati da | (creati se non esistono)
  valido_da, valido_a      validita' del badge: gg/mm/aaaa, gg/mm/aaaa hh:mm o aaaa-mm-gg
                           (ora locale; senza orario valido_a vale fino a fine giornata)
"""
import csv
import io
import unicodedata
from datetime import datetime, time, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Persona, Gruppo, AccessMedia
from services import tempo
from services.lingua import t

COLONNE = ["nome", "cognome", "email", "codice", "gruppo", "valido_da", "valido_a"]
MAX_RIGHE = 5000
MAX_BYTE = 1_000_000

SINONIMI = {
    "name": "nome", "first name": "nome", "firstname": "nome",
    "surname": "cognome", "last name": "cognome", "lastname": "cognome",
    "e-mail": "email", "mail": "email",
    "badge": "codice", "tessera": "codice", "uid": "codice", "code": "codice",
    "group": "gruppo", "gruppi": "gruppo",
    "da": "valido_da", "dal": "valido_da", "valido dal": "valido_da", "valid from": "valido_da",
    "a": "valido_a", "al": "valido_a", "scadenza": "valido_a", "valido fino a": "valido_a",
    "valido al": "valido_a", "valid to": "valido_a",
}
FORMATI_DATA = ("%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d")


def modello_csv() -> str:
    righe = [
        COLONNE,
        ["Mario", "Rossi", "mario.rossi@example.it", "0004523981", "Uffici", "", ""],
        ["Giulia", "Bianchi", "", "0004523982", "Uffici|Amministrazione", "", ""],
    ]
    out = io.StringIO()
    csv.writer(out, delimiter=";", lineterminator="\r\n").writerows(righe)
    return "﻿" + out.getvalue()


def _norma(testo: str) -> str:
    s = unicodedata.normalize("NFKD", testo or "").encode("ascii", "ignore").decode().strip().lower()
    return SINONIMI.get(s, s)


def _data(testo: str, fine_giornata: bool):
    testo = (testo or "").strip()
    if not testo:
        return None
    for formato in FORMATI_DATA:
        try:
            dt = datetime.strptime(testo, formato)
        except ValueError:
            continue
        if fine_giornata and "%H" not in formato:
            dt = datetime.combine(dt.date(), time(23, 59))
        return tempo.locale_a_utc(dt)
    raise ValueError(t("data non valida: %(testo)s", testo=repr(testo)))


def _si(testo: str) -> bool:
    return (testo or "").strip().lower() in ("1", "si", "sì", "s", "x", "true", "yes", "y", "vero")


def decodifica(dati: bytes) -> str:
    try:
        return dati.decode("utf-8-sig")
    except UnicodeDecodeError:
        return dati.decode("latin-1")   # esportazioni Excel "CSV (separato da punto e virgola)"


def importa(db: Session, dati: bytes) -> dict:
    """Importa il CSV. Ritorna il resoconto: creati, aggiornati, badge, gruppi_creati, invariati, errori [(riga, motivo)]."""
    rep = {"creati": 0, "aggiornati": 0, "badge": 0, "gruppi_creati": 0, "invariati": 0, "errori": [], "fatale": None}
    if len(dati) > MAX_BYTE:
        rep["fatale"] = t("File troppo grande (%(p)s kB): il limite e' %(p2)s kB.", p=len(dati) // 1000, p2=MAX_BYTE // 1000)
        return rep
    testo = decodifica(dati)
    prima_riga = testo.split("\n", 1)[0]
    sep = max((";", ",", "\t"), key=prima_riga.count)
    lettore = csv.reader(io.StringIO(testo), delimiter=sep)
    try:
        intestazione = [_norma(c) for c in next(lettore)]
    except StopIteration:
        rep["fatale"] = t("Il file e' vuoto.")
        return rep
    mancanti = [c for c in ("nome", "cognome") if c not in intestazione]
    if mancanti:
        rep["fatale"] = (
            t("Intestazione non riconosciuta: mancano le colonne ") + ", ".join(mancanti)
            + t(". La prima riga deve contenere i nomi delle colonne (scarica il modello).")
        )
        return rep
    idx = {nome: intestazione.index(nome) for nome in COLONNE if nome in intestazione}

    def cella(riga, nome):
        i = idx.get(nome)
        return riga[i].strip() if i is not None and i < len(riga) else ""

    gruppi = {g.nome.lower(): g for g in db.query(Gruppo).all()}
    for n, riga in enumerate(lettore, start=2):
        if not any(c.strip() for c in riga):
            continue
        if n - 1 > MAX_RIGHE:
            rep["fatale"] = t("Troppe righe: il limite e' %(MAX_RIGHE)s. Le righe oltre la %(MAX_RIGHE2)s non sono state importate.", MAX_RIGHE=MAX_RIGHE, MAX_RIGHE2=MAX_RIGHE)
            break
        nome, cognome = cella(riga, "nome"), cella(riga, "cognome")
        if not nome or not cognome:
            rep["errori"].append((n, t("nome o cognome mancante")))
            continue
        try:
            valido_da = _data(cella(riga, "valido_da"), fine_giornata=False)
            valido_a = _data(cella(riga, "valido_a"), fine_giornata=True)
        except ValueError as e:
            rep["errori"].append((n, str(e)))
            continue
        codice = cella(riga, "codice")
        if valido_da and valido_a and valido_a < valido_da:
            rep["errori"].append((n, t("la fine validita' e' prima dell'inizio")))
            continue

        persona = (
            db.query(Persona)
            .filter(func.lower(Persona.nome) == nome.lower(), func.lower(Persona.cognome) == cognome.lower())
            .first()
        )
        nuova = persona is None
        modificata = False
        if nuova:
            persona = Persona(nome=nome, cognome=cognome, email=cella(riga, "email") or None)
            db.add(persona)
            db.flush()
        else:
            email = cella(riga, "email")
            if email and not persona.email:
                persona.email = email
                modificata = True

        for nome_gruppo in [g.strip() for g in cella(riga, "gruppo").split("|") if g.strip()]:
            g = gruppi.get(nome_gruppo.lower())
            if g is None:
                g = Gruppo(nome=nome_gruppo)
                db.add(g)
                db.flush()
                gruppi[nome_gruppo.lower()] = g
                rep["gruppi_creati"] += 1
            if g not in persona.gruppi:
                persona.gruppi.append(g)
                modificata = True

        badge_nuovo = False
        if codice:
            esistente = db.query(AccessMedia).filter(AccessMedia.codice == codice).first()
            if esistente is None:
                db.add(AccessMedia(codice=codice, persona_id=persona.id, valido_da=valido_da, valido_a=valido_a))
                rep["badge"] += 1
                badge_nuovo = True
            elif esistente.persona_id != persona.id:
                altro = esistente.persona
                rep["errori"].append((n, t("il badge %(codice)s e' gia' di %(nome)s %(cognome)s", codice=codice, nome=altro.nome, cognome=altro.cognome)))
        if nuova:
            rep["creati"] += 1
        elif modificata or badge_nuovo:
            rep["aggiornati"] += 1
        else:
            rep["invariati"] += 1
    db.commit()
    return rep

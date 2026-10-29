# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Credenziali (titoli di accesso) di una persona: tipi, etichette e riassunto per l'elenco Persone.

Una persona puo' averne piu' d'una e di tipi diversi (badge, PIN da tastiera, tag UHF, telefono).
Il codice e' sempre quello che legge il lettore; il tipo serve a mostrarlo e, in futuro, a mandare a
ogni varco solo le credenziali che i suoi lettori sanno leggere.
"""
import re
from datetime import datetime

from models import TipoMedia
from services.lingua import pigro

# Ordine di visualizzazione (e dei filtri).
ORDINE = ("rfid", "uhf", "ble", "pin", "qr")
ETICHETTE = {"rfid": pigro("Badge"), "pin": pigro("PIN"), "uhf": pigro("UHF"), "ble": pigro("BLE"), "qr": pigro("QR")}
SUGGERIMENTI = {
    "rfid": pigro("Codice del badge (UID letto dal lettore)"),
    "pin": pigro("Numero da digitare sulla tastiera (4-10 cifre)"),
    "uhf": pigro("Codice del tag UHF (EPC)"),
    "ble": pigro("Identificativo della credenziale sul telefono"),
    "qr": pigro("Contenuto del codice QR"),
}
PIN_VALIDO = re.compile(r"^\d{4,10}$")
MAX_CHIP = 3          # oltre, le altre finiscono in "+N" (tutte visibili nel dettaglio della persona)


def tipo_valido(valore: str) -> TipoMedia:
    """Il tipo inviato da un form; qualunque valore sconosciuto vale badge."""
    try:
        return TipoMedia(valore)
    except ValueError:
        return TipoMedia.rfid


def chip(m, persona_attiva: bool, adesso: datetime) -> dict:
    """Una credenziale come la mostra l'elenco: il PIN non si legge mai (mascherato)."""
    tipo = m.tipo.value if m.tipo else "rfid"
    spenta = (not m.attivo) or (not persona_attiva) or bool(m.valido_a and m.valido_a < adesso)
    return {
        "tipo": tipo, "etichetta": ETICHETTE.get(tipo, tipo),
        "codice": "••••" if tipo == "pin" else m.codice, "spenta": spenta,
        "scade": m.valido_a if (m.valido_a and m.valido_a >= adesso) else None,
        "non_ancora": bool(m.valido_da and m.valido_da > adesso),
    }


def riassunto(persona, adesso: datetime) -> dict:
    """{"chips": [...], "extra": [etichette delle credenziali oltre le prime], "attive": n}"""
    tutte = sorted(
        (chip(m, persona.attivo, adesso) for m in persona.media),
        key=lambda c: ORDINE.index(c["tipo"]) if c["tipo"] in ORDINE else len(ORDINE),
    )
    return {
        "chips": tutte[:MAX_CHIP], "extra": [c["etichetta"] for c in tutte[MAX_CHIP:]],
        "n": len(tutte), "attive": sum(1 for c in tutte if not c["spenta"]),
    }


def conteggi(persone) -> dict:
    """Quante persone hanno almeno una credenziale di ciascun tipo, e quante non ne hanno nessuna attiva."""
    out = {t: 0 for t in ORDINE}
    senza = 0
    for p in persone:
        tipi = {m.tipo.value for m in p.media if m.tipo}
        for t in tipi:
            if t in out:
                out[t] += 1
        if not any(m.attivo for m in p.media):
            senza += 1
    return {"tipi": out, "senza": senza, "tutti": len(persone)}

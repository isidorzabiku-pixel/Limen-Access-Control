# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Gestione dell'ora: tutto il database ragiona in UTC (datetime "naive" prodotti
da datetime.utcnow()), ma le persone vivono nell'ora locale. Qui c'è l'unico
punto che converte tra i due, così interfaccia, conteggi "di oggi" e fasce
orarie ragionano nello stesso fuso (di default Europe/Rome, cambiabile con la
variabile d'ambiente TIMEZONE nel .env).

Regole:
  - nel database si scrive sempre UTC naive (come già fatto);
  - per MOSTRARE un orario si passa da utc_a_locale() (o dal filtro Jinja "ora");
  - per leggere un orario scritto da una persona (es. "valido fino al ...")
    si passa da locale_a_utc();
  - per decidere "che giorno/ora è per le fasce orarie" si usa adesso_locale().
"""
import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from services.lingua import pigro

NOME_FUSO = os.environ.get("TIMEZONE", "Europe/Rome")


def _carica_fuso():
    try:
        return ZoneInfo(NOME_FUSO)
    except Exception:
        try:
            return ZoneInfo("Europe/Rome")
        except Exception:
            # tzdata assente (es. container minimale): ricade sul fuso del sistema.
            return datetime.now().astimezone().tzinfo


FUSO = _carica_fuso()


def utc_a_locale(dt: datetime | None) -> datetime | None:
    """Datetime naive in UTC (come nel database) -> datetime con fuso locale."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(FUSO)


def locale_a_utc(dt: datetime | None) -> datetime | None:
    """Datetime naive scritto in ora locale (es. da un form) -> naive in UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=FUSO).astimezone(timezone.utc).replace(tzinfo=None)


def adesso_locale() -> datetime:
    return datetime.now(timezone.utc).astimezone(FUSO)


def fuso_minuti() -> int:
    """Scarto dell'ora locale da UTC in questo momento, in minuti (+120 = UTC+2 estate in Italia)."""
    return int(adesso_locale().utcoffset().total_seconds() // 60)


def inizio_oggi_utc() -> datetime:
    """Mezzanotte locale di oggi, espressa come naive UTC (per filtrare Evento.timestamp)."""
    ora = adesso_locale()
    mezzanotte = ora.replace(hour=0, minute=0, second=0, microsecond=0)
    return mezzanotte.astimezone(timezone.utc).replace(tzinfo=None)


def inizio_giorno_locale_utc(giorni_fa: int) -> datetime:
    """Mezzanotte locale di N giorni fa (0 = oggi), come naive UTC."""
    ora = adesso_locale()
    giorno = (ora - timedelta(days=giorni_fa)).replace(hour=0, minute=0, second=0, microsecond=0)
    return giorno.astimezone(timezone.utc).replace(tzinfo=None)


GIORNI_BREVI = [pigro(g) for g in ("lun", "mar", "mer", "gio", "ven", "sab", "dom")]
GIORNI_ESTESI = [pigro(g) for g in ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")]


def formatta(dt: datetime | None, formato: str = "%d/%m/%Y %H:%M:%S") -> str:
    """Filtro Jinja "ora": mostra un datetime UTC del database in ora locale.
    %a / %A danno il giorno della settimana nella lingua dell'interfaccia (strftime userebbe la lingua del server)."""
    if dt is None:
        return "-"
    locale = utc_a_locale(dt)
    formato = formato.replace("%A", str(GIORNI_ESTESI[locale.weekday()])).replace("%a", str(GIORNI_BREVI[locale.weekday()]))
    return locale.strftime(formato)


def a_input_locale(dt: datetime | None) -> str:
    """Valore per un <input type="datetime-local"> (ora locale), vuoto se None."""
    if dt is None:
        return ""
    return utc_a_locale(dt).strftime("%Y-%m-%dT%H:%M")


def da_input_locale(testo: str) -> datetime | None:
    """Legge il valore di un <input type="datetime-local"> o una data e lo
    converte in naive UTC; None se vuoto o non valido."""
    testo = (testo or "").strip()
    if not testo:
        return None
    for formato in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return locale_a_utc(datetime.strptime(testo, formato))
        except ValueError:
            continue
    return None

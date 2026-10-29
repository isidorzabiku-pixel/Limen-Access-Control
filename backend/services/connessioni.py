# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Diario dei dispositivi: quando uno slave si collega o si scollega, quali pacchetti
vengono scartati e perche', cache inviate e confermate. Tutto finisce nel Log di
sistema (categorie «dispositivi» e «rete») con il varco di riferimento, in modo che
chi fa troubleshooting possa rispondere a «cos'e' successo alle 14:32?» senza
accedere al server.

Due regole per non inondare il database:
  - le transizioni online/offline si scrivono UNA volta per cambio di stato
    (gli heartbeat normali non scrivono nulla);
  - le anomalie ripetitive (firma sbagliata, IP non registrato, orologio sballato...)
    si scrivono la prima volta subito, poi al massimo una riga ogni PAUSA_ANOMALIE_SEC
    con il numero di ripetizioni nel frattempo.
"""
import logging
import threading
import time
from datetime import datetime
from typing import Optional

from database import SessionLocal
from models import Varco
from services import presenza, tempo
from services.logger import registra_log
from services.lingua import t

logger = logging.getLogger("connessioni")

PAUSA_ANOMALIE_SEC = 300
MAX_CHIAVI_ANOMALIE = 500      # tetto alla memoria: chi spedisce pacchetti con id inventati non la riempie

_lock = threading.Lock()
# varco_id -> {"online": bool, "dal": datetime (da quando e' in questo stato), "tipo": ultimo pacchetto, "ip": str}
_stato: dict[int, dict] = {}
# chiave anomalia -> [monotonic dell'ultima riga scritta, ripetizioni non ancora scritte]
_anomalie: dict[tuple, list] = {}


def durata(secondi: float) -> str:
    s = int(max(0, secondi))
    if s >= 86400:
        return f"{s // 86400} g {(s % 86400) // 3600} h"
    if s >= 3600:
        return f"{s // 3600} h {(s % 3600) // 60} min"
    if s >= 60:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s} s"


def _ora(dt: Optional[datetime]) -> str:
    return tempo.formatta(dt, "%d/%m %H:%M:%S") if dt else t("mai")


def scrivi(messaggio: str, livello: str = "info", categoria: str = "dispositivi", varco_id: Optional[int] = None, db=None,
           codice: Optional[str] = None, dettagli: Optional[dict] = None) -> None:
    """Una riga di log con sessione propria (o quella passata, senza commit). Non solleva mai."""
    try:
        if db is not None:
            registra_log(db, messaggio, livello, categoria, varco_id, commit=False, codice=codice, dettagli=dettagli)
            return
        d = SessionLocal()
        try:
            registra_log(d, messaggio, livello, categoria, varco_id, codice=codice, dettagli=dettagli)
        finally:
            d.close()
    except Exception:
        logger.exception("Impossibile scrivere nel log di sistema")


# Prima parola della chiave di un'anomalia -> codice dell'evento nel log.
CODICI_ANOMALIA = {
    "ignoto": "PKT_VARCO_IGNOTO", "ip": "PKT_IP_ERRATO", "firma": "PKT_FIRMA", "orologio": "PKT_OROLOGIO",
    "esito": "PKT_ESITO_IGNOTO", "porta": "PKT_PORTA_IGNOTA", "pr-ignoto": "ABB_VARCO_IGNOTO", "pr-attivo": "ABB_VARCO_ATTIVO",
    "invio": "TX_ERRORE",
}


def anomalia(chiave: tuple, messaggio: str, varco_id: Optional[int] = None, livello: str = "warning",
             categoria: str = "rete", db=None, dettagli: Optional[dict] = None, codice: Optional[str] = None) -> None:
    """Come `scrivi`, ma una chiave uguale non si ripete prima di PAUSA_ANOMALIE_SEC."""
    codice = codice or CODICI_ANOMALIA.get(chiave[0] if chiave else "")
    ora = time.monotonic()
    with _lock:
        voce = _anomalie.get(chiave)
        if voce is not None and ora - voce[0] < PAUSA_ANOMALIE_SEC and not diagnostica_attiva():
            voce[1] += 1
            return
        ripetuta = voce[1] if voce else 0
        if len(_anomalie) >= MAX_CHIAVI_ANOMALIE and chiave not in _anomalie:
            scadute = [k for k, v in _anomalie.items() if ora - v[0] >= PAUSA_ANOMALIE_SEC]
            for k in scadute:
                del _anomalie[k]
            if len(_anomalie) >= MAX_CHIAVI_ANOMALIE:
                return
        _anomalie[chiave] = [ora, 0]
    if ripetuta:
        messaggio += (t(" [ripetuto 1 volta nei %(durata)s precedenti]", durata=durata(PAUSA_ANOMALIE_SEC)) if ripetuta == 1
                      else t(" [ripetuto %(n)s volte nei %(durata)s precedenti]", n=ripetuta, durata=durata(PAUSA_ANOMALIE_SEC)))
        dettagli = {**(dettagli or {}), "ripetizioni": ripetuta}
    scrivi(messaggio, livello, categoria, varco_id, db, codice, dettagli)


# ---------- Online / offline ----------

def contatto(db, varco: Varco, ip: str, tipo: str, precedente: Optional[datetime]) -> None:
    """Chiamata a ogni pacchetto valido, PRIMA di aggiornare la presenza. `precedente` =
    ultimo contatto noto in memoria (None dopo un avvio del master). Scrive solo al passaggio
    offline -> online."""
    adesso = datetime.utcnow()
    with _lock:
        noto = _stato.get(varco.id)
        if noto and noto["online"]:
            noto["tipo"], noto["ip"] = tipo, ip
            return
        _stato[varco.id] = {"online": True, "dal": adesso, "tipo": tipo, "ip": ip}
    if noto:   # sappiamo da quando era offline
        quanto = t("Era offline da %(durata)s (dalle %(dalle)s).", durata=durata((adesso - noto['dal']).total_seconds()), dalle=_ora(noto['dal']))
    else:
        ultimo = precedente or varco.ultimo_heartbeat
        if ultimo:
            quanto = (t("Primo contatto da quando il master e' stato avviato; il contatto precedente risaliva a %(p)s (%(p2)s prima).", p=_ora(ultimo), p2=durata((adesso - ultimo).total_seconds())))
        else:
            quanto = t("E' il primo contatto in assoluto con questo varco.")
    scrivi(t("ONLINE: il dispositivo del varco '%(nome)s' si e' collegato (IP %(ip)s, primo pacchetto %(tipo)s). %(quanto)s", nome=varco.nome, ip=ip, tipo=tipo, quanto=quanto),
           "info", "dispositivi", varco.id, db, "DEV_ONLINE",
           {"ip": ip, "primo_pacchetto": tipo, "offline_da_s": round((adesso - noto["dal"]).total_seconds()) if noto else None,
            "ultimo_contatto": _ora(precedente or varco.ultimo_heartbeat) if not noto else _ora(noto["dal"])})


def controlla_offline(adesso: Optional[datetime] = None) -> int:
    """Segna offline i varchi senza contatti da piu' di SOGLIA_OFFLINE_SEC e lo scrive nel log.
    Ritorna quanti ne ha segnati. Chiamata a intervalli dal thread del master."""
    adesso = adesso or datetime.utcnow()
    scaduti = []
    with _lock:
        for varco_id, st in _stato.items():
            if not st["online"]:
                continue
            ultimo = presenza.ultimo_contatto(varco_id)
            if ultimo is None or (adesso - ultimo).total_seconds() > presenza.SOGLIA_OFFLINE_SEC:
                st["online"], st["dal"] = False, (ultimo or adesso)
                scaduti.append((varco_id, ultimo, st["tipo"], st["ip"]))
    if not scaduti:
        return 0
    db = SessionLocal()
    try:
        for varco_id, ultimo, tipo, ip in scaduti:
            varco = db.query(Varco).filter(Varco.id == varco_id).first()
            nome = varco.nome if varco else t("n. %(varco_id)s", varco_id=varco_id)
            testo = t("OFFLINE: nessun pacchetto dal varco '%(nome)s' da %(da)s (ultimo: %(tipo)s alle %(ora)s, IP %(ip)s).",
                      nome=nome, da=durata((adesso - ultimo).total_seconds()) if ultimo else t("molto tempo"), tipo=tipo, ora=_ora(ultimo), ip=ip)
            misura = presenza.deriva(varco_id)
            if misura and abs(misura[0]) > 30:
                testo += t(" Il suo orologio e' sballato di %(s)s s: i pacchetti vengono scartati.", s=f"{misura[0]:+.0f}")
            else:
                testo += t(" Controlla alimentazione, cavo di rete e che lo slave sia acceso.")
            registra_log(db, testo, "warning", "dispositivi", varco_id, commit=False, codice="DEV_OFFLINE",
                         dettagli={"ip": ip, "ultimo_pacchetto": tipo, "ultimo_contatto": _ora(ultimo),
                                   "silenzio_s": round((adesso - ultimo).total_seconds()) if ultimo else None,
                                   "soglia_s": presenza.SOGLIA_OFFLINE_SEC})
        db.commit()
    except Exception:
        logger.exception("Errore scrivendo i varchi offline nel log")
        db.rollback()
    finally:
        db.close()
    return len(scaduti)


# ---------- Modalita' diagnostica ----------
# Per cercare un problema di rete o di firmware serve vedere OGNI pacchetto: in condizioni normali
# sarebbe rumore (un heartbeat ogni 3 s per varco), quindi si accende a tempo e si spegne da sola.

DIAGNOSTICA_MAX_MIN = 120
_diagnostica_fino = 0.0        # time.time() di spegnimento automatico


def diagnostica_attiva() -> bool:
    return time.time() < _diagnostica_fino


def diagnostica_residuo_sec() -> int:
    return max(0, int(_diagnostica_fino - time.time()))


def attiva_diagnostica(minuti: int) -> int:
    global _diagnostica_fino
    minuti = max(1, min(int(minuti), DIAGNOSTICA_MAX_MIN))
    _diagnostica_fino = time.time() + minuti * 60
    return minuti


def disattiva_diagnostica() -> None:
    global _diagnostica_fino
    _diagnostica_fino = 0.0


def traccia(messaggio: str, varco_id: Optional[int] = None, codice: str = "PKT_RX", dettagli: Optional[dict] = None, db=None) -> None:
    """Riga di livello «debug»: scritta solo in modalita' diagnostica."""
    if diagnostica_attiva():
        scrivi(messaggio, "debug", "rete", varco_id, db, codice, dettagli)


def dimentica(varco_id: int) -> None:
    with _lock:
        _stato.pop(varco_id, None)
        for k in [k for k in _anomalie if varco_id in k]:
            del _anomalie[k]


def azzera() -> None:
    """Solo per i test."""
    with _lock:
        _stato.clear()
        _anomalie.clear()

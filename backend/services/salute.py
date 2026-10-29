# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Stato di salute del sistema: una serie di controlli, ognuno con un livello
(ok / warning / error), un dettaglio leggibile e, dove serve, cosa fare.
La pagina Stato li mostra tutti insieme con un verdetto unico in alto.
"""
import os
import shutil
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Evento, LogSistema, Varco
from services import presenza, tempo, udp_agent, funzionamento, backup as backup_service
from services.avvio import AVVIATO_IL
from services.system_info import _temperatura_cpu_c, _memoria_mb
from services.lingua import t

SOGLIA_DISCO_WARN = 0.15      # frazione libera
SOGLIA_DISCO_ERR = 0.05
SOGLIA_TEMP_WARN = 70.0
SOGLIA_TEMP_ERR = 80.0
SOGLIA_CACHE_VECCHIA_ORE = 8  # l'invio automatico e' ogni 6 h
SOGLIA_BACKUP_GIORNI = 30
SOGLIA_CODA_SLAVE = 20        # eventi in coda sullo slave oltre i quali segnalare
SOGLIA_DERIVA_SEC = presenza.SOGLIA_DERIVA_SEC

ORDINE = {"error": 0, "warning": 1, "ok": 2}


def _c(id_: str, titolo: str, livello: str, dettaglio: str, azione: Optional[tuple[str, str, bool]] = None) -> dict:
    """azione = (etichetta, url, solo_admin)"""
    return {"id": id_, "titolo": titolo, "livello": livello, "dettaglio": dettaglio, "azione": azione}


def _durata(secondi: float) -> str:
    s = int(max(0, secondi))
    if s >= 86400:
        return f"{s // 86400} g {(s % 86400) // 3600} h"
    if s >= 3600:
        return f"{s // 3600} h {(s % 3600) // 60} min"
    if s >= 60:
        return f"{s // 60} min"
    return f"{s} s"


def _dimensione(byte: int) -> str:
    if byte >= 1024 ** 3:
        return f"{byte / 1024 ** 3:.1f} GB"
    if byte >= 1024 ** 2:
        return f"{byte / 1024 ** 2:.1f} MB"
    return f"{byte / 1024:.0f} kB"


def controlli_sistema(db: Session, adesso: Optional[datetime] = None) -> list[dict]:
    adesso = adesso or datetime.utcnow()
    risultato = []

    # --- Canale UDP
    stato = udp_agent.stato()
    if stato["ricezione_attiva"] and stato["cache_attiva"]:
        ultimo = udp_agent.ultimo_invio_cache()
        quando = t("ultima cache inviata %(p)s fa", p=_durata((adesso - ultimo).total_seconds())) if ultimo else t("nessuna cache ancora inviata da questo avvio")
        risultato.append(_c("udp", t("Canale con i varchi (UDP)"), "ok", t("In ascolto; %(quando)s.", quando=quando)))
    else:
        parti = []
        if not stato["ricezione_attiva"]:
            parti.append(t("la ricezione e' ferma"))
        if not stato["cache_attiva"]:
            parti.append(t("l'invio periodico della cache e' fermo"))
        risultato.append(_c("udp", t("Canale con i varchi (UDP)"), "error", t("Problema: %(parti)s. I varchi non ricevono aggiornamenti.", parti=(" " + t("e") + " ").join(parti)),
                            (t("Riavvia l'app"), "/sistema", True)))

    # --- Database
    try:
        percorso = backup_service._percorso_db()
        dim = os.path.getsize(percorso)
        wal = os.path.getsize(percorso + "-wal") if os.path.exists(percorso + "-wal") else 0
        n_eventi = db.query(func.count(Evento.id)).scalar() or 0
        risultato.append(_c("db", t("Database"), "ok", t("%(p)s (+%(p2)s di journal), %(n_eventi)s passaggi registrati.", p=_dimensione(dim), p2=_dimensione(wal), n_eventi=n_eventi),
                            (t("Conservazione dei dati"), "/sistema", True)))
    except OSError as e:
        risultato.append(_c("db", t("Database"), "warning", t("Impossibile leggere la dimensione del file (%(e)s).", e=e)))

    # --- Disco
    try:
        disco = shutil.disk_usage("/")
        libero = disco.free / disco.total
        dett = t("%(p)s liberi su %(p2)s (%(p3)s%%).", p=_dimensione(disco.free), p2=_dimensione(disco.total), p3=format(libero * 100, ".0f"))
        if libero < SOGLIA_DISCO_ERR:
            risultato.append(_c("disco", t("Spazio su disco"), "error", dett + t(" Quasi pieno: il database potrebbe smettere di scrivere."),
                                (t("Conservazione dei dati"), "/sistema", True)))
        elif libero < SOGLIA_DISCO_WARN:
            risultato.append(_c("disco", t("Spazio su disco"), "warning", dett + t(" Sta finendo."), (t("Conservazione dei dati"), "/sistema", True)))
        else:
            risultato.append(_c("disco", t("Spazio su disco"), "ok", dett))
    except OSError:
        pass

    # --- Temperatura e memoria
    temp = _temperatura_cpu_c()
    if temp is not None:
        liv = "error" if temp >= SOGLIA_TEMP_ERR else "warning" if temp >= SOGLIA_TEMP_WARN else "ok"
        extra = t(" Troppo calda: rischio di rallentamenti e blocchi.") if liv != "ok" else ""
        risultato.append(_c("temp", t("Temperatura CPU"), liv, t("%(temp)s °C.%(extra)s", temp=format(temp, ".0f"), extra=extra)))
    totale, disp = _memoria_mb()
    if totale and disp is not None:
        frazione = disp / totale
        liv = "warning" if frazione < 0.1 else "ok"
        risultato.append(_c("ram", t("Memoria"), liv, t("%(disp)s MB disponibili su %(totale)s MB.", disp=disp, totale=totale) + (t(" Poca memoria libera.") if liv != "ok" else "")))

    # --- Ora / fuso
    risultato.append(_c("ora", t("Ora del sistema"), "ok", t("%(p)s (%(NOME_FUSO)s). L'app e' attiva da %(p2)s.", p=tempo.adesso_locale().strftime('%d/%m/%Y %H:%M'), NOME_FUSO=tempo.NOME_FUSO, p2=_durata((adesso - AVVIATO_IL).total_seconds()))))

    # --- Configurazione
    if not os.environ.get("SESSION_SECRET"):
        risultato.append(_c("segreto", t("Chiave delle sessioni"), "warning",
                            t("SESSION_SECRET non e' impostato nel file .env: a ogni riavvio tutti vengono disconnessi.")))

    # --- Backup
    ultimo_backup = (
        db.query(func.max(LogSistema.timestamp)).filter(LogSistema.messaggio.like("Backup del database scaricato%")).scalar()
    )
    if ultimo_backup is None:
        risultato.append(_c("backup", t("Backup"), "warning", t("Nessun backup scaricato finora (o log ripulito). Se la scheda si guasta, si perde tutto."),
                            (t("Scarica un backup"), "/sistema", True)))
    else:
        eta = adesso - ultimo_backup
        if eta > timedelta(days=SOGLIA_BACKUP_GIORNI):
            risultato.append(_c("backup", t("Backup"), "warning", t("L'ultimo backup scaricato risale a %(days)s giorni fa.", days=eta.days), (t("Scarica un backup"), "/sistema", True)))
        else:
            risultato.append(_c("backup", t("Backup"), "ok", t("Ultimo backup scaricato %(p)s.", p=tempo.formatta(ultimo_backup, '%d/%m/%Y'))))
    return risultato


def controlli_varco(varco: Varco, adesso: Optional[datetime] = None) -> list[dict]:
    """Controlli del singolo varco (richiede gli attributi calcolati da _calcola_stato_online)."""
    adesso = adesso or datetime.utcnow()
    nome = varco.nome
    out = []
    if not getattr(varco, "online", False):
        ult = getattr(varco, "ultimo_contatto", None)
        if ult or getattr(varco, "deriva_sec", None) is not None:
            ult_txt = t("ultimo contatto %(p)s.", p=tempo.formatta(ult)) if ult else t("nessun pacchetto valido ricevuto.")
            out.append(_c(f"v{varco.id}-online", t("%(nome)s: dispositivo", nome=nome), "error", t("Offline: %(ult_txt)s", ult_txt=ult_txt), (t("Apri Varchi"), f"/varchi?apri={varco.id}", False)))
        else:
            # Varco appena creato: il dispositivo non si e' ancora collegato. Non e' un guasto.
            out.append(_c(f"v{varco.id}-online", t("%(nome)s: dispositivo", nome=nome), "warning", t("In attesa del primo collegamento del dispositivo."), (t("Apri Varchi"), f"/varchi?apri={varco.id}", False)))
    else:
        out.append(_c(f"v{varco.id}-online", t("%(nome)s: dispositivo", nome=nome), "ok", t("Online.")))
    if not varco.ip_address:
        out.append(_c(f"v{varco.id}-ip", t("%(nome)s: indirizzo IP", nome=nome), "warning", t("Nessun IP configurato: non si possono mandare aperture remote e i suoi pacchetti vengono scartati."),
                      (t("Apri Varchi"), "/varchi", False)))
    # cache
    if varco.ultima_cache_versione is None:
        out.append(_c(f"v{varco.id}-cache", t("%(nome)s: dati badge", nome=nome), "warning", t("La cache non e' ancora stata inviata."), (t("Scarica dati"), "/varchi", False)))
    elif varco.ultima_cache_confermata != varco.ultima_cache_versione:
        out.append(_c(f"v{varco.id}-cache", t("%(nome)s: dati badge", nome=nome), "warning" if getattr(varco, "online", False) else "error",
                      t("L'ultima cache inviata non e' stata confermata dal dispositivo: potrebbe avere dati vecchi."), (t("Scarica dati"), "/varchi", False)))
    else:
        out.append(_c(f"v{varco.id}-cache", t("%(nome)s: dati badge", nome=nome), "ok", t("Cache consegnata e confermata.")))
    # orologio
    if getattr(varco, "deriva_sec", None) is not None and getattr(varco, "online", False):     # offline = misura vecchia, non conta
        liv = "ok" if varco.deriva_corretta else "warning"
        out.append(_c(f"v{varco.id}-orologio", t("%(nome)s: orologio", nome=nome), liv,
                      t("Sfasato di %(p)s s", p=format(abs(varco.deriva_sec), ".0f")) + (t(", ma lo slave compensa da solo.") if varco.deriva_corretta else t(": serve riallineare.")),
                      None if varco.deriva_corretta else (t("Scarica dati"), "/varchi", False)))
    # coda eventi
    coda = presenza.coda(varco.id)
    if coda is not None and coda > 0:
        liv = "warning" if coda >= SOGLIA_CODA_SLAVE or not getattr(varco, "online", False) else "ok"
        out.append(_c(f"v{varco.id}-coda", t("%(nome)s: eventi da consegnare", nome=nome), liv, t("%(coda)s eventi in coda sul dispositivo", coda=coda) + (t(" (verranno consegnati a breve).") if liv == "ok" else t(": non riesce a consegnarli."))))
    # modalita'
    if varco.modalita and varco.modalita != "normale":
        etichette = {"sblocco": t("sblocco programmato"), "manutenzione": t("manutenzione"), "emergenza": t("EMERGENZA (porta libera)")}
        liv = "error" if varco.modalita == "emergenza" else "warning"
        out.append(_c(f"v{varco.id}-modo", t("%(nome)s: modalita'", nome=nome), liv, t("In modalita' %(p)s.", p=etichette.get(varco.modalita, varco.modalita)), (t("Apri Varchi"), f"/varchi?apri={varco.id}", False)))
    # sensore porta: solo se il varco ne ha uno configurato (un vecchio stato rimasto in memoria non conta)
    # (offline lo stato porta non e' noto: il problema e' gia' segnalato dal controllo «dispositivo»)
    if varco.porta_stato and getattr(varco, "online", False) and funzionamento.sensore_porta_attivo(varco):
        aperta_da = (adesso - varco.porta_stato_at).total_seconds() if varco.porta_stato_at else 0
        if varco.porta_stato == "aperta" and aperta_da > 300 and varco.modalita in (None, "normale"):
            out.append(_c(f"v{varco.id}-porta", t("%(nome)s: porta", nome=nome), "warning", t("Risulta aperta da %(p)s minuti.", p=format(aperta_da/60, ".0f"))))
        else:
            out.append(_c(f"v{varco.id}-porta", t("%(nome)s: porta", nome=nome), "ok", t("Sensore attivo, porta %(porta_stato)s.", porta_stato=varco.porta_stato)))
    if not varco.attivo:
        out.append(_c(f"v{varco.id}-attivo", t("%(nome)s: attivazione", nome=nome), "warning", t("Il varco e' disattivato: nega tutti gli accessi."), (t("Apri Varchi"), f"/varchi?apri={varco.id}", False)))
    return out


def verdetto(controlli: list[dict]) -> dict:
    errori = sum(1 for c in controlli if c["livello"] == "error")
    avvisi = sum(1 for c in controlli if c["livello"] == "warning")
    if errori:
        return {"livello": "error", "titolo": t("1 problema da risolvere") if errori == 1 else t("%(n)s problemi da risolvere", n=errori), "errori": errori, "avvisi": avvisi}
    if avvisi:
        return {"livello": "warning", "titolo": t("1 cosa da controllare") if avvisi == 1 else t("%(n)s cose da controllare", n=avvisi), "errori": 0, "avvisi": avvisi}
    return {"livello": "ok", "titolo": t("Tutto regolare"), "errori": 0, "avvisi": 0}

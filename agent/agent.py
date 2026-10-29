# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Agent di varco: gira su ogni Pi (o in futuro microcontrollore/SBC con un
firmware dedicato) che gestisce un varco fisico (anche sulla Pi master
stessa, per i test con lettore collegato direttamente).

Canale col backend: solo UDP (vedi services/udp_client.py), niente HTTP e
niente websocket — quel canale è stato ritirato: tutti gli slave, Pi
comprese, useranno lo stesso protocollo leggero così la stessa logica vale
anche su un microcontrollore futuro (al momento questo agent Python resta
anche il modo più comodo per testare il protocollo end-to-end).

Flusso:
  1. Riceve dal master, in unicast, la cache delle credenziali autorizzate
     su questo varco (badge + fasce orarie) — periodicamente (ogni 6h) o a
     richiesta (bottone "Scarica dati" sulla webapp).
  2. Legge un badge dal lettore: la decisione (concesso/negato) è presa SUL
     POSTO usando solo quella cache, nessuna richiesta al master.
  3. Se concesso, apre il relè, POI manda al master un report dell'evento
     (asincrono, non bloccante: l'accesso è già avvenuto, non si resta in
     attesa di un'approvazione).
  4. In parallelo: manda un heartbeat periodico e ascolta eventuali comandi
     'apri' push dal master (apertura remota da webapp), rispondendo con un
     esito.

Avvio: python agent.py [config.yaml]
"""
from __future__ import annotations
import sys
import time
import logging
import threading

from hardware.reader_serial import SerialReader
from hardware.relay_gpio import Relay
from hardware.ingresso import Ingresso
from services.ingressi import GestoreIngressi
from services.porta import MonitorPorta
from services.uscite import GestoreUscite
from services.udp_client import ClienteUDP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("agent")


def load_config(path: str) -> dict:
    import yaml   # qui e non in cima: la modalita' di prova (test_slave.py) non ne ha bisogno
    with open(path) as f:
        return yaml.safe_load(f)


def loop_ricezione(cliente: ClienteUDP, uscite: GestoreUscite, stop: threading.Event):
    """
    Thread dedicato: ascolta in continuazione i pacchetti UDP dal master
    (aggiornamenti di cache, comandi 'apri' e comandi alle uscite). Un timeout breve sul socket
    (impostato in ClienteUDP) fa sì che questo loop controlli regolarmente
    anche 'stop', senza bisogno di altro.
    """
    while not stop.is_set():
        comando = cliente.ricevi_pacchetto()
        if not comando:
            continue
        if comando["tipo"] == "apri":
            logger.info("Apertura remota richiesta dal master (UDP)")
            uscite.apri_porte(comando["durata_sec"])
            cliente.manda_esito_apertura("ok")
        elif comando["tipo"] == "uscita":
            uscite.comando(comando["indice"], comando["azione"])
        elif comando["tipo"] == "abbinato":
            logger.info("Abbinamento completato: chiedo la cache al master")
            cliente.manda_richiesta_cache()


INTERVALLO_RICHIESTA_CACHE_SEC = 30
INTERVALLO_INGRESSI_SEC = 0.1


def loop_ingressi(gestore: GestoreIngressi, stop: threading.Event):
    """Campiona gli ingressi (sensore porta, pulsanti, "Altro") e passa i cambi di stato alla logica."""
    while not stop.is_set():
        try:
            gestore.campiona()
        except Exception:
            logger.exception("Errore nel controllo degli ingressi")
        stop.wait(INTERVALLO_INGRESSI_SEC)


def loop_uscite(uscite: GestoreUscite, stop: threading.Event):
    """Tiene le uscite mantenute come richiesto (porta libera in emergenza/sblocco, inseritore
    acceso/spento), controllando ogni secondo con l'ora corretta dal master.
    Decide solo la cache locale: funziona anche se il master e' spento."""
    while not stop.is_set():
        try:
            uscite.tick()
        except Exception:
            logger.exception("Errore nel controllo delle uscite")
        stop.wait(1.0)


def loop_heartbeat(cliente: ClienteUDP, intervallo_sec: float, stop: threading.Event):
    ultima_richiesta = 0.0
    while not stop.is_set():
        cliente.manda_heartbeat()
        cliente.pompa()   # ritrasmette l'evento in coda se il master non ha ancora confermato
        # Finche' non e' arrivata una cache dal master in questa esecuzione
        # (all'avvio, o dopo che il master era spento) la chiede da solo ogni
        # 30 s, invece di aspettare il broadcast periodico di 6 ore.
        if not cliente.cache_fresca and time.monotonic() - ultima_richiesta >= INTERVALLO_RICHIESTA_CACHE_SEC:
            cliente.manda_richiesta_cache()
            ultima_richiesta = time.monotonic()
        stop.wait(intervallo_sec)


def loop_lettore(indice: int, lettore: SerialReader, cliente: ClienteUDP, uscite: GestoreUscite, stop: threading.Event):
    """Un lettore badge: la decisione e' locale, poi apre le uscite di QUESTO lettore e riporta l'evento."""
    while not stop.is_set():
        try:
            codice = lettore.leggi_badge()
            if not codice:
                continue
            fn = cliente.fn
            if fn is not None and indice >= len(fn.lettori):
                logger.info(f"Lettore {indice + 1} non usato nel funzionamento del varco: badge ignorato")
                continue

            logger.info(f"Badge letto (lettore {indice + 1}): {codice}")
            concesso, esito = cliente.valuta_accesso(codice, cliente.ora_locale())
            logger.info(f"Esito (deciso in locale): {esito}")

            if concesso:
                uscite.apri_per_lettore(indice)

            # Report mandato DOPO l'apertura, non prima: non è una richiesta
            # di permesso, solo una notifica di cosa è già avvenuto.
            cliente.manda_evento(codice, esito, lettore=indice if fn is not None else None)
        except Exception as e:
            logger.exception(f"Errore inatteso nel lettore {indice + 1}: {e}")
            stop.wait(1)


def elenco(cfg: dict, chiave: str, vecchia: str | None = None) -> list[dict]:
    """Elenco di dispositivi dal config: `chiave` (lista) oppure, per i config vecchi, la voce singola `vecchia`."""
    valore = cfg.get(chiave)
    if isinstance(valore, list):
        return [v for v in valore if isinstance(v, dict) or v is None]
    if vecchia and isinstance(cfg.get(vecchia), dict):
        return [cfg[vecchia]]
    return []


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg = load_config(config_path)

    varco_id = cfg["varco_id"]
    api_key = cfg.get("api_key") or None   # senza: arriva dal master con l'abbinamento

    # Hardware: i pin restano qui; cosa fa ogni cosa lo decide il master (popup Funzionamento).
    # Config vecchio (reader / relay / door_sensor singoli) ancora accettato.
    lettori_cfg = elenco(cfg, "readers", "reader")
    relays_cfg = elenco(cfg, "relays", "relay")
    inputs_cfg = elenco(cfg, "inputs", "door_sensor")
    lettori = [SerialReader(port=r["port"], baudrate=r.get("baudrate", 9600)) for r in lettori_cfg]
    relays = [Relay(pin=r["pin"], active_high=r.get("active_high", True)) for r in relays_cfg]
    ingressi = [Ingresso(r["pin"]) if r else None for r in inputs_cfg]
    durata_apertura_fallback = (relays_cfg[0].get("open_duration_sec", 3) if relays_cfg else 3)
    heartbeat_interval = cfg.get("udp", {}).get("heartbeat_interval_sec", 3)

    legacy_porta = cfg.get("door_sensor") if isinstance(cfg.get("door_sensor"), dict) else None
    legacy_max = legacy_porta.get("max_open_sec", 30) if legacy_porta else 30
    legacy_nc = (legacy_porta.get("closed_level", "low").lower() == "low") if legacy_porta else True

    udp_cfg = cfg.get("udp", {})
    # Le porte cambiano solo nelle prove con master e slave sullo stesso PC; in produzione restano 57990.
    cliente = ClienteUDP(varco_id, api_key, stato_dir=cfg.get("state_dir", "stato"),
                         porta_ascolto=udp_cfg.get("listen_port", 57990), porta_master=udp_cfg.get("master_port", 57990))
    if not cliente.cache_ricevuta:
        cliente.durata_apertura_sec = durata_apertura_fallback  # finché non arriva la prima cache

    monitor_porta = MonitorPorta(cliente, legacy_max)
    uscite = GestoreUscite(cliente, relays, stato_dir=cfg.get("state_dir", "stato"), monitor=monitor_porta)
    monitor_porta._su_allarme = lambda tipo: uscite.sirena()
    cliente.stato_uscite = uscite.stato_testo
    gestore_ingressi = GestoreIngressi(cliente, ingressi, monitor_porta, uscite, legacy_nc=legacy_nc)
    monitor_porta._soglia = lambda: gestore_ingressi.soglia_aperta() or legacy_max
    # Lo stato porta nel battito vale solo se il master ha configurato un sensore.
    cliente.stato_porta = lambda: monitor_porta.stato_testo() if gestore_ingressi.indice_sensore() is not None and cliente.fn is not None else None

    stop = threading.Event()
    threading.Thread(target=loop_ricezione, args=(cliente, uscite, stop), daemon=True).start()
    threading.Thread(target=loop_heartbeat, args=(cliente, heartbeat_interval, stop), daemon=True).start()
    threading.Thread(target=loop_uscite, args=(uscite, stop), daemon=True).start()
    if any(i is not None for i in ingressi):
        threading.Thread(target=loop_ingressi, args=(gestore_ingressi, stop), daemon=True).start()
        logger.info(f"{sum(1 for i in ingressi if i is not None)} ingressi attivi")

    logger.info(f"Agent avviato per varco_id={varco_id} (canale UDP, porta {cliente.sock.getsockname()[1]}), "
                f"{len(lettori)} lettori, {len(relays)} uscite")
    if not cliente.abbinato:
        logger.info(f"Non abbinato: aspetto che il master mi contatti (varco {varco_id}, IP scritto nella pagina Varchi). "
                    "Poi premi «Abbina» nella pagina Varchi della webapp.")
    elif cliente.cache_ricevuta:
        logger.info("Cache dell'ultima sessione caricata da disco: si può lavorare subito, intanto la chiedo al master.")
    else:
        logger.info("In attesa della prima cache dal master: finché non arriva, ogni badge risulterà non riconosciuto.")

    if not lettori:
        logger.info("Nessun lettore nel config.yaml: questo slave comanda solo uscite e ingressi (apertura da webapp, pulsanti, sensore porta)")
    for i, lettore in enumerate(lettori):
        threading.Thread(target=loop_lettore, args=(i, lettore, cliente, uscite, stop), daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Arresto agent richiesto, chiudo.")

    stop.set()
    for r in relays:
        r.mantieni(False)
    for l in lettori:
        l.close()


if __name__ == "__main__":
    main()

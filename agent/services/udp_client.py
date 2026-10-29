# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Lato slave del canale UDP leggero (vedi backend/services/udp_agent.py per la
spiegazione completa del protocollo: qui c'è solo la controparte che parla
lo stesso formato). Nessun server/API nello slave: un socket UDP, HMAC con
l'api_key del varco, basta.

Pensato per restare una guida diretta a un firmware (ESP32/microcontrollore)
quando lo scriveremo: stessa logica, stesso formato pacchetti, solo altro
linguaggio. Per ora questo agent Python è anche il modo più comodo per
testare il protocollo end-to-end prima di scrivere firmware vero.
"""
from __future__ import annotations
import os
import hmac
import ipaddress
import time
import json
import socket
import re
import hashlib
import secrets
import logging
import tempfile
import threading
from datetime import datetime, timezone

from services import funzionamento as fnmod

logger = logging.getLogger("udp_client")

PORTA = 57990
FINESTRA_REPLAY_SEC = 30
LUNGHEZZA_FIRMA = 32

# Coda eventi (vedi accoda()): massimo di eventi tenuti in attesa di conferma
# se il master e' irraggiungibile, e tempi di ritrasmissione (raddoppiano a ogni
# tentativo fino al tetto).
MAX_EVENTI_IN_CODA = 2000
RITRASMISSIONE_MIN_SEC = 3.0
RITRASMISSIONE_MAX_SEC = 60.0

# Rete: tutto in unicast. Lo slave non manda mai niente per primo: aspetta che il master lo "tocchi"
# (HI, all'IP che l'admin ha scritto nella pagina Varchi) e da quel pacchetto impara l'IP del master,
# che poi ricorda su disco. Senza chiave risponde al tocco con una PR (richiesta di abbinamento); con
# la chiave verifica la firma del tocco, aggiorna l'IP del master e risponde con un battito.
# Una PA (la chiave) vale solo se arriva dallo stesso IP che ha appena toccato lo slave.
TOCCO_VALIDITA_SEC = 60
_CHIAVE_VALIDA = re.compile(r"^[0-9a-f]{16,64}$")


def _ip_valido(testo: str) -> bool:
    try:
        ipaddress.IPv4Address(testo)
        return True
    except ValueError:
        return False


def _firma(payload: bytes, api_key: str) -> str:
    return hmac.new(api_key.encode(), payload, hashlib.sha256).hexdigest()[:LUNGHEZZA_FIRMA]


def _verifica(payload: bytes, api_key: str, firma_ricevuta: str) -> bool:
    return hmac.compare_digest(_firma(payload, api_key), firma_ricevuta)


class ClienteUDP:
    """
    Un'istanza per varco. Mantiene un unico socket (bind su 0.0.0.0:porta_ascolto)
    usato sia per ricevere (cache dal master, comandi 'apri', tocchi) sia per mandare
    (heartbeat, report evento, esito apertura) — tutto in unicast verso l'IP del master,
    che lo slave impara dal primo tocco firmato e ricorda su disco. Il master filtra per
    IP registrato nella pagina Varchi.
    """

    def __init__(self, varco_id: int, api_key: str | None, crea_socket: bool = True, stato_dir: str | None = None,
                 porta_ascolto: int = PORTA, porta_master: int = PORTA):
        # crea_socket=False serve solo ai test: la logica del protocollo si prova
        # senza aprire la porta 57990.
        # stato_dir: cartella dove lo slave tiene la cache su disco, cosi' dopo
        # un riavvio (o un'interruzione di corrente) riparte con le regole
        # dell'ultima cache valida invece di negare tutti per ore.
        self.varco_id = varco_id
        # api_key None = non indicata nel config: la chiave la manda il master all'abbinamento
        # e resta su disco (stato_dir). Una chiave scritta a mano nel config vale come prima.
        self.api_key = api_key
        self.chiave_da_file = not api_key
        self.percorso_chiave = None
        self._nonce = secrets.token_hex(8)     # lega la risposta del master a QUESTA richiesta
        self.porta_ascolto = porta_ascolto
        self.porta_master = porta_master       # dove ascolta il master (uguale per tutti; diversa solo nelle prove su un solo PC)
        self.master_ip: str | None = None      # imparato da un tocco firmato (o dall'abbinamento), ricordato su disco
        self.percorso_master = None
        self._tocco_ip: str | None = None      # chi ha toccato per ultimo lo slave senza chiave (la PA vale solo da lui)
        self._tocco_alle = 0.0
        # Fornitori dello stato attuale per il battito (li imposta chi possiede sensore e uscite).
        self.stato_porta = None       # () -> "a" aperta | "c" chiusa | None (nessun sensore / nessuna lettura)
        self.stato_uscite = None      # () -> es. "1-0": acceso/spento per ogni uscita, "-" se non e' un interruttore
        self.autorizzazioni: dict[str, dict] = {}   # codice -> {"sempre": True} | {"f": [[g,ini,fine],...]}
        self.durata_apertura_sec = 3                # aggiornata dalla prima cache ricevuta
        self.cache_ricevuta = False
        # True finché non arriva una cache con "attivo": false esplicito (il
        # master lo manda solo da quando esiste la disattivazione varco in
        # webapp): di default un varco è attivo, anche prima della prima
        # cache mai ricevuta.
        self.varco_attivo = True
        self._assemblaggio = None                   # cache multi-pacchetto in corso di ricezione
        # False finche' non arriva una cache dal master in questa esecuzione:
        # se la cache in uso e' quella letta da disco, lo slave la chiede al master (RC).
        self.cache_fresca = False
        self.versione_cache = None
        self.percorso_cache = None

        # Ora e fuso del master (arrivano con ogni cache): lo slave puo' avere
        # l'orologio sballato (niente batteria/NTP) o il fuso sbagliato. deriva_sec
        # = ora master - ora locale; fuso_min = scarto dell'ora locale del sito da UTC.
        self.deriva_sec = 0.0
        self.fuso_min = None

        # Giorni festivi ("AAAA-MM-GG") dall'ultima cache: le righe di fascia che li
        # escludono non valgono in quei giorni.
        self.festivi: set[str] = set()

        # Modalita' del varco dall'ultima cache: "n" normale, "s" sblocco programmato
        # (porta libera nelle righe di self.sblocco), "m" manutenzione, "e" emergenza.
        self.modo = "n"
        self.sblocco: list = []

        # Funzionamento (lettori, uscite, ingressi, avvisi) dall'ultima cache: None = il master
        # non lo manda (versione vecchia, o varco mai configurato dal popup) e si fa come prima.
        self._fn_raw = None
        self.fn: fnmod.Funzionamento | None = None

        # Coda eventi: gli eventi (passaggi, sensore porta) non si perdono se il
        # master e' spento o un pacchetto si perde: restano in coda, su disco, e
        # si ritrasmettono uno alla volta finche' il master conferma con AE.
        self._lock = threading.RLock()
        self._coda: list[dict] = []
        self._ultimo_seq = 0
        self._inviato_seq = None
        self._inviato_alle = 0.0
        self._tentativi = 0
        self.percorso_coda = None
        if stato_dir:
            os.makedirs(stato_dir, exist_ok=True)
            self.percorso_chiave = os.path.join(stato_dir, f"chiave_varco_{varco_id}.txt")
            self.percorso_master = os.path.join(stato_dir, f"master_varco_{varco_id}.txt")
            self._carica_master()
            if not self.api_key:
                self._carica_chiave()
            self.percorso_cache = os.path.join(stato_dir, f"cache_varco_{varco_id}.json")
            self.percorso_coda = os.path.join(stato_dir, f"coda_eventi_varco_{varco_id}.json")
            self._carica_cache_da_disco()
            self._carica_coda()

        self.sock = None
        if crea_socket:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                # Utile per i test con master e agent sulla stessa macchina: due
                # processi diversi che vogliono entrambi ricevere sulla stessa porta.
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            # Buffer di ricezione piu' grande del default: la cache arriva a raffica
            # (un datagramma ogni pochi ms) e un buffer piccolo perderebbe pezzi.
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
            except OSError:
                pass
            self.sock.bind(("0.0.0.0", self.porta_ascolto))
            self.sock.settimeout(1.0)

    # ---------- Ora ----------

    def ora(self) -> float:
        """Unix time corretto con l'ora del master (se la cache l'ha comunicata)."""
        return time.time() + self.deriva_sec

    def ora_locale(self) -> datetime:
        """Ora locale del sito (naive), per valutare le fasce orarie. Con il fuso
        comunicato dal master non dipende dal fuso configurato sullo slave."""
        if self.fuso_min is None:
            return datetime.now()
        return datetime.fromtimestamp(self.ora() + self.fuso_min * 60, tz=timezone.utc).replace(tzinfo=None)

    # ---------- Invio ----------

    # ---------- Abbinamento ----------

    @property
    def abbinato(self) -> bool:
        return bool(self.api_key)

    def _carica_chiave(self) -> None:
        try:
            with open(self.percorso_chiave) as f:
                chiave = f.read().strip()
        except OSError:
            return
        if _CHIAVE_VALIDA.match(chiave):
            self.api_key = chiave

    def _salva_chiave(self, chiave: str) -> None:
        if not self.percorso_chiave:
            return
        tmp = self.percorso_chiave + ".tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(chiave + "\n")
            os.replace(tmp, self.percorso_chiave)
        except OSError as e:
            logger.warning(f"Impossibile salvare la chiave su disco ({self.percorso_chiave}): {e}")

    # ---------- Master: indirizzo e tocco ----------

    def _carica_master(self) -> None:
        try:
            with open(self.percorso_master) as f:
                ip = f.read().strip()
        except OSError:
            return
        if _ip_valido(ip):
            self.master_ip = ip

    def _imposta_master(self, ip: str) -> None:
        if ip == self.master_ip:
            return
        self.master_ip = ip
        logger.info(f"Master: {ip}")
        if self.percorso_master:
            try:
                with open(self.percorso_master, "w") as f:
                    f.write(ip + "\n")
            except OSError as e:
                logger.warning(f"Impossibile salvare l'IP del master su disco: {e}")

    def manda_presentazione(self, ip: str | None = None) -> None:
        """PR: "sono lo slave N, abbinami". Non e' firmato (non c'e' ancora una chiave) e non porta
        segreti: il master lo mostra all'admin, che decide. Va a chi ci ha appena toccato."""
        ip = ip or self._tocco_ip
        if not ip:
            return
        corpo = "|".join(str(c) for c in ["PR", self.varco_id, int(time.time()), self._nonce]).encode()
        try:
            self.sock.sendto(corpo, (ip, self.porta_master))
        except OSError as e:
            logger.warning(f"Invio UDP a {ip} fallito: {e}")

    def _gestisci_tocco(self, raw: bytes, mittente: str | None):
        """HI|varco_id|ts|firma dal master. Senza chiave: risponde con una PR. Con la chiave: se la firma e'
        valida impara l'IP del master e risponde subito con un battito; se non lo e' (varco ricreato sul master,
        chiave reimpostata) risponde con una PR solo se il tocco arriva dal master che conosciamo."""
        if not mittente:
            return
        try:
            parti = raw.decode().split("|")
            if len(parti) != 4 or int(parti[1]) != self.varco_id:
                return
            ts = int(parti[2])
        except (UnicodeDecodeError, ValueError):
            return
        if not self.api_key:
            self._tocco_ip, self._tocco_alle = mittente, time.monotonic()
            self.manda_presentazione(mittente)
            return
        corpo = "|".join(parti[:3]).encode()
        if _verifica(corpo, self.api_key, parti[3]):
            if abs(time.time() + self.deriva_sec - ts) <= FINESTRA_REPLAY_SEC:
                self._imposta_master(mittente)
                self.manda_heartbeat()
            elif mittente == self.master_ip:
                self.manda_heartbeat()   # orologio non allineato: il master lo insegna con la cache; da uno sconosciuto puo' essere un replay
        elif mittente == self.master_ip and self.chiave_da_file:
            self._tocco_ip, self._tocco_alle = mittente, time.monotonic()
            self.manda_presentazione(mittente)

    def _gestisci_abbinamento(self, raw: bytes, mittente: str | None):
        """PA|varco_id|ts|nonce|chiave dal master. Vale solo con il nonce di questa esecuzione e solo da
        chi ci ha toccato poco fa (stesso IP): l'IP del master si impara da qui."""
        try:
            parti = raw.decode().split("|")
            if len(parti) != 5 or int(parti[1]) != self.varco_id:
                return None
        except (UnicodeDecodeError, ValueError):
            return None
        if not self.chiave_da_file:
            return None   # chiave scritta a mano nel config: l'abbinamento non la tocca
        if not mittente or mittente != self._tocco_ip or time.monotonic() - self._tocco_alle > TOCCO_VALIDITA_SEC:
            return None
        if not hmac.compare_digest(parti[3], self._nonce) or not _CHIAVE_VALIDA.match(parti[4]):
            return None
        self.api_key = parti[4]
        self._salva_chiave(parti[4])
        self._imposta_master(mittente)
        logger.info("Abbinato al master: chiave ricevuta e salvata.")
        return {"tipo": "abbinato"}

    def _manda_a_master(self, campi: list) -> None:
        if not self.api_key or not self.master_ip:
            return   # senza chiave non si firma; senza IP del master si aspetta il suo tocco
        corpo = "|".join(str(c) for c in campi).encode()
        pacchetto = corpo + b"|" + _firma(corpo, self.api_key).encode()
        try:
            self.sock.sendto(pacchetto, (self.master_ip, self.porta_master))
        except OSError as e:
            logger.warning(f"Invio UDP al master {self.master_ip} fallito: {e}")

    def manda_heartbeat(self) -> None:
        # Oltre a orologio e coda, il battito porta lo stato attuale (porta, uscite acceso/spento):
        # e' l'autoguarigione di quanto il master mostra se un evento si e' perso o il master e' ripartito.
        # "-" = non applicabile (nessun sensore / nessun interruttore).
        self._manda_a_master(["HB", self.varco_id, int(self.ora()), round(self.deriva_sec), self.eventi_in_coda(),
                               self._stato_o_trattino(self.stato_porta), self._stato_o_trattino(self.stato_uscite)])

    def manda_evento(self, media_codice: str, esito: str, lettore: int | None = None) -> None:
        """
        Report dell'esito di una lettura badge, mandato DOPO che la decisione
        è già stata presa ed eseguita in locale — mai una richiesta di
        permesso: il master qui si limita a prendere nota (log, anti-passback,
        monitoraggio), non decide nulla. L'evento entra nella coda con
        conferma (accoda): l'orario registrato e' quello della lettura, non
        quello in cui il master lo riceve.
        """
        # Il numero del lettore va in coda dopo seq/ts: un master vecchio lo ignora.
        self.accoda("EV", [media_codice, esito], extra=[lettore] if lettore is not None else None)

    def manda_ingresso(self, indice: int, attivo: bool) -> None:
        """IN: un ingresso (pulsante, telecomando, "Altro") e' scattato o e' tornato a riposo."""
        self.accoda("IN", [indice, 1 if attivo else 0])

    def manda_stato_uscita(self, indice: int, acceso: bool) -> None:
        """SU: stato acceso/spento di un'uscita (inseritore, "Altro" a interruttore)."""
        self.accoda("SU", [indice, 1 if acceso else 0])

    def manda_evento_porta(self, stato: str) -> None:
        """Evento del sensore porta (aperta, chiusa, forzata, lasciata_aperta),
        con la stessa garanzia di consegna degli eventi badge."""
        self.accoda("SP", [stato])

    # ---------- Coda eventi con conferma ----------

    def _prossimo_seq(self) -> int:
        # Numero progressivo che non dipende dall'orologio: parte dai millisecondi
        # attuali ma cresce sempre di almeno 1 anche se l'ora torna indietro
        # (e' salvato su disco con la coda).
        self._ultimo_seq = max(self._ultimo_seq + 1, int(time.time() * 1000))
        return self._ultimo_seq

    def accoda(self, tipo: str, campi: list, extra: list | None = None) -> None:
        with self._lock:
            voce = {"seq": self._prossimo_seq(), "tipo": tipo, "ts": int(self.ora()), "campi": campi}
            if extra:
                voce["extra"] = list(extra)
            self._coda.append(voce)
            if len(self._coda) > MAX_EVENTI_IN_CODA:
                scartati = len(self._coda) - MAX_EVENTI_IN_CODA
                del self._coda[:scartati]
                logger.warning(f"Coda eventi piena: scartati i {scartati} eventi piu' vecchi")
            self._salva_coda()
        self.pompa()

    @staticmethod
    def _stato_o_trattino(fornitore) -> str:
        try:
            valore = fornitore() if fornitore else None
        except Exception:
            valore = None
        return valore or "-"

    def eventi_in_coda(self) -> int:
        with self._lock:
            return len(self._coda)

    def pompa(self) -> None:
        """Manda il primo evento in coda se non e' ancora stato inviato o se la
        conferma non e' arrivata in tempo (un evento alla volta: l'ordine resta
        garantito e il master puo' scartare i duplicati con un solo numero)."""
        with self._lock:
            if not self._coda:
                return
            testa = self._coda[0]
            ora = time.monotonic()
            attesa = min(RITRASMISSIONE_MIN_SEC * (2 ** self._tentativi), RITRASMISSIONE_MAX_SEC)
            if self._inviato_seq == testa["seq"] and ora - self._inviato_alle < attesa:
                return
            if self._inviato_seq == testa["seq"]:
                self._tentativi += 1
            else:
                self._tentativi = 0
            self._inviato_seq = testa["seq"]
            self._inviato_alle = ora
            self._manda_a_master([testa["tipo"], self.varco_id, int(self.ora())] + list(testa["campi"]) + [testa["seq"], testa["ts"]] + list(testa.get("extra", [])))

    def _conferma_evento(self, seq: int) -> None:
        """AE dal master: toglie dalla coda tutto fino a `seq` e manda il successivo."""
        with self._lock:
            prima = len(self._coda)
            self._coda = [e for e in self._coda if e["seq"] > seq]
            if len(self._coda) != prima:
                self._inviato_seq = None
                self._tentativi = 0
                self._salva_coda()
        self.pompa()

    def _salva_coda(self) -> None:
        if not self.percorso_coda:
            return
        cartella = os.path.dirname(self.percorso_coda) or "."
        try:
            fd, tmp = tempfile.mkstemp(dir=cartella, prefix=".coda-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump({"ultimo_seq": self._ultimo_seq, "eventi": self._coda}, f, separators=(",", ":"))
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.percorso_coda)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.warning(f"Impossibile salvare la coda eventi su disco: {e}")

    def _carica_coda(self) -> None:
        try:
            with open(self.percorso_coda) as f:
                dato = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            logger.warning(f"Coda eventi su disco illeggibile, la ignoro ({e})")
            return
        eventi = dato.get("eventi")
        if not isinstance(eventi, list):
            return
        self._coda = [e for e in eventi if isinstance(e, dict) and {"seq", "tipo", "ts", "campi"} <= e.keys()]
        self._ultimo_seq = int(dato.get("ultimo_seq", 0))
        if self._coda:
            logger.info(f"Coda eventi ripresa da disco: {len(self._coda)} eventi ancora da consegnare al master")

    def manda_richiesta_cache(self) -> None:
        """RC: chiede al master di rimandare la cache di questo varco (dopo un
        avvio, o finche' quella in uso viene solo dal disco). Il master risponde
        con la solita cache (unicast) e rispetta un intervallo minimo."""
        self._manda_a_master(["RC", self.varco_id, int(self.ora())])

    def manda_esito_apertura(self, esito: str = "ok") -> None:
        self._manda_a_master(["EA", self.varco_id, int(self.ora()), esito])

    def manda_cache_ack(self, versione: int) -> None:
        """
        Conferma di consegna: mandata subito dopo aver verificato ed
        elaborato con successo un pacchetto cache, riportando la stessa
        "versione" ricevuta (così il master sa che non è l'ack di una cache
        vecchia). Il bottone "Scarica dati" sulla webapp aspetta questo
        pacchetto per mostrare una conferma reale, non solo "mandato".
        """
        self._manda_a_master(["CA", self.varco_id, int(self.ora()), versione])

    # ---------- Ricezione ----------

    def _gestisci_cache(self, raw: bytes) -> None:
        try:
            corpo_json, firma_ricevuta = raw.rsplit(b"\n", 1)
            dato = json.loads(corpo_json)
        except (ValueError, UnicodeDecodeError):
            return
        if not _verifica(corpo_json, self.api_key, firma_ricevuta.decode()):
            # Quasi certamente una cache per un altro varco (firmata con
            # un'altra api_key): normale quando il master manda un pacchetto
            # per ciascun varco, non è un errore da segnalare.
            return
        if dato.get("varco_id") != self.varco_id:
            return

        # La cache puo' arrivare in piu' pezzi (campi "parte"/"parti"): si
        # applica solo quando sono arrivati tutti, con la stessa "versione".
        # Un master vecchio manda un pezzo solo, senza questi campi.
        parti = int(dato.get("parti", 1) or 1)
        if parti <= 1:
            self._applica_cache(self._decodifica_voci(dato.get("badge", []), dato.get("profili", {})), dato)
            return

        versione = dato.get("versione")
        asm = self._assemblaggio
        if asm is None or asm["versione"] != versione or asm["parti"] != parti:
            asm = self._assemblaggio = {"versione": versione, "parti": parti, "pezzi": {}}
        asm["pezzi"][int(dato.get("parte", 0))] = dato
        if len(asm["pezzi"]) < parti:
            return
        autorizzazioni: dict[str, dict] = {}
        for i in range(parti):
            pezzo = asm["pezzi"].get(i)
            if pezzo is None:
                return  # indici non coerenti: aspetta un altro invio
            autorizzazioni.update(self._decodifica_voci(pezzo.get("badge", []), pezzo.get("profili", {})))
        self._assemblaggio = None
        self._applica_cache(autorizzazioni, asm["pezzi"][0])

    @staticmethod
    def _decodifica_voci(voci: list, profili: dict) -> dict[str, dict]:
        """Dal formato compatto ({"c","s"} / {"c","p"} + tabella profili) o da
        quello vecchio ({"c","sempre"} / {"c","f"}) a {codice: voce}."""
        risultato: dict[str, dict] = {}
        for voce in voci:
            codice = voce.get("c")
            if not codice:
                continue
            if voce.get("s") or voce.get("sempre"):
                dec = {"sempre": True}
            elif "p" in voce:
                dec = {"f": profili.get(str(voce["p"]), [])}
            else:
                dec = {"f": voce.get("f", [])}
            for k in ("d", "e"):   # validita' del badge (unix, secondi)
                if isinstance(voce.get(k), (int, float)):
                    dec[k] = voce[k]
            risultato[codice] = dec
        return risultato

    def _applica_cache(self, autorizzazioni: dict[str, dict], meta: dict) -> None:
        """Sostituisce in un colpo solo la cache in uso (mai a meta') e conferma al master."""
        self.autorizzazioni = autorizzazioni
        self.durata_apertura_sec = meta.get("durata_apertura_sec", self.durata_apertura_sec)
        # "attivo" manca nei pacchetti di master vecchi: default True per
        # restare compatibile (nessun varco si disattiva da solo per un
        # aggiornamento del master che non manda ancora questo campo).
        self.varco_attivo = meta.get("attivo", True)
        self.cache_ricevuta = True
        self.cache_fresca = True
        self.versione_cache = meta.get("versione")
        self.festivi = {f for f in meta.get("festivi", []) if isinstance(f, str)}
        self.modo = meta.get("modo", "n") if meta.get("modo") in ("n", "s", "m", "e") else "n"
        self.sblocco = meta.get("sblocco", []) if isinstance(meta.get("sblocco", []), list) else []
        if self.modo != "n":
            logger.warning(f"Modalità del varco: {self.modo!r} (e=emergenza, m=manutenzione, s=sblocco programmato)")
        self._imposta_fn(meta.get("fn"))
        self._allinea_ora(meta)
        logger.info(f"Cache aggiornata dal master: {len(self.autorizzazioni)} credenziali autorizzate")
        self._salva_cache_su_disco()

        versione = meta.get("versione")
        if versione is not None:
            self.manda_cache_ack(versione)

    def _imposta_fn(self, raw) -> None:
        """Applica la sezione "fn" della cache (None/malformata = comportamento di prima)."""
        self.fn = fnmod.leggi(raw)
        self._fn_raw = raw if self.fn is not None else None

    def _allinea_ora(self, meta: dict) -> None:
        """Una cache appena ricevuta porta l'ora del master e il fuso del sito."""
        fuso = meta.get("fuso_min")
        if isinstance(fuso, int):
            self.fuso_min = fuso
        ora_master = meta.get("ora")
        if isinstance(ora_master, (int, float)):
            nuova = float(ora_master) - time.time()
            if abs(nuova) >= 1 or abs(self.deriva_sec) >= 1:
                if abs(nuova - self.deriva_sec) >= 2:
                    logger.warning(f"Orologio di questo dispositivo sfasato di {-nuova:+.0f} s rispetto al master: lo compenso")
                self.deriva_sec = nuova
            else:
                self.deriva_sec = 0.0

    # ---------- Cache su disco ----------

    def _salva_cache_su_disco(self) -> None:
        """Scrittura atomica (file temporaneo + rename): un'interruzione di
        corrente a meta' non lascia mai un file di cache corrotto."""
        if not self.percorso_cache:
            return
        contenuto = {
            "formato": 1,
            "varco_id": self.varco_id,
            "salvata_alle": int(time.time()),
            "versione": self.versione_cache,
            "durata_apertura_sec": self.durata_apertura_sec,
            "attivo": self.varco_attivo,
            "fuso_min": self.fuso_min,
            "festivi": sorted(self.festivi),
            "modo": self.modo,
            "sblocco": self.sblocco,
            "fn": self._fn_raw,
            "autorizzazioni": self.autorizzazioni,
        }
        cartella = os.path.dirname(self.percorso_cache) or "."
        try:
            fd, tmp = tempfile.mkstemp(dir=cartella, prefix=".cache-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(contenuto, f, separators=(",", ":"))
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.percorso_cache)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.warning(f"Impossibile salvare la cache su disco ({self.percorso_cache}): {e}")

    def _carica_cache_da_disco(self) -> None:
        try:
            with open(self.percorso_cache) as f:
                dato = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            logger.warning(f"Cache su disco illeggibile, la ignoro ({e})")
            return
        if dato.get("varco_id") != self.varco_id or not isinstance(dato.get("autorizzazioni"), dict):
            logger.warning("Cache su disco di un altro varco o in formato inatteso: ignorata")
            return
        self.autorizzazioni = dato["autorizzazioni"]
        self.durata_apertura_sec = dato.get("durata_apertura_sec", self.durata_apertura_sec)
        self.varco_attivo = dato.get("attivo", True)
        fuso = dato.get("fuso_min")
        if isinstance(fuso, int):
            self.fuso_min = fuso
        self.festivi = {f for f in dato.get("festivi", []) if isinstance(f, str)}
        # La modalita' resta valida anche dopo un riavvio senza master: un'emergenza
        # deve tenere la porta libera finche' il master non dice altro.
        self.modo = dato.get("modo", "n") if dato.get("modo") in ("n", "s", "m", "e") else "n"
        self.sblocco = dato.get("sblocco", []) if isinstance(dato.get("sblocco", []), list) else []
        self._imposta_fn(dato.get("fn"))
        self.versione_cache = dato.get("versione")
        self.cache_ricevuta = True   # usabile subito; cache_fresca resta False
        eta_min = (time.time() - dato.get("salvata_alle", time.time())) / 60
        logger.info(
            f"Cache caricata da disco: {len(self.autorizzazioni)} credenziali "
            f"(salvata {eta_min:.0f} minuti fa), la chiedo comunque al master"
        )

    def _parse_piccolo(self, raw: bytes):
        try:
            testo = raw.decode()
            parti = testo.split("|")
            if len(parti) < 4:
                return None
            tipo, varco_id_str, ts_str = parti[0], parti[1], parti[2]
            firma_ricevuta = parti[-1]
            campi_specifici = parti[3:-1]
            varco_id = int(varco_id_str)
            ts = int(ts_str)
        except (UnicodeDecodeError, ValueError, IndexError):
            return None

        canonico = "|".join(parti[:-1]).encode()
        if not _verifica(canonico, self.api_key, firma_ricevuta):
            return None  # firmato per un altro varco: normale, ignora senza log
        if varco_id != self.varco_id:
            return None
        if abs(self.ora() - ts) > FINESTRA_REPLAY_SEC:
            logger.warning(f"Comando '{tipo}' scartato: fuori finestra temporale (possibile replay)")
            return None
        return {"tipo": tipo, "campi": campi_specifici}

    def ricevi_pacchetto(self):
        """
        Legge un pacchetto (se arrivato entro il timeout di 1s) e lo elabora.
        Ritorna un comando 'apri' da eseguire (dict con 'durata_sec'), oppure
        None se non c'era nulla da fare per il chiamante (cache aggiornata
        internamente, o pacchetto scartato/di un altro varco).
        """
        try:
            dato, (ip, _porta) = self.sock.recvfrom(65535)
        except socket.timeout:
            return None
        except OSError:
            return None
        return self.elabora_datagramma(dato, ip)

    def elabora_datagramma(self, dato: bytes, mittente: str | None = None):
        if dato.startswith(b"HI|"):
            self._gestisci_tocco(dato, mittente)
            return None
        if dato.startswith(b"PA|"):
            return self._gestisci_abbinamento(dato, mittente)
        if not self.api_key:
            return None   # non abbinato: tutto il resto e' firmato e non si puo' verificare
        if dato.startswith(b"{") or b"\n" in dato:
            self._gestisci_cache(dato)
            return None

        comando = self._parse_piccolo(dato)
        if comando is None:
            return None
        if comando["tipo"] == "AP":
            durata = int(comando["campi"][0]) if comando["campi"] else self.durata_apertura_sec
            return {"tipo": "apri", "durata_sec": durata}
        if comando["tipo"] == "UC":
            # Comando a un'uscita dal pannello: campi = [indice, azione] (i impulso, 1 accendi, 0 spegni)
            try:
                indice, azione = int(comando["campi"][0]), comando["campi"][1]
            except (IndexError, ValueError):
                return None
            if azione in ("i", "1", "0"):
                return {"tipo": "uscita", "indice": indice, "azione": azione}
            return None
        if comando["tipo"] == "AE":
            # Conferma di ricezione di un evento: campi = [seq]
            try:
                self._conferma_evento(int(comando["campi"][0]))
            except (IndexError, ValueError):
                pass
            return None
        return None

    # ---------- Decisione di accesso locale ----------

    def valuta_accesso(self, media_codice: str, now) -> tuple[bool, str]:
        """
        Decisione 100% locale sulla cache ricevuta: nessuna richiesta al
        master. Ritorna (concesso, esito) con 'esito' nei valori già usati
        dal master (models.EsitoEvento) così il report EV si integra senza
        bisogno di mappature aggiuntive.

        Il controllo "varco attivo" viene prima di tutto: uno slave UDP non
        ha altro modo di sapere se è stato disattivato dalla webapp (riceve
        comunque una cache con badge vuoti, indistinguibile altrimenti da
        "nessuno ha policy qui") — senza questo controllo riporterebbe ogni
        tentativo come 'negato_credenziale_sconosciuta', un esito sbagliato
        e fuorviante nei log del master.
        """
        # Porta libera (emergenza, o dentro la fascia di sblocco): nessun badge richiesto.
        if self.porta_libera(now):
            return True, "concesso"
        if not self.varco_attivo:
            return False, "negato_varco_disattivato"
        if self.modo == "m":
            return False, "negato_varco_manutenzione"

        voce = self.autorizzazioni.get(media_codice)
        if voce is None:
            return False, "negato_credenziale_sconosciuta"
        # Validita' del badge (ospiti, scadenze), con l'orologio corretto dal master.
        adesso = self.ora()
        if ("d" in voce and adesso < voce["d"]) or ("e" in voce and adesso > voce["e"]):
            return False, "negato_credenziale_scaduta"
        if voce.get("sempre"):
            return True, "concesso"
        if self._in_fasce(voce.get("f", []), now):
            return True, "concesso"
        return False, "negato_fuori_fascia"

    def _in_fasce(self, righe: list, now) -> bool:
        """True se `now` (ora locale) cade in una delle righe [giorno, min_inizio, min_fine(, 1)];
        il 4o elemento 1 esclude la riga nei giorni festivi; il giorno 7 e' la riga dei festivi."""
        oggi = now.date().isoformat()
        festivo = oggi in self.festivi
        giorno = 7 if festivo else now.weekday()   # nei festivi vale solo la riga «giorno 7» della griglia
        minuti = now.hour * 60 + now.minute
        for riga in righe:
            if len(riga) > 3 and riga[3] and festivo:
                continue
            if riga[0] == giorno and riga[1] <= minuti <= riga[2]:
                return True
        return False

    def porta_libera(self, now) -> bool:
        """La porta deve restare aperta senza badge: emergenza, o sblocco programmato in corso."""
        if self.modo == "e":
            return True
        return self.modo == "s" and self._in_fasce(self.sblocco, now)

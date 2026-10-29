# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Gestore delle uscite (relè) del varco: decide cosa fa ogni uscita in base al
funzionamento mandato dal master (sezione "fn" della cache) e ai comandi che arrivano.

Senza "fn" (master vecchio o varco mai configurato dal popup) c'e' una sola uscita,
la numero 1, che si comporta come prima: un badge valido o il comando 'apri' la azionano
per `durata_apertura_sec`, e in emergenza/sblocco resta eccitata (porta libera).

Con "fn":
  apre        impulso per il suo tempo (badge del lettore, pulsante, 'apri' dalla webapp);
              eccitata fissa in emergenza e nello sblocco programmato (porta libera)
  sirena      impulso quando il monitor porta segnala forzata / lasciata aperta
  inseritore  acceso/spento, comandato dal pannello (a livello, oppure impulso di 1 s per ogni cambio)
  altro       impulso o acceso/spento, dal pannello o da un ingresso "Altro"
Lo stato acceso/spento sta su disco: dopo un riavvio le uscite tornano come erano.

Tutto qui e' logica pura: le uscite reali (Relay) si passano da fuori e nei test sono finte.
"""
from __future__ import annotations
import json
import logging
import os
import tempfile
import threading
import time

logger = logging.getLogger("agent.uscite")

IMPULSO_INSERITORE_SEC = 1


class GestoreUscite:
    def __init__(self, cliente, relays: list, stato_dir: str | None = None, monitor=None, orologio=time.monotonic, avvia=None):
        self.cliente = cliente
        self.relays = relays
        self.monitor = monitor          # MonitorPorta: va avvisato quando un'apertura e' legittima
        self._orologio = orologio
        self._avvia = avvia or self._avvia_thread
        self._lock = threading.RLock()
        self._stato: dict[int, bool] = {}
        self._sirena_fino = 0.0
        self.percorso = os.path.join(stato_dir, f"uscite_varco_{cliente.varco_id}.json") if stato_dir else None
        if self.percorso:
            self._carica()

    # ---------- thread ----------

    @staticmethod
    def _avvia_thread(funzione, *args) -> threading.Thread:
        t = threading.Thread(target=funzione, args=args, daemon=True)
        t.start()
        return t

    # ---------- configurazione corrente ----------

    @property
    def fn(self):
        return self.cliente.fn

    def indici_apre(self) -> list[int]:
        fn = self.fn
        if fn is None:
            return [0] if self.relays else []
        return [i for i in fn.indici_apre() if i < len(self.relays)]

    def indici_lettore(self, lettore: int) -> list[int]:
        """Uscite che apre il lettore `lettore` con un badge valido."""
        fn = self.fn
        if fn is None:
            return [0] if self.relays else []
        if 0 <= lettore < len(fn.lettori):
            return [i for i in fn.lettori[lettore].apre if i < len(self.relays)]
        return []

    def _durata(self, indice: int, override: int | None = None) -> int:
        fn = self.fn
        if fn is None:
            return override if override else self.cliente.durata_apertura_sec
        return fn.uscite[indice].sec

    # ---------- aperture ----------

    def apri(self, indici: list[int], durata_override: int | None = None) -> list[threading.Thread]:
        """Impulso sulle uscite indicate, ciascuna per il suo tempo, senza bloccare chi chiama."""
        fn = self.fn
        lanciati = []
        for i in indici:
            if not 0 <= i < len(self.relays):
                continue
            sec = self._durata(i, durata_override)
            porta = fn is None or fn.uscite[i].ruolo == "apre"
            if porta and self.monitor is not None:
                self.monitor.apertura_autorizzata(sec)       # la porta che si apre ora e' legittima
            lanciati.append(self._avvia(self.relays[i].apri, sec))
        return lanciati

    def apri_porte(self, durata_override: int | None = None) -> list[threading.Thread]:
        """Comando 'apri' (webapp) o pulsante: tutte le uscite "apre". Con "fn" ognuna usa il suo
        tempo; senza, la durata mandata dal master."""
        return self.apri(self.indici_apre(), durata_override)

    def apri_per_lettore(self, lettore: int) -> list[threading.Thread]:
        return self.apri(self.indici_lettore(lettore))

    # ---------- sirena ----------

    def sirena(self) -> list[threading.Thread]:
        """Suona le uscite "sirena" (porta forzata o lasciata aperta). Muta in porta libera e
        se sta gia' suonando."""
        fn = self.fn
        if fn is None:
            return []
        adesso = self._orologio()
        if adesso < self._sirena_fino or self.cliente.porta_libera(self.cliente.ora_locale()):
            return []
        lanciati = []
        fine = adesso
        for i in fn.indici_sirena():
            if i < len(self.relays):
                sec = fn.uscite[i].sec
                fine = max(fine, adesso + sec)
                lanciati.append(self._avvia(self.relays[i].apri, sec))
        self._sirena_fino = fine
        if lanciati:
            logger.warning("Sirena attivata")
        return lanciati

    # ---------- stato acceso/spento ----------

    def stato(self, indice: int) -> bool:
        return self._stato.get(indice, False)

    def stato_testo(self) -> str | None:
        """Per il battito: un carattere per uscita, 1 acceso / 0 spento / - se non e' un interruttore.
        None se non c'e' nessun interruttore (o senza funzionamento dal master)."""
        fn = self.fn
        if fn is None:
            return None
        testo = "".join(("1" if self.stato(i) else "0") if u.e_interruttore else "-" for i, u in enumerate(fn.uscite))
        return testo if any(c != "-" for c in testo) else None

    def imposta_stato(self, indice: int, acceso: bool) -> None:
        """Acceso/spento di un'uscita (inseritore o "Altro" a interruttore): lo salva, lo riporta
        al master e lo applica."""
        fn = self.fn
        if fn is None or not 0 <= indice < len(fn.uscite) or not fn.uscite[indice].e_interruttore:
            return
        with self._lock:
            cambiato = self._stato.get(indice) != acceso
            self._stato[indice] = acceso
            self._salva()
        if cambiato:
            self.cliente.manda_stato_uscita(indice, acceso)
            logger.info(f"Uscita {indice + 1}: {'accesa' if acceso else 'spenta'}")
            u = fn.uscite[indice]
            if u.ruolo == "inseritore" and fn.inseritore_impulso and indice < len(self.relays):
                self._avvia(self.relays[indice].apri, IMPULSO_INSERITORE_SEC)
        self.tick()

    def comando(self, indice: int, azione: str) -> bool:
        """Comando dal pannello (UC): "i" impulso, "1" accendi, "0" spegni. Ritorna True se eseguito."""
        fn = self.fn
        if fn is None or not 0 <= indice < len(fn.uscite):
            logger.warning(f"Comando all'uscita {indice + 1} ignorato: funzionamento non configurato")
            return False
        u = fn.uscite[indice]
        if u.e_impulso_comandabile and azione == "i":
            self.apri([indice])
            return True
        if u.e_interruttore and azione in ("1", "0"):
            self.imposta_stato(indice, azione == "1")
            return True
        logger.warning(f"Comando '{azione}' non valido per l'uscita {indice + 1} ({u.ruolo})")
        return False

    # ---------- livelli mantenuti ----------

    def tick(self) -> None:
        """Allinea le uscite mantenute alla situazione: porta libera sulle uscite "apre", stato
        acceso/spento sugli interruttori, tutto il resto rilasciato. Da chiamare ogni secondo
        circa: decide solo la cache locale, quindi funziona anche a master spento."""
        fn = self.fn
        libera = self.cliente.porta_libera(self.cliente.ora_locale())
        for i, relay in enumerate(self.relays):
            if fn is None:
                voluto = libera if i == 0 else False
            else:
                u = fn.uscite[i] if i < len(fn.uscite) else None
                if u is None:
                    voluto = False
                elif u.ruolo == "apre":
                    voluto = libera
                elif u.e_interruttore and not (u.ruolo == "inseritore" and fn.inseritore_impulso):
                    voluto = self._stato.get(i, False)
                else:
                    voluto = False
            relay.mantieni(voluto)

    # ---------- ingressi ----------

    def ingresso(self, indice: int, attivo: bool) -> None:
        """Un ingresso e' cambiato di stato (letto con il suo contatto NC/NO gia' applicato)."""
        fn = self.fn
        if fn is None or not 0 <= indice < len(fn.ingressi):
            return
        g = fn.ingressi[indice]
        if g.ruolo == "pulsante":
            if attivo:
                logger.info(f"Ingresso {indice + 1} (pulsante/telecomando): apro")
                self.apri_porte()
                self.cliente.manda_ingresso(indice, True)
        elif g.ruolo == "altro":
            if attivo:
                self.cliente.manda_ingresso(indice, True)
            if 0 <= g.azione < len(fn.uscite):
                u = fn.uscite[g.azione]
                if u.e_interruttore:
                    self.imposta_stato(g.azione, attivo)       # l'uscita segue l'ingresso
                elif attivo:
                    self.apri([g.azione])

    # ---------- su disco ----------

    def _salva(self) -> None:
        if not self.percorso:
            return
        cartella = os.path.dirname(self.percorso) or "."
        try:
            os.makedirs(cartella, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=cartella, prefix=".uscite-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump({"stato": {str(k): v for k, v in self._stato.items()}}, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.percorso)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.warning(f"Impossibile salvare lo stato delle uscite su disco: {e}")

    def _carica(self) -> None:
        try:
            with open(self.percorso) as f:
                dato = json.load(f)
            self._stato = {int(k): bool(v) for k, v in dato.get("stato", {}).items() if str(k).isdigit()}
        except FileNotFoundError:
            return
        except (OSError, ValueError, AttributeError) as e:
            logger.warning(f"Stato delle uscite su disco illeggibile, lo ignoro ({e})")

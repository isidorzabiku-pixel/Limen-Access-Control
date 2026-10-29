# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Sensore porta (contatto magnetico sul telaio): distingue un'apertura autorizzata da
una forzata e segnala la porta lasciata aperta troppo a lungo.

Logica pura, senza hardware: riceve le letture del sensore e decide cosa riportare
al master (tramite ClienteUDP.manda_evento_porta, che li consegna con conferma):

  aperta            la porta si e' aperta (apertura autorizzata o porta libera)
  chiusa            la porta si e' richiusa
  forzata           la porta si e' aperta senza che il relè fosse stato azionato
  lasciata_aperta   la porta e' rimasta aperta oltre `max_aperta_sec`

"Autorizzata" = c'e' stata un'apertura del relè (badge o comando dal master) da non
piu' di durata + tolleranza secondi, oppure la porta e' libera (emergenza/sblocco).
"""
from __future__ import annotations
import logging
import time

logger = logging.getLogger("agent.porta")

TOLLERANZA_SEC = 5.0   # margine dopo la fine dell'apertura del relè (la porta si apre un attimo dopo)


class MonitorPorta:
    def __init__(self, cliente, max_aperta_sec: float = 30.0, orologio=time.monotonic, soglia=None, su_allarme=None):
        """`soglia`: funzione senza argomenti che da' i secondi di "lasciata aperta" correnti (il master li
        puo' cambiare dal popup Funzionamento); `su_allarme(tipo)`: chiamata su "forzata"/"lasciata_aperta"
        per suonare la sirena. Senza funzionamento dal master (cliente.fn None) si resta come prima."""
        self.cliente = cliente
        self._max_aperta_sec = max_aperta_sec
        self._soglia = soglia
        self._su_allarme = su_allarme
        self._orologio = orologio
        self._aperta: bool | None = None       # None = ancora nessuna lettura
        self._aperta_dal = 0.0
        self._autorizzata_fino = 0.0
        self._avvisata = False                 # allarme "lasciata aperta" gia' mandato per questa apertura

    @property
    def max_aperta_sec(self) -> float:
        return self._soglia() if self._soglia else self._max_aperta_sec

    def _avvisi(self) -> tuple[bool, bool]:
        """(porta lasciata aperta, porta forzata) attivi: sempre senza funzionamento dal master."""
        fn = getattr(self.cliente, "fn", None)
        return (True, True) if fn is None else (fn.avvisa_aperta, fn.avvisa_forzata)

    def _allarme(self, tipo: str) -> None:
        if self._su_allarme:
            self._su_allarme(tipo)

    def stato_testo(self) -> str | None:
        """Per il battito: "a" aperta, "c" chiusa, None se non c'e' ancora una lettura."""
        return None if self._aperta is None else ("a" if self._aperta else "c")

    def apertura_autorizzata(self, durata_sec: float) -> None:
        """Da chiamare quando il relè viene azionato: la prossima apertura della porta e' legittima."""
        self._autorizzata_fino = max(self._autorizzata_fino, self._orologio() + durata_sec + TOLLERANZA_SEC)

    def _legittima(self, adesso: float) -> bool:
        return adesso <= self._autorizzata_fino or self.cliente.porta_libera(self.cliente.ora_locale())

    def lettura(self, aperta: bool) -> None:
        """Una lettura stabile (gia' filtrata dal rimbalzo) del sensore."""
        adesso = self._orologio()
        if aperta == self._aperta:
            return
        primo = self._aperta is None
        self._aperta = aperta
        if aperta:
            self._aperta_dal = adesso
            self._avvisata = False
            if primo:
                self.cliente.manda_evento_porta("aperta")      # all'avvio la porta era gia' aperta: solo stato
            elif self._legittima(adesso):
                self.cliente.manda_evento_porta("aperta")
            elif not self._avvisi()[1]:
                self.cliente.manda_evento_porta("aperta")      # avviso "porta forzata" spento: solo stato
            else:
                logger.warning("Porta aperta senza autorizzazione: FORZATA")
                self.cliente.manda_evento_porta("forzata")
                self._allarme("forzata")
        else:
            self.cliente.manda_evento_porta("chiusa")

    def tick(self) -> None:
        """Da chiamare periodicamente (anche piu' volte al secondo): porta lasciata aperta."""
        if not self._aperta or self._avvisata:
            return
        adesso = self._orologio()
        if adesso - self._aperta_dal < self.max_aperta_sec:
            return
        if self.cliente.porta_libera(self.cliente.ora_locale()):
            return   # in emergenza/sblocco la porta puo' restare aperta
        self._avvisata = True
        if not self._avvisi()[0]:
            return
        logger.warning(f"Porta aperta da oltre {self.max_aperta_sec:.0f} s: LASCIATA APERTA")
        self.cliente.manda_evento_porta("lasciata_aperta")
        self._allarme("lasciata_aperta")

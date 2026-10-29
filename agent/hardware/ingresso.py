# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Ingresso generico (contatto o pulsante tra il pin e massa, con pull-up interno), con
filtro antirimbalzo. Serve a tutti gli ingressi del varco: sensore porta, pulsante o
telecomando, ingresso "Altro". Cosa significhi lo decide il master (funzione e contatto
NC/NO nel popup Funzionamento): qui si legge solo se l'ingresso e' ATTIVO.

  NC (normalmente chiuso): a riposo il contatto e' chiuso (pin basso), e' attivo quando si apre (pin alto)
  NO (normalmente aperto): a riposo il contatto e' aperto (pin alto), e' attivo quando si chiude (pin basso)
"""
from __future__ import annotations
import logging

logger = logging.getLogger("agent.ingresso")

try:
    from gpiozero import DigitalInputDevice
    _HAS_GPIO = True
except Exception:
    _HAS_GPIO = False


class Ingresso:
    def __init__(self, pin: int, letture_stabili: int = 3):
        self.pin = pin
        self.letture_stabili = max(1, letture_stabili)
        self._stabile = None          # ultimo livello stabile (True = pin alto)
        self._candidato = None
        self._conteggio = 0
        if _HAS_GPIO:
            self._device = DigitalInputDevice(pin, pull_up=True)
        else:
            self._device = None
            logger.warning(f"gpiozero non disponibile: ingresso {pin} in simulazione (a riposo)")

    def _grezza(self, nc: bool) -> bool:
        """Livello istantaneo del pin (True = alto). In simulazione: lo stato di riposo."""
        if self._device is None:
            return not nc          # NC a riposo = basso, NO a riposo = alto
        return bool(self._device.value)

    def livello_alto(self, nc: bool = False) -> bool:
        """Una lettura: ritorna il livello stabile del pin (True = alto)."""
        v = self._grezza(nc)
        if self._stabile is None:
            self._stabile = v
        if v == self._stabile:
            self._candidato, self._conteggio = None, 0
        elif v == self._candidato:
            self._conteggio += 1
            if self._conteggio >= self.letture_stabili:
                self._stabile, self._candidato, self._conteggio = v, None, 0
        else:
            self._candidato, self._conteggio = v, 1
            if self.letture_stabili <= 1:
                self._stabile, self._candidato, self._conteggio = v, None, 0
        return self._stabile

    def attivo(self, nc: bool) -> bool:
        """True se l'ingresso e' attivo (contatto aperto per NC, chiuso per NO)."""
        return self.livello_alto(nc) == nc

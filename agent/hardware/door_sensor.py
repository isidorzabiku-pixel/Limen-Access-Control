# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Sensore porta (contatto magnetico/reed) su un ingresso GPIO, con gpiozero.
`aperta` e' il valore filtrato dal rimbalzo: serve che la lettura resti uguale per
`letture_stabili` campionamenti consecutivi prima di cambiare.
"""
from __future__ import annotations
import logging

logger = logging.getLogger("agent.door")

try:
    from gpiozero import DigitalInputDevice
    _HAS_GPIO = True
except Exception:
    _HAS_GPIO = False


class DoorSensor:
    def __init__(self, pin: int, chiusa_a_livello: str = "low", letture_stabili: int = 3):
        """chiusa_a_livello: livello del pin quando la porta e' CHIUSA ("low" = contatto verso massa chiuso)."""
        self.pin = pin
        self.chiusa_alto = chiusa_a_livello.lower() == "high"
        self.letture_stabili = max(1, letture_stabili)
        self._stabile = None
        self._candidato = None
        self._conteggio = 0
        if _HAS_GPIO:
            self._device = DigitalInputDevice(pin, pull_up=not self.chiusa_alto)
        else:
            self._device = None
            logger.warning("gpiozero non disponibile: sensore porta in simulazione (porta sempre chiusa)")

    def _grezza(self) -> bool:
        """True se la porta risulta aperta, lettura istantanea."""
        if self._device is None:
            return False
        alto = bool(self._device.value)
        return (not alto) if self.chiusa_alto else alto

    def campiona(self) -> bool:
        """Una lettura: ritorna lo stato stabile (True = aperta)."""
        v = self._grezza()
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

# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Driver minimale del relè via GPIO, usando gpiozero (funziona su qualsiasi
Raspberry Pi: Zero 2W, 3, 5... stesso codice, cambia solo la velocità).
"""
from __future__ import annotations
import time
import logging
import threading

logger = logging.getLogger("agent.relay")

try:
    from gpiozero import OutputDevice
    _HAS_GPIO = True
except Exception:
    # Permette di sviluppare/testare il resto dell'agent anche su un PC
    # senza GPIO reale: logga soltanto cosa farebbe.
    _HAS_GPIO = False


class Relay:
    def __init__(self, pin: int, active_high: bool = True):
        # active_high=False per i moduli relè «attivi bassi» (molto comuni): il relè scatta col pin a 0.
        self.pin = pin
        # Due modi di tenere il relè eccitato: apertura a tempo (apri) e "porta libera"
        # (mantieni, per emergenza/sblocco programmato). Il relè si diseccita solo
        # quando nessuno dei due lo richiede.
        self._lock = threading.Lock()
        self._aperture_in_corso = 0
        self._mantenuto = False
        if _HAS_GPIO:
            self._device = OutputDevice(pin, active_high=active_high, initial_value=False)
        else:
            self._device = None
            logger.warning("gpiozero non disponibile: modalità simulazione (nessun GPIO reale)")

    def _aggiorna(self):
        """Da chiamare con il lock preso: eccita se serve, diseccita se nessuno lo richiede."""
        if not self._device:
            return
        if self._aperture_in_corso > 0 or self._mantenuto:
            self._device.on()
        else:
            self._device.off()

    def apri(self, durata_sec: int):
        logger.info(f"Apertura relè su pin {self.pin} per {durata_sec}s")
        with self._lock:
            self._aperture_in_corso += 1
            self._aggiorna()
        try:
            time.sleep(durata_sec)
        finally:
            with self._lock:
                self._aperture_in_corso -= 1
                self._aggiorna()
        logger.info("Relè richiuso" if not self._mantenuto else "Apertura a tempo finita (il relè resta eccitato: porta libera)")

    def mantieni(self, acceso: bool):
        """Porta libera: tiene il relè eccitato finche' non viene richiamato con False."""
        with self._lock:
            if acceso == self._mantenuto:
                return
            self._mantenuto = acceso
            self._aggiorna()
        logger.warning("Porta LIBERA: relè mantenuto eccitato" if acceso else "Fine porta libera: relè rilasciato")

    @property
    def mantenuto(self) -> bool:
        return self._mantenuto

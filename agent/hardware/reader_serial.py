# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Lettura badge da un lettore collegato via seriale/RS485.

NOTA IMPORTANTE: il protocollo esatto (framing, checksum, formato del
pacchetto) dipende dal tuo lettore reale. Qui c'è uno scheletro che apre
la porta e legge righe di testo: va adattato quando sai esattamente
cosa manda il tuo lettore (puoi scoprirlo con uno sniffer seriale o
semplicemente stampando i byte grezzi ricevuti la prima volta).
"""
from __future__ import annotations
import logging

logger = logging.getLogger("agent.reader")


class SerialReader:
    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        import serial   # qui e non in cima: i test senza lettore reale non richiedono pyserial
        self._ser = serial.Serial(port, baudrate, timeout=timeout)
        logger.info(f"Lettore seriale aperto su {port} @ {baudrate}bps")

    def leggi_badge(self) -> str | None:
        """
        Bloccante fino a timeout. Ritorna il codice badge come stringa,
        o None se non è arrivato nulla in questo ciclo.

        Adatta il parsing qui sotto al formato reale del tuo lettore:
        - se manda righe di testo terminate da \\n, va già bene
        - se manda byte grezzi con un framing specifico, sostituisci
          readline() con read(n) e un parser del tuo protocollo
        """
        raw = self._ser.readline()
        if not raw:
            return None
        try:
            codice = raw.decode(errors="ignore").strip()
        except Exception:
            logger.warning(f"Pacchetto non decodificabile: {raw!r}")
            return None
        return codice or None

    def close(self):
        self._ser.close()

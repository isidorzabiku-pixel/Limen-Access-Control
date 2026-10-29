# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Raccoglie informazioni di base sul dispositivo su cui gira l'app, usando
solo la libreria standard (niente psutil o altre dipendenze aggiuntive):
resta leggero, va bene anche su hardware modesto.
"""
import platform
import shutil
import socket
from datetime import datetime
from typing import Optional


def _ip_locale() -> str:
    """IP usato per uscire verso la rete (non serve connettività reale: è solo
    il routing locale a decidere l'interfaccia, nessun pacchetto viene inviato)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "N/D"


def _uptime_leggibile() -> str:
    try:
        with open("/proc/uptime") as f:
            secondi = float(f.read().split()[0])
    except (FileNotFoundError, ValueError, OSError):
        return "N/D"
    giorni, resto = divmod(int(secondi), 86400)
    ore, resto = divmod(resto, 3600)
    minuti, _ = divmod(resto, 60)
    parti = []
    if giorni:
        parti.append(f"{giorni}g")
    if ore or giorni:
        parti.append(f"{ore}h")
    parti.append(f"{minuti}m")
    return " ".join(parti)


def _memoria_mb() -> tuple[Optional[int], Optional[int]]:
    """Ritorna (totale_mb, disponibile_mb) leggendo /proc/meminfo."""
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for riga in f:
                chiave, _, valore = riga.partition(":")
                info[chiave.strip()] = int(valore.strip().split()[0])  # kB
    except (FileNotFoundError, ValueError, OSError, IndexError):
        return None, None
    totale = info.get("MemTotal")
    disponibile = info.get("MemAvailable")
    return (
        round(totale / 1024) if totale else None,
        round(disponibile / 1024) if disponibile else None,
    )


def _temperatura_cpu_c() -> Optional[float]:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except (FileNotFoundError, ValueError, OSError):
        return None


def raccogli_info_sistema() -> dict:
    disco = shutil.disk_usage("/")
    memoria_totale, memoria_disponibile = _memoria_mb()
    return {
        "ora_sistema": datetime.now(),
        "hostname": socket.gethostname(),
        "ip_locale": _ip_locale(),
        "sistema_operativo": platform.platform(),
        "architettura": platform.machine(),
        "versione_python": platform.python_version(),
        "uptime": _uptime_leggibile(),
        "memoria_totale_mb": memoria_totale,
        "memoria_disponibile_mb": memoria_disponibile,
        "disco_totale_gb": round(disco.total / (1024 ** 3), 1),
        "disco_libero_gb": round(disco.free / (1024 ** 3), 1),
        "temperatura_cpu_c": _temperatura_cpu_c(),
    }

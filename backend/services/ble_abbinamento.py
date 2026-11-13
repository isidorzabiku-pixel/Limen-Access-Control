# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Credenziale BLE del telefono: titolo, segreto, inviti (parte pura, nessun database, solo libreria standard).

Schema (12/11): l'impianto ha una chiave dei telefoni, creata dal master al primo uso e mandata agli slave
cifrata dentro la cache. Ogni telefono riceve all'abbinamento un titolo (il codice che sta in cache come un
badge: 23 caratteri base32) e un segreto S = HMAC(chiave dei telefoni, "LIMEN-PHONE" | titolo): diverso per
ogni telefono, e il telefono non conosce la chiave dell'impianto. Per aprire il telefono risponde alla sfida
dello slave con HMAC(S, "LIMEN-OPEN" | sfida): chi ascolta non puo' riusarla.
L'invito e' una chiave: da esso master e telefono derivano K (PBKDF2, lenta apposta); il master la passa
cifrata allo slave del varco scelto, il telefono prova di averla con un HMAC e riceve titolo e segreto
cifrati con K. L'invito non viaggia mai via radio. Stessi calcoli in firmware/esp32/lib/limen/ble.cpp.
"""
import base64
import hashlib
import hmac
import secrets

LUNGHEZZA_TITOLO = 23            # come LUNGHEZZA_CODICE - 1 del firmware
LUNGHEZZA_NONCE = 16
LUNGHEZZA_ETICHETTA = 16
PREFISSO_ABBINAMENTO = b"LIMEN-PAIR"
PREFISSO_APERTURA = b"LIMEN-OPEN"
PREFISSO_TELEFONO = b"LIMEN-PHONE"
SCADENZA_INVITO_SEC = 600
ALFABETO_INVITO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # senza 0/O/1/I/L: si legge e si digita senza errori
LUNGHEZZA_INVITO = 10
SALE_INVITO = b"LIMEN-INVITE-v1"
ITERAZIONI_INVITO = 20000        # uguale nell'app: rende impraticabile provare tutti gli inviti in 10 minuti


def _hmac(chiave: bytes, dati: bytes) -> bytes:
    return hmac.new(chiave, dati, hashlib.sha256).digest()


# ---------- Chiave dei telefoni, titolo e segreto ----------

def nuova_chiave_telefoni() -> bytes:
    return secrets.token_bytes(32)


def nuovo_titolo() -> str:
    """Codice di cache di un telefono: 23 caratteri base32 casuali (A-Z, 2-7). Non e' un segreto."""
    return base64.b32encode(secrets.token_bytes(15)).decode()[:LUNGHEZZA_TITOLO]


def segreto_telefono(chiave_telefoni: bytes, titolo: str) -> bytes:
    return _hmac(chiave_telefoni, PREFISSO_TELEFONO + titolo.encode())


def mac_apertura(segreto: bytes, sfida: bytes) -> bytes:
    return _hmac(segreto, PREFISSO_APERTURA + sfida)


def cifra_chiave_telefoni(chiave_telefoni: bytes, api_key: str, varco_id: int, versione: int) -> str:
    """Campo "kt" della cache: chiave XOR HMAC(chiave del varco, "PHONE-KEY|id|versione"). La versione non si ripete."""
    pad = _hmac(api_key.encode(), f"PHONE-KEY|{varco_id}|{versione}".encode())
    return bytes(a ^ b for a, b in zip(chiave_telefoni, pad)).hex()


# ---------- Invito ----------

def nuovo_invito() -> str:
    return "".join(secrets.choice(ALFABETO_INVITO) for _ in range(LUNGHEZZA_INVITO))


def normalizza_invito(codice: str) -> str:
    return "".join(c for c in (codice or "").upper() if c.isalnum())


def deriva_chiave(codice: str) -> bytes:
    """K = PBKDF2-HMAC-SHA256(invito normalizzato, sale fisso, 20000 iterazioni), 32 byte."""
    return hashlib.pbkdf2_hmac("sha256", normalizza_invito(codice).encode(), SALE_INVITO, ITERAZIONI_INVITO, 32)


def id_invito(chiave: bytes) -> str:
    """Identificativo pubblico dell'invito nei pacchetti (non rivela la chiave)."""
    return hashlib.sha256(b"LIMEN-KID" + chiave).hexdigest()[:16]


def prova_telefono(chiave: bytes, sfida: bytes, nonce_tel: bytes) -> bytes:
    return _hmac(chiave, PREFISSO_ABBINAMENTO + sfida + nonce_tel)


def cifra_per_varco(chiave: bytes, api_key: str, varco_id: int, ts: int) -> str:
    """K cifrata per il pacchetto BP: XOR con HMAC(chiave del varco, "BP-KEY|id|ts"). Il ts e' quello del pacchetto."""
    pad = _hmac(api_key.encode(), f"BP-KEY|{varco_id}|{ts}".encode())
    return bytes(a ^ b for a, b in zip(chiave, pad)).hex()


# ---------- Credenziale cifrata per il telefono (la cifra lo slave; qui serve ai test e come riferimento per l'app) ----------

def _flusso(k: bytes, sfida: bytes, nonce_tel: bytes, n: int) -> bytes:
    out = b""
    blocco = 0
    while len(out) < n:
        out += _hmac(k, b"LIMEN-CRED" + sfida + nonce_tel + bytes([blocco]))
        blocco += 1
    return out[:n]


def cifra_credenziale(k: bytes, sfida: bytes, nonce_tel: bytes, titolo: str, segreto: bytes) -> bytes:
    chiaro = titolo.encode() + segreto
    cifrato = bytes(a ^ b for a, b in zip(chiaro, _flusso(k, sfida, nonce_tel, len(chiaro))))
    return cifrato + _hmac(k, b"LIMEN-CTAG" + sfida + nonce_tel + cifrato)[:LUNGHEZZA_ETICHETTA]


def decifra_credenziale(k: bytes, sfida: bytes, nonce_tel: bytes, dati: bytes) -> tuple[str, bytes] | None:
    """(titolo, segreto) oppure None se l'etichetta non torna (varco finto o invito sbagliato)."""
    if len(dati) != LUNGHEZZA_TITOLO + 32 + LUNGHEZZA_ETICHETTA:
        return None
    cifrato, etichetta = dati[:-LUNGHEZZA_ETICHETTA], dati[-LUNGHEZZA_ETICHETTA:]
    if not hmac.compare_digest(_hmac(k, b"LIMEN-CTAG" + sfida + nonce_tel + cifrato)[:LUNGHEZZA_ETICHETTA], etichetta):
        return None
    chiaro = bytes(a ^ b for a, b in zip(cifrato, _flusso(k, sfida, nonce_tel, len(cifrato))))
    return chiaro[:LUNGHEZZA_TITOLO].decode(), chiaro[LUNGHEZZA_TITOLO:]

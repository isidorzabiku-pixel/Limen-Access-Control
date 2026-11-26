#!/usr/bin/env python3
# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""Tessera MIFARE DESFire simulata (solo i comandi che usa Limen), scritta a parte rispetto al firmware e con
un'altra libreria crittografica (pyca/cryptography), per provare lib/limen/desfire.* sul PC:

    python3 test/sim_desfire.py /tmp/td

Comandi nativi in APDU ISO (90 cmd 00 00 Lc dati 00), risposta dati + 91 stato:
  5A SelectApplication, CA CreateApplication, DA DeleteApplication, AA/AF AuthenticateAES (EV1), C4 ChangeKey (AES, EV1).
Comportamento come da documentazione pubblica DESFire EV1 (libfreefare, note NXP); non sostituisce la prova con
una tessera vera.
"""
import os
import subprocess
import sys
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

OK, AF, NO_APP, AUTH_ERR, PERM, DUP, INTEGRITY, NO_KEY, LEN_ERR = 0x00, 0xAF, 0xA0, 0xAE, 0x9D, 0xDE, 0x1E, 0x40, 0x7E


def crc32_desfire(b: bytes) -> bytes:
    return ((~zlib.crc32(b)) & 0xFFFFFFFF).to_bytes(4, "little")


def cbc(key, iv, data, enc):
    c = Cipher(algorithms.AES(key), modes.CBC(iv))
    o = c.encryptor() if enc else c.decryptor()
    return o.update(data) + o.finalize()


class Tessera:
    def __init__(self, uid: bytes, picc_bloccata=False):
        self.uid = uid
        self.picc_bloccata = picc_bloccata            # tessera di un altro sistema: crea app solo con la loro chiave
        self.apps = {}                                 # aid -> lista chiavi AES
        self.sel = b"\x00\x00\x00"
        self.auth = None                               # (n.chiave, chiave di sessione)
        self.attesa = None                             # (n.chiave, rndB, iv) tra AA e AF

    def apdu(self, a: bytes) -> bytes:
        if len(a) < 5 or a[0] != 0x90:
            return bytes([0x6E, 0x00])
        cmd = a[1]
        dati = a[5:5 + a[4]] if len(a) > 5 else b""
        try:
            r, s = getattr(self, f"c_{cmd:02X}", self.sconosciuto)(dati)
        except Exception as e:                          # noqa: BLE001
            print("sim: eccezione", e, file=sys.stderr)
            r, s = b"", 0x9E
        if cmd != 0xAF:
            pass
        return r + bytes([0x91, s])

    def sconosciuto(self, d):
        return b"", 0x1C

    def c_5A(self, d):                                 # SelectApplication
        self.auth = None
        self.attesa = None
        if d == b"\x00\x00\x00" or d in self.apps:
            self.sel = d
            return b"", OK
        return b"", NO_APP

    def c_CA(self, d):                                 # CreateApplication (PICC selezionato)
        if self.sel != b"\x00\x00\x00":
            return b"", PERM
        if self.picc_bloccata:
            return b"", AUTH_ERR
        aid, ks1, ks2 = d[0:3], d[3], d[4]
        if aid in self.apps:
            return b"", DUP
        assert ks2 & 0x80, "attese chiavi AES"
        self.apps[aid] = [bytes(16)] * (ks2 & 0x0F)
        return b"", OK

    def c_DA(self, d):                                 # DeleteApplication
        aid = d[0:3]
        if aid not in self.apps:
            return b"", NO_APP
        # Qui solo il caso che usa Limen: applicazione selezionata e autenticazione con la sua chiave di gestione
        # (permesso dalle impostazioni di fabbrica della carta: crea/cancella applicazioni senza la chiave master).
        if not (self.sel == aid and self.auth is not None and self.auth[0] == 0 and not self.picc_bloccata):
            return b"", AUTH_ERR
        del self.apps[aid]
        self.sel = b"\x00\x00\x00"
        self.auth = None
        return os.urandom(8), OK

    def c_AA(self, d):                                 # Authenticate AES, passo 1
        self.auth = None
        n = d[0]
        chiavi = self.apps.get(self.sel)
        if chiavi is None or n >= len(chiavi):
            return b"", NO_KEY
        rndb = os.urandom(16)
        ek = cbc(chiavi[n], bytes(16), rndb, True)
        self.attesa = (n, rndb, ek)
        return ek, AF

    def c_AF(self, d):                                 # Authenticate AES, passo 2
        if self.attesa is None or len(d) != 32:
            return b"", 0x91 if False else 0xCA       # comando fuori sequenza
        n, rndb, iv = self.attesa
        self.attesa = None
        chiave = self.apps[self.sel][n]
        chiaro = cbc(chiave, iv, d, False)
        rnda, rndb_r = chiaro[:16], chiaro[16:]
        if rndb_r != rndb[1:] + rndb[:1]:
            return b"", AUTH_ERR
        risposta = cbc(chiave, d[-16:], rnda[1:] + rnda[:1], True)
        sessione = rnda[0:4] + rndb[0:4] + rnda[12:16] + rndb[12:16]
        self.auth = (n, sessione)
        return risposta, OK

    def c_C4(self, d):                                 # ChangeKey AES
        if self.auth is None:
            return b"", AUTH_ERR
        n_auth, sess = self.auth
        n = d[0]
        crit = d[1:]
        if len(crit) != 32:
            return b"", LEN_ERR
        chiaro = cbc(sess, bytes(16), crit, False)
        chiavi = self.apps[self.sel]
        if n == n_auth:
            nuova, ver, crc = chiaro[:16], chiaro[16], chiaro[17:21]
            if crc != crc32_desfire(bytes([0xC4, n]) + nuova + bytes([ver])):
                return b"", INTEGRITY
            chiavi[n] = nuova
            self.auth = None                           # cambiata la chiave della sessione: si deve riautenticare
            return b"", OK
        x, ver, crc1, crc2 = chiaro[:16], chiaro[16], chiaro[17:21], chiaro[21:25]
        if crc1 != crc32_desfire(bytes([0xC4, n]) + x + bytes([ver])):
            return b"", INTEGRITY
        nuova = bytes(a ^ b for a, b in zip(x, chiavi[n]))
        if crc2 != crc32_desfire(nuova):
            return b"", INTEGRITY                       # vecchia chiave sbagliata
        chiavi[n] = nuova
        return os.urandom(8), OK                       # CMAC in risposta (non verificato dal firmware)


def esegui(prog, modo, chiave, tessera, interrompi_dopo=None):
    p = subprocess.Popen([prog, "proc", modo, chiave, tessera.uid.hex()], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True)
    n = 0
    while True:
        riga = p.stdout.readline().strip()
        if riga.startswith("ESITO"):
            p.wait()
            return riga.split(" ", 2)[1]
        n += 1
        if interrompi_dopo is not None and n > interrompi_dopo:
            p.kill()
            return "interrotta"
        r = tessera.apdu(bytes.fromhex(riga))
        p.stdin.write(r.hex() + "\n")
        p.stdin.flush()


def main():
    prog = sys.argv[1] if len(sys.argv) > 1 else "/tmp/td"
    K = os.urandom(32).hex()
    ALTRO = os.urandom(32).hex()
    casi = 0

    def atteso(ottenuto, voluto, cosa):
        nonlocal casi
        casi += 1
        stato = "ok" if ottenuto == voluto else "SBAGLIATO"
        print(f"  {stato:9} {cosa}: {ottenuto}")
        if ottenuto != voluto:
            sys.exit(1)

    t = Tessera(bytes.fromhex("04A1B2C3D4E5F6"))
    atteso(esegui(prog, "lettura", K, t), "assente", "tessera vergine al varco")
    atteso(esegui(prog, "scrittura", K, t), "scritta", "scrittura tessera vergine")
    atteso(t.apps[b"\x4C\x4D\x01"][0] != bytes(16) and t.apps[b"\x4C\x4D\x01"][1] != bytes(16), True, "chiavi cambiate")
    atteso(esegui(prog, "lettura", K, t), "valida", "lettura tessera scritta")
    atteso(esegui(prog, "lettura", ALTRO, t), "nonValida", "lettura con la chiave di un altro impianto")
    atteso(esegui(prog, "scrittura", K, t), "giaNostra", "riscrittura della stessa tessera")
    atteso(esegui(prog, "scrittura", ALTRO, t), "altroSistema", "scrittura da un altro impianto")

    copia = Tessera(bytes.fromhex("04A1B2C3D4E5F7"))  # stesso contenuto, UID diverso (clone con altro UID)
    copia.apps = {k: list(v) for k, v in t.apps.items()}
    atteso(esegui(prog, "lettura", K, copia), "nonValida", "copia delle chiavi su un altro UID")

    bloccata = Tessera(bytes.fromhex("04000000000001"), picc_bloccata=True)
    atteso(esegui(prog, "scrittura", K, bloccata), "altroSistema", "tessera bloccata da un altro sistema")

    # Scritture interrotte a ogni passo, poi ripresa: deve finire scritta e leggibile
    for stop in range(1, 12):
        m = Tessera(os.urandom(7))
        esegui(prog, "scrittura", K, m, interrompi_dopo=stop)
        r = esegui(prog, "scrittura", K, m)
        atteso(r in ("scritta", "giaNostra"), True, f"interrotta dopo {stop} comandi e ripresa ({r})")
        chiavi = m.apps.get(b"\x4C\x4D\x01", [bytes(16)] * 2)
        atteso(bytes(16) not in chiavi, True, f"  nessuna chiave di fabbrica rimasta (interrotta dopo {stop})")
        atteso(esegui(prog, "lettura", K, m), "valida", f"  poi letta (interrotta dopo {stop})")
    # Pulizia
    atteso(esegui(prog, "pulizia", K, Tessera(os.urandom(7))), "assente", "pulizia di una tessera vergine")
    atteso(esegui(prog, "pulizia", ALTRO, t), "altroSistema", "pulizia da un altro impianto: rifiutata")
    atteso(esegui(prog, "lettura", K, t), "valida", "  la tessera funziona ancora")
    atteso(esegui(prog, "pulizia", K, t), "pulita", "pulizia della tessera scritta")
    atteso(t.apps == {}, True, "  applicazione Limen cancellata")
    atteso(esegui(prog, "lettura", K, t), "assente", "  al varco non e' piu' una tessera Limen")
    atteso(esegui(prog, "scrittura", K, t), "scritta", "  si puo' riscrivere")
    for stop in (5, 7):                               # scrittura interrotta (chiave di gestione ancora di fabbrica o no)
        m = Tessera(os.urandom(7))
        esegui(prog, "scrittura", K, m, interrompi_dopo=stop)
        atteso(esegui(prog, "pulizia", K, m), "pulita", f"pulizia dopo una scrittura interrotta al comando {stop}")
    print(f"sim_desfire: {casi} casi ok")


if __name__ == "__main__":
    main()

# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Prova del master per tessere DESFire, chiave d'impianto e backup a due file, su un database temporaneo
(serve l'ambiente del backend: SQLAlchemy ecc.). Da backend/:

    python3 tools/test_desfire_master.py

Non apre porte e non manda pacchetti: l'invio UDP e' sostituito.
"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import zipfile

CARTELLA = tempfile.mkdtemp(prefix="limen-test-")
os.environ["LIMEN_DATA"] = CARTELLA
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Base, SessionLocal, engine  # noqa: E402
import models  # noqa: E402,F401
from models import AccessMedia, Persona, TipoMedia, Varco  # noqa: E402
from services import backup, chiavi_impianto, credenziali, desfire, udp_agent  # noqa: E402

Base.metadata.create_all(bind=engine)
inviati = []
udp_agent._invia_firmato = lambda varco, tipo, *campi: inviati.append((varco.id, tipo, campi)) or True

casi = 0


def ok(cond, cosa):
    global casi
    casi += 1
    print(("  ok        " if cond else "  SBAGLIATO ") + cosa)
    if not cond:
        sys.exit(1)


db = SessionLocal()
chiavi_impianto.assicura(db)
k1 = chiavi_impianto.chiave(db)
ok(len(k1) == 32, "chiave d'impianto creata al primo avvio")
chiavi_impianto.assicura(db)
ok(chiavi_impianto.chiave(db) == k1, "assicura non la cambia")

p = Persona(nome="Marta", cognome="Bianchi")
q = Persona(nome="Luca", cognome="Ferrari")
v = Varco(nome="Banco ufficio", api_key="k" * 32, ip_address="192.168.1.50")
db.add_all([p, q, v])
db.commit()

ok(desfire.prossimo_numero(db) == "000001", "primo numero proposto 000001")
ok(desfire.normalizza_numero(" 000 123 ") == "000123" and desfire.normalizza_numero("12a") == "", "N° tessera normalizzato")

# Scrittura riuscita
tok = desfire.avvia(db, p, v, "000123")
ok(inviati[-1][1] == "DW" and inviati[-1][2] == (tok, desfire.SECONDI_FINESTRA), "DW mandato al varco")
ok(desfire.stato(tok)["stato"] == "attesa", "sessione in attesa")
r = desfire.esito_ricevuto(db, v, [tok, "scritta", "04a1b2c3d4e5f6", ""])
ok(r == tok, "DR accettato")
m = db.query(AccessMedia).filter(AccessMedia.tipo == TipoMedia.desfire).one()
ok(m.codice == "DF-04A1B2C3D4E5F6" and m.numero == "000123" and m.uid == "04A1B2C3D4E5F6" and m.persona_id == p.id, "credenziale DESFire registrata")
ok(desfire.stato(tok)["stato"] == "fatto", "sessione chiusa con successo")
ok(desfire.esito_ricevuto(db, v, [tok, "scritta", "04A1B2C3D4E5F6", ""]) == tok, "DR ripetuto: nessun doppione")
ok(db.query(AccessMedia).filter(AccessMedia.tipo == TipoMedia.desfire).count() == 1, "  una sola credenziale")

# Ricerca per numero senza zeri iniziali
ok(desfire.ids_persone_con_numero(db, "123") == {p.id}, "ricerca «123» trova la 000123")
ok(desfire.ids_persone_con_numero(db, "0000000123") == {p.id}, "ricerca con 10 zeri davanti")
ok(desfire.ids_persone_con_numero(db, "12") == set(), "ricerca «12» non la trova")
ok(desfire.prossimo_numero(db) == "000124", "numero successivo 000124")
ok(desfire.numero_da_codice("DF-04A1B2C3D4E5F6") == "000123", "N° tessera da codice (Monitoraggio)")
ok(credenziali.codice_riservato("DF-04A1B2C3D4E5F6") and not credenziali.codice_riservato("04A1B2C3"), "codici DF- riservati")

# Stessa tessera a un'altra persona: rifiutata
tok2 = desfire.avvia(db, q, v, "000200")
desfire.esito_ricevuto(db, v, [tok2, "gia", "04A1B2C3D4E5F6", ""])
s2 = desfire.stato(tok2)
ok(s2["stato"] == "errore" and "000123" in s2["messaggio"], "tessera gia' di un'altra persona: errore con N° e nome")

# Numero gia' usato
tok3 = desfire.avvia(db, q, v, "123")
desfire.esito_ricevuto(db, v, [tok3, "scritta", "04000000000002", ""])
ok(desfire.stato(tok3)["stato"] == "errore", "N° 123 = 000123 gia' usato: errore")

# Esiti negativi dal lettore
for esito in ("altro", "nondesfire", "tolta", "errore"):
    t4 = desfire.avvia(db, q, v, "000300")
    desfire.esito_ricevuto(db, v, [t4, esito, "", "motivo"])
    ok(desfire.stato(t4)["stato"] == "errore", f"esito «{esito}» mostrato come errore")

# Pacchetto da un altro varco: ignorato
v2 = Varco(nome="Altro", api_key="z" * 32, ip_address="192.168.1.51")
db.add(v2)
db.commit()
t5 = desfire.avvia(db, q, v, "000400")
ok(desfire.esito_ricevuto(db, v2, [t5, "scritta", "04000000000005", ""]) is None, "DR da un altro varco ignorato")

# Riscrittura (tessera persa): stessa credenziale, UID nuovo
t6 = desfire.avvia(db, p, v, "000123", sostituisce=m)
desfire.esito_ricevuto(db, v, [t6, "scritta", "04BBBBBBBBBBBB", ""])
db.refresh(m)
ok(m.uid == "04BBBBBBBBBBBB" and m.codice == "DF-04BBBBBBBBBBBB" and m.numero == "000123", "riscrittura: stesso N°, UID nuovo")

# Rigenera: telefoni e DESFire eliminati
db.add(AccessMedia(codice="ABCDEFGHIJKLMNOPQRSTUVW", tipo=TipoMedia.ble, persona_id=q.id))
db.add(AccessMedia(codice="0011AABB", tipo=TipoMedia.rfid, persona_id=q.id))
db.commit()
info = chiavi_impianto.info(db)
ok(info["telefoni"] == 1 and info["tessere"] == 1, "conteggi per la conferma di Rigenera")
nt, nd = chiavi_impianto.rigenera(db)
ok((nt, nd) == (1, 1) and chiavi_impianto.chiave(db) != k1, "rigenera: chiave nuova, 1 telefono e 1 tessera tolti")
ok(db.query(AccessMedia).filter(AccessMedia.codice == "0011AABB").count() == 1, "  il badge semplice resta")

# Pulisci tessera: tessera assegnata (identifica -> conferma -> pulisce)
db.add(AccessMedia(codice="DF-04CC0000000001", tipo=TipoMedia.desfire, persona_id=q.id, numero="000500", uid="04CC0000000001"))
db.commit()
n0 = len(inviati)
tp = desfire.avvia_pulizia(db, v)
ok(inviati[n0][1] == "DC" and inviati[n0][2][0] == tp and inviati[n0][2][-1] == "i", "DC (identifica) mandato al varco")
ok(desfire.esito_ricevuto(db, v, [tp + desfire.SUFFISSO_PULIZIA, "pulita", "04CC0000000001", ""]) == tp + desfire.SUFFISSO_PULIZIA
   and desfire.stato(tp)["stato"] == "attesa", "esito di pulizia fuori fase ignorato")
desfire.esito_ricevuto(db, v, [tp, "nostra", "04CC0000000001", ""])
ok(desfire.stato(tp)["stato"] == "conferma" and "000500" in desfire.stato(tp)["messaggio"], "tessera assegnata: chiede conferma con N° e nome")
desfire.conferma_pulizia(tp, db)
ok(inviati[-1][1] == "DC" and inviati[-1][2][0] == tp + desfire.SUFFISSO_PULIZIA and inviati[-1][2][-1] == "c", "DC (pulisci) dopo la conferma")
desfire.esito_ricevuto(db, v, [tp + desfire.SUFFISSO_PULIZIA, "pulita", "04CC0000000001", ""])
ok(desfire.stato(tp)["stato"] == "fatto" and db.query(AccessMedia).filter(AccessMedia.codice == "DF-04CC0000000001").count() == 0,
   "tessera pulita: titolo eliminato")

# Tessera di nessuno: si pulisce subito, senza conferma
tp2 = desfire.avvia_pulizia(db, v)
desfire.esito_ricevuto(db, v, [tp2, "nostra", "04DD0000000002", ""])
ok(inviati[-1][1] == "DC" and inviati[-1][2][-1] == "c" and desfire.stato(tp2)["stato"] == "attesa", "tessera di nessuno: pulizia diretta")
desfire.esito_ricevuto(db, v, [tp2 + desfire.SUFFISSO_PULIZIA, "pulita", "04DD0000000002", ""])
ok(desfire.stato(tp2)["stato"] == "fatto", "  pulita")

# Tessera non di questo impianto: errore, nessuna modifica
tp3 = desfire.avvia_pulizia(db, v)
desfire.esito_ricevuto(db, v, [tp3, "nonnostra", "04EE0000000003", ""])
ok(desfire.stato(tp3)["stato"] == "errore", "tessera di un altro impianto: errore")

# Backup a due file e controllo del ripristino
nome, z = backup.crea_backup_dati()
kn, kb = backup.crea_file_chiavi(db)
ok(nome.endswith(".zip") and kn.endswith(".limenkey"), "nomi dei due file")
raw = zipfile.ZipFile(io.BytesIO(z)).read("access_control.db")
ok(chiavi_impianto.chiave(db).hex().encode() not in raw and b"k" * 32 not in raw, "nessun segreto nel file dei dati")
r = backup.controlla(z, kb)
ok(r["ok"], "ripristino: file giusti accettati")
con = sqlite3.connect(r["db"])
chiave_db = dict(con.execute("SELECT chiave, valore FROM impostazioni").fetchall())["chiave_telefoni"]
api = dict(con.execute("SELECT id, api_key FROM varchi").fetchall())
con.close()
ok(chiave_db == chiavi_impianto.chiave(db).hex() and api[v.id] == "k" * 32, "  chiavi rimesse nel database da ripristinare")
os.remove(r["db"])
ok(not backup.controlla(z, None)["ok"], "ripristino: senza file chiavi rifiutato")
altro = json.loads(kb)
altro["impianto_id"] = "altro"
ok(not backup.controlla(z, json.dumps(altro).encode())["ok"], "ripristino: impianto diverso rifiutato")

db.close()
print(f"test_desfire_master: {casi} casi ok (cartella {CARTELLA})")

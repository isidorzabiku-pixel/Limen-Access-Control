# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Cambia la password di un utente esistente della webapp.
Uso: python change_admin_password.py <username> <nuova-password>

Usalo subito se pensi che una password possa essere stata esposta (es. hash
finito in un vecchio commit git, backup non protetto): l'hash bcrypt non si
"apre" all'istante, ma se la password è debole o riusata altrove è comunque
da considerare compromessa.
"""
import sys
from database import SessionLocal
import models
from auth import hash_password


def main():
    if len(sys.argv) != 3:
        print("Uso: python change_admin_password.py <username> <nuova-password>")
        sys.exit(1)

    username, nuova_password = sys.argv[1], sys.argv[2]
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.username == username).first()
        if not user:
            print(f"Nessun utente '{username}' trovato.")
            sys.exit(1)
        user.password_hash = hash_password(nuova_password)
        db.commit()
        print(f"Password di '{username}' aggiornata.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

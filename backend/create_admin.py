# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Crea il primo utente amministratore della webapp.
Uso: python create_admin.py <username> <password>
"""
import sys
from database import Base, engine, SessionLocal
import models
from auth import hash_password

Base.metadata.create_all(bind=engine)


def main():
    if len(sys.argv) != 3:
        print("Uso: python create_admin.py <username> <password>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]
    db = SessionLocal()
    try:
        existing = db.query(models.User).filter(models.User.username == username).first()
        if existing:
            print(f"L'utente '{username}' esiste già.")
            return
        user = models.User(username=username, password_hash=hash_password(password), is_admin=True)
        db.add(user)
        db.commit()
        print(f"Utente admin '{username}' creato con successo.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

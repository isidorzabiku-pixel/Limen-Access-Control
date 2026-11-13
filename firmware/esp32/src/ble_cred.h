// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Lettore BLE per i telefoni: sfida (nonce) + HMAC. Vedi docs/ARCHITECTURE.md e lib/limen/ble.h.
// Ogni telefono ha un titolo (23 caratteri, in cache come un badge) e un segreto S ricevuti all'abbinamento.
// Un telefono alla volta. Il telefono legge la sfida (16 byte), scrive un comando e legge l'esito:
//   'O' + titolo (23) + HMAC-SHA256(S, "LIMEN-OPEN" + sfida) (32)                 apertura
//   'P' + nonce del telefono (16) + HMAC-SHA256(K, "LIMEN-PAIR" + sfida + nonce) (32)   abbinamento, solo con la finestra
//        aperta dal master. K = PBKDF2-HMAC-SHA256(invito, "LIMEN-INVITE-v1", 20000 iterazioni, 32 byte): l'invito non viaggia mai.
// Esiti: "OK <esito>", "NO" o "NO <esito>", "IGNORED", "ERR challenge", "PAIR SENT" poi "PAIR NO" oppure
//   "PAIR OK <esadecimale>": (titolo | S) XOR flusso, piu' etichetta di 16 byte.
//        flusso = HMAC(K, "LIMEN-CRED" + sfida + nonce + i), i = 0, 1; etichetta = HMAC(K, "LIMEN-CTAG" + sfida + nonce + cifrato)[:16].
//        Il telefono controlla l'etichetta (solo il varco vero, che ha K, la sa calcolare), poi conserva titolo e S.
// Un telefono non abbinato riceve solo "NO": non genera eventi ne' pacchetti verso il master. Dopo 5 comandi respinti
// in 30 s il lettore tace per 30 s; una connessione dura al massimo 20 s.
#pragma once
#ifdef LIMEN_BLE_CRED
#include <Arduino.h>

namespace gk { class Slave; }

void bleCredAvvia();
void bleCredLoop(gk::Slave& slave, int lettore);   // da chiamare in loop()
#endif

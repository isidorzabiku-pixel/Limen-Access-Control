// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Lettore NFC PN532 in HSU (UART) su Serial1: RX GPIO16 <- TX del PN532 (SDA), TX GPIO17 -> RX del PN532 (SCL),
// 115200 8N1, interruttori del modulo entrambi su OFF. Cerca tessere ISO 14443A circa 10 volte al secondo senza
// bloccare il loop. Telefono Android con l'app Limen: credenziale del telefono via APDU (lib/limen/nfc_telefono.h),
// stessa verifica del BLE. Tessera DESFire scritta da questo impianto: autenticazione AES (lib/limen/desfire.h),
// codice «DF-<UID>». Ogni altra tessera (MIFARE Classic, DESFire vergini o di altri sistemi): UID.
// Con una finestra aperta dal master la prima DESFire appoggiata viene scritta, riconosciuta o pulita invece che letta.
// Se il PN532 non risponde riprova da solo ogni 2 s.
#pragma once
#include <Arduino.h>
#include <string>

constexpr int NFC_RX = 16, NFC_TX = 17;

struct GestoriNfc {
  // UID in esadecimale maiuscolo (es. «04A1B2C3D4E5F6»).
  void (*uid)(const String& uid);
  // Telefono: titolo + HMAC sulla sfida del varco. Ritorna 1 concesso, 0 negato, -1 credenziale non valida (silenzio).
  int (*telefono)(const std::string& titolo, const uint8_t sfida[16], const uint8_t mac[32]);
  // Chiave d'impianto (32 byte, "" se non ancora arrivata con la cache): serve per leggere e scrivere le DESFire.
  const std::string& (*chiaveImpianto)();
  // Finestra DESFire aperta dal master: 'w' scrivere, 'i' riconoscere, 'c' pulire, 0 = nessuna (Slave::modoDesfire);
  // e dove riportare l'esito (vedi Slave::esitoDesfire).
  char (*finestra)();
  void (*scritto)(const std::string& esito, const std::string& uid, const std::string& motivo);
};

void nfcAvvia();
// Da chiamare in loop(). La stessa credenziale tenuta sul lettore conta una volta sola; va tolta per almeno 1 s
// prima di contare di nuovo.
void nfcLoop(const GestoriNfc& gestori);
// «PN532 1.6» quando risponde, altrimenti «not found».
String nfcStato();

// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Prova dei frame PN532 (lib/limen/pn532.*). Compila: g++ -std=c++17 -I../lib/limen test_pn532.cpp ../lib/limen/pn532.cpp
#include <cassert>
#include <cstdio>
#include <cstring>
#include "ble.h"
#include "nfc_telefono.h"
#include "pn532.h"

using namespace gk::pn532;

static Pezzo dai(Ricevitore& r, const std::vector<uint8_t>& b) {
  Pezzo ultimo = Pezzo::niente;
  for (uint8_t x : b) { Pezzo p = r.aggiungi(x); if (p != Pezzo::niente) ultimo = p; }
  return ultimo;
}

int main() {
  // Frame di GetFirmwareVersion come nel manuale: 00 00 FF 02 FE D4 02 2A 00
  assert((comandoCampo(false) == std::vector<uint8_t>{0x00, 0x00, 0xFF, 0x04, 0xFC, 0xD4, 0x32, 0x01, 0x00, 0xF9, 0x00}));
  assert((comandoCampo(true) == std::vector<uint8_t>{0x00, 0x00, 0xFF, 0x04, 0xFC, 0xD4, 0x32, 0x01, 0x01, 0xF8, 0x00}));
  assert((comandoFirmware() == std::vector<uint8_t>{0x00, 0x00, 0xFF, 0x02, 0xFE, 0xD4, 0x02, 0x2A, 0x00}));
  // SAMConfiguration: 00 00 FF 05 FB D4 14 01 14 01 02 00
  assert((comandoSam() == std::vector<uint8_t>{0x00, 0x00, 0xFF, 0x05, 0xFB, 0xD4, 0x14, 0x01, 0x14, 0x01, 0x02, 0x00}));

  Ricevitore r;
  assert(dai(r, {0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00}) == Pezzo::ack);
  assert(dai(r, {0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00}) == Pezzo::nack);

  // Firmware 1.6 (risposta reale tipica): 00 00 FF 06 FA D5 03 32 01 06 07 E8 00, con rumore davanti
  assert(dai(r, {0x55, 0x13, 0x00, 0x00, 0xFF, 0x06, 0xFA, 0xD5, 0x03, 0x32, 0x01, 0x06, 0x07, 0xE8, 0x00}) == Pezzo::risposta);
  assert(versioneDaRisposta(r.risposta()) == "PN532 1.6");

  // Checksum sbagliato
  assert(dai(r, {0x00, 0x00, 0xFF, 0x06, 0xFA, 0xD5, 0x03, 0x32, 0x01, 0x06, 0x07, 0xE9, 0x00}) == Pezzo::errore);

  // Tessera MIFARE Classic con UID a 4 byte DE AD BE EF
  std::vector<uint8_t> d = {0xD5, 0x4B, 0x01, 0x01, 0x00, 0x04, 0x08, 0x04, 0xDE, 0xAD, 0xBE, 0xEF};
  uint8_t s = 0; for (uint8_t x : d) s = uint8_t(s + x);
  std::vector<uint8_t> f = {0x00, 0x00, 0xFF, uint8_t(d.size()), uint8_t(0x100 - d.size())};
  f.insert(f.end(), d.begin(), d.end()); f.push_back(uint8_t(0x100 - s)); f.push_back(0x00);
  assert(dai(r, f) == Pezzo::risposta);
  bool cas = true;
  assert(uidDaRisposta(r.risposta(), &cas) == "DEADBEEF" && !cas);

  // DESFire con UID a 7 byte (piu' ATS in coda, ignorato)
  std::vector<uint8_t> r7 = {0x4B, 0x01, 0x01, 0x03, 0x44, 0x20, 0x07, 0x04, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x06, 0x75, 0x77, 0x81, 0x02, 0x80};
  assert(uidDaRisposta(r7, &cas) == "04112233445566" && !cas);

  // Nessuna tessera, UID casuale, risposte troncate
  assert(uidDaRisposta({0x4B, 0x00}) == "");
  assert(uidDaRisposta({0x4B, 0x01, 0x01, 0x00, 0x04, 0x20, 0x04, 0x08, 0x12, 0x34, 0x56}, &cas) == "08123456" && cas);
  assert(uidDaRisposta({0x4B, 0x01, 0x01, 0x00, 0x04, 0x08, 0x07, 0x04}) == "");
  assert(versioneDaRisposta({0x03, 0x31, 0x01, 0x06}) == "");
  // InDataExchange: SELECT AID dell'app Limen al bersaglio 1
  auto sel = gk::nfctel::apduSeleziona();
  assert((sel == std::vector<uint8_t>{0x00, 0xA4, 0x04, 0x00, 0x07, 0xF0, 0x4C, 0x49, 0x4D, 0x45, 0x4E, 0x01, 0x00}));
  auto fs = comandoScambio(sel);
  assert(fs[3] == 2 + 1 + sel.size() && fs[5] == 0xD4 && fs[6] == 0x40 && fs[7] == 0x01);
  uint8_t somma = 0; for (size_t i = 5; i < fs.size() - 2; i++) somma = uint8_t(somma + fs[i]);
  assert(uint8_t(somma + fs[fs.size() - 2]) == 0);
  std::vector<uint8_t> apdu;
  assert(scambioDaRisposta({0x41, 0x00, 0x90, 0x00}, apdu) && gk::nfctel::esitoOk(apdu));
  assert(!scambioDaRisposta({0x41, 0x01}, apdu));                          // timeout del PN532
  assert(scambioDaRisposta({0x41, 0x00, 0x6A, 0x82}, apdu) && !gk::nfctel::esitoOk(apdu));

  // SAK: telefono/DESFire (20) contro MIFARE Classic (08)
  assert(sakDaRisposta(r7) == 0x20 && sakDaRisposta(d.size() ? std::vector<uint8_t>(d.begin() + 1, d.end()) : d) == 0x08);
  assert(sakDaRisposta({0x4B, 0x00}) == -1);

  // Risposta alla sfida come la calcola l'app: titolo | HMAC(S, "LIMEN-OPEN" | sfida) | 90 00
  std::string chiave(32, '\x5A'), titolo = "ABCDEFGHIJKLMNOPQRSTUVW";
  uint8_t sfida[16]; for (int i = 0; i < 16; i++) sfida[i] = uint8_t(i);
  auto as = gk::nfctel::apduSfida(sfida);
  assert(as.size() == 22 && as[0] == 0x80 && as[1] == 0x10 && as[4] == 16 && as[5] == 0 && as[20] == 15 && as[21] == 0);
  uint8_t segreto[32], mac[32];
  gk::segretoTelefono(chiave, titolo, segreto);
  gk::macBle(std::string(reinterpret_cast<const char*>(segreto), 32), "LIMEN-OPEN", sfida, nullptr, mac);
  std::vector<uint8_t> rs(titolo.begin(), titolo.end());
  rs.insert(rs.end(), mac, mac + 32); rs.push_back(0x90); rs.push_back(0x00);
  std::string t2; uint8_t m2[32];
  assert(gk::nfctel::leggiApertura(rs, t2, m2) && t2 == titolo && std::memcmp(m2, mac, 32) == 0);
  rs[0] = 'a';                                                              // titolo non base32
  assert(!gk::nfctel::leggiApertura(rs, t2, m2));
  assert(!gk::nfctel::leggiApertura({0x6A, 0x82}, t2, m2));
  assert((gk::nfctel::apduEsito(true) == std::vector<uint8_t>{0x80, 0x12, 0x00, 0x00, 0x01, 0x01}));
  std::puts("pn532: ok");
  return 0;
}

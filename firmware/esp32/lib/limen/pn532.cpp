// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#include "pn532.h"

namespace gk {
namespace pn532 {

std::vector<uint8_t> frame(uint8_t cmd, const std::vector<uint8_t>& dati) {
  uint8_t len = uint8_t(2 + dati.size());
  std::vector<uint8_t> f = {0x00, 0x00, 0xFF, len, uint8_t(0x100 - len), 0xD4, cmd};
  uint8_t s = uint8_t(0xD4 + cmd);
  for (uint8_t d : dati) { f.push_back(d); s = uint8_t(s + d); }
  f.push_back(uint8_t(0x100 - s));
  f.push_back(0x00);
  return f;
}

std::vector<uint8_t> risveglio() {
  std::vector<uint8_t> r = {0x55, 0x55};
  r.insert(r.end(), 14, 0x00);
  return r;
}

std::vector<uint8_t> comandoSam() { return frame(CMD_SAM, {0x01, 0x14, 0x01}); }
std::vector<uint8_t> comandoFirmware() { return frame(CMD_FIRMWARE); }
// Item 05 (MaxRetries): MxRtyATR = FF, MxRtyPSL = 01, MxRtyPassiveActivation = 00 (un tentativo).
std::vector<uint8_t> comandoTentativi() { return frame(CMD_RF, {0x05, 0xFF, 0x01, 0x00}); }
// Item 01 (campo RF): bit 0 = campo acceso/spento (auto RFCA spento). Spegnere e riaccendere il campo azzera la tessera:
// una carta ISO 14443-4 gia' attivata non risponde a una nuova ricerca finche' non la si toglie o non si resetta il campo.
std::vector<uint8_t> comandoCampo(bool acceso) { return frame(CMD_RF, {0x01, uint8_t(acceso ? 0x01 : 0x00)}); }
std::vector<uint8_t> comandoCerca() { return frame(CMD_LISTA, {0x01, 0x00}); }
std::vector<uint8_t> comandoScambio(const std::vector<uint8_t>& apdu) {
  std::vector<uint8_t> d = {0x01};
  d.insert(d.end(), apdu.begin(), apdu.end());
  return frame(CMD_SCAMBIO, d);
}

int sakDaRisposta(const std::vector<uint8_t>& r) {
  if (r.size() < 7 || r[0] != uint8_t(CMD_LISTA + 1) || r[1] < 1) return -1;
  return r[5];
}

bool scambioDaRisposta(const std::vector<uint8_t>& r, std::vector<uint8_t>& apdu) {
  if (r.size() < 2 || r[0] != uint8_t(CMD_SCAMBIO + 1) || (r[1] & 0x3F) != 0) return false;
  apdu.assign(r.begin() + 2, r.end());
  return true;
}

void Ricevitore::azzera() { stato_ = inizio; corpo_.clear(); }

Pezzo Ricevitore::aggiungi(uint8_t b) {
  switch (stato_) {
    case inizio: if (b == 0x00) stato_ = zero; return Pezzo::niente;
    case zero:
      if (b == 0xFF) stato_ = lunghezza;
      else if (b != 0x00) stato_ = inizio;
      return Pezzo::niente;
    case lunghezza: len_ = b; stato_ = controllo; return Pezzo::niente;
    case controllo:
      lcs_ = b;
      stato_ = inizio;
      if (len_ == 0x00 && lcs_ == 0xFF) return Pezzo::ack;
      if (len_ == 0xFF && lcs_ == 0x00) return Pezzo::nack;
      if (uint8_t(len_ + lcs_) != 0 || len_ < 2) return Pezzo::errore;
      corpo_.clear(); sommaCorpo_ = 0;
      stato_ = corpo;
      return Pezzo::niente;
    case corpo:
      corpo_.push_back(b); sommaCorpo_ = uint8_t(sommaCorpo_ + b);
      if (corpo_.size() == len_) stato_ = somma;
      return Pezzo::niente;
    case somma:
      stato_ = inizio;
      if (uint8_t(sommaCorpo_ + b) != 0) return Pezzo::errore;
      if (corpo_[0] != 0xD5) return Pezzo::errore;         // 7F = frame d'errore del chip (errore di sintassi)
      dati_.assign(corpo_.begin() + 1, corpo_.end());
      return Pezzo::risposta;
  }
  return Pezzo::niente;
}

static const char* HEX_ = "0123456789ABCDEF";

std::string uidDaRisposta(const std::vector<uint8_t>& r, bool* casuale) {
  if (casuale) *casuale = false;
  // 4B NbTg Tg SENS_RES(2) SEL_RES NFCIDLength NFCID1...
  if (r.size() < 7 || r[0] != uint8_t(CMD_LISTA + 1) || r[1] < 1) return "";
  uint8_t n = r[6];
  if (n == 0 || n > 10 || r.size() < size_t(7 + n)) return "";
  std::string s;
  for (uint8_t i = 0; i < n; i++) { s += HEX_[r[7 + i] >> 4]; s += HEX_[r[7 + i] & 0x0F]; }
  if (casuale) *casuale = n == 4 && r[7] == 0x08;
  return s;
}

std::string versioneDaRisposta(const std::vector<uint8_t>& r) {
  if (r.size() < 5 || r[0] != uint8_t(CMD_FIRMWARE + 1) || r[1] != 0x32) return "";
  return "PN532 " + std::to_string(r[2]) + "." + std::to_string(r[3]);
}

}  // namespace pn532
}  // namespace gk

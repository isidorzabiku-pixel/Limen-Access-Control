// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#include "nfc_telefono.h"
#include <cstring>
#include "ble.h"

namespace gk {
namespace nfctel {

std::vector<uint8_t> apduSeleziona() {
  std::vector<uint8_t> a = {0x00, 0xA4, 0x04, 0x00, uint8_t(sizeof(AID))};
  a.insert(a.end(), AID, AID + sizeof(AID));
  a.push_back(0x00);
  return a;
}

std::vector<uint8_t> apduSfida(const uint8_t sfida[16]) {
  std::vector<uint8_t> a = {0x80, INS_APRI, 0x00, 0x00, uint8_t(LUNGHEZZA_NONCE_BLE)};
  a.insert(a.end(), sfida, sfida + LUNGHEZZA_NONCE_BLE);
  a.push_back(0x00);
  return a;
}

std::vector<uint8_t> apduEsito(bool concesso) {
  return {0x80, INS_ESITO, 0x00, 0x00, 0x01, uint8_t(concesso ? 1 : 0)};
}

bool esitoOk(const std::vector<uint8_t>& r) {
  return r.size() >= 2 && r[r.size() - 2] == 0x90 && r[r.size() - 1] == 0x00;
}

bool leggiApertura(const std::vector<uint8_t>& r, std::string& titolo, uint8_t mac[32]) {
  if (r.size() != LUNGHEZZA_TITOLO_BLE + LUNGHEZZA_MAC_BLE + 2 || !esitoOk(r)) return false;
  titolo.assign(reinterpret_cast<const char*>(r.data()), LUNGHEZZA_TITOLO_BLE);
  if (!titoloBleValido(titolo)) return false;
  memcpy(mac, r.data() + LUNGHEZZA_TITOLO_BLE, LUNGHEZZA_MAC_BLE);
  return true;
}

}  // namespace nfctel
}  // namespace gk

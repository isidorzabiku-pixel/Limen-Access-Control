// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Telefono via NFC (Android, emulazione di carta HCE): stessa credenziale e stessa verifica del BLE (ble.h),
// cambia solo il trasporto. APDU ISO 7816-4 scambiate dal lettore con l'app Limen:
//   1. SELECT AID F0 4C 49 4D 45 4E 01 ("LIMEN" v1)        → 90 00 se il telefono e' abbinato, altrimenti 6A 82
//   2. 80 10 00 00 10 <sfida 16 byte> 00                    → titolo (23) | HMAC(S, "LIMEN-OPEN" | sfida) (32) | 90 00
//   3. 80 12 00 00 01 <01 concesso / 00 negato>             → 90 00 (il telefono vibra se concesso)
// La sfida la genera il varco a ogni passaggio: niente da riusare. Un titolo o un HMAC sbagliati non producono
// eventi, come per il BLE.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace gk {
namespace nfctel {

constexpr uint8_t AID[] = {0xF0, 0x4C, 0x49, 0x4D, 0x45, 0x4E, 0x01};
constexpr uint8_t INS_APRI = 0x10, INS_ESITO = 0x12;

std::vector<uint8_t> apduSeleziona();
std::vector<uint8_t> apduSfida(const uint8_t sfida[16]);
std::vector<uint8_t> apduEsito(bool concesso);

bool esitoOk(const std::vector<uint8_t>& risposta);    // termina con 90 00
// Risposta alla sfida → titolo e HMAC; false se lunghezza, stato o titolo non tornano.
bool leggiApertura(const std::vector<uint8_t>& risposta, std::string& titolo, uint8_t mac[32]);

}  // namespace nfctel
}  // namespace gk

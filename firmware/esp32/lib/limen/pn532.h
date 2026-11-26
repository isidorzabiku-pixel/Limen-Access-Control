// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// PN532 in HSU (UART): costruzione dei frame e lettura delle risposte, senza Arduino (provato su PC).
// Riferimento: NXP PN532 User Manual (UM0701-02), capitoli «Host controller communication protocol» e comandi.
//   frame:  00 00 FF LEN LCS D4 CMD dati... DCS 00     (LEN conta D4 + CMD + dati; LCS e DCS: somma = 0 mod 256)
//   ACK:    00 00 FF 00 FF 00        NACK: 00 00 FF FF 00 00
//   risposta: stesso frame con D5 e CMD+1.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace gk {
namespace pn532 {

constexpr uint8_t CMD_FIRMWARE = 0x02;          // GetFirmwareVersion
constexpr uint8_t CMD_SAM = 0x14;               // SAMConfiguration
constexpr uint8_t CMD_RF = 0x32;                // RFConfiguration
constexpr uint8_t CMD_LISTA = 0x4A;             // InListPassiveTarget
constexpr uint8_t CMD_SCAMBIO = 0x40;           // InDataExchange (APDU verso la carta o il telefono)

// Frame completo per un comando (dati = parametri dopo CMD).
std::vector<uint8_t> frame(uint8_t cmd, const std::vector<uint8_t>& dati = {});

// In HSU il chip dorme fino a 0x55 0x55 seguiti da zeri: da mandare prima del primo comando (manuale, §7.2.11).
std::vector<uint8_t> risveglio();

// Comandi usati dal lettore.
std::vector<uint8_t> comandoSam();              // modo normale, niente SAM
std::vector<uint8_t> comandoFirmware();
std::vector<uint8_t> comandoTentativi();        // un solo tentativo per InListPassiveTarget: torna subito senza tessera
std::vector<uint8_t> comandoCampo(bool acceso);    // campo RF acceso/spento (reset della tessera ancora sul lettore)
std::vector<uint8_t> comandoCerca();            // una tessera ISO 14443A a 106 kbps (MIFARE, DESFire, NFC)
std::vector<uint8_t> comandoScambio(const std::vector<uint8_t>& apdu);   // al bersaglio 1, trovato da comandoCerca

enum class Pezzo { niente, ack, nack, risposta, errore };

// Riceve i byte uno alla volta; quando un frame e' completo torna ack/nack/risposta (dati in `risposta()`)
// oppure errore (checksum sbagliato o frame d'errore del chip). Il rumore prima di 00 FF si scarta.
class Ricevitore {
 public:
  Pezzo aggiungi(uint8_t b);
  // Dati della risposta: CMD+1 seguito dai parametri (senza D5).
  const std::vector<uint8_t>& risposta() const { return dati_; }
  void azzera();

 private:
  enum Stato { inizio, zero, lunghezza, controllo, corpo, somma };
  Stato stato_ = inizio;
  uint8_t len_ = 0, lcs_ = 0, sommaCorpo_ = 0;
  std::vector<uint8_t> corpo_, dati_;
};

// Risposta di InListPassiveTarget (4B ...) → UID in esadecimale maiuscolo, "" se nessuna tessera.
// `casuale` = UID a 4 byte che inizia con 08 (UID casuale per ISO 14443-3: telefoni, alcune DESFire): cambia a ogni lettura.
std::string uidDaRisposta(const std::vector<uint8_t>& r, bool* casuale = nullptr);

// SEL_RES (SAK) del bersaglio trovato, -1 se nessuno. Bit 0x20 = ISO 14443-4 (telefono, DESFire): parla per APDU.
int sakDaRisposta(const std::vector<uint8_t>& r);
constexpr int SAK_ISO14443_4 = 0x20;

// Risposta di InDataExchange (41 stato dati...): true se lo stato e' 0 e mette in `apdu` la risposta della carta.
bool scambioDaRisposta(const std::vector<uint8_t>& r, std::vector<uint8_t>& apdu);

// Risposta di GetFirmwareVersion (03 IC Ver Rev Support) → "PN532 1.6", "" se non valida.
std::string versioneDaRisposta(const std::vector<uint8_t>& r);

}  // namespace pn532
}  // namespace gk

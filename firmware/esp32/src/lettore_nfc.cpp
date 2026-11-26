// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#include "lettore_nfc.h"
#include <driver/gpio.h>
#include <esp_system.h>
#include <memory>
#include "desfire.h"
#include "nfc_telefono.h"
#include "pn532.h"

using namespace gk;

namespace {

constexpr uint32_t BAUD = 115200;
constexpr uint32_t ATTESA_ACK_MS = 100;
constexpr uint32_t ATTESA_RISPOSTA_MS = 300;
constexpr uint32_t ATTESA_TELEFONO_MS = 1500;   // la prima APDU puo' dover avviare il servizio dell'app
constexpr uint32_t PAUSA_RICERCA_MS = 80;       // tra una ricerca e la successiva
constexpr uint32_t RIPROVA_MS = 2000;           // PN532 assente o muto
constexpr uint32_t STESSA_TESSERA_MS = 1000;    // la stessa credenziale entro questo tempo non conta di nuovo

enum Passo { sam, firmware, tentativi, cerca, seleziona, sfida, esito, tessera, campoOff, campoOn };
enum Stato { riposo, attesaAck, attesaRisposta };

pn532::Ricevitore rx;
Passo passo = sam;
Stato stato = riposo;
uint32_t dal = 0, prossimo = 0;
bool trovato = false, segnalatoAssente = false;
String versione;

// Bersaglio corrente
String uidCorrente;
bool uidCasuale = false;
uint8_t nonce[16];
int esitoTelefono = 0;
std::unique_ptr<desfire::Procedura> procedura;   // lettura o scrittura DESFire in corso
char modoTessera = 0;                             // 0 lettura al varco, altrimenti la finestra del master: w, i, c

// Ultima credenziale contata (UID o titolo del telefono)
String ultima;
uint32_t ultimaVista = 0;

void invia() {
  while (Serial1.available()) Serial1.read();     // avanzi di una risposta precedente
  std::vector<uint8_t> b;
  switch (passo) {
    case sam: b = pn532::risveglio(); { auto c = pn532::comandoSam(); b.insert(b.end(), c.begin(), c.end()); } break;
    case firmware: b = pn532::comandoFirmware(); break;
    case tentativi: b = pn532::comandoTentativi(); break;
    case cerca: b = pn532::comandoCerca(); break;
    case seleziona: b = pn532::comandoScambio(nfctel::apduSeleziona()); break;
    case sfida:
      esp_fill_random(nonce, sizeof(nonce));
      b = pn532::comandoScambio(nfctel::apduSfida(nonce));
      break;
    case esito: b = pn532::comandoScambio(nfctel::apduEsito(esitoTelefono == 1)); break;
    case tessera: b = pn532::comandoScambio(procedura->apdu()); break;
    case campoOff: b = pn532::comandoCampo(false); break;
    case campoOn: b = pn532::comandoCampo(true); break;
  }
  Serial1.write(b.data(), b.size());
  rx.azzera();
  stato = attesaAck;
  dal = millis();
}

void guasto() {
  if (trovato || !segnalatoAssente) Serial.println("[nfc] PN532 not found (HSU on GPIO16/17, both switches OFF)");
  trovato = false;
  segnalatoAssente = true;
  versione = "";
  passo = sam;
  stato = riposo;
  prossimo = millis() + RIPROVA_MS;
}

// Torna a cercare dopo una pausa.
// Una carta ISO 14443-4 (DESFire, telefono) toccata resta attiva e non risponde a una nuova ricerca finche' non esce dal
// campo: dopo ogni scambio con una di esse il campo si spegne e riaccende, cosi' la stessa tessera ferma sul lettore si
// riconosce di nuovo (finestra di pulizia/scrittura aperta dal master mentre e' ancora li').
bool cartaAttiva = false;
void riposa() {
  if (cartaAttiva) { cartaAttiva = false; passo = campoOff; stato = riposo; prossimo = millis(); return; }
  passo = cerca; stato = riposo; prossimo = millis() + PAUSA_RICERCA_MS;
}

// true se la credenziale e' la stessa di poco fa (e rinfresca il momento in cui e' stata vista).
bool giaContata(const String& c) {
  uint32_t ora = millis();
  bool stessa = c == ultima && ora - ultimaVista < STESSA_TESSERA_MS;
  ultima = c; ultimaVista = ora;
  return stessa;
}

// Il bersaglio non e' un telefono Limen: vale l'UID, se e' un UID vero.
void comeBadge(const GestoriNfc& g) {
  if (uidCasuale) {
    if (!giaContata(uidCorrente)) Serial.println("[nfc] random UID " + uidCorrente + " ignored (phone without Limen app, or random-ID card)");
  } else if (!giaContata(uidCorrente)) {
    g.uid(uidCorrente);
  }
  riposa();
}

void casuale(uint8_t* p, size_t n) { esp_fill_random(p, n); }

std::vector<uint8_t> daHex(const String& h) {
  std::vector<uint8_t> v;
  for (unsigned i = 0; i + 1 < h.length(); i += 2) v.push_back(uint8_t(strtoul(h.substring(i, i + 2).c_str(), nullptr, 16)));
  return v;
}

// Inizia una procedura DESFire (lettura o scrittura) sul bersaglio corrente.
bool iniziaTessera(const GestoriNfc& g, char modo) {
  const std::string& k = g.chiaveImpianto();
  if (k.size() != 32) return false;                // la chiave d'impianto arriva con la cache
  uint8_t sito[16];
  desfire::chiaveSito(k, sito);
  auto uid = daHex(uidCorrente);
  procedura.reset(new desfire::Procedura(modo == 'w' ? desfire::Procedura::scrittura(sito, uid, casuale)
                                         : modo == 'c' ? desfire::Procedura::pulizia(sito, uid, casuale)
                                                       : desfire::Procedura::lettura(sito, uid, casuale)));
  modoTessera = modo;
  cartaAttiva = true;
  passo = tessera; prossimo = millis();
  return true;
}

// Fine della procedura DESFire.
void fineTessera(const GestoriNfc& g, bool scambioRiuscito) {
  desfire::Esito e = procedura->esito();
  std::string motivo = procedura->motivo();
  procedura.reset();
  String codice = String(desfire::codice(uidCorrente.c_str()).c_str());
  if (modoTessera) {
    using E = desfire::Esito;
    const char* esito = !scambioRiuscito ? "tolta" : "errore";
    if (scambioRiuscito && modoTessera == 'w') esito = e == E::scritta ? "scritta" : e == E::giaNostra ? "gia" : e == E::altroSistema ? "altro" : "errore";
    if (scambioRiuscito && modoTessera == 'i') esito = e == E::valida ? "nostra" : (e == E::assente || e == E::nonValida) ? "nonnostra" : "errore";
    if (scambioRiuscito && modoTessera == 'c') esito = e == E::pulita ? "pulita" : (e == E::assente || e == E::altroSistema) ? "nonnostra" : "errore";
    g.scritto(esito, uidCorrente.c_str(), motivo);
    // La tessera e' ancora sul lettore e ora si rilegge: non deve anche aprire (o, pulita, risultare un badge sconosciuto).
    giaContata(modoTessera == 'c' && e == E::pulita ? uidCorrente : codice);
    riposa();
    return;
  }
  switch (e) {
    case desfire::Esito::valida:
      if (!giaContata(codice)) g.uid(codice);
      riposa(); return;
    case desfire::Esito::nonValida:
      if (!giaContata(codice)) Serial.println("[nfc] DESFire " + uidCorrente + " not valid for this site (copy or other site)");
      riposa(); return;
    case desfire::Esito::assente:
      comeBadge(g); return;                        // DESFire vergine o di un altro sistema: vale l'UID come badge
    default:
      // Ha risposto ma non parla DESFire (altre carte ISO 14443-4): vale l'UID. Tolta a meta': si riprova.
      if (scambioRiuscito) comeBadge(g); else riposa();
      return;
  }
}

void risposta(const std::vector<uint8_t>& r, const GestoriNfc& g) {
  stato = riposo;
  uint32_t ora = millis();
  std::vector<uint8_t> apdu;
  switch (passo) {
    case sam: passo = firmware; prossimo = ora; return;
    case firmware:
      versione = pn532::versioneDaRisposta(r).c_str();
      if (versione.isEmpty()) { guasto(); return; }
      passo = tentativi; prossimo = ora; return;
    case tentativi:
      trovato = true; segnalatoAssente = false;
      Serial.println("[nfc] reader ready: " + versione);
      passo = cerca; prossimo = ora; return;
    case cerca: {
      uidCorrente = pn532::uidDaRisposta(r, &uidCasuale).c_str();
      if (uidCorrente.isEmpty()) { riposa(); return; }
      int sak = pn532::sakDaRisposta(r);
      bool iso4 = sak >= 0 && (sak & pn532::SAK_ISO14443_4);
      if (char modo = g.finestra()) {              // scrittura, riconoscimento o pulizia DESFire chiesti dal master
        if (iso4 && !uidCasuale && iniziaTessera(g, modo)) return;
        if (!giaContata(uidCorrente))
          g.scritto(iso4 ? "errore" : "nondesfire", uidCasuale ? "" : uidCorrente.c_str(),
                    !iso4 ? "" : uidCasuale ? "random UID (phone or card in privacy mode)" : "no site key yet");
        riposa();
        return;
      }
      if (iso4) { cartaAttiva = true; passo = seleziona; prossimo = ora; return; }  // telefono Limen, DESFire o altra carta ISO 14443-4
      comeBadge(g);
      return;
    }
    case seleziona:
      if (pn532::scambioDaRisposta(r, apdu) && nfctel::esitoOk(apdu)) { passo = sfida; prossimo = ora; return; }
      if (!uidCasuale && iniziaTessera(g, 0)) return;   // non e' un telefono Limen: forse una DESFire Limen
      comeBadge(g);
      return;
    case sfida: {
      std::string titolo;
      uint8_t mac[32];
      if (!pn532::scambioDaRisposta(r, apdu) || !nfctel::leggiApertura(apdu, titolo, mac)) { riposa(); return; }
      if (giaContata(String(titolo.c_str()))) { riposa(); return; }   // telefono ancora appoggiato
      esitoTelefono = g.telefono(titolo, nonce, mac);
      if (esitoTelefono < 0) { riposa(); return; }   // credenziale sbagliata: nessuna risposta al telefono
      passo = esito; prossimo = ora;
      return;
    }
    case esito: riposa(); return;
    case campoOff: passo = campoOn; prossimo = ora + 40; return;   // la tessera resta senza campo il tempo di azzerarsi
    case campoOn: riposa(); return;
    case tessera: {
      bool ok = pn532::scambioDaRisposta(r, apdu);
      procedura->risposta(ok ? apdu : std::vector<uint8_t>());
      if (procedura->esito() == desfire::Esito::inCorso) { prossimo = ora; return; }
      fineTessera(g, ok);
      return;
    }
  }
}

}  // namespace

void nfcAvvia() {
  Serial1.begin(BAUD, SERIAL_8N1, NFC_RX, NFC_TX);
  gpio_pullup_en(gpio_num_t(NFC_RX));            // RX scollegato = linea a riposo, non rumore
  Serial.println("NFC reader: PN532 HSU, RX GPIO" + String(NFC_RX) + ", TX GPIO" + String(NFC_TX));
  prossimo = millis();
}

void nfcLoop(const GestoriNfc& g) {
  uint32_t ora = millis();
  if (stato == riposo) {
    if (int32_t(ora - prossimo) >= 0) invia();
    return;
  }
  while (Serial1.available()) {
    pn532::Pezzo p = rx.aggiungi(uint8_t(Serial1.read()));
    if (p == pn532::Pezzo::niente) continue;
    if (stato == attesaAck) {
      if (p == pn532::Pezzo::ack) { stato = attesaRisposta; dal = millis(); continue; }
      guasto(); return;
    }
    if (p == pn532::Pezzo::risposta) { risposta(rx.risposta(), g); return; }
    if (p != pn532::Pezzo::ack) { guasto(); return; }
  }
  uint32_t limite = stato == attesaAck ? ATTESA_ACK_MS
                  : (passo == seleziona || passo == sfida || passo == esito || passo == tessera) ? ATTESA_TELEFONO_MS
                  : ATTESA_RISPOSTA_MS;
  if (millis() - dal > limite) {
    // Un telefono o una carta tolti a meta' scambio non sono un guasto del lettore.
    if (passo == tessera) { rx.azzera(); fineTessera(g, false); return; }
    if (passo == seleziona || passo == sfida || passo == esito) { rx.azzera(); riposa(); return; }
    guasto();
  }
}

String nfcStato() { return trovato ? versione : String("not found"); }

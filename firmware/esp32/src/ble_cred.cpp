// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Vedi ble_cred.h. Le scritture BLE arrivano nel thread di NimBLE: vanno in una coda e le tratta il loop().
#ifdef LIMEN_BLE_CRED
#include "ble_cred.h"
#include <NimBLEDevice.h>
#include <WiFi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <esp_system.h>
#include "ble.h"
#include "slave.h"

using namespace gk;

namespace {
constexpr const char* UUID_SERVIZIO = "6b1c4a10-7a3e-4c1d-9d6f-3a1e5b0c8f21";
constexpr const char* UUID_SFIDA = "6b1c4a11-7a3e-4c1d-9d6f-3a1e5b0c8f21";    // lettura: nonce nuovo a ogni lettura
constexpr const char* UUID_COMANDO = "6b1c4a12-7a3e-4c1d-9d6f-3a1e5b0c8f21";  // scrittura
constexpr const char* UUID_ESITO = "6b1c4a13-7a3e-4c1d-9d6f-3a1e5b0c8f21";    // lettura / notifica

constexpr size_t LUN_APERTURA = 1 + LUNGHEZZA_TITOLO_BLE + LUNGHEZZA_MAC_BLE;        // 56
constexpr size_t LUN_ABBINAMENTO = 1 + LUNGHEZZA_NONCE_BLE + LUNGHEZZA_MAC_BLE;      // 49
constexpr size_t MAX_COMANDO = LUN_APERTURA;              // il piu' lungo
constexpr uint32_t SCADENZA_SFIDA_MS = 30000;
constexpr uint32_t ATTESA_MASTER_MS = 6000;               // lo slave ripete la richiesta al master fino a 3 volte
constexpr uint32_t MAX_CONNESSIONE_MS = 20000;            // un telefono non puo' tenere occupato il lettore
constexpr int MAX_ERRORI = 5;                             // comandi respinti in FINESTRA_ERRORI_MS, poi silenzio
constexpr uint32_t FINESTRA_ERRORI_MS = 30000;
constexpr uint32_t SILENZIO_MS = 30000;

struct Comando { uint8_t n; uint8_t d[MAX_COMANDO]; };

QueueHandle_t coda = nullptr;
NimBLEServer* server = nullptr;
NimBLECharacteristic* esito = nullptr;
portMUX_TYPE muxSfida = portMUX_INITIALIZER_UNLOCKED;
uint8_t sfida[LUNGHEZZA_NONCE_BLE];
uint32_t sfidaCreataMs = 0;
bool sfidaValida = false;
volatile uint32_t connessoMs = 0;                         // 0 = nessun telefono collegato

// Abbinamento inoltrato al master, in attesa della risposta AR.
bool inAttesaMaster = false;
uint32_t inviatoMs = 0;

// Comandi respinti: dopo MAX_ERRORI il lettore tace per un po' (niente verifiche, niente risposte utili).
int errori = 0;
uint32_t primoErroreMs = 0, silenzioFinoMs = 0;

class SfidaCb : public NimBLECharacteristicCallbacks {
  void onRead(NimBLECharacteristic* c) override {
    uint8_t n[LUNGHEZZA_NONCE_BLE];
    esp_fill_random(n, sizeof(n));
    portENTER_CRITICAL(&muxSfida);
    memcpy(sfida, n, sizeof(n));
    sfidaCreataMs = millis();
    sfidaValida = true;
    portEXIT_CRITICAL(&muxSfida);
    c->setValue(n, sizeof(n));
  }
};

class ComandoCb : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* c) override {
    std::string v = c->getValue();
    if (v.empty() || v.size() > MAX_COMANDO) return;
    Comando k;
    k.n = uint8_t(v.size());
    memcpy(k.d, v.data(), v.size());
    if (coda) xQueueSend(coda, &k, 0);                   // coda piena: si scarta
  }
};

// Un solo telefono alla volta: la pubblicita' si ferma alla connessione e riparte alla disconnessione,
// quindi la sfida (una sola) e' sempre di chi e' collegato.
class ServerCb : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer*) override {
    connessoMs = millis() | 1;
    portENTER_CRITICAL(&muxSfida); sfidaValida = false; portEXIT_CRITICAL(&muxSfida);
  }
  void onDisconnect(NimBLEServer*) override { connessoMs = 0; NimBLEDevice::startAdvertising(); }
};

void rispondi(const String& testo) {
  Serial.println("[ble] " + testo);
  if (!esito) return;
  esito->setValue(std::string(testo.c_str()));
  esito->notify();
}

void respinto(const char* risposta) {
  uint32_t ora = millis();
  if (errori == 0 || ora - primoErroreMs > FINESTRA_ERRORI_MS) { errori = 0; primoErroreMs = ora; }
  if (++errori >= MAX_ERRORI) { errori = 0; silenzioFinoMs = ora + SILENZIO_MS; Serial.println("[ble] too many rejected commands: quiet for 30 s"); }
  rispondi(risposta);
}

// Prende la sfida una sola volta (monouso, scade dopo 30 s).
bool consumaSfida(uint8_t out[LUNGHEZZA_NONCE_BLE]) {
  bool ok;
  portENTER_CRITICAL(&muxSfida);
  ok = sfidaValida && (millis() - sfidaCreataMs) < SCADENZA_SFIDA_MS;
  if (ok) memcpy(out, sfida, LUNGHEZZA_NONCE_BLE);
  sfidaValida = false;
  portEXIT_CRITICAL(&muxSfida);
  return ok;
}

// Apertura. 'O' + titolo (23) + HMAC(S, "LIMEN-OPEN" | sfida) (32). Un telefono non abbinato, o con il segreto
// sbagliato, riceve solo "NO": nessun evento, nessun pacchetto al master. Solo chi passa arriva a badge().
void trattaApertura(Slave& slave, int lettore, const uint8_t* d, size_t n) {
  if (n != LUN_APERTURA) { respinto("NO"); return; }
  std::string titolo(reinterpret_cast<const char*>(d + 1), LUNGHEZZA_TITOLO_BLE);
  const uint8_t* mac = d + 1 + LUNGHEZZA_TITOLO_BLE;
  uint8_t nonce[LUNGHEZZA_NONCE_BLE];
  if (!consumaSfida(nonce)) { rispondi("ERR challenge"); return; }
  if (!slave.verificaAperturaBle(titolo, nonce, mac)) { respinto("NO"); return; }
  EsitoBadge e = slave.badge(titolo, lettore);          // da qui valgono tutte le regole, come per un badge
  rispondi(e.ignorato ? String("IGNORED") : String(String(e.concesso ? "OK " : "NO ") + e.esito.c_str()));
}

// Abbinamento. 'P' + nonce del telefono (16) + HMAC(K, "LIMEN-PAIR" | sfida | nonce) (32), K = chiave dell'invito.
// Senza invito giusto qui non passa niente e niente va al master.
void trattaAbbinamento(Slave& slave, const uint8_t* d, size_t n) {
  if (n != LUN_ABBINAMENTO || !slave.finestraBleAperta()) { respinto("PAIR NO"); return; }
  const uint8_t* nonceTel = d + 1;
  const uint8_t* mac = nonceTel + LUNGHEZZA_NONCE_BLE;
  uint8_t nonce[LUNGHEZZA_NONCE_BLE];
  if (!consumaSfida(nonce)) { rispondi("ERR challenge"); return; }
  if (!slave.verificaProvaBle(nonce, nonceTel, mac)) { respinto("PAIR NO"); return; }
  if (!slave.inoltraAbbinamentoBle(nonce, nonceTel, mac)) { rispondi("PAIR NO"); return; }
  inAttesaMaster = true;
  inviatoMs = millis();
  rispondi("PAIR SENT");
}
}  // namespace

void bleCredAvvia() {
  coda = xQueueCreate(4, sizeof(Comando));
  char nome[24];
  snprintf(nome, sizeof(nome), "Limen-%04X", unsigned((ESP.getEfuseMac() >> 32) & 0xFFFF));
  // Wi-Fi e BLE condividono la radio: senza modem sleep il controller BT si ferma con abort() (coex_core_enable).
  WiFi.setSleep(true);
  NimBLEDevice::init(nome);
  NimBLEDevice::setMTU(247);
  server = NimBLEDevice::createServer();
  server->setCallbacks(new ServerCb());
  NimBLEService* servizio = server->createService(UUID_SERVIZIO);
  NimBLECharacteristic* s = servizio->createCharacteristic(UUID_SFIDA, NIMBLE_PROPERTY::READ);
  s->setCallbacks(new SfidaCb());
  NimBLECharacteristic* k = servizio->createCharacteristic(UUID_COMANDO, NIMBLE_PROPERTY::WRITE);
  k->setCallbacks(new ComandoCb());
  esito = servizio->createCharacteristic(UUID_ESITO, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);
  esito->setValue("READY");
  servizio->start();
  NimBLEAdvertising* pubblicita = NimBLEDevice::getAdvertising();
  pubblicita->addServiceUUID(UUID_SERVIZIO);
  pubblicita->setScanResponse(true);
  NimBLEDevice::startAdvertising();
  Serial.println(String("BLE credential reader: ") + nome);
}

void bleCredLoop(Slave& slave, int lettore) {
  if (!coda || !esito) return;
  Comando k;
  while (xQueueReceive(coda, &k, 0) == pdTRUE) {
    if (silenzioFinoMs && int32_t(millis() - silenzioFinoMs) < 0) continue;      // troppi comandi respinti: si tace
    silenzioFinoMs = 0;
    if (k.d[0] == 'O') trattaApertura(slave, lettore, k.d, k.n);
    else if (k.d[0] == 'P') trattaAbbinamento(slave, k.d, k.n);
    else respinto("NO");
  }
  if (inAttesaMaster) {
    std::string r = slave.prendiEsitoAbbinamentoBle();
    // "ok <credenziale cifrata>": titolo e segreto per il telefono, leggibili solo con l'invito
    if (!r.empty()) { inAttesaMaster = false; rispondi(r.rfind("ok ", 0) == 0 ? String("PAIR OK ") + r.substr(3).c_str() : String("PAIR NO")); }
    else if (millis() - inviatoMs > ATTESA_MASTER_MS) { inAttesaMaster = false; rispondi("PAIR NO"); }
  }
  // Connessione troppo lunga: si chiude, cosi' un telefono fermo non blocca gli altri.
  uint32_t c = connessoMs;
  if (c && !inAttesaMaster && millis() - c > MAX_CONNESSIONE_MS && server) {
    for (uint16_t h : server->getPeerDevices()) server->disconnect(h);
    connessoMs = 0;
  }
}
#endif

// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Vedi ble_test.h. La scrittura BLE arriva nel thread di NimBLE: il codice va in una coda e lo tratta il loop(),
// perche' lo Slave non e' pensato per essere chiamato da piu' thread.
#ifdef LIMEN_BLE_TEST
#include "ble_test.h"
#include <NimBLEDevice.h>
#include <WiFi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <esp_system.h>

namespace {
// UUID di prova (generati a caso): servizio, «codice» (scrivibile) ed «esito» (leggibile / notifica).
constexpr const char* UUID_SERVIZIO = "2fd7f8e4-0c6a-4a82-8100-50d9c598ca5e";
constexpr const char* UUID_CODICE = "d112ee41-dffe-4656-9c99-1bb61f1c2130";
constexpr const char* UUID_ESITO = "edbda37c-4492-4096-ae7c-2b0241ff83cf";

constexpr size_t MAX_RIGA = 64;
struct Riga { char s[MAX_RIGA]; };

QueueHandle_t coda = nullptr;
NimBLECharacteristic* esito = nullptr;

class CodiceCb : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* c) override {
    std::string v = c->getValue();
    Riga r;
    size_t n = v.size() < MAX_RIGA - 1 ? v.size() : MAX_RIGA - 1;
    memcpy(r.s, v.data(), n);
    r.s[n] = 0;
    if (coda) xQueueSend(coda, &r, 0);                   // coda piena: il codice si scarta
  }
};

class ServerCb : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer*) override { Serial.println("[ble] connected"); }
  void onDisconnect(NimBLEServer*) override {
    Serial.println("[ble] disconnected");
    NimBLEDevice::startAdvertising();                     // dopo una connessione la pubblicita' si ferma
  }
};
}  // namespace

void bleAvvia() {
  coda = xQueueCreate(4, sizeof(Riga));
  char nome[24];
  snprintf(nome, sizeof(nome), "Limen-BLE-%04X", unsigned((ESP.getEfuseMac() >> 32) & 0xFFFF));
  // Wi-Fi e BLE condividono la radio: senza modem sleep il controller BT si ferma con abort() (coex_core_enable).
  WiFi.setSleep(true);
  NimBLEDevice::init(nome);
  NimBLEServer* server = NimBLEDevice::createServer();
  server->setCallbacks(new ServerCb());
  NimBLEService* servizio = server->createService(UUID_SERVIZIO);
  NimBLECharacteristic* codice =
      servizio->createCharacteristic(UUID_CODICE, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
  codice->setCallbacks(new CodiceCb());
  esito = servizio->createCharacteristic(UUID_ESITO, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);
  esito->setValue("READY");
  servizio->start();
  NimBLEAdvertising* pubblicita = NimBLEDevice::getAdvertising();
  pubblicita->addServiceUUID(UUID_SERVIZIO);
  pubblicita->setScanResponse(true);
  NimBLEDevice::startAdvertising();
  Serial.println(String("BLE test reader: ") + nome + " (NO authentication: bench use only)");
}

void bleLoop(const std::function<String(const String&)>& gestore) {
  if (!coda || !esito) return;
  Riga r;
  while (xQueueReceive(coda, &r, 0) == pdTRUE) {
    String risposta = gestore(String(r.s));
    esito->setValue(std::string(risposta.c_str()));
    esito->notify();
  }
}
#endif

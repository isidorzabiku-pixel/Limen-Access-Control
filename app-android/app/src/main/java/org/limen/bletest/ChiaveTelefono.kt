// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Credenziale BLE del telefono: un titolo (23 caratteri, non segreto) e un segreto S di 32 byte, ricevuti dal varco
// all'abbinamento. S sta nell'Android Keystore (non si puo' rileggere, si puo' solo usare); il titolo nelle preferenze.
// Calcoli come firmware/esp32/lib/limen/ble.* e backend/services/ble_abbinamento.py.
package org.limen.bletest

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyProperties
import android.security.keystore.KeyProtection
import java.security.KeyStore
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec

object ChiaveTelefono {
    private const val ALIAS = "limen-ble-secret"
    private const val PREF_TITOLO = "titolo"
    const val LUNGHEZZA_TITOLO = 23
    private const val LUNGHEZZA_SEGRETO = 32
    private const val LUNGHEZZA_ETICHETTA = 16

    private val PREFISSO_APERTURA = "LIMEN-OPEN".toByteArray(Charsets.US_ASCII)
    private val PREFISSO_ABBINAMENTO = "LIMEN-PAIR".toByteArray(Charsets.US_ASCII)
    private val PREFISSO_FLUSSO = "LIMEN-CRED".toByteArray(Charsets.US_ASCII)
    private val PREFISSO_ETICHETTA = "LIMEN-CTAG".toByteArray(Charsets.US_ASCII)
    private val SALE_INVITO = "LIMEN-INVITE-v1".toByteArray(Charsets.US_ASCII)
    private const val ITERAZIONI_INVITO = 20000          // come backend/services/ble_abbinamento.py

    private var prefs: SharedPreferences? = null

    fun inizia(ctx: Context) {
        if (prefs == null) prefs = ctx.applicationContext.getSharedPreferences("limen-ble", Context.MODE_PRIVATE)
    }

    private fun keystore(): KeyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    // Titolo di questo telefono, oppure "" se non e' abbinato.
    fun titolo(): String {
        val t = prefs?.getString(PREF_TITOLO, "") ?: ""
        return if (t.length == LUNGHEZZA_TITOLO && runCatching { keystore().containsAlias(ALIAS) }.getOrDefault(false)) t else ""
    }

    fun abbinato(): Boolean = titolo().isNotEmpty()

    private fun salva(titolo: String, segreto: ByteArray) {
        keystore().setEntry(
            ALIAS,
            KeyStore.SecretKeyEntry(SecretKeySpec(segreto, "HmacSHA256")),
            KeyProtection.Builder(KeyProperties.PURPOSE_SIGN).build(),
        )
        prefs?.edit()?.putString(PREF_TITOLO, titolo)?.apply()
    }

    // Toglie la credenziale da questo telefono (sul master il titolo va eliminato a parte).
    fun dimentica() {
        runCatching { keystore().deleteEntry(ALIAS) }
        prefs?.edit()?.remove(PREF_TITOLO)?.apply()
    }

    // Apertura: HMAC-SHA256(S, "LIMEN-OPEN" || sfida), calcolato dentro il Keystore.
    fun macApertura(sfida: ByteArray): ByteArray {
        val chiave = keystore().getKey(ALIAS, null) ?: error("not paired")
        return Mac.getInstance("HmacSHA256").run { init(chiave); doFinal(PREFISSO_APERTURA + sfida) }
    }

    // L'invito e' una chiave e non viaggia mai: K = PBKDF2-HMAC-SHA256(invito, sale fisso, 20000 iterazioni), 32 byte.
    fun chiaveInvito(invito: String): ByteArray =
        SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
            .generateSecret(PBEKeySpec(invito.toCharArray(), SALE_INVITO, ITERAZIONI_INVITO, 256)).encoded

    private fun hmac(chiave: ByteArray, dati: ByteArray): ByteArray =
        Mac.getInstance("HmacSHA256").run { init(SecretKeySpec(chiave, "HmacSHA256")); doFinal(dati) }

    // Abbinamento: prova di avere l'invito, HMAC(K, "LIMEN-PAIR" || sfida || nonce del telefono).
    fun provaAbbinamento(k: ByteArray, sfida: ByteArray, nonceTel: ByteArray): ByteArray =
        hmac(k, PREFISSO_ABBINAMENTO + sfida + nonceTel)

    // «PAIR OK <esadecimale>»: (titolo || S) cifrati con K piu' un'etichetta di 16 byte. Solo il varco vero, che ha K,
    // sa calcolare l'etichetta. Se torna, la credenziale viene salvata. Ritorna il titolo, o null se non torna.
    fun riceviCredenziale(k: ByteArray, sfida: ByteArray, nonceTel: ByteArray, dati: ByteArray): String? {
        if (dati.size != LUNGHEZZA_TITOLO + LUNGHEZZA_SEGRETO + LUNGHEZZA_ETICHETTA) return null
        val cifrato = dati.copyOfRange(0, dati.size - LUNGHEZZA_ETICHETTA)
        val etichetta = dati.copyOfRange(dati.size - LUNGHEZZA_ETICHETTA, dati.size)
        val attesa = hmac(k, PREFISSO_ETICHETTA + sfida + nonceTel + cifrato).copyOf(LUNGHEZZA_ETICHETTA)
        if (!MessageDigest.isEqual(attesa, etichetta)) return null
        val flusso = hmac(k, PREFISSO_FLUSSO + sfida + nonceTel + byteArrayOf(0)) +
            hmac(k, PREFISSO_FLUSSO + sfida + nonceTel + byteArrayOf(1))
        val chiaro = ByteArray(cifrato.size) { i -> (cifrato[i].toInt() xor flusso[i].toInt()).toByte() }
        val titolo = String(chiaro, 0, LUNGHEZZA_TITOLO, Charsets.US_ASCII)
        if (!titolo.all { it in 'A'..'Z' || it in '2'..'7' }) return null
        salva(titolo, chiaro.copyOfRange(LUNGHEZZA_TITOLO, chiaro.size))
        return titolo
    }
}

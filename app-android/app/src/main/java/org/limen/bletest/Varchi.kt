// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// I varchi che questo telefono conosce: restano in prima pagina, la ricerca serve solo per aggiungerne uno.
package org.limen.bletest

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

data class Varco(
    val indirizzo: String,           // indirizzo Bluetooth del dispositivo: ci si collega senza cercarlo
    val nome: String,                // nome scelto dalla persona
    val dispositivo: String,         // nome annunciato dal dispositivo (Limen-XXXX)
    val aggiuntoIl: Long,
    val ultimoEsito: String = "",
    val ultimaApertura: Long = 0,
)

class Varchi(ctx: Context) {
    private val prefs = ctx.applicationContext.getSharedPreferences("limen-gates", Context.MODE_PRIVATE)

    fun tutti(): List<Varco> {
        val testo = prefs.getString("varchi", null) ?: return emptyList()
        return try {
            val a = JSONArray(testo)
            (0 until a.length()).map { i ->
                val o = a.getJSONObject(i)
                Varco(
                    indirizzo = o.getString("indirizzo"),
                    nome = o.optString("nome", "Gate"),
                    dispositivo = o.optString("dispositivo", ""),
                    aggiuntoIl = o.optLong("aggiuntoIl", 0),
                    ultimoEsito = o.optString("ultimoEsito", ""),
                    ultimaApertura = o.optLong("ultimaApertura", 0),
                )
            }
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun salva(lista: List<Varco>): List<Varco> {
        val a = JSONArray()
        for (v in lista) {
            a.put(
                JSONObject()
                    .put("indirizzo", v.indirizzo).put("nome", v.nome).put("dispositivo", v.dispositivo)
                    .put("aggiuntoIl", v.aggiuntoIl).put("ultimoEsito", v.ultimoEsito).put("ultimaApertura", v.ultimaApertura)
            )
        }
        prefs.edit().putString("varchi", a.toString()).apply()
        return lista
    }

    // Aggiunge il varco, o lo aggiorna se c'e' gia' (stesso indirizzo).
    fun aggiungi(v: Varco): List<Varco> {
        val lista = tutti()
        val esiste = lista.any { it.indirizzo == v.indirizzo }
        return salva(if (esiste) lista.map { if (it.indirizzo == v.indirizzo) it.copy(nome = v.nome, dispositivo = v.dispositivo) else it } else lista + v)
    }

    fun rinomina(indirizzo: String, nome: String): List<Varco> =
        salva(tutti().map { if (it.indirizzo == indirizzo) it.copy(nome = nome) else it })

    fun rimuovi(indirizzo: String): List<Varco> = salva(tutti().filter { it.indirizzo != indirizzo })

    fun esito(indirizzo: String, esito: String, aperto: Boolean, ora: Long): List<Varco> =
        salva(tutti().map {
            if (it.indirizzo != indirizzo) it
            else it.copy(ultimoEsito = esito, ultimaApertura = if (aperto) ora else it.ultimaApertura)
        })
}

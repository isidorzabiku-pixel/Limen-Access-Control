// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Pagine web del firmware (HTML in flash). Interfaccia solo in inglese, senza risorse esterne.
#pragma once
#include <Arduino.h>

static const char STILE[] PROGMEM = R"CSS(
:root{--bg:#f4f6f8;--card:#fff;--fg:#17202a;--mu:#5d6b7a;--bd:#d9e0e7;--ac:#0b7a93;--ok:#1a8f4d;--wa:#b7791f;--er:#c0392b}
@media(prefers-color-scheme:dark){:root{--bg:#10161c;--card:#18212a;--fg:#e6edf3;--mu:#8a98a6;--bd:#2a3743;--ac:#3cc1dc;--ok:#46c47a;--wa:#e0a84a;--er:#ef6b5c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}h1{font-size:1.25rem;margin:.2rem 0 1rem}h2{font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;color:var(--mu);margin:0 0 .6rem}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px;margin-bottom:12px}
.riga{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin:.35rem 0}.riga>span.k{color:var(--mu);min-width:9rem}
button{font:inherit;background:var(--ac);color:#fff;border:0;border-radius:8px;padding:.5rem .9rem;cursor:pointer}button.g{background:transparent;color:var(--ac);border:1px solid var(--ac)}button.r{background:var(--er)}button:disabled{opacity:.45;cursor:default}
input,select{font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--bd);border-radius:8px;padding:.45rem .6rem;min-width:0}
.b{display:inline-block;border-radius:99px;padding:.05rem .6rem;font-size:.8rem;border:1px solid var(--bd)}.ok{color:var(--ok);border-color:var(--ok)}.wa{color:var(--wa);border-color:var(--wa)}.er{color:var(--er);border-color:var(--er)}
pre{margin:0;max-height:16rem;overflow:auto;font:12px/1.5 ui-monospace,monospace;white-space:pre-wrap;color:var(--mu)}
label{display:block;margin:.5rem 0 .15rem;color:var(--mu);font-size:.85rem}.due{display:grid;grid-template-columns:1fr 1fr;gap:.6rem}@media(max-width:520px){.due{grid-template-columns:1fr}}
.esito{font-weight:600;margin-left:.3rem}.lamp{display:inline-block;width:14px;height:14px;border-radius:50%;background:var(--bd);margin-left:.45rem;vertical-align:middle}.lamp.v{background:#1fbf5f;box-shadow:0 0 8px #1fbf5f}.lamp.r{background:#e5484d;box-shadow:0 0 8px #e5484d}.mu{color:var(--mu)}a{color:var(--ac)}
)CSS";

static const char PAGINA_PRINCIPALE[] PROGMEM = R"HTML(<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Limen slave</title><link rel="stylesheet" href="/stile.css"></head><body><main>
<h1>Limen · slave <span id="idv"></span></h1>
<div class="card"><h2>Status</h2>
<div class="riga"><span class="k">Master</span><span id="abb"></span></div>
<div class="riga"><span class="k">Credentials</span><span id="cache"></span></div>
<div class="riga"><span class="k">Time</span><span id="ora"></span></div>
<div class="riga"><span class="k">Events to deliver</span><span id="coda"></span></div>
<div class="riga"><span class="k">Network</span><span id="rete" class="mu"></span></div>
</div>
<div class="card"><h2>Test console</h2>
<form id="fb" class="riga"><input id="cod" placeholder="Badge code" maxlength="23" autocomplete="off" style="flex:1;min-width:10rem"><select id="let"></select><button>Swipe badge</button></form>
<div class="riga"><span class="mu">Result</span><span id="esito" class="esito">—</span><span id="led"></span></div>
<p class="mu" style="margin:.4rem 0 0">Works like a real reader: decides locally from the master's cache, drives the outputs and reports the event. The two lamps are the green LED (door open) and the red one (denied).</p></div>
<div class="card"><h2>Outputs</h2><div id="uscite"></div><div class="riga"><button class="g" id="apri">Open as from the dashboard</button></div></div>
<div class="card"><h2>Inputs</h2><div id="ingressi"></div><p class="mu" style="margin:.4rem 0 0">With nothing wired the input reads “high” (pull-up). To test the sensor or the button, force the level below; for an NC contact, a high pin means the contact is open.</p></div>
<div class="card"><h2>Log</h2><pre id="log"></pre></div>
<div class="card"><h2>Management</h2><div class="riga"><a href="/setup">Settings (ID, network, pins)</a><button class="g" id="dim">Forget pairing</button><button class="g" id="riavvia">Restart</button></div></div>
</main><script>
const $=i=>document.getElementById(i);
async function post(u,d){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams(d)});return r.json().catch(()=>({}))}
function b(t,c){return '<span class="b '+c+'">'+t+'</span>'}
let nl=-1;
const RU={apre:'opens',sirena:'siren',inseritore:'arming',altro:'other','non usata':'unused'},RG={'sensore porta':'door sensor',pulsante:'button',altro:'other','non usato':'unused'};
const ES={negato_varco_disattivato:'Denied: gate disabled',negato_varco_manutenzione:'Denied: gate in maintenance',negato_credenziale_sconosciuta:'Denied: unknown credential',negato_credenziale_scaduta:'Denied: credential expired',negato_anti_passback:'Denied: anti-passback',negato_fuori_fascia:'Denied: outside time window'};
async function agg(){try{
 const s=await (await fetch('/api/status')).json();
 $('idv').textContent='#'+s.id;
 $('abb').innerHTML=s.abbinato?b('paired','ok'):b('not paired — press “Pair” in the master','wa');
 $('cache').innerHTML=s.cache_ricevuta?(s.badge+' badges'+(s.cache_fresca?'':' (from flash, waiting for the master)')+(s.attivo?'':' · gate disabled')+(s.modo!='n'?' · mode: '+({s:'unlocked',m:'maintenance',e:'EMERGENCY'})[s.modo]:'')):b('no cache','wa');
 $('led').innerHTML='<span class="lamp'+(s.led_verde?' v':'')+'" title="Green LED: door open"></span><span class="lamp'+(s.led_rosso?' r':'')+'" title="Red LED: badge denied"></span>';
 $('ora').innerHTML=s.ora_valida?s.ora_locale:b('not synced','wa');
 $('coda').textContent=s.coda;
 $('rete').textContent=s.ip+' · '+s.rssi+' dBm · up '+s.uptime+' s';
 if(nl!==s.lettori){nl=s.lettori;$('let').innerHTML=Array.from({length:s.lettori},(_,i)=>'<option value="'+i+'">Reader '+(i+1)+'</option>').join('')}
 $('uscite').innerHTML=s.uscite.map((u,i)=>'<div class="riga"><span class="k">Output '+(i+1)+' · '+(RU[u.ruolo]||u.ruolo)+'</span>'+(u.eccitata?b('active','ok'):b('idle',''))+
  (u.interruttore?'<button class="g" data-u="'+i+'" data-a="'+(u.acceso?'0':'1')+'">'+(u.acceso?'Turn off':'Turn on')+'</button>':'')+
  (u.impulsoComandabile?'<button class="g" data-u="'+i+'" data-a="i">Pulse</button>':'')+'</div>').join('');
 $('ingressi').innerHTML=s.ingressi.map((g,i)=>'<div class="riga"><span class="k">Input '+(i+1)+' · '+(RG[g.ruolo]||g.ruolo)+'</span>'+(g.presente?b('pin '+(g.alto?'high':'low'),''):b('not connected','wa'))+
  '<select data-s="'+i+'"><option value="auto"'+(g.sim<0?' selected':'')+'>Real pin</option><option value="1"'+(g.sim==1?' selected':'')+'>Force high</option><option value="0"'+(g.sim==0?' selected':'')+'>Force low</option></select></div>').join('')+
  (s.sensore_porta?'<div class="riga"><span class="k">Door</span>'+b(s.porta,s.porta=='open'?'wa':'ok')+'</div>':'');
 const l=await (await fetch('/api/log')).json();$('log').textContent=l.join('\n');
}catch(e){}}
$('fb').onsubmit=async e=>{e.preventDefault();const c=$('cod').value.trim();if(!c)return;const r=await post('/api/badge',{codice:c,lettore:$('let').value});
 $('esito').innerHTML=r.ignorato?b('reader not used','wa'):(r.concesso?b('ACCESS GRANTED','ok'):b(ES[r.esito]||r.esito||'error','er'));$('cod').select();agg()};
document.addEventListener('click',async e=>{const t=e.target;if(t.dataset.u!==undefined){await post('/api/uscita',{indice:t.dataset.u,azione:t.dataset.a});agg()}});
document.addEventListener('change',async e=>{const t=e.target;if(t.dataset.s!==undefined){await post('/api/sim',{indice:t.dataset.s,valore:t.value});agg()}});
$('apri').onclick=async()=>{await post('/api/apri',{});agg()};
$('dim').onclick=async()=>{if(confirm('Forget the pairing? You will have to press “Pair” in the master again.')){await post('/api/dimentica',{});agg()}};
$('riavvia').onclick=async()=>{await post('/api/riavvia',{})};
agg();setInterval(agg,1500);
</script></body></html>)HTML";

// Il modulo di configurazione si compone a runtime (valori attuali) in main.cpp.
static const char SETUP_TESTA[] PROGMEM = R"HTML(<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Limen setup</title><link rel="stylesheet" href="/stile.css"></head><body><main>
<h1>Slave settings</h1>
)HTML";

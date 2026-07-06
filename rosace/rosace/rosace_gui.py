#!/usr/bin/env python3
"""
rosace_gui.py — Serveur d'interface graphique pour l'orchestrateur Rosace.

Lance un serveur HTTP local qui :
  • Sert l'interface Bootstrap à http://localhost:<UI_PORT>/
  • Proxifie /api/* → http://<API_HOST>:<API_PORT>/*
    (élimine les erreurs CORS du navigateur)

Usage :
    python rosace_gui.py                  # API sur localhost:8000, UI sur 8080
    python rosace_gui.py --api-port 9000  # API sur port 9000
    python rosace_gui.py --ui-port 8888   # UI sur port 8888
    python rosace_gui.py --no-browser     # Ne pas ouvrir le navigateur auto.

Dépendances : stdlib uniquement (Python ≥ 3.9)
"""

import argparse
import http.server
import json
import logging
import threading
import urllib.error
import urllib.request
import webbrowser
from http import HTTPStatus

# ─────────────────────────────────────────────────────────────
# Configuration par défaut
# ─────────────────────────────────────────────────────────────

DEFAULT_UI_PORT  = 8080
DEFAULT_API_HOST = "localhost"
DEFAULT_API_PORT = 8000

# ─────────────────────────────────────────────────────────────
# HTML embarqué (interface Bootstrap)
# ─────────────────────────────────────────────────────────────
# Toutes les requêtes fetch() utilisent /api/... (chemin relatif).
# Le serveur proxy les renvoie vers l'API FastAPI.

HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Rosace — Orchestrateur Spectroscopique</title>
  <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.min.css" rel="stylesheet" />
  <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap-icons/1.11.3/font/bootstrap-icons.min.css" rel="stylesheet" />
  <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <style>
    :root {
      --bg-dark:     #080c14;
      --bg-panel:    #0d1626;
      --bg-card:     #111e33;
      --accent:      #00d4ff;
      --accent-dim:  #0080aa;
      --accent-glow: rgba(0,212,255,0.18);
      --green:       #00ff88;
      --green-dim:   rgba(0,255,136,0.12);
      --red:         #ff3c5a;
      --red-dim:     rgba(255,60,90,0.12);
      --amber:       #ffb830;
      --amber-dim:   rgba(255,184,48,0.12);
      --text:        #c8ddf0;
      --text-muted:  #4a6a8a;
      --border:      rgba(0,212,255,0.12);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg-dark);
      color: var(--text);
      font-family: 'Rajdhani', sans-serif;
      font-size: 1rem;
      min-height: 100vh;
      overflow-x: hidden;
    }
    body::before {
      content: '';
      position: fixed;
      inset: 0;
      background-image:
        radial-gradient(1px 1px at 10% 20%, rgba(255,255,255,.5) 0%, transparent 100%),
        radial-gradient(1px 1px at 30% 70%, rgba(255,255,255,.35) 0%, transparent 100%),
        radial-gradient(1px 1px at 55% 40%, rgba(255,255,255,.4) 0%, transparent 100%),
        radial-gradient(1px 1px at 75% 80%, rgba(255,255,255,.3) 0%, transparent 100%),
        radial-gradient(1px 1px at 90% 15%, rgba(255,255,255,.5) 0%, transparent 100%),
        radial-gradient(1px 1px at 20% 55%, rgba(255,255,255,.25) 0%, transparent 100%),
        radial-gradient(1px 1px at 60% 90%, rgba(255,255,255,.4) 0%, transparent 100%),
        radial-gradient(1px 1px at 80% 35%, rgba(255,255,255,.3) 0%, transparent 100%),
        radial-gradient(1.5px 1.5px at 45% 10%, rgba(180,220,255,.7) 0%, transparent 100%),
        radial-gradient(1.5px 1.5px at 85% 60%, rgba(180,220,255,.5) 0%, transparent 100%);
      pointer-events: none;
      z-index: 0;
    }
    header {
      position: relative; z-index: 10;
      border-bottom: 1px solid var(--border);
      background: linear-gradient(to bottom, rgba(0,212,255,.05), transparent);
      padding: 1rem 2rem;
    }
    .logo-title {
      font-size: 1.6rem; font-weight: 700;
      letter-spacing: .15em; text-transform: uppercase;
      color: var(--accent); text-shadow: 0 0 18px var(--accent-glow);
    }
    .logo-sub {
      font-family: 'Share Tech Mono', monospace;
      font-size: .7rem; color: var(--text-muted); letter-spacing: .2em;
    }
    main { position: relative; z-index: 5; padding: 2rem; }
    .panel {
      background: var(--bg-panel); border: 1px solid var(--border);
      border-radius: 6px; padding: 1.5rem; margin-bottom: 1.5rem;
      box-shadow: 0 4px 32px rgba(0,0,0,.5);
    }
    .panel-title {
      font-size: .7rem; letter-spacing: .25em; text-transform: uppercase;
      color: var(--text-muted); margin-bottom: 1.2rem;
      display: flex; align-items: center; gap: .5rem;
    }
    .panel-title::after { content: ''; flex: 1; height: 1px; background: var(--border); }
    .status-badge {
      display: inline-flex; align-items: center; gap: .5rem;
      font-family: 'Share Tech Mono', monospace; font-size: 1.3rem;
      padding: .5rem 1.2rem; border-radius: 4px; border: 1px solid;
      text-transform: uppercase; letter-spacing: .1em; transition: all .4s ease;
    }
    .status-idle    { color: var(--text-muted); border-color: var(--text-muted); background: transparent; }
    .status-running { color: var(--green); border-color: var(--green); background: var(--green-dim); box-shadow: 0 0 20px rgba(0,255,136,.15); }
    .status-error   { color: var(--red);   border-color: var(--red);   background: var(--red-dim);   box-shadow: 0 0 20px rgba(255,60,90,.15); }
    .pulse-dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: currentColor; animation: pulse 1.2s ease-in-out infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50%       { opacity: .4; transform: scale(.6); }
    }
    .pipeline { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
    .stage-node {
      font-family: 'Share Tech Mono', monospace; font-size: .72rem;
      letter-spacing: .08em; padding: .3rem .7rem; border-radius: 3px;
      border: 1px solid var(--border); color: var(--text-muted);
      background: var(--bg-card); transition: all .3s ease;
      text-transform: uppercase; white-space: nowrap;
    }
    .stage-node.active {
      color: var(--accent); border-color: var(--accent);
      background: var(--accent-glow); box-shadow: 0 0 12px var(--accent-glow);
      animation: stage-pulse 1.5s ease-in-out infinite;
    }
    .stage-node.error { color: var(--red); border-color: var(--red); background: var(--red-dim); }
    @keyframes stage-pulse {
      0%, 100% { box-shadow: 0 0 8px var(--accent-glow); }
      50%       { box-shadow: 0 0 20px rgba(0,212,255,.4); }
    }
    .pipeline-arrow { color: var(--text-muted); font-size: .8rem; }
    .metric-box {
      background: var(--bg-card); border: 1px solid var(--border);
      border-radius: 4px; padding: 1rem; text-align: center;
    }
    .metric-value {
      font-family: 'Share Tech Mono', monospace; font-size: 2.2rem;
      color: var(--accent); line-height: 1; text-shadow: 0 0 12px var(--accent-glow);
    }
    .metric-label {
      font-size: .7rem; letter-spacing: .15em; text-transform: uppercase;
      color: var(--text-muted); margin-top: .3rem;
    }
    .btn-rosace {
      font-family: 'Rajdhani', sans-serif; font-weight: 700;
      font-size: 1rem; letter-spacing: .12em; text-transform: uppercase;
      border-radius: 4px; padding: .65rem 2rem; border: 2px solid; transition: all .2s ease;
    }
    .btn-start { color: var(--green); border-color: var(--green); background: transparent; }
    .btn-start:hover:not(:disabled) { background: var(--green-dim); box-shadow: 0 0 18px rgba(0,255,136,.3); color: var(--green); }
    .btn-start:disabled { opacity: .3; cursor: not-allowed; color: var(--green); border-color: var(--green); background: transparent; }
    .btn-stop  { color: var(--red);   border-color: var(--red);   background: transparent; }
    .btn-stop:hover:not(:disabled)  { background: var(--red-dim);   box-shadow: 0 0 18px rgba(255,60,90,.3);   color: var(--red); }
    .btn-stop:disabled  { opacity: .3; cursor: not-allowed; color: var(--red); border-color: var(--red); background: transparent; }
    .log-terminal {
      background: #06090f; border: 1px solid var(--border); border-radius: 4px;
      padding: 1rem; height: 200px; overflow-y: auto;
      font-family: 'Share Tech Mono', monospace; font-size: .78rem; line-height: 1.6;
    }
    .log-line { display: flex; gap: .8rem; animation: logfade .3s ease; }
    @keyframes logfade {
      from { opacity: 0; transform: translateY(4px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    .log-time  { color: var(--text-muted); flex-shrink: 0; }
    .log-info  { color: var(--text); }
    .log-ok    { color: var(--green); }
    .log-warn  { color: var(--amber); }
    .log-error { color: var(--red); }
    .refresh-ring {
      display: inline-block; width: 14px; height: 14px;
      border: 2px solid var(--text-muted); border-top-color: var(--accent);
      border-radius: 50%; animation: spin 1s linear infinite; vertical-align: middle;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    footer {
      position: relative; z-index: 5; border-top: 1px solid var(--border);
      padding: .8rem 2rem; font-family: 'Share Tech Mono', monospace;
      font-size: .65rem; color: var(--text-muted); letter-spacing: .1em;
      display: flex; justify-content: space-between; align-items: center;
    }
    .api-status-dot {
      width: 8px; height: 8px; border-radius: 50%; display: inline-block;
      background: var(--text-muted); transition: background .3s;
    }
    .api-status-dot.ok    { background: var(--green); box-shadow: 0 0 6px var(--green); }
    .api-status-dot.error { background: var(--red);   box-shadow: 0 0 6px var(--red); }
    .obs-list { display: flex; flex-direction: column; gap: .3rem; }
    .obs-row {
      display: grid;
      grid-template-columns: 1.2rem 7rem 5rem 1fr;
      align-items: center; gap: .8rem;
      padding: .45rem .6rem; border-radius: 3px;
      background: var(--bg-card); border: 1px solid var(--border);
      font-family: 'Share Tech Mono', monospace; font-size: .78rem;
    }
    .obs-id   { color: var(--text-muted); font-size: .65rem; }
    .obs-target { color: var(--accent); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .obs-time { color: var(--text-muted); font-size: .7rem; }
    .obs-stages { display: flex; gap: .6rem; align-items: center; }
    .stage-icon { font-size: 1rem; position: relative; cursor: default; }
    .stage-icon .stage-tip {
      display: none; position: absolute; bottom: 125%; left: 50%; transform: translateX(-50%);
      background: #1a2a40; border: 1px solid var(--border); border-radius: 3px;
      padding: .2rem .4rem; font-size: .65rem; white-space: nowrap; z-index: 100;
      color: var(--text);
    }
    .stage-icon:hover .stage-tip { display: block; }
    @keyframes stage-spin { to { transform: rotate(360deg); } }
    .spin { display: inline-block; animation: stage-spin 1s linear infinite; }
    @media (max-width: 576px) {
      main, header, footer { padding-left: 1rem; padding-right: 1rem; }
      .metric-value { font-size: 1.6rem; }
    }
  </style>
</head>
<body>

<header class="d-flex justify-content-between align-items-center">
  <div>
    <div class="logo-title"><i class="bi bi-stars me-2"></i>Rosace</div>
    <div class="logo-sub">Orchestrateur Spectroscopique v0.5</div>
  </div>
  <div class="d-flex align-items-center gap-2">
    <span class="api-status-dot" id="apiDot"></span>
    <span class="logo-sub" id="apiLabel">API…</span>
    <span class="refresh-ring d-none ms-2" id="spinnerIcon"></span>
    <span class="logo-sub ms-1" id="lastUpdate">—</span>
  </div>
</header>

<main>
  <div class="row g-3">
    <div class="col-lg-8">

      <!-- Status -->
      <div class="panel">
        <div class="panel-title"><i class="bi bi-activity"></i> État du système</div>
        <div class="d-flex flex-wrap align-items-center gap-3 mb-3">
          <div id="statusBadge" class="status-badge status-idle">
            <span class="pulse-dot d-none" id="pulseDot"></span>
            <span id="statusText">—</span>
          </div>
        </div>
        <div class="pipeline" id="pipeline"></div>
      </div>

      <!-- Controls -->
      <div class="panel">
        <div class="panel-title"><i class="bi bi-sliders"></i> Contrôles</div>
        <div class="d-flex gap-3 flex-wrap">
          <button id="btnStart" class="btn-rosace btn-start" onclick="controlStart()">
            <i class="bi bi-play-fill me-1"></i> Démarrer
          </button>
          <button id="btnStop" class="btn-rosace btn-stop" onclick="controlStop()">
            <i class="bi bi-stop-fill me-1"></i> Arrêter
          </button>
        </div>
        <div id="controlMsg" class="mt-2"
          style="font-family:'Share Tech Mono',monospace;font-size:.78rem;min-height:1.2rem;"></div>
      </div>

      <!-- Log -->
      <div class="panel">
        <div class="panel-title"><i class="bi bi-terminal"></i> Journal</div>
        <div class="log-terminal" id="logTerminal"></div>
      </div>

      <!-- Observations -->
      <div class="panel">
        <div class="panel-title"><i class="bi bi-table"></i> Observations</div>
        <div class="obs-list" id="obsList">
          <span style="color:var(--text-muted);font-size:.8rem">Aucune observation.</span>
        </div>
      </div>

    </div>

    <!-- Metrics + Legend -->
    <div class="col-lg-4">
      <div class="panel">
        <div class="panel-title"><i class="bi bi-bar-chart-line"></i> Métriques</div>
        <div class="row g-2">
          <div class="col-6">
            <div class="metric-box">
              <div class="metric-value" id="metricCompleted">—</div>
              <div class="metric-label">Observations</div>
            </div>
          </div>
          <div class="col-6">
            <div class="metric-box">
              <div class="metric-value" id="metricRunning">—</div>
              <div class="metric-label">En cours</div>
            </div>
          </div>
          <div class="col-6">
            <div class="metric-box">
              <div class="metric-value" id="metricPostObs">—</div>
              <div class="metric-label">Post-obs (fond)</div>
            </div>
          </div>
          <div class="col-12">
            <div class="metric-box">
              <div class="metric-value" id="metricStage"
                style="font-size:1rem;letter-spacing:.08em;">—</div>
              <div class="metric-label">Stage actif</div>
            </div>
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-title"><i class="bi bi-diagram-3"></i> Séquence</div>
        <div class="d-flex flex-column gap-2" id="stageLegend"></div>
      </div>
    </div>

  </div>
</main>

<footer>
  <span>ROSACE // ST-PANCRASSE</span>
  <span id="clockDisplay">—</span>
</footer>

<script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/js/bootstrap.bundle.min.js"></script>
<script>
// Toutes les requêtes passent par le proxy Python (/api/...)
const API = '/api';
let lastStage = null;

const STAGES = [
  { key: 'idle',          label: 'IDLE' },
  { key: 'init_session',  label: 'INIT' },
  { key: 'scheduler',     label: 'SCHEDULER' },
  { key: 'sequencer',     label: 'SEQUENCER' },
  { key: 'reduction',     label: 'REDUCTION' },
  { key: 'sharing',       label: 'SHARING' },
  { key: 'archive',       label: 'ARCHIVE' },
  { key: 'close_session', label: 'CLOSE' },
  { key: 'error',         label: 'ERROR' },
];

const STAGE_DESC = {
  idle: 'Système en attente', init_session: 'Initialisation',
  scheduler: 'Sélection de la cible', sequencer: 'Acquisition des spectres',
  reduction: 'Réduction des données (fond)', sharing: 'Partage (fond)',
  archive: 'Archivage (fond)', close_session: 'Fermeture de session', error: 'Erreur',
};

function buildPipeline() {
  const pipe   = document.getElementById('pipeline');
  const legend = document.getElementById('stageLegend');
  pipe.innerHTML = ''; legend.innerHTML = '';
  STAGES.forEach((s, i) => {
    if (s.key !== 'error') {
      if (i > 0) {
        const a = document.createElement('span');
        a.className = 'pipeline-arrow'; a.textContent = '›'; pipe.appendChild(a);
      }
      const n = document.createElement('span');
      n.className = 'stage-node'; n.id = `stage-${s.key}`; n.textContent = s.label;
      pipe.appendChild(n);
    }
    const row = document.createElement('div');
    row.className = 'd-flex align-items-center gap-2';
    row.innerHTML = `
      <span class="stage-node" id="legend-${s.key}"
        style="font-size:.65rem;padding:.2rem .5rem;">${s.label}</span>
      <span style="font-size:.75rem;color:var(--text-muted);">${STAGE_DESC[s.key] || ''}</span>`;
    legend.appendChild(row);
  });
}

function applyStatus(data) {
  const { stage, is_running: running, completed_count: completed, pending_post_obs_count: postObs } = data;
  document.getElementById('metricCompleted').textContent = completed ?? '—';
  document.getElementById('metricRunning').textContent   = running ? 'OUI' : 'NON';
  document.getElementById('metricPostObs').textContent   = postObs ?? '0';
  document.getElementById('metricStage').textContent     = stage?.toUpperCase() ?? '—';

  STAGES.forEach(s => {
    ['stage', 'legend'].forEach(pfx => {
      const el = document.getElementById(`${pfx}-${s.key}`);
      if (!el) return;
      const isActive = s.key === stage;
      const isErr    = stage === 'error' && s.key === 'error';
      el.classList.toggle('active', isActive && !isErr);
      el.classList.toggle('error',  isErr && isActive);
    });
  });

  const badge = document.getElementById('statusBadge');
  const txt   = document.getElementById('statusText');
  const dot   = document.getElementById('pulseDot');
  badge.className = 'status-badge';
  dot.classList.add('d-none');
  if (stage === 'error') {
    badge.classList.add('status-error'); txt.textContent = 'ERREUR';
  } else if (running) {
    badge.classList.add('status-running');
    dot.classList.remove('d-none'); txt.textContent = stage?.toUpperCase();
  } else {
    badge.classList.add('status-idle'); txt.textContent = 'IDLE';
  }
  document.getElementById('btnStart').disabled = running;
  document.getElementById('btnStop').disabled  = !running;

  if (stage !== lastStage) {
    if (lastStage !== null) addLog(`Stage → ${stage?.toUpperCase()}`, running ? 'ok' : 'info');
    lastStage = stage;
  }
}

async function fetchStatus() {
  document.getElementById('spinnerIcon').classList.remove('d-none');
  try {
    const res = await fetch(`${API}/status`, { signal: AbortSignal.timeout(5000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    applyStatus(data);
    setApiIndicator(true);
    document.getElementById('lastUpdate').textContent =
      'MAJ ' + new Date().toLocaleTimeString('fr-FR');
  } catch (err) {
    setApiIndicator(false);
    addLog(`/status inaccessible — ${err.message}`, 'error');
    document.getElementById('lastUpdate').textContent = 'ERREUR';
  } finally {
    document.getElementById('spinnerIcon').classList.add('d-none');
  }
}

function setApiIndicator(ok) {
  const dot   = document.getElementById('apiDot');
  const label = document.getElementById('apiLabel');
  dot.className = `api-status-dot ${ok ? 'ok' : 'error'}`;
  label.textContent = ok ? 'API connectée' : 'API hors ligne';
}

async function controlStart() {
  document.getElementById('btnStart').disabled = true;
  setControlMsg('Envoi de la commande START…', 'info');
  try {
    const res  = await fetch(`${API}/control/start`, { method: 'POST', signal: AbortSignal.timeout(5000) });
    const data = await res.json();
    addLog(`START → ${data.message}`, 'ok');
    setControlMsg(data.message, 'ok');
    await fetchStatus();
  } catch (err) {
    addLog(`Erreur START : ${err.message}`, 'error');
    setControlMsg(`Erreur : ${err.message}`, 'error');
    document.getElementById('btnStart').disabled = false;
  }
}

async function controlStop() {
  document.getElementById('btnStop').disabled = true;
  setControlMsg('Envoi de la commande STOP…', 'warn');
  try {
    const res  = await fetch(`${API}/control/stop`, { method: 'POST', signal: AbortSignal.timeout(5000) });
    const data = await res.json();
    addLog(`STOP → ${data.message}`, 'warn');
    setControlMsg(data.message, 'warn');
    await fetchStatus();
  } catch (err) {
    addLog(`Erreur STOP : ${err.message}`, 'error');
    setControlMsg(`Erreur : ${err.message}`, 'error');
    document.getElementById('btnStop').disabled = false;
  }
}

function addLog(msg, type = 'info') {
  const el   = document.getElementById('logTerminal');
  const time = new Date().toLocaleTimeString('fr-FR');
  const line = document.createElement('div');
  line.className = 'log-line';
  line.innerHTML = `<span class="log-time">${time}</span><span class="log-${type}">${msg}</span>`;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
  while (el.children.length > 200) el.removeChild(el.firstChild);
}

function setControlMsg(msg, type = 'info') {
  const colors = { info: 'var(--text-muted)', ok: 'var(--green)', warn: 'var(--amber)', error: 'var(--red)' };
  const el = document.getElementById('controlMsg');
  el.textContent = msg;
  el.style.color = colors[type] ?? 'var(--text)';
  setTimeout(() => { el.textContent = ''; }, 6000);
}

function updateClock() {
  document.getElementById('clockDisplay').textContent =
    new Date().toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'medium' });
}

// ── Observations table ────────────────────────────────────────
const OBS_STAGES_KEYS = ['scheduler', 'sequencer', 'reduction', 'sharing', 'archive'];
const OBS_STAGE_LABEL = {
  scheduler: 'SCHED', sequencer: 'SEQ', reduction: 'RED', sharing: 'SHARE', archive: 'ARC'
};

function stageIcon(key, status, done_at) {
  const label = OBS_STAGE_LABEL[key] || key;
  const tip = done_at ? `${label}: ${status} (${new Date(done_at).toLocaleTimeString('fr-FR')})` : `${label}: ${status}`;
  let icon;
  if (status === 'done')    icon = `<i class="bi bi-check-circle-fill" style="color:var(--green)"></i>`;
  else if (status === 'running') icon = `<i class="bi bi-arrow-repeat spin" style="color:var(--amber)"></i>`;
  else if (status === 'error')   icon = `<i class="bi bi-x-circle-fill" style="color:var(--red)"></i>`;
  else                           icon = `<i class="bi bi-circle" style="color:var(--text-muted)"></i>`;
  return `<span class="stage-icon">${icon}<span class="stage-tip">${tip}</span></span>`;
}

function renderObsList(observations) {
  const container = document.getElementById('obsList');
  if (!observations || !observations.length) {
    container.innerHTML = '<span style="color:var(--text-muted);font-size:.8rem">Aucune observation.</span>';
    return;
  }
  container.innerHTML = observations.map(obs => {
    const time = new Date(obs.created_at).toLocaleTimeString('fr-FR');
    const icons = OBS_STAGES_KEYS.map(k => stageIcon(k, obs.stages[k].status, obs.stages[k].done_at)).join('');
    const errBadge = obs.error_message
      ? `<span title="${obs.error_message}" style="color:var(--red);font-size:.7rem;margin-left:.5rem">⚠ ERR</span>`
      : '';
    return `
      <div class="obs-row">
        <span class="obs-id">${obs.id}</span>
        <span class="obs-target">${obs.target_name}</span>
        <span class="obs-time">${time}</span>
        <span class="obs-stages">${icons}${errBadge}</span>
      </div>`;
  }).join('');
}

async function fetchRecentObs() {
  try {
    const res = await fetch(`${API}/observations/recent?limit=15`, { signal: AbortSignal.timeout(5000) });
    if (!res.ok) return;
    renderObsList(await res.json());
  } catch {}
}

buildPipeline();
addLog('Interface Rosace initialisée', 'info');
fetchStatus();
fetchRecentObs();
setInterval(fetchStatus, 5000);
setInterval(fetchRecentObs, 5000);
setInterval(updateClock, 1000);
updateClock();
</script>
</body>
</html>
"""

# ─────────────────────────────────────────────────────────────
# Proxy HTTP handler
# ─────────────────────────────────────────────────────────────

class RosaceHandler(http.server.BaseHTTPRequestHandler):

    # Rempli par le serveur au démarrage
    api_base: str = ""

    # ── Routing ───────────────────────────────────────────────

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_html()
        elif self.path.startswith("/api/"):
            self._proxy("GET")
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._proxy("POST")
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    # ── HTML ──────────────────────────────────────────────────

    def _serve_html(self):
        body = HTML.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── Proxy ─────────────────────────────────────────────────

    def _proxy(self, method: str):
        # /api/foo/bar  →  http://localhost:8000/foo/bar
        upstream_path = self.path[len("/api"):]  # retire le préfixe /api
        url = self.api_base + upstream_path

        # Corps de la requête (pour POST)
        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""

        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except Exception as e:
            err = json.dumps({"detail": str(e)}).encode()
            self.send_response(HTTPStatus.BAD_GATEWAY)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    # ── Silence les logs HTTP dans le terminal ────────────────

    def log_message(self, fmt, *args):
        logging.debug(f"[HTTP] {fmt % args}")


# ─────────────────────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Interface graphique Rosace (serveur HTTP + proxy API)"
    )
    parser.add_argument("--ui-port",   type=int, default=DEFAULT_UI_PORT,
                        help=f"Port de l'interface web (défaut : {DEFAULT_UI_PORT})")
    parser.add_argument("--api-host",  type=str, default=DEFAULT_API_HOST,
                        help=f"Hôte de l'API Rosace (défaut : {DEFAULT_API_HOST})")
    parser.add_argument("--api-port",  type=int, default=DEFAULT_API_PORT,
                        help=f"Port de l'API Rosace (défaut : {DEFAULT_API_PORT})")
    parser.add_argument("--no-browser", action="store_true",
                        help="Ne pas ouvrir le navigateur automatiquement")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Injecte la config dans le handler
    RosaceHandler.api_base = f"http://{args.api_host}:{args.api_port}"

    ui_url = f"http://localhost:{args.ui_port}/"

    server = http.server.ThreadingHTTPServer(("", args.ui_port), RosaceHandler)

    logging.info("─" * 55)
    logging.info("  Rosace GUI — serveur démarré")
    logging.info(f"  Interface  : {ui_url}")
    logging.info(f"  API cible  : {RosaceHandler.api_base}")
    logging.info("  Arrêt      : Ctrl+C")
    logging.info("─" * 55)

    if not args.no_browser:
        # Ouvre le navigateur dans un thread séparé (légère temporisation)
        threading.Timer(0.8, lambda: webbrowser.open(ui_url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Arrêt du serveur GUI.")
        server.shutdown()


if __name__ == "__main__":
    main()

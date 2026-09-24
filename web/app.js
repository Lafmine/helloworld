// ZhukoGPT для iPhone: камера → ИИ → ответ. Настройки и история хранятся только на телефоне.
import * as api from "./api.js";
import { renderMarkdown, latexToPlain } from "./mathfmt.js";

const $ = (id) => document.getElementById(id);
const STORE_KEY = "zhukogpt";
const MAX_SIDE = 1600;  // больше не нужно моделям, а меньше — быстрее и дешевле по лимитам
const HISTORY_LIMIT = 30;

let CFG;  // web/config.json, собранный из zhukogpt/config.py
let state;
let stream = null;
let request = null;  // текущий запрос к ИИ
let lastAnswer = null;  // для уточняющих вопросов
let busyTimer = null;

// ---------- настройки ----------
function defaults() {
  const providers = {};
  for (const [pid, p] of Object.entries(CFG.providers)) {
    providers[pid] = { api_key: "", model: p.default_models[0], models: [...p.default_models] };
  }
  return {
    provider: CFG.default_provider, providers, prompts: structuredClone(CFG.prompts), active_prompt: 0,
    accurate: false, accurate_warned: false, fallback_services: true,
  };
}

function loadState() {
  const s = defaults();
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch { /* приватный режим и т. п. */ }
  if (!saved || typeof saved !== "object") return s;
  for (const [pid, p] of Object.entries(saved.providers || {})) {
    if (!s.providers[pid] || !p) continue;
    if (typeof p.api_key === "string") s.providers[pid].api_key = p.api_key;
    if (typeof p.model === "string" && p.model) s.providers[pid].model = p.model;
    if (Array.isArray(p.models) && p.models.length) s.providers[pid].models = p.models.filter((m) => typeof m === "string");
  }
  if (saved.provider in CFG.providers) s.provider = saved.provider;
  if (Array.isArray(saved.prompts)) {
    const prompts = saved.prompts.filter((p) => p && String(p.text || "").trim())
      .map((p) => ({ name: String(p.name || "Без названия"), text: String(p.text) }));
    if (prompts.length) s.prompts = prompts;
  }
  for (const k of ["accurate", "accurate_warned", "fallback_services"]) if (typeof saved[k] === "boolean") s[k] = saved[k];
  if (Number.isInteger(saved.active_prompt)) s.active_prompt = saved.active_prompt;
  s.active_prompt = Math.min(Math.max(s.active_prompt, 0), s.prompts.length - 1);
  return s;
}

function saveState() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch { /* нет места или запрещено */ }
}

const current = () => state.providers[state.provider];
const activePrompt = () => state.prompts[state.active_prompt];

/** Выбранный сервис первым, затем остальные с ключами: [[provider, key, models], …]. */
function servicesOrder() {
  const entry = (pid) => {
    const p = state.providers[pid];
    return [pid, p.api_key, [p.model, ...p.models.filter((m) => m !== p.model)]];
  };
  const order = [entry(state.provider)];
  if (state.fallback_services) {
    for (const pid of Object.keys(CFG.providers)) {
      if (pid !== state.provider && state.providers[pid].api_key) order.push(entry(pid));
    }
  }
  return order;
}

// ---------- экраны ----------
function show(id) {
  for (const s of document.querySelectorAll(".screen")) s.classList.toggle("active", s.id === id);
  if (id === "cam") startCamera(); else stopCamera();
}

function refreshCamUi() {
  $("prompt-name").textContent = activePrompt().name;
  $("btn-accurate").classList.toggle("on", state.accurate);
  $("btn-last").classList.toggle("hidden", !lastAnswer);
  const hint = $("cam-hint");
  if (!current().api_key) {
    hint.textContent = `Сначала вставь ключ ${CFG.providers[state.provider].name} в ⚙`;
    hint.classList.remove("hidden");
  } else {
    hint.classList.add("hidden");
  }
}

// ---------- камера ----------
async function startCamera() {
  if (stream || document.hidden) return;
  $("cam-error").classList.add("hidden");
  if (!navigator.mediaDevices?.getUserMedia) {
    camError("Этот браузер не даёт доступ к камере из страницы. Можно сфоткать через камеру iPhone.");
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: "environment" }, width: { ideal: 2560 }, height: { ideal: 1920 } },
    });
    if (!$("cam").classList.contains("active") || document.hidden) { stopCamera(); return; }
    $("video").srcObject = stream;
    await $("video").play().catch(() => {});
  } catch (e) {
    stream = null;
    camError(e?.name === "NotAllowedError"
      ? "Доступ к камере запрещён. Разреши его: Настройки iPhone → Safari → Камера → «Разрешить» — или сфоткай через камеру iPhone."
      : "Не получилось включить камеру. Можно сфоткать через камеру iPhone.");
  }
}

function stopCamera() {
  if (stream) for (const t of stream.getTracks()) t.stop();
  stream = null;
  $("video").srcObject = null;
}

function camError(text) {
  $("cam-error-text").textContent = text;
  $("cam-error").classList.remove("hidden");
}

/** Картинка → JPEG data URL не больше maxSide по длинной стороне. */
function toJpeg(source, width, height, maxSide = MAX_SIDE, quality = 0.85) {
  const scale = Math.min(1, maxSide / Math.max(width, height));
  const canvas = $("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", quality);
}

function snap() {
  const video = $("video");
  if (!stream || !video.videoWidth) return;
  $("cam").classList.remove("flash");
  void $("cam").offsetWidth;
  $("cam").classList.add("flash");
  navigator.vibrate?.(30);
  send(toJpeg(video, video.videoWidth, video.videoHeight));
}

async function fromFile(input) {
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    send(toJpeg(bitmap, bitmap.width, bitmap.height));
  } catch {
    showAnswerScreen(null);
    showError("Не получилось открыть это фото. Попробуй другое.");
  }
}

// ---------- запрос ----------
function setBusy(busy) {
  $("btn-stop").classList.toggle("hidden", !busy);
  $("btn-copy").classList.toggle("hidden", busy);
  $("ask-bar").classList.toggle("hidden", busy || !lastAnswer);
  $("shutter").disabled = busy;
  clearInterval(busyTimer);
  if (busy) {
    const started = Date.now();
    let dots = 0;
    const tick = () => {
      dots = (dots + 1) % 4;
      const sec = Math.floor((Date.now() - started) / 1000);
      $("ans-card").innerHTML = `<div class="thinking">🪲 Думаю${".".repeat(dots)} ${sec} с</div>`;
    };
    tick();
    busyTimer = setInterval(tick, 400);
  }
}

function showAnswerScreen(photo) {
  $("ans-photo").classList.toggle("hidden", !photo);
  if (photo) $("ans-photo").src = photo;
  $("ans-accurate").classList.toggle("hidden", !state.accurate);
  show("ans");
}

function renderAnswer(text, question) {
  const full = question ? `> ${question}\n\n${text}` : text;
  $("ans-card").innerHTML = renderMarkdown(full);
  $("ans-card").dataset.copy = latexToPlain(text);
  $("btn-copy").disabled = false;
}

function showError(text) {
  $("ans-card").innerHTML = `<div class="error-title">⚠ Ошибка</div>${renderMarkdown(text)}`;
  $("ans-card").dataset.copy = "";
  $("btn-copy").disabled = true;
}

function doneStatus(answer) {
  const service = CFG.providers[answer.provider].name.split(" (")[0];
  const switched = answer.provider === state.provider ? "" : " (основной сервис не ответил)";
  return `Готово • ${answer.model.split("/").pop()} • ${service}${switched}`;
}

async function send(photo) {
  if (request) return;
  if (!current().api_key) {
    showAnswerScreen(photo);
    showError(`Сначала вставь API-ключ **${CFG.providers[state.provider].name}** в ⚙ ` +
      `(${CFG.providers[state.provider].keys_page}).`);
    $("ans-status").textContent = "";
    return;
  }
  lastAnswer = null;
  showAnswerScreen(photo);
  setBusy(true);
  const services = servicesOrder();
  $("ans-status").textContent = `Модель: ${services[0][2][0].split("/").pop()}`;
  request = new api.Request();
  const req = request;
  try {
    const answer = await api.askServices(services, [photo], {
      req, promptText: activePrompt().text, accurate: state.accurate,
      onStatus: (s) => { if (request === req) $("ans-status").textContent = s; },
      onTry: (m) => { if (request === req) $("ans-status").textContent = `Прошлая модель занята, пробую ${m.split("/").pop()}`; },
      onService: (p) => { if (request === req) $("ans-status").textContent = `${api.providerName(p)}: пробую этот сервис…`; },
    });
    if (request !== req) return;
    lastAnswer = answer;
    request = null;
    setBusy(false);
    renderAnswer(answer.text);
    $("ans-status").textContent = doneStatus(answer);
    addHistory(photo, answer).catch(() => {});
  } catch (e) {
    if (request !== req) return;
    request = null;
    setBusy(false);
    if (e.kind === "cancelled") {
      $("ans-card").innerHTML = renderMarkdown("Запрос остановлен. Сфоткай ещё раз.");
      $("ans-status").textContent = "";
    } else {
      showError(e instanceof api.ApiError ? e.fullText() : `Непредвиденная ошибка: ${e}`);
      $("ans-status").textContent = "";
    }
  }
}

async function followup() {
  const question = $("ask-input").value.trim();
  if (!question || !lastAnswer || request) return;
  $("ask-input").value = "";
  $("ask-input").blur();
  const key = state.providers[lastAnswer.provider].api_key;
  setBusy(true);
  request = new api.Request();
  const req = request;
  try {
    const answer = await api.askFollowup(lastAnswer, key, question, req, state.accurate);
    if (request !== req) return;
    lastAnswer = answer;
    request = null;
    setBusy(false);
    renderAnswer(answer.text, question);
    $("ans-status").textContent = doneStatus(answer);
  } catch (e) {
    if (request !== req) return;
    request = null;
    setBusy(false);
    if (e.kind === "cancelled") $("ans-card").innerHTML = renderMarkdown("Запрос остановлен.");
    else showError(e instanceof api.ApiError ? e.fullText() : `Непредвиденная ошибка: ${e}`);
    $("ask-bar").classList.remove("hidden");
  }
}

function stopRequest() {
  if (!request) return;
  const req = request;
  request = null;
  req.cancel();
  setBusy(false);
  $("ans-card").innerHTML = renderMarkdown("Запрос остановлен. Сфоткай ещё раз.");
  $("ans-status").textContent = "";
}

// ---------- история (IndexedDB) ----------
function db() {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("zhukogpt", 1);
    open.onupgradeneeded = () => open.result.createObjectStore("history", { keyPath: "id", autoIncrement: true });
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
}

async function historyStore(mode) {
  return (await db()).transaction("history", mode).objectStore("history");
}

const done = (r) => new Promise((resolve, reject) => { r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });

async function addHistory(photo, answer) {
  const img = new Image();
  img.src = photo;
  await img.decode();
  const small = toJpeg(img, img.naturalWidth, img.naturalHeight, 900, 0.8);
  const store = await historyStore("readwrite");
  await done(store.add({ photo: small, text: answer.text, model: answer.model, provider: answer.provider,
    prompt: activePrompt().name, time: Date.now() }));
  const keys = await done(store.getAllKeys());
  for (const k of keys.slice(0, Math.max(0, keys.length - HISTORY_LIMIT))) store.delete(k);
}

async function openHistory() {
  show("hist");
  const list = $("hist-list");
  list.innerHTML = "";
  let items = [];
  try { items = (await done((await historyStore("readonly")).getAll())).reverse(); } catch { /* нет IndexedDB */ }
  if (!items.length) { list.innerHTML = '<div class="empty">Здесь появятся твои ответы</div>'; return; }
  for (const it of items) {
    const btn = document.createElement("button");
    btn.className = "hist-item glass";
    const first = latexToPlain(it.text).replace(/[*#>`_]/g, "").split("\n").find((l) => l.trim()) || "";
    const when = new Date(it.time).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    btn.innerHTML = `<img alt=""><div><div class="t"></div><div class="m"></div></div>`;
    btn.querySelector("img").src = it.photo;
    btn.querySelector(".t").textContent = first;
    btn.querySelector(".m").textContent = `${when} • ${it.prompt} • ${it.model.split("/").pop()}`;
    btn.onclick = () => {
      lastAnswer = null;  // из истории без переписки: уточнять можно только свежий ответ
      showAnswerScreen(it.photo);
      $("ans-accurate").classList.add("hidden");
      setBusy(false);
      renderAnswer(it.text);
      $("ans-status").textContent = `Из истории • ${it.model.split("/").pop()}`;
    };
    list.appendChild(btn);
  }
}

// ---------- промпты ----------
function openPromptSheet() {
  const list = $("sheet-list");
  list.innerHTML = "";
  state.prompts.forEach((p, i) => {
    const b = document.createElement("button");
    b.className = "sheet-item" + (i === state.active_prompt ? " cur" : "");
    b.textContent = (i === state.active_prompt ? "✓ " : "") + p.name;
    b.onclick = () => { state.active_prompt = i; saveState(); closeSheet(); refreshCamUi(); };
    list.appendChild(b);
  });
  $("sheet").classList.remove("hidden");
}
const closeSheet = () => $("sheet").classList.add("hidden");

/** Диалог: title, узел с содержимым, текст кнопки OK. Возвращает true/false. */
function modal(title, body, okText, { countdown = 0 } = {}) {
  return new Promise((resolve) => {
    $("modal-title").textContent = title;
    $("modal-body").replaceChildren(body);
    const ok = $("modal-ok");
    let left = countdown;
    let timer = null;
    const update = () => { ok.textContent = left > 0 ? `${okText} (${left})` : okText; ok.disabled = left > 0; };
    update();
    if (countdown) timer = setInterval(() => { left -= 1; update(); if (left <= 0) clearInterval(timer); }, 1000);
    const finish = (v) => { clearInterval(timer); $("modal").classList.add("hidden"); resolve(v); };
    ok.onclick = () => finish(true);
    $("modal-cancel").onclick = () => finish(false);
    $("modal").classList.remove("hidden");
  });
}

async function editPrompt(index) {
  const p = index === null ? { name: "", text: "" } : state.prompts[index];
  const wrap = document.createElement("div");
  wrap.className = "form";
  wrap.innerHTML = '<input type="text" placeholder="Название, например «Химия»"><textarea placeholder="Что ИИ должен сделать с фото…"></textarea>' +
    '<div class="note">Правила «отвечай по-русски» и «без LaTeX» добавятся сами.</div>';
  const [name, text] = [wrap.querySelector("input"), wrap.querySelector("textarea")];
  name.value = p.name;
  text.value = p.text;
  if (!(await modal(index === null ? "Новый промпт" : "Изменить промпт", wrap, "Сохранить"))) return;
  if (!text.value.trim()) return;
  const item = { name: name.value.trim() || "Без названия", text: text.value.trim() };
  if (index === null) { state.prompts.push(item); state.active_prompt = state.prompts.length - 1; }
  else state.prompts[index] = item;
  saveState();
  fillSettings();
}

// ---------- точный режим ----------
async function setAccurate(on) {
  if (on && !state.accurate_warned) {
    const p = document.createElement("div");
    p.innerHTML = "<p>ИИ будет решать внимательнее: думать дольше и перепроверять вычисления.</p>" +
      "<p>⏳ Ответ может приходить заметно дольше — 10–30 секунд, иногда до полутора минут.</p>" +
      "<p>📉 Бесплатные лимиты тратятся быстрее.</p><p>Выключить можно той же кнопкой 🎯.</p>";
    if (!(await modal("🎯 Точный режим", p, "Я понял", { countdown: 5 }))) { fillSettings(); return; }
    state.accurate_warned = true;
  }
  state.accurate = on;
  saveState();
  refreshCamUi();
  fillSettings();
}

// ---------- экран настроек ----------
function fillModels() {
  const p = current();
  const sel = $("model");
  sel.innerHTML = "";
  const models = p.models.includes(p.model) ? p.models : [p.model, ...p.models];
  for (const m of models) {
    const o = document.createElement("option");
    o.value = m;
    o.textContent = `${api.hasVision(state.provider, m) ? "👁" : "🔤"} ${m}`;
    sel.appendChild(o);
  }
  sel.value = p.model;
  $("model-hint").textContent = api.hasVision(state.provider, p.model)
    ? "👁 Модель видит фото — оно отправляется как есть"
    : "🔤 Модель без зрения: фото сначала перепишет модель со зрением этого сервиса";
}

function fillSettings() {
  $("version").textContent = `v${CFG.version}`;
  const info = CFG.providers[state.provider];
  $("provider").value = state.provider;
  $("api-key").value = current().api_key;
  $("api-key").placeholder = `API-ключ ${info.name.split(" (")[0]} (${info.key_placeholder})`;
  $("key-link").href = info.keys_page;
  $("key-link").textContent = info.keys_page.replace(/^https?:\/\//, "");
  $("key-status").textContent = "";
  fillModels();
  const sel = $("prompt");
  sel.innerHTML = "";
  state.prompts.forEach((p, i) => { const o = document.createElement("option"); o.value = i; o.textContent = p.name; sel.appendChild(o); });
  sel.value = state.active_prompt;
  $("prompt-preview").textContent = activePrompt().text;
  $("prompt-del").disabled = state.prompts.length <= 1;
  $("fallback").checked = state.fallback_services;
  $("accurate-set").checked = state.accurate;
}

function bindSettings() {
  const sel = $("provider");
  for (const [pid, p] of Object.entries(CFG.providers)) {
    const o = document.createElement("option");
    o.value = pid;
    o.textContent = p.name;
    sel.appendChild(o);
  }
  sel.onchange = () => { state.provider = sel.value; saveState(); fillSettings(); };
  $("api-key").oninput = () => { current().api_key = $("api-key").value.trim(); saveState(); $("key-status").textContent = ""; };
  $("key-eye").onclick = () => { const k = $("api-key"); k.type = k.type === "password" ? "text" : "password"; };
  $("key-check").onclick = async () => {
    const st = $("key-status");
    st.className = "note";
    st.textContent = "Проверяю…";
    const text = await api.checkKey(state.provider, current().api_key);
    st.textContent = text;
    st.className = "note " + (text.startsWith("✓") ? "key-ok" : text.startsWith("✗") ? "key-bad" : "");
  };
  $("model").onchange = () => { current().model = $("model").value; saveState(); fillModels(); };
  $("models-refresh").onclick = async () => {
    const btn = $("models-refresh");
    btn.disabled = true;
    btn.textContent = "…";
    try {
      const models = await api.fetchModels(state.provider, current().api_key);
      if (models.length) {
        current().models = models;
        if (!models.includes(current().model)) current().model = models[0];
        saveState();
        $("model-hint").textContent = `Загружено моделей: ${models.length}`;
      }
      fillModels();
    } catch (e) {
      $("model-hint").textContent = `Не удалось обновить: ${e.message}`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Обновить";
    }
  };
  $("prompt").onchange = () => { state.active_prompt = Number($("prompt").value); saveState(); fillSettings(); };
  $("prompt-edit").onclick = () => editPrompt(state.active_prompt);
  $("prompt-new").onclick = () => editPrompt(null);
  $("prompt-del").onclick = async () => {
    const p = document.createElement("p");
    p.textContent = `Удалить промпт «${activePrompt().name}»?`;
    if (state.prompts.length <= 1 || !(await modal("Удалить промпт", p, "Удалить"))) return;
    state.prompts.splice(state.active_prompt, 1);
    state.active_prompt = Math.max(0, state.active_prompt - 1);
    saveState();
    fillSettings();
  };
  $("fallback").onchange = () => { state.fallback_services = $("fallback").checked; saveState(); };
  $("accurate-set").onchange = () => setAccurate($("accurate-set").checked);
}

// ---------- запуск ----------
async function main() {
  CFG = await (await fetch("config.json", { cache: "no-cache" })).json();
  api.init(CFG);
  state = loadState();
  bindSettings();

  $("shutter").onclick = snap;
  $("file-gallery").onchange = (e) => fromFile(e.target);
  $("file-camera").onchange = (e) => fromFile(e.target);
  $("cam-retry").onclick = () => { stopCamera(); startCamera(); };
  $("btn-accurate").onclick = () => setAccurate(!state.accurate);
  $("btn-history").onclick = openHistory;
  $("btn-settings").onclick = () => { fillSettings(); show("set"); };
  $("btn-last").onclick = () => show("ans");
  $("prompt-chip").onclick = openPromptSheet;
  $("sheet-close").onclick = closeSheet;
  $("sheet").onclick = (e) => { if (e.target === $("sheet")) closeSheet(); };

  const toCamera = () => { refreshCamUi(); show("cam"); };
  $("ans-back").onclick = toCamera;
  $("ans-new").onclick = toCamera;
  $("set-back").onclick = toCamera;
  $("hist-back").onclick = toCamera;
  $("hist-clear").onclick = async () => {
    const p = document.createElement("p");
    p.textContent = "Удалить все сохранённые ответы с этого телефона?";
    if (!(await modal("Очистить историю", p, "Очистить"))) return;
    try { await done((await historyStore("readwrite")).clear()); } catch { /* нет IndexedDB */ }
    openHistory();
  };
  $("btn-stop").onclick = stopRequest;
  $("btn-copy").onclick = async () => {
    try {
      await navigator.clipboard.writeText($("ans-card").dataset.copy || "");
      $("btn-copy").textContent = "Скопировано ✓";
      setTimeout(() => { $("btn-copy").textContent = "Копировать"; }, 1500);
    } catch { /* нет доступа к буферу */ }
  };
  $("ask-send").onclick = followup;
  $("ask-input").onkeydown = (e) => { if (e.key === "Enter") followup(); };

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopCamera();
    else if ($("cam").classList.contains("active")) startCamera();
  });

  refreshCamUi();
  if (!current().api_key) { fillSettings(); show("set"); } else show("cam");
  if ("serviceWorker" in navigator && location.protocol === "https:") {
    navigator.serviceWorker.register(`sw.js?v=${CFG.version}`).catch(() => {});
  }
}

main().catch((e) => {
  document.body.innerHTML = `<p style="padding:40px 20px">Не удалось запустить ZhukoGPT: ${String(e)}</p>`;
});

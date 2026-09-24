// Запросы к ИИ-сервисам из браузера — порт zhukogpt/api.py (Groq, Gemini, OrcaRouter, OpenRouter, TeamoRouter).
// Все сервисы совместимы с API OpenAI и разрешают запросы со страниц (CORS). Ключ уходит только в сам сервис.

export const MODEL_DEADLINE = 45;  // секунд на одну модель, потом — следующая
export const ACCURATE_DEADLINE = 90;  // в точном режиме модель думает дольше
const MAX_FALLBACKS = 2;  // сколько запасных моделей пробовать после выбранной
const RETRYABLE = new Set(["upstream", "unavailable", "server", "timeout", "empty", "network"]);

const ORCA_VISION = ["glm-5.3-flash", "vision", "-vl"];
const ORCA_NOT_CHAT = ["orcaverify", "embed", "rerank", "tts", "whisper", "image-gen", "moderation"];
const GROQ_VISION = ["qwen3.8", "vision", "-vl", "scout", "maverick"];
const GROQ_NOT_CHAT = ["whisper", "orpheus", "guard", "safeguard", "allam", "tts", "distil"];
const GEMINI_NOT_CHAT = ["embedding", "tts", "image", "live", "audio", "aqa", "robotics", "computer-use"];
const EXCLUDE = ["safety", "guard"];

let CFG = null;
export function init(cfg) { CFG = cfg; }
export const providerName = (p) => CFG.providers[p].name;
const short = (model) => model.split("/").pop();

export class ApiError extends Error {
  constructor(kind, message, detail = "", status = null) {
    super(message);
    this.kind = kind;
    this.detail = detail;
    this.status = status;
    this.retryAfter = null;  // сколько секунд просит подождать сервис
    this.source = "сервиса";
  }
  fullText() {
    return this.detail ? `${this.message}\n\n*Подробности от ${this.source}: ${this.detail.replace(/[*_`[\]<>#]/g, "\\$&")}*`
      : this.message;
  }
}

export function hasVision(provider, model) {
  const name = model.toLowerCase();
  if (provider === "openrouter") return true;  // в списке OpenRouter только модели с картинками
  if (provider === "gemini") return name.startsWith("gemini");
  if (provider === "groq") return GROQ_VISION.some((w) => name.includes(w));
  if (provider === "orcarouter") return ORCA_VISION.some((w) => name.includes(w));
  return name.includes("vision") || name.includes("-vl") || name.endsWith("vl");
}

function headers(provider, key) {
  const h = { "Authorization": `Bearer ${key}`, "Content-Type": "application/json" };
  if (provider === "openrouter") h["X-Title"] = "ZhukoGPT";
  return h;
}

function systemPrompt(promptText, transcript) {
  const intro = transcript ? CFG.transcript_intro : CFG.image_intro;
  return `${intro}\n\nЗадача:\n${promptText.trim()}\n\n${CFG.common_rules}`;
}

/** Запрос с фото (data URL) или, для моделей без зрения, с переписанным заданием. */
export function buildPayload(provider, model, { images = null, text = null, promptText, accurate = false }) {
  promptText = promptText || CFG.prompts[0].text;
  if (accurate) promptText = promptText.trimEnd() + "\n\n" + CFG.accurate_addon;
  let messages;
  if (text !== null) {
    messages = [
      { role: "system", content: systemPrompt(promptText, true) },
      { role: "user", content: CFG.user_prompt_transcript + text },
    ];
  } else {
    const content = [{ type: "text", text: CFG.user_prompt.replace("скриншот", "фото") }];
    for (const url of images) content.push({ type: "image_url", image_url: { url } });
    messages = [{ role: "system", content: systemPrompt(promptText, false).replace("скриншот части экрана", "фото") },
      { role: "user", content }];
  }
  const payload = { model, messages };
  applyReasoning(payload, provider, model, accurate);
  return payload;
}

function applyReasoning(payload, provider, model, accurate) {
  const name = model.toLowerCase();
  if (provider === "groq" && name.includes("qwen")) {
    // Бесплатный Groq: не больше 1000 выходных токенов в минуту, а qwen по умолчанию может запросить 2048.
    payload.max_tokens = 700;
  }
  if (provider === "openrouter") {
    payload.reasoning = { effort: accurate ? "high" : "low", exclude: true };
  } else if (provider === "gemini" && accurate) {
    payload.reasoning_effort = "high";
  } else if (provider === "groq" && accurate) {
    if (name.includes("gpt-oss")) payload.reasoning_effort = "high";
    else if (name.includes("qwen")) { payload.reasoning_effort = "default"; payload.reasoning_format = "hidden"; }
  }
}

function errorDetail(error) {
  const parts = [];
  if (error.message) parts.push(String(error.message));
  const meta = error.metadata || {};
  if (meta.provider_name) parts.push(`провайдер: ${meta.provider_name}`);
  if (meta.raw) parts.push(String(meta.raw).slice(0, 300));
  return parts.join(" • ");
}

const isBalanceError = (error, text) =>
  `${error.type || ""} ${error.code || ""}`.toLowerCase().includes("insufficient_balance") ||
  text.includes("balance is insufficient");

/** Ошибка сервиса → ApiError с понятным русским текстом. */
export function classifyError(status, error, hdrs, provider) {
  const name = providerName(provider);
  const detail = errorDetail(error);
  const text = detail.toLowerCase();
  const meta = error.metadata || {};
  const code = String(error.code || "").toLowerCase();
  const reason = String(meta.reason || "").toLowerCase();
  if (code === "free_rate_limited" && (reason.includes("access_denied") || text.includes("not available to this account"))) {
    return new ApiError("free_access", `Бесплатные модели ${name} для твоего аккаунта ещё закрыты. Открой настройки ` +
      "профиля на orcarouter.ai и привяжи давний GitHub-аккаунт. После этого free-модели заработают.", detail, status);
  }
  if (code === "free_quota_exhausted") {
    return new ApiError("daily_limit", `Бесплатный лимит ${name} на сегодня закончился. Попробуй позже или выбери другой сервис в ⚙.`,
      detail, status);
  }
  if (isBalanceError(error, text)) {
    return new ApiError("balance", `На балансе ${name} нет денег. Даже бесплатные (free) модели там работают только ` +
      `при ненулевом балансе. Пополнить: ${CFG.providers[provider].keys_page}`, detail, status);
  }
  if (status === 401 || (status === 400 && text.includes("api key") && text.includes("valid"))) {
    return new ApiError("auth", `Неверный API-ключ ${name}. Проверь его в ⚙ (кнопка «Проверить»).`, detail, status);
  }
  if (status === 402) {
    return new ApiError("credits", `${name} просит пополнить баланс: ключ создан с лимитом 0 или модель стала платной. ` +
      "Выбери другую модель или проверь ключ.", detail, status);
  }
  if (status === 403) return new ApiError("other", `${name} отклонил запрос (модерация или ограничения ключа).`, detail, status);
  if ([400, 415, 422].includes(status) && ["image", "multimodal", "vision", "content must be a string"].some((w) => text.includes(w))) {
    return new ApiError("no_vision", "Модель не принимает картинки.", detail, status);
  }
  if (status === 404) return new ApiError("unavailable", "Модель сейчас недоступна.", detail, status);
  if (status === 408) return new ApiError("timeout", "Модель не успела ответить.", detail, status);
  if (status === 429) {
    let err;
    if (provider === "openrouter" && /per-day|per day|daily/.test(text)) {
      err = new ApiError("daily_limit", "Закончились бесплатные запросы OpenRouter на сегодня (без пополнения — 50 в день " +
        "на все бесплатные модели). Лимит обновится в 03:00 по МСК.", detail, status);
    } else if (text.includes("upstream") || meta.provider_name) {
      err = new ApiError("upstream", `Провайдер этой модели сейчас перегружен (это общий лимит для всех пользователей ${name}).`,
        detail, status);
    } else {
      const limit = { groq: " (у Groq бесплатно 1000 запросов в день и 8000 токенов в минуту)", gemini: " (бесплатный лимит Gemini)" }[provider] || "";
      err = new ApiError("minute_limit", `Слишком много запросов подряд${limit}. Подожди минуту.`, detail, status);
    }
    const ra = parseFloat(hdrs?.get?.("retry-after"));
    if (!Number.isNaN(ra)) err.retryAfter = ra;
    return err;
  }
  if (status >= 500) return new ApiError("server", "Сбой на стороне провайдера модели.", detail, status);
  return new ApiError("other", `Ошибка ${name} (${status}).`, detail, status);
}

/** Текущий запрос: «Стоп» обрывает и fetch, и паузы ожидания. */
export class Request {
  constructor() { this.cancelled = false; this.controllers = new Set(); this.waiters = new Set(); }
  cancel() {
    this.cancelled = true;
    for (const c of this.controllers) c.abort();
    for (const w of this.waiters) w();
  }
  check() { if (this.cancelled) throw new ApiError("cancelled", "Остановлено."); }
}

async function askOnce(provider, key, payload, req, limit = MODEL_DEADLINE) {
  req.check();
  const ctl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctl.abort(); }, limit * 1000);
  req.controllers.add(ctl);
  let resp, body;
  try {
    resp = await fetch(CFG.providers[provider].chat_url, {
      method: "POST", headers: headers(provider, key), body: JSON.stringify(payload), signal: ctl.signal,
    });
    body = await resp.text();  // тоже под общим дедлайном: keep-alive пробелы не продлевают ожидание
  } catch (e) {
    req.check();
    if (timedOut) throw new ApiError("timeout", `Модель не ответила за ${limit} с.`);
    throw new ApiError("network", `Нет соединения с ${providerName(provider)}. Проверь интернет.`);
  } finally {
    clearTimeout(timer);
    req.controllers.delete(ctl);
  }
  req.check();
  let data = null;
  try { data = JSON.parse(body); } catch { /* не JSON */ }
  if (Array.isArray(data) && data[0] && typeof data[0] === "object") data = data[0];  // Gemini иногда оборачивает ошибку в список
  if (resp.status !== 200) {
    let error = data && typeof data === "object" ? data.error : null;
    if (!error || typeof error !== "object") error = { message: body.trim().slice(0, 300) };
    throw classifyError(resp.status, error, resp.headers, provider);
  }
  if (!data || typeof data !== "object") throw new ApiError("server", `${providerName(provider)} вернул непонятный ответ.`);
  if (data.error && typeof data.error === "object") {
    throw classifyError(Number.isInteger(data.error.code) ? data.error.code : 502, data.error, resp.headers, provider);
  }
  const choice = data.choices?.[0];
  if (choice?.error) throw classifyError(502, choice.error, resp.headers, provider);
  let content = choice?.message?.content;
  if (Array.isArray(content)) content = content.map((p) => p?.text || "").join("");
  content = (content || "").replace(/<think>[\s\S]*?<\/think>/g, "").trim();
  if (!content) throw new ApiError("empty", "Модель вернула пустой ответ.");
  return content;
}

/** Пауза с обратным отсчётом в статусе; «Стоп» прерывает её сразу. */
function wait(seconds, req, onStatus, label) {
  return new Promise((resolve, reject) => {
    const end = Date.now() + seconds * 1000;
    let timer;
    const stop = () => { clearInterval(timer); req.waiters.delete(stop); reject(new ApiError("cancelled", "Остановлено.")); };
    req.waiters.add(stop);
    const tick = () => {
      const left = end - Date.now();
      if (left <= 0) { clearInterval(timer); req.waiters.delete(stop); resolve(); return; }
      onStatus?.(`${label}: ${Math.ceil(left / 1000)} с`);
    };
    tick();
    timer = setInterval(tick, 250);
  });
}

/** Запрос, который при минутном лимите один раз ждёт и повторяет (точный режим: два шага подряд). */
async function askPatient(provider, key, payload, req, limit, onStatus) {
  try {
    return await askOnce(provider, key, payload, req, limit);
  } catch (e) {
    if (e.kind !== "minute_limit") throw e;
    await wait(Math.min(e.retryAfter || 15, 25), req, onStatus, "Жду минутный лимит");
    return askOnce(provider, key, payload, req, limit);
  }
}

/** Модель со зрением переписывает задание с фото — для моделей без зрения (в браузере нет OCR). */
async function transcribe(provider, key, models, images, req, onStatus, except, patient) {
  const reader = [...models, ...CFG.providers[provider].default_models].find((m) => m !== except && hasVision(provider, m));
  if (!reader) {
    throw new ApiError("no_vision", "Эта модель не видит фото, а у сервиса нет модели со зрением, чтобы прочитать задание. " +
      "Выбери в ⚙ модель со значком 👁.");
  }
  onStatus?.(`Читаю фото: ${short(reader)}`);
  const payload = buildPayload(provider, reader, { images, promptText: "-" });
  payload.messages[0].content = CFG.transcribe_prompt;
  if ("max_tokens" in payload) payload.max_tokens = 1000;
  return patient ? askPatient(provider, key, payload, req, MODEL_DEADLINE, onStatus)
    : askOnce(provider, key, payload, req, MODEL_DEADLINE);
}

/** Переписанное задание — один раз на запрос; неудача тоже запоминается, чтобы не повторять её для каждой модели. */
async function cachedTranscript(cache, provider, make) {
  const k = `transcript:${provider}`;
  if (cache[`${k}:error`]) throw cache[`${k}:error`];
  if (!(k in cache)) {
    try { cache[k] = await make(); } catch (e) { if (e.kind !== "cancelled") cache[`${k}:error`] = e; throw e; }
  }
  return cache[k];
}

async function askProvider(provider, key, models, images, opts) {
  const { onTry, onStatus, req, promptText, accurate, cache } = opts;
  const name = providerName(provider);
  if (!key) throw new ApiError("auth", `Не указан API-ключ ${name}. Открой ⚙ и вставь ключ.`);
  const candidates = [...new Set(models.filter(Boolean))].slice(0, 1 + MAX_FALLBACKS);
  const limit = accurate ? ACCURATE_DEADLINE : MODEL_DEADLINE;
  let last = null;
  for (const [i, model] of candidates.entries()) {
    req.check();
    if (i > 0) onTry?.(model);
    try {
      let payload = null;
      let text;
      if (hasVision(provider, model)) {
        onStatus?.(`Модель: ${short(model)}`);
        payload = buildPayload(provider, model, { images, promptText, accurate });
        try { text = await askOnce(provider, key, payload, req, limit); }
        catch (e) { if (e.kind !== "no_vision") throw e; payload = null; }
      }
      if (payload === null) {
        const transcript = await cachedTranscript(cache, provider,
          () => transcribe(provider, key, models, images, req, onStatus, model, false));
        onStatus?.(`Модель: ${short(model)} (по переписанному заданию)`);
        payload = buildPayload(provider, model, { text: transcript, promptText, accurate });
        text = await askOnce(provider, key, payload, req, limit);
      }
      return { text, model, provider, messages: [...payload.messages, { role: "assistant", content: text }] };
    } catch (e) {
      if (e.kind === "cancelled") throw e;
      e.source = name;
      last = e;
      const perModelLimit = e.kind === "minute_limit" && provider === "groq";  // у Groq лимиты у каждой модели свои
      if (!RETRYABLE.has(e.kind) && !perModelLimit) break;
    }
  }
  if (RETRYABLE.has(last.kind) && candidates.length > 1) {
    last.message = `${last.message} Пробовал модели: ${candidates.map(short).join(", ")}. Попробуй чуть позже.`;
  }
  throw last;
}

async function askTwoStep(provider, key, models, images, opts) {
  const { onStatus, req, promptText, cache } = opts;
  const reasoner = CFG.providers[provider].reasoner;
  const status = (s) => onStatus?.(`🎯 ${s}`);
  const transcript = await cachedTranscript(cache, provider,
    () => transcribe(provider, key, models, images, req, status, reasoner, true));
  req.check();
  status(`Решаю и проверяю: ${short(reasoner)}`);
  const payload = buildPayload(provider, reasoner, { text: transcript, promptText, accurate: true });
  const text = await askPatient(provider, key, payload, req, ACCURATE_DEADLINE, status);
  return { text, model: reasoner, provider, messages: [...payload.messages, { role: "assistant", content: text }] };
}

/**
 * Спрашивает сервисы по очереди: services = [[provider, key, models], …]. Если сервис упёрся в лимит,
 * не отвечает или не принял ключ — запрос уходит в следующий.
 */
export async function askServices(services, images, opts) {
  const { onService, req } = opts;
  services = services.filter((s, i) => i === 0 || s[1]);  // запасные — только с ключом
  const cache = {};
  const failures = [];
  let last = null;
  for (const [i, [provider, key, models]] of services.entries()) {
    req.check();
    if (i > 0) onService?.(provider);
    try {
      if (opts.accurate && CFG.providers[provider].reasoner) {
        // Без отката на однопроходный режим: неверный «точный» ответ хуже ошибки.
        return await askTwoStep(provider, key, models, images, { ...opts, cache });
      }
      return await askProvider(provider, key, models, images, { ...opts, cache });
    } catch (e) {
      if (e.kind === "cancelled") throw e;
      if (e.source === "сервиса") e.source = providerName(provider);
      last = e;
      failures.push(`${providerName(provider)} — ${e.message.split(".")[0]}`);
    }
  }
  if (failures.length > 1) last.message = "Ни один сервис не ответил:\n" + failures.map((f) => `- ${f}`).join("\n");
  throw last;
}

/** Уточняющий вопрос к той же модели: она видит фото, свой ответ и новый вопрос. */
export async function askFollowup(answer, key, question, req, accurate) {
  const messages = [...answer.messages, { role: "user", content: question }];
  const payload = { model: answer.model, messages };
  applyReasoning(payload, answer.provider, answer.model, accurate);
  try {
    const text = await askOnce(answer.provider, key, payload, req, accurate ? ACCURATE_DEADLINE : MODEL_DEADLINE);
    return { ...answer, text, messages: [...messages, { role: "assistant", content: text }] };
  } catch (e) {
    e.source = providerName(answer.provider);
    throw e;
  }
}

async function getJson(url, key, provider) {
  const resp = await fetch(url, { headers: key ? { Authorization: `Bearer ${key}` } : {} });
  const body = await resp.text();
  let data = null;
  try { data = JSON.parse(body); } catch { /* не JSON */ }
  const bad = resp.status === 401 || resp.status === 403 || (resp.status === 400 && body.toLowerCase().includes("api key"));
  return { status: resp.status, data, bad, provider };
}

/** Проверка ключа: строка для показа под полем ключа. */
export async function checkKey(provider, key) {
  if (!key) return "✗ Ключ не указан";
  try {
    if (provider === "groq" || provider === "gemini") {
      const r = await getJson(CFG.providers[provider].models_url, key);
      if (r.bad) return "✗ Неверный ключ";
      return r.status === 200 ? "✓ Ключ работает" : `? ${providerName(provider)} ответил ${r.status}`;
    }
    if (provider === "openrouter") {
      const r = await getJson("https://openrouter.ai/api/v1/key", key);
      if (r.status === 401) return "✗ Неверный ключ";
      if (r.status !== 200) return `? OpenRouter ответил ${r.status}`;
      const d = r.data?.data || {};
      const daily = d.free_model_daily_requests || {};
      const limit = daily.limit ?? (d.is_free_tier === undefined ? null : d.is_free_tier ? 50 : 1000);
      const parts = ["✓ Ключ работает"];
      if (daily.remaining != null && limit != null) parts.push(`запросов на сегодня осталось: ${daily.remaining} из ${limit}`);
      else if (limit != null) parts.push(`лимит бесплатных запросов: ${limit} в день`);
      return parts.join(" • ");
    }
    // OrcaRouter и TeamoRouter отдают список моделей всем — проверяем крошечным запросом.
    const model = CFG.providers[provider].default_models[0];
    const resp = await fetch(CFG.providers[provider].chat_url, {
      method: "POST", headers: headers(provider, key),
      body: JSON.stringify({ model, max_tokens: 1, messages: [{ role: "user", content: "1" }] }),
    });
    if (resp.status === 200) return "✓ Ключ работает • бесплатные модели отвечают";
    if (resp.status === 401 || resp.status === 403) return "✗ Неверный ключ";
    let error = {};
    try { error = (await resp.json()).error || {}; } catch { /* не JSON */ }
    const e = classifyError(resp.status, error, resp.headers, provider);
    if (["free_access", "balance", "daily_limit", "minute_limit"].includes(e.kind)) return `✓ Ключ работает • ⚠ ${e.message}`;
    return `✓ Ключ принят • ⚠ пробный запрос вернул код ${resp.status}`;
  } catch {
    return "? Нет соединения — проверь интернет";
  }
}

/** Актуальный список моделей сервиса: модели со зрением сверху. */
export async function fetchModels(provider, key) {
  const info = CFG.providers[provider];
  if (!key && ["groq", "gemini", "teamorouter"].includes(provider)) throw new Error("сначала вставь API-ключ выше");
  const r = await getJson(info.models_url, key);
  if (r.bad) throw new Error("нужен рабочий API-ключ — вставь его выше");
  if (r.status !== 200) throw new Error(`${providerName(provider)} ответил ${r.status}`);
  const result = [];
  for (const m of r.data?.data || []) {
    let id = m.id || "";
    const low = id.toLowerCase();
    if (EXCLUDE.some((w) => low.includes(w))) continue;
    if (provider === "openrouter") {
      if (id.endsWith(":free") && (m.architecture?.input_modalities || []).includes("image")) result.push(id);
    } else if (provider === "orcarouter") {
      const nums = Object.values(m.pricing || {}).map(Number).filter((v) => !Number.isNaN(v));
      const free = id === "orcarouter/free" || (nums.length && nums.every((v) => v === 0));
      if (free && !ORCA_NOT_CHAT.some((w) => low.includes(w))) result.push(id);
    } else if (provider === "groq") {
      if (m.active !== false && !GROQ_NOT_CHAT.some((w) => low.includes(w))) result.push(id);
    } else if (provider === "gemini") {
      id = id.replace(/^models\//, "");
      if (id.startsWith("gemini") && !GEMINI_NOT_CHAT.some((w) => id.includes(w))) result.push(id);
    } else if (id.endsWith("-free") || hasVision(provider, id)) {
      result.push(id);
    }
  }
  return result.sort((a, b) => (hasVision(provider, b) - hasVision(provider, a)) || a.localeCompare(b));
}

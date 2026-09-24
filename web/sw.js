// Кэш оболочки приложения: ZhukoGPT открывается даже при плохой связи. Запросы к ИИ не кэшируются.
// Версия приходит в адресе (sw.js?v=1.6.0): новая версия сайта регистрирует новый воркер и сама заменяет кэш.
const CACHE = "zhukogpt-" + (new URL(location.href).searchParams.get("v") || "dev");
const SHELL = ["./", "index.html", "style.css", "app.js", "api.js", "mathfmt.js", "config.json",
  "manifest.webmanifest", "icons/icon-180.png", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Сначала сеть (чтобы обновления приходили сразу), без сети — из кэша.
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  e.respondWith(
    fetch(e.request)
      .then((resp) => {
        if (resp.ok) { const copy = resp.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
        return resp;
      })
      .catch(() => caches.match(e.request, { ignoreSearch: true })),
  );
});

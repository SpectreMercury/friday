// Only non-sensitive, fixed assets are stored. Never cache app pages, APIs or downloads.
const CACHE = "harness-public-v21";
const PUBLIC = ["/pwa/friday-icon-192.png", "/pwa/friday-icon-512.png", "/pwa/friday-apple-touch-icon.png", "/pwa/offline.html", "/pwa/style.css"];
self.addEventListener("install", event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(PUBLIC)).then(() => self.skipWaiting())));
self.addEventListener("activate", event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith("harness-public-") && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim())));
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || event.request.method !== "GET") return;
  if (PUBLIC.includes(url.pathname) && !url.search) {
    event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
  } else if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(async () => (await caches.match("/pwa/offline.html")) || Response.error()));
  }
});

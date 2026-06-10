const CACHE = 'tradingai-v1';
const ASSETS = ['./index.html', './app.js', './style.css', './config.js', './manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

// Serve cached files when offline, fetch fresh when online
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  // Don't cache OKX / Anthropic API calls — always go live
  const url = e.request.url;
  if (url.includes('okx.com') || url.includes('anthropic.com') || url.includes('cryptocompare')) return;
  e.respondWith(
    fetch(e.request)
      .then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});

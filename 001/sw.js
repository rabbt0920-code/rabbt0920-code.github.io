/* 人生模擬器遊戲館 Service Worker
   ─────────────────────────────────────────
   ★ 每次更新網站（改入口頁或任何遊戲）後，把版本號 +1，
     玩家下次開啟就會自動拿到新版。
   遊戲清單不用寫在這裡：入口頁會自動把 GAMES 裡的遊戲交給它離線快取。 */
const VERSION = 'v4';
const CACHE = 'life-sim-' + VERSION;
const SHELL = [
  './',
  './index.html',
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-180.png'
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k.startsWith('life-sim-') && k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

/* 入口頁送來遊戲網址 → 背景下載，讓遊戲也能離線玩 */
self.addEventListener('message', e => {
  if (e.data && e.data.type === 'CACHE_GAMES') {
    e.waitUntil(caches.open(CACHE).then(c =>
      Promise.all(e.data.urls.map(u =>
        fetch(u, { cache: 'no-cache' }).then(r => r.ok && c.put(u, r)).catch(() => {})
      ))
    ));
  }
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // 字型：先用快取，沒有才下載
  if (url.host === 'fonts.googleapis.com' || url.host === 'fonts.gstatic.com') {
    e.respondWith(
      caches.match(req).then(hit => hit || fetch(req).then(res => {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy));
        return res;
      }))
    );
    return;
  }

  if (url.origin !== location.origin) return;

  // 網頁與遊戲：先連網拿最新版，斷線時用快取
  e.respondWith(
    fetch(req).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
      return res;
    }).catch(() =>
      caches.match(req, { ignoreSearch: true }).then(hit =>
        hit || (req.mode === 'navigate' ? caches.match('./index.html') : Response.error())
      )
    )
  );
});

/* 股海人生模擬器 Service Worker：離線可玩；有網路時優先抓最新版 */
const CACHE='stock-life-v1';
const ASSETS=['./','./manifest.webmanifest','./icon-192.png','./icon-512.png','./apple-touch-icon.png',"./股海人生模擬器.html"];
self.addEventListener('install',e=>{ e.waitUntil(caches.open(CACHE).then(c=>Promise.all(ASSETS.map(a=>c.add(a).catch(()=>{})))).then(()=>self.skipWaiting())); });
self.addEventListener('activate',e=>{ e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())); });
self.addEventListener('fetch',e=>{
  const r=e.request; if(r.method!=='GET'||new URL(r.url).origin!==location.origin) return;
  e.respondWith(fetch(r).then(res=>{ const cp=res.clone(); caches.open(CACHE).then(c=>c.put(r,cp)); return res; }).catch(()=>caches.match(r).then(m=>m||caches.match('./股海人生模擬器.html'))));
});

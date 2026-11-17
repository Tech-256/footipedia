// Footipedia service worker: app shell cached for offline use; data always fetched fresh when online.
const SHELL="fp-shell-v1";
const FILES=["./","index.html","manifest.json","icons/icon-192.png","icons/icon-512.png"];
self.addEventListener("install",e=>{e.waitUntil(caches.open(SHELL).then(c=>c.addAll(FILES)));self.skipWaiting()});
self.addEventListener("activate",e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==SHELL&&k!=="fp-data").map(k=>caches.delete(k)))));self.clients.claim()});
self.addEventListener("fetch",e=>{
  const u=new URL(e.request.url);if(e.request.method!=="GET"||u.origin!==location.origin)return;
  // Network first for everything on our site, so updates show straight away; fall back to the last copy when offline.
  e.respondWith(fetch(e.request,{cache:"no-store"}).then(r=>{if(r.ok){const c=r.clone();caches.open(u.pathname.endsWith(".json")?"fp-data":SHELL).then(x=>x.put(e.request,c))}return r})
    .catch(()=>caches.match(e.request,{ignoreSearch:true}).then(r=>r||caches.match("index.html"))));
});

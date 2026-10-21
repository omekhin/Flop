/* Офлайн-режим: файлы сайта кэшируются на устройстве. При новой версии сайта поменяй VERSION. */
const VERSION='flop-v2';
const FILES=["./", "calc.html", "favicon.svg", "fonts.css", "fonts/jetbrains-mono-cyrillic-500-normal.woff2", "fonts/jetbrains-mono-cyrillic-700-normal.woff2", "fonts/jetbrains-mono-latin-500-normal.woff2", "fonts/jetbrains-mono-latin-700-normal.woff2", "fonts/onest-cyrillic-400-normal.woff2", "fonts/onest-cyrillic-500-normal.woff2", "fonts/onest-cyrillic-600-normal.woff2", "fonts/onest-cyrillic-700-normal.woff2", "fonts/onest-latin-400-normal.woff2", "fonts/onest-latin-500-normal.woff2", "fonts/onest-latin-600-normal.woff2", "fonts/onest-latin-700-normal.woff2", "fonts/unbounded-cyrillic-500-normal.woff2", "fonts/unbounded-cyrillic-700-normal.woff2", "fonts/unbounded-latin-500-normal.woff2", "fonts/unbounded-latin-700-normal.woff2", "guide.html", "icon-180.png", "icon-192.png", "icon-512.png", "index.html", "manifest.webmanifest", "outs.html", "play.html", "privacy.html", "pushfold.html", "ranges.js", "site.css", "site.js", "stats.html"];
self.addEventListener('install',e=>{ e.waitUntil(caches.open(VERSION).then(c=>c.addAll(FILES)).then(()=>self.skipWaiting())); });
self.addEventListener('activate',e=>{ e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==VERSION).map(k=>caches.delete(k)))).then(()=>self.clients.claim())); });
self.addEventListener('fetch',e=>{
  const r=e.request; if(r.method!=='GET'||new URL(r.url).origin!==location.origin) return;
  // Сначала из кэша (быстро и без сети), параллельно обновляем кэш из сети.
  e.respondWith(caches.open(VERSION).then(async c=>{
    const hit=await c.match(r,{ignoreSearch:r.mode==='navigate'});
    const net=fetch(r).then(res=>{ if(res&&res.ok) c.put(r.mode==='navigate'?new Request(new URL(r.url).pathname):r,res.clone()); return res; }).catch(()=>null);
    return hit||net.then(res=>res||c.match('./play.html'));
  }));
});

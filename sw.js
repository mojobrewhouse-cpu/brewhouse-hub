// Brewhouse Command Center Service Worker
// Enables PWA install + offline shell

const CACHE_NAME = 'brewhouse-v1';

self.addEventListener('install', function(e) {
  self.skipWaiting();
});

self.addEventListener('activate', function(e) {
  e.waitUntil(clients.claim());
});

// Network-first strategy: always try fresh, fall back to cache
self.addEventListener('fetch', function(e) {
  // Don't cache data.json — always fetch fresh
  if (e.request.url.includes('data.json')) {
    return;
  }
  e.respondWith(
    fetch(e.request).then(function(response) {
      // Cache successful responses
      if (response.ok) {
        var clone = response.clone();
        caches.open(CACHE_NAME).then(function(cache) {
          cache.put(e.request, clone);
        });
      }
      return response;
    }).catch(function() {
      // Offline fallback
      return caches.match(e.request);
    })
  );
});

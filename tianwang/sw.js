// Service Worker - 天网运维 PWA
// ⚠️ HTML/JS 不缓存（由服务端 no-cache 控制），只缓存静态资源
const CACHE_NAME = 'tianwang-ocr-v21';
const CACHE_URLS = [
  '/manifest.json',
  '/index.html'
];

// 跳过等待（新 SW 安装后立即激活）
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// 安装
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(CACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

// 激活 - 清理旧缓存，通知所有客户端刷新
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim()).then(() => {
      // 通知所有打开的页面：SW 已更新，请刷新
      return self.clients.matchAll({ type: 'window' }).then(clients => {
        clients.forEach(client => {
          client.postMessage({ type: 'SW_UPDATED' });
        });
      });
    })
  );
});

// 拦截请求
self.addEventListener('fetch', event => {
  const url = event.request.url;
  // 跳过 API 请求
  if (url.includes('/api/')) return;

  // HTML 和 SW 脚本：强制网络，不缓存（确保手机总是获取最新版本）
  if (url.endsWith('.html') || url.endsWith('.htm') || url.includes('sw.js')) {
    event.respondWith(
      fetch(event.request, { cache: 'no-store' })
    );
    return;
  }

  // 其他资源：网络优先
  event.respondWith(
    fetch(event.request).then(response => {
      if (response && response.status === 200) {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
      }
      return response;
    }).catch(() => {
      return caches.match(event.request);
    })
  );
});

/* Service Worker — Web Push 通知 + オフラインキャッシュ。
 *
 * 役割:
 *  1. push イベント受信 → OS 通知を表示（緊急度でアイコン/バイブを変える）
 *  2. notificationclick → ダッシュボードを開く/フォーカスする
 *  3. install/fetch → index.html を最低限オフライン表示できるようキャッシュ
 */

const CACHE_NAME = "trading-bot-v1";
const OFFLINE_URLS = ["/", "/static/index.html"];

// 緊急度ごとのアイコン（high=赤 / medium=黄 / low/normal=青）。
const ICONS = {
  high: "/static/icon-192.png",
  medium: "/static/icon-192.png",
  low: "/static/icon-192.png",
  normal: "/static/icon-192.png",
};

// インストール時に最低限の資産をキャッシュ。
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(OFFLINE_URLS))
      .catch(() => {})
  );
  self.skipWaiting();
});

// 古いキャッシュを削除。
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
        )
      )
  );
  self.clients.claim();
});

// ネット優先・失敗時はキャッシュ（ナビゲーションのみ）。
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match("/static/index.html"))
    );
  }
});

// プッシュ受信 → 通知表示。
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "通知", body: event.data ? event.data.text() : "" };
  }

  const urgency = data.urgency || "normal";
  const title = data.title || "Trading Bot";
  const options = {
    body: data.body || "",
    icon: ICONS[urgency] || ICONS.normal,
    badge: "/static/icon-192.png",
    tag: data.tag || urgency,
    renotify: urgency === "high",
    requireInteraction: urgency === "high",
    vibrate: urgency === "high" ? [200, 100, 200, 100, 200] : [120, 60, 120],
    data: { url: data.url || "/" },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// 通知タップ → ダッシュボードを開く/フォーカス。
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clientList) => {
        for (const client of clientList) {
          if ("focus" in client) {
            client.navigate(targetUrl);
            return client.focus();
          }
        }
        if (self.clients.openWindow) {
          return self.clients.openWindow(targetUrl);
        }
      })
  );
});

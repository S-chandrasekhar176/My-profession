'use client';

/**
 * Browser desktop notifications (Web Notification API) — v0.4.16.
 *
 * WHY: user-testing feedback (2026-09-08) — approvals felt like "desktop OR
 * Telegram, only one works". In reality the backend always fans out to BOTH
 * the web dashboard and Telegram; what was missing was a true DESKTOP
 * notification (visible even when the tab is in the background), so users
 * experienced the dashboard arm of the fan-out as silent. This module adds
 * the third leg: Web Notification API popups for new opportunities and trade
 * fills, fully COMBINABLE with Telegram (they are independent channels).
 *
 * Permission model: browsers only grant Notification.permission through a
 * user gesture — the Settings → Notifications "Desktop notifications" toggle
 * requests it and persists the choice. Until permission is 'granted' this
 * module is a no-op (honest: it never nags).
 *
 * Preference: localStorage flag `ultrabot_desktop_notifications_enabled`
 * ('false' opts out). Unset counts as enabled — the permission gate is the
 * real consent mechanism.
 */

const PREF_KEY = 'ultrabot_desktop_notifications_enabled';

export function isDesktopNotificationSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window;
}

export function getDesktopNotificationPermission(): NotificationPermission | 'unsupported' {
  if (!isDesktopNotificationSupported()) return 'unsupported';
  return Notification.permission;
}

export function isDesktopNotificationEnabled(): boolean {
  if (!isDesktopNotificationSupported()) return false;
  try {
    const pref = localStorage.getItem(PREF_KEY);
    if (pref === 'false') return false;
  } catch {}
  return Notification.permission === 'granted';
}

/** Must be called from a user gesture (Settings toggle). Returns the resulting permission. */
export async function requestDesktopNotificationPermission(): Promise<NotificationPermission | 'unsupported'> {
  if (!isDesktopNotificationSupported()) return 'unsupported';
  try {
    return await Notification.requestPermission();
  } catch {
    return Notification.permission;
  }
}

export function setDesktopNotificationPref(enabled: boolean) {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(PREF_KEY, enabled ? 'true' : 'false');
  } catch {}
}

function fire(title: string, body: string, tag: string, onClick?: () => void) {
  if (!isDesktopNotificationEnabled()) return;
  try {
    // Reuse per-entity tags so a burst of engine events cannot spam the OS
    // notification center — the newest replaces the older one per tag.
    const n = new Notification(title, { body, tag, icon: '/favicon.ico', silent: false });
    n.onclick = () => {
      try {
        window.focus();
        if (onClick) onClick();
      } catch {}
    };
  } catch {
    // Some browsers require ServiceWorkerRegistration.showNotification —
    // silently degrade; the in-app UI + Telegram still cover the alert.
  }
}

export function notifyNewOpportunity(opp: {
  symbol?: string;
  direction?: string;
  strategy?: string;
  entry_price?: number;
  entry?: number;
  stop_loss?: number;
  target?: number;
  confidence?: number;
}) {
  if (!opp?.symbol) return;
  const dir = String(opp.direction || '').toUpperCase();
  const entry = Number(opp.entry_price ?? opp.entry ?? 0);
  fire(
    `🎯 ${opp.symbol} ${dir === 'SELL' ? 'SELL' : 'BUY'} setup — approval needed`,
    [
      opp.strategy ? `${opp.strategy}` : null,
      entry > 0 ? `Entry ₹${entry.toFixed(2)}` : null,
      opp.stop_loss ? `SL ₹${Number(opp.stop_loss).toFixed(2)}` : null,
      opp.target ? `TGT ₹${Number(opp.target).toFixed(2)}` : null,
    ]
      .filter(Boolean)
      .join(' · '),
    `opp-${opp.symbol}`,
    () => {
      window.location.hash = '#/opportunities';
      window.history.pushState({}, '', '/opportunities');
    },
  );
}

export function notifyTradeFill(payload: {
  symbol?: string;
  direction?: string;
  quantity?: number;
  filled_price?: number;
  trade_id?: string;
}) {
  if (!payload?.symbol) return;
  fire(
    `✅ ${payload.symbol} filled × ${payload.quantity ?? '—'} @ ₹${Number(payload.filled_price ?? 0).toFixed(2)}`,
    'Paper trade executed — position now managed by the engine.',
    `fill-${payload.trade_id || payload.symbol}`,
    () => {
      window.history.pushState({}, '', '/trades');
    },
  );
}

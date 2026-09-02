'use client';

/**
 * Browser-side UI state helpers for opportunity tracking + legacy cache cleanup.
 *
 * HISTORY: this module used to implement a full parallel "paper trading"
 * ledger in localStorage (positions, trade history, auto square-off, partial
 * bookings). That dual ledger showed numbers that diverged from the engine
 * and has been REMOVED — the engine database is the single source of truth.
 *
 * What remains:
 *  - Confirmed/skipped opportunity ID tracking (pure UI state so a
 *    confirmed/skipped card stays that way across reloads).
 *  - clearAllPaperData() — clears this UI state plus any legacy ledger keys
 *    left over from older sessions.
 */

const CONFIRMED_OPPS_KEY = 'ultrabot_confirmed_opportunities';
const SKIPPED_OPPS_KEY = 'ultrabot_skipped_opportunities';

// Legacy keys written by the removed local ledger — cleared on demand.
const LEGACY_KEYS = [
  'ultrabot_open_positions',
  'ultrabot_trade_history',
  CONFIRMED_OPPS_KEY,
  SKIPPED_OPPS_KEY,
];

export function getConfirmedOppIds(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = localStorage.getItem(CONFIRMED_OPPS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function addConfirmedOppId(id: string) {
  if (typeof window === 'undefined') return;
  try {
    const ids = getConfirmedOppIds();
    if (!ids.includes(id)) {
      ids.push(id);
      localStorage.setItem(CONFIRMED_OPPS_KEY, JSON.stringify(ids));
      window.dispatchEvent(new Event('ultrabot_opportunities_updated'));
    }
  } catch { }
}

export function getSkippedOppIds(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = localStorage.getItem(SKIPPED_OPPS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function addSkippedOppId(id: string) {
  if (typeof window === 'undefined') return;
  try {
    const ids = getSkippedOppIds();
    if (!ids.includes(id)) {
      ids.push(id);
      localStorage.setItem(SKIPPED_OPPS_KEY, JSON.stringify(ids));
      window.dispatchEvent(new Event('ultrabot_opportunities_updated'));
    }
  } catch { }
}

export function clearAllPaperData() {
  if (typeof window === 'undefined') return;
  try {
    for (const key of LEGACY_KEYS) {
      localStorage.removeItem(key);
    }
    window.dispatchEvent(new Event('ultrabot_opportunities_updated'));
  } catch { }
}

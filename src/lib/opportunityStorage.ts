'use client';

import { getMarketHoursInfo } from './marketHours';

export interface StoredOpportunity {
  id: string;
  symbol: string;
  direction: 'BUY' | 'SELL';
  strategy: string;
  kronosScore: number;
  entry: number;
  stopLoss: number;
  target: number;
  riskReward: number;
  capitalRequired: number;
  expiryAt: string;
  riskGates: { name: string; passed: boolean; detail: string }[];
  vix: number;
  niftyTrend: 'Bullish' | 'Bearish' | 'Sideways';
  sector: string;
  winRate: number;
  status: 'pending' | 'confirmed' | 'skipped' | 'rejected' | 'expired';
  rejectionReason?: string;
  invalidationReason?: string;
  type: string;
  lotSize: number;
  quantity: number;
  margin: number;
  strike?: string;
  optionExpiry?: string;
  premium?: number;
  createdAt: string;
}

const SESSION_OPPS_KEY = 'ultrabot_opportunities_session_v2';
const EXPIRED_IDS_KEY = 'ultrabot_expired_opp_ids_v2';
const EXPIRED_REASONS_KEY = 'ultrabot_expired_reasons_v2';
const SESSION_DATE_KEY = 'ultrabot_session_date_v2';

export function getTodayISTDateString(): string {
  try {
    return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date());
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
}

export function ensureDateScopedStorage(): void {
  if (typeof window === 'undefined') return;
  try {
    const today = getTodayISTDateString();
    const storedDate = localStorage.getItem(SESSION_DATE_KEY);
    if (storedDate !== today) {
      clearStoredOpportunitiesSession();
      localStorage.setItem(SESSION_DATE_KEY, today);
    }
  } catch {}
}

export function getStoredExpiredOppIds(): Set<string> {
  if (typeof window === 'undefined') return new Set();
  ensureDateScopedStorage();
  try {
    const raw = localStorage.getItem(EXPIRED_IDS_KEY);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch {
    return new Set();
  }
}

export function saveStoredExpiredOppId(id: string, reason?: string) {
  if (typeof window === 'undefined') return;
  ensureDateScopedStorage();
  try {
    const ids = getStoredExpiredOppIds();
    ids.add(id);
    localStorage.setItem(EXPIRED_IDS_KEY, JSON.stringify(Array.from(ids)));

    if (reason) {
      const rawReasons = localStorage.getItem(EXPIRED_REASONS_KEY);
      const reasonsMap = rawReasons ? JSON.parse(rawReasons) : {};
      reasonsMap[id] = reason;
      localStorage.setItem(EXPIRED_REASONS_KEY, JSON.stringify(reasonsMap));
    }
  } catch {}
}

export function getStoredInvalidationReason(id: string): string | undefined {
  if (typeof window === 'undefined') return undefined;
  try {
    const rawReasons = localStorage.getItem(EXPIRED_REASONS_KEY);
    const reasonsMap = rawReasons ? JSON.parse(rawReasons) : {};
    return reasonsMap[id];
  } catch {
    return undefined;
  }
}

export function getStoredOpportunitiesSession(): StoredOpportunity[] | null {
  if (typeof window === 'undefined') return null;
  ensureDateScopedStorage();
  try {
    const raw = localStorage.getItem(SESSION_OPPS_KEY);
    if (!raw) return null;
    const opps: StoredOpportunity[] = JSON.parse(raw);
    const expiredIds = getStoredExpiredOppIds();
    const marketInfo = getMarketHoursInfo();

    return opps.map((opp) => {
      // If already expired in store
      if (expiredIds.has(opp.id)) {
        return {
          ...opp,
          status: 'expired',
          invalidationReason: opp.invalidationReason || getStoredInvalidationReason(opp.id) || 'Opportunity expired in earlier session',
        };
      }

      // If market is closed, intraday opportunities cannot be active
      if (!marketInfo.isOpen && opp.status === 'pending') {
        const reason = 'Market Session Closed (09:15 - 15:30 IST) — Intraday setup expired with market close';
        saveStoredExpiredOppId(opp.id, reason);
        return {
          ...opp,
          status: 'expired',
          invalidationReason: reason,
        };
      }

      // Check time expiry
      if (opp.expiryAt && new Date(opp.expiryAt).getTime() <= Date.now() && opp.status === 'pending') {
        const reason = opp.invalidationReason || 'Momentum window elapsed (TTL Expired) — opportunity invalidated to prevent stale entry';
        saveStoredExpiredOppId(opp.id, reason);
        return {
          ...opp,
          status: 'expired',
          invalidationReason: reason,
        };
      }

      return opp;
    });
  } catch {
    return null;
  }
}

export function saveStoredOpportunitiesSession(opps: StoredOpportunity[]) {
  if (typeof window === 'undefined') return;
  ensureDateScopedStorage();
  try {
    localStorage.setItem(SESSION_OPPS_KEY, JSON.stringify(opps));
    window.dispatchEvent(new Event('ultrabot_opportunities_session_updated'));
  } catch {}
}

export function clearStoredOpportunitiesSession() {
  if (typeof window === 'undefined') return;
  try {
    localStorage.removeItem(SESSION_OPPS_KEY);
    localStorage.removeItem(EXPIRED_IDS_KEY);
    localStorage.removeItem(EXPIRED_REASONS_KEY);
  } catch {}
}

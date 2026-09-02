'use client';

import { useEffect, useRef, useCallback, useState } from 'react';
import { wsManager } from '@/lib/ws';
import { useStore } from '@/lib/store';
import type { LivePrice, Opportunity } from '@/lib/store';

interface UseWebSocketOptions {
  autoConnect?: boolean;
  token?: string | null;
}

interface UseWebSocketReturn {
  connected: boolean;
  lastMessage: unknown;
}

export function useWebSocket(options: UseWebSocketOptions = {}): UseWebSocketReturn {
  const { autoConnect = true, token } = options;
  const [connected, setConnected] = useState(wsManager.connected);
  const [lastMessage, setLastMessage] = useState(wsManager.lastMessage);

  const connect = useCallback(() => {
    const t = token ?? localStorage.getItem('ultrabot_token');
    // Never attempt an unauthenticated connection — the server would reject
    // it and trigger reconnect churn.
    if (!t) return;
    wsManager.connect(t);
  }, [token]);

  useEffect(() => {
    // Subscribe to connection state
    const unsubConnected = wsManager.on('connected', () => setConnected(true));
    const unsubDisconnected = wsManager.on('disconnected', () => setConnected(false));

    // Subscribe to all messages for lastMessage tracking
    const unsubMessage = wsManager.on('message', (msg) => {
      setLastMessage(msg);
    });

    // Dispatch live price updates to store
    const unsubPrices = wsManager.on('live_price_updates', (data) => {
      const store = useStore.getState();
      if (Array.isArray(data)) {
        store.realtime.updatePrices(data as LivePrice[]);
      } else if (data && typeof data === 'object' && 'symbol' in (data as LivePrice)) {
        store.realtime.updatePrice(data as LivePrice);
      }
    });

    // Dispatch opportunity events (new, invalidated, confirmed) to store
    const handleOpportunity = (data: any) => {
      const store = useStore.getState();
      if (!data || typeof data !== 'object') return;

      if (data.type === 'opportunity_invalidated' || data.type === 'opportunity_confirmed') {
        const oppId = data.opportunity_id || data.id;
        if (oppId) {
          store.realtime.removeOpportunity(oppId);
        }
      } else if (data.type === 'new_opportunity' && data.opportunity) {
        store.realtime.addOpportunity(data.opportunity);
      } else if (data.id && (data.symbol || data.entry_price || data.entry)) {
        store.realtime.addOpportunity(data);
      }
    };

    const unsubOpps1 = wsManager.on('new_opportunity', handleOpportunity);
    const unsubOpps2 = wsManager.on('opportunity', handleOpportunity);

    // Dispatch engine status updates to store.
    // NOTE: the backend message envelope carries the ORIGINAL engine channel
    // name ("engine"), not the resolved subscriber channel, so we must listen
    // on "engine" (keep "engine_status" as an alias for forward compatibility).
    const handleEngineStatus = (data: any) => {
      const store = useStore.getState();
      if (data && typeof data === 'object') {
        const engine = data as Record<string, unknown>;
        if (typeof engine.status === 'string') {
          store.engine.setEngineStatus(engine.status as 'running' | 'stopped' | 'paused');
        }
        if (typeof engine.state === 'string' && typeof engine.status !== 'string') {
          store.engine.setEngineStatus(engine.state as 'running' | 'stopped' | 'paused');
        }
        if (typeof engine.broker === 'string') {
          store.engine.setActiveBroker(engine.broker);
        }
        if (typeof engine.regime === 'string') {
          store.engine.setRegime(engine.regime as 'bull' | 'bear' | 'sideways' | 'volatile');
        }
        if (typeof engine.vix === 'number') {
          store.engine.setVix(engine.vix);
        }
        if (typeof engine.nifty_value === 'number' || typeof engine.nifty_price === 'number') {
          store.engine.setNifty(
            (engine.nifty_value as number) ?? (engine.nifty_price as number),
            (engine.nifty_change as number) ?? 0,
          );
        }
        if (typeof engine.market_close_seconds === 'number') {
          store.engine.setMarketCloseSeconds(engine.market_close_seconds);
        }
      }
    };

    const unsubEngine = wsManager.on('engine_status', handleEngineStatus);
    const unsubEngineAlias = wsManager.on('engine', handleEngineStatus);

    // Dispatch scan telemetry updates to store with debounced batching
    let telemetryEventBuffer: any[] = [];
    let telemetryFlushTimer: ReturnType<typeof setTimeout> | null = null;

    const flushTelemetryEvents = () => {
      if (telemetryEventBuffer.length === 0) return;
      const eventsToFlush = [...telemetryEventBuffer];
      telemetryEventBuffer = [];
      useStore.getState().engine.addTelemetryEvents(eventsToFlush);
    };

    const handleTelemetry = (data: any) => {
      const store = useStore.getState();
      if (!data) return;
      if (data.type === 'scan_telemetry_event' && data.event) {
        telemetryEventBuffer.push(data.event);
        if (!telemetryFlushTimer) {
          telemetryFlushTimer = setTimeout(() => {
            telemetryFlushTimer = null;
            flushTelemetryEvents();
          }, 150);
        }
      } else if (data.type === 'scan_telemetry' && data.telemetry) {
        flushTelemetryEvents();
        store.engine.setScanTelemetry(data.telemetry);
        if (data.telemetry.broker) {
          store.engine.setActiveBroker(data.telemetry.broker);
        }
      } else if (data.recent_events || data.total_scans !== undefined) {
        flushTelemetryEvents();
        store.engine.setScanTelemetry(data);
        if (data.broker) {
          store.engine.setActiveBroker(data.broker);
        }
      }
    };

    const unsubTelemetry = wsManager.on('telemetry', handleTelemetry);
    const unsubScanTelemetry = wsManager.on('scan_telemetry', handleTelemetry);

    // Auto-connect on mount — waits for a token if the user hasn't logged
    // in yet (covers same-tab login, which fires no storage event).
    if (autoConnect) {
      const t = token ?? (typeof window !== 'undefined' ? localStorage.getItem('ultrabot_token') : null);
      if (t) {
        connect();
      } else {
        const onStorage = (e: StorageEvent) => {
          if (e.key === 'ultrabot_token' && e.newValue) {
            clearInterval(tokenPoll);
            connect();
          }
        };
        window.addEventListener('storage', onStorage);
        const tokenPoll = setInterval(() => {
          if (localStorage.getItem('ultrabot_token')) {
            clearInterval(tokenPoll);
            window.removeEventListener('storage', onStorage);
            connect();
          }
        }, 1000);
        return () => {
          window.removeEventListener('storage', onStorage);
          clearInterval(tokenPoll);
        };
      }
    }

    return () => {
      if (telemetryFlushTimer) {
        clearTimeout(telemetryFlushTimer);
        telemetryFlushTimer = null;
      }
      flushTelemetryEvents();
      unsubConnected();
      unsubDisconnected();
      unsubMessage();
      unsubPrices();
      unsubOpps1();
      unsubOpps2();
      unsubEngine();
      unsubEngineAlias();
      unsubTelemetry();
      unsubScanTelemetry();
    };
  }, [autoConnect, connect]);

  return { connected, lastMessage };
}

export default useWebSocket;

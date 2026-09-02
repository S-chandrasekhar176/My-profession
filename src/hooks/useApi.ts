'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  getDashboard,
  getMarketData,
  getOpportunities,
  confirmOpportunity,
  skipOpportunity,
  getTrades,
  getPositions,
  closePosition,
  getStrategies,
  toggleStrategy,
  getWatchlist,
  getRiskStatus,
  getRiskGates,
  getErrors,
  getEngineStatus,
  getSettings,
  updateSettings,
  getBacktestHistory,
  getKronosHotlist,
  getSessionPreflight,
} from '@/lib/api';

// ─────────────────────────────────────────────
// Shared query options
// ─────────────────────────────────────────────

const STALE_TIME = 10_000;       // 10 seconds
const REFETCH_INTERVAL = 30_000; // 30 seconds

const sharedOptions = {
  staleTime: STALE_TIME,
  refetchInterval: REFETCH_INTERVAL,
  retry: 1,
};

// ─────────────────────────────────────────────
// Dashboard
// ─────────────────────────────────────────────

export function useDashboard() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: getDashboard,
    ...sharedOptions,
  });
}

export function useMarketData() {
  return useQuery({
    queryKey: ['market-data'],
    queryFn: getMarketData,
    refetchInterval: 10_000, // Fetch every 10 seconds
  });
}

// ─────────────────────────────────────────────
// Opportunities
// ─────────────────────────────────────────────

export function useOpportunities() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['opportunities'],
    queryFn: getOpportunities,
    ...sharedOptions,
  });

  const confirmMutation = useMutation({
    mutationFn: (id: string) => confirmOpportunity(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['opportunities'] }),
    onError: (err: any) => {
      toast.error(err?.message || 'Failed to confirm opportunity');
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
    },
  });

  const skipMutation = useMutation({
    mutationFn: (id: string) => skipOpportunity(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['opportunities'] }),
    onError: (err: any) => {
      toast.error(err?.message || 'Failed to skip opportunity');
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
    },
  });

  return {
    ...query,
    confirm: confirmMutation.mutate,
    confirmAsync: confirmMutation.mutateAsync,
    skip: skipMutation.mutate,
    skipAsync: skipMutation.mutateAsync,
    isConfirming: confirmMutation.isPending,
    isSkipping: skipMutation.isPending,
  };
}

// ─────────────────────────────────────────────
// Trades
// ─────────────────────────────────────────────

export function useTrades(params?: { page?: number; limit?: number; status?: string }) {
  return useQuery({
    queryKey: ['trades', params],
    queryFn: () => getTrades(params),
    ...sharedOptions,
  });
}

// ─────────────────────────────────────────────
// Positions
// ─────────────────────────────────────────────

export function usePositions() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['positions'],
    queryFn: getPositions,
    ...sharedOptions,
  });

  const closeMutation = useMutation({
    mutationFn: (args: string | { id: string; payload?: { exit_price?: number; exit_reason?: string; notes?: string } }) => {
      if (typeof args === 'string') {
        return closePosition(args);
      }
      return closePosition(args.id, args.payload);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['positions'] }),
    onError: (err: any) => {
      toast.error(err?.message || 'Failed to close position');
      queryClient.invalidateQueries({ queryKey: ['positions'] });
    },
  });

  return {
    ...query,
    closePosition: closeMutation.mutate,
    closeAsync: closeMutation.mutateAsync,
    isClosing: closeMutation.isPending,
  };
}

// ─────────────────────────────────────────────
// Strategies
// ─────────────────────────────────────────────

export function useStrategies() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['strategies'],
    queryFn: getStrategies,
    ...sharedOptions,
  });

  const toggleMutation = useMutation({
    mutationFn: ({ name, isEnabled }: { name: string; isEnabled: boolean }) => toggleStrategy(name, isEnabled),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['strategies'] }),
    onError: (err: any) => {
      toast.error(err?.message || 'Failed to toggle strategy');
      queryClient.invalidateQueries({ queryKey: ['strategies'] });
    },
  });

  return {
    ...query,
    toggle: toggleMutation.mutate,
    toggleAsync: toggleMutation.mutateAsync,
    isToggling: toggleMutation.isPending,
  };
}

// ─────────────────────────────────────────────
// Watchlist
// ─────────────────────────────────────────────

export function useWatchlist() {
  return useQuery({
    queryKey: ['watchlist'],
    queryFn: getWatchlist,
    ...sharedOptions,
  });
}

// ─────────────────────────────────────────────
// Risk
// ─────────────────────────────────────────────

export function useRiskStatus() {
  return useQuery({
    queryKey: ['risk-status'],
    queryFn: getRiskStatus,
    ...sharedOptions,
  });
}

export function useRiskGates() {
  return useQuery({
    queryKey: ['risk-gates'],
    queryFn: getRiskGates,
    ...sharedOptions,
  });
}

// ─────────────────────────────────────────────
// Errors
// ─────────────────────────────────────────────

export function useErrors(params?: { page?: number; limit?: number }) {
  return useQuery({
    queryKey: ['errors', params],
    queryFn: () => getErrors(params),
    ...sharedOptions,
  });
}

// ─────────────────────────────────────────────
// Engine Status
// ─────────────────────────────────────────────

export function useEngineStatus() {
  return useQuery({
    queryKey: ['engine-status'],
    queryFn: getEngineStatus,
    ...sharedOptions,
    refetchInterval: 10_000, // More frequent for engine
  });
}

// ─────────────────────────────────────────────
// Broker session pre-flight
// ─────────────────────────────────────────────

/** Daily-session pre-flight for the given broker (or the running engine's
 * active broker when omitted). Polled slowly — it only needs to notice a
 * session flip (valid → expired) within a minute or so. */
export function useSessionPreflight(broker?: string, enabled = true) {
  return useQuery({
    queryKey: ['session-preflight', broker ?? '_active'],
    queryFn: () => getSessionPreflight(broker),
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: 1,
    enabled,
  });
}

// ─────────────────────────────────────────────
// Settings
// ─────────────────────────────────────────────

export function useSettings() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
    staleTime: 60_000, // Settings change infrequently
  });

  const updateMutation = useMutation({
    mutationFn: (settings: Record<string, unknown>) => updateSettings(settings),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
    onError: (err: any) => {
      toast.error(err?.message || 'Failed to update settings');
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
  });

  return {
    ...query,
    update: updateMutation.mutate,
    updateAsync: updateMutation.mutateAsync,
    isUpdating: updateMutation.isPending,
  };
}

// ─────────────────────────────────────────────
// Backtest
// ─────────────────────────────────────────────

export function useBacktestHistory(params?: { strategy?: string; limit?: number; offset?: number }) {
  return useQuery({
    queryKey: ['backtest-history', params],
    queryFn: () => getBacktestHistory(params),
    staleTime: 60_000,
  });
}

// ─────────────────────────────────────────────
// Scanner
// ─────────────────────────────────────────────

export function useKronosHotlist() {
  return useQuery({
    queryKey: ['kronos-hotlist'],
    queryFn: getKronosHotlist,
    ...sharedOptions,
  });
}

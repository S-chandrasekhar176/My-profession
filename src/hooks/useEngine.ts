'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { startEngine, stopEngine, pauseEngine, resumeEngine, type EngineStartParams } from '@/lib/api';
import { useEngineStatus } from './useApi';
import { useStore } from '@/lib/store';

export function useEngine() {
  const queryClient = useQueryClient();
  const setEngineStatus = useStore((s) => s.engine.setEngineStatus);

  const statusQuery = useEngineStatus();

  const startMutation = useMutation({
    mutationFn: (params: EngineStartParams) => startEngine(params),
    onSuccess: () => {
      setEngineStatus('running');
      queryClient.invalidateQueries({ queryKey: ['engine-status'] });
    },
    onError: (err: any) => {
      queryClient.invalidateQueries({ queryKey: ['engine-status'] });
    },
  });

  const stopMutation = useMutation({
    mutationFn: stopEngine,
    onSuccess: () => {
      setEngineStatus('stopped');
      queryClient.invalidateQueries({ queryKey: ['engine-status'] });
    },
    onError: (err: any) => {
      queryClient.invalidateQueries({ queryKey: ['engine-status'] });
    },
  });

  const pauseMutation = useMutation({
    mutationFn: pauseEngine,
    onSuccess: () => {
      setEngineStatus('paused');
      queryClient.invalidateQueries({ queryKey: ['engine-status'] });
    },
    onError: (err: any) => {
      queryClient.invalidateQueries({ queryKey: ['engine-status'] });
    },
  });

  const resumeMutation = useMutation({
    mutationFn: resumeEngine,
    onSuccess: () => {
      setEngineStatus('running');
      queryClient.invalidateQueries({ queryKey: ['engine-status'] });
    },
    onError: (err: any) => {
      queryClient.invalidateQueries({ queryKey: ['engine-status'] });
    },
  });

  return {
    ...statusQuery,
    start: startMutation.mutate,
    startAsync: startMutation.mutateAsync,
    stop: stopMutation.mutate,
    stopAsync: stopMutation.mutateAsync,
    pause: pauseMutation.mutate,
    pauseAsync: pauseMutation.mutateAsync,
    resume: resumeMutation.mutate,
    resumeAsync: resumeMutation.mutateAsync,
    isStarting: startMutation.isPending,
    isStopping: stopMutation.isPending,
    isPausing: pauseMutation.isPending,
    isResuming: resumeMutation.isPending,
  };
}

export default useEngine;

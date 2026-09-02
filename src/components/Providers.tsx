'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createContext, useContext, useEffect, useState } from 'react';
import { useStore } from '@/lib/store';
import AppShell from '@/components/layout/AppShell';
import { Toaster } from '@/components/ui/sonner';

// Lightweight, zero-script Theme Context for React 19 / Next.js 16 compatibility
interface ThemeContextType {
  theme: string;
  setTheme: (theme: string) => void;
  themes: string[];
  resolvedTheme: string;
}

const ThemeContext = createContext<ThemeContextType>({
  theme: 'dark',
  setTheme: () => {},
  themes: ['dark'],
  resolvedTheme: 'dark',
});

export const useTheme = () => useContext(ThemeContext);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    document.documentElement.classList.add('dark');
  }, []);

  return (
    <ThemeContext.Provider
      value={{
        theme: 'dark',
        setTheme: () => {},
        themes: ['dark'],
        resolvedTheme: 'dark',
      }}
    >
      {children}
    </ThemeContext.Provider>
  );
}

export default function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  const hydrate = useStore((s) => s.auth.hydrate);
  const hydrateEngine = useStore((s) => s.engine.hydrateEngine);

  useEffect(() => {
    hydrate();
    hydrateEngine();
  }, [hydrate, hydrateEngine]);

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AppShell>{children}</AppShell>
        <Toaster
          position="top-right"
          toastOptions={{
            style: {
              background: '#111827',
              border: '1px solid #1e293b',
              color: '#f1f5f9',
            },
          }}
        />
      </ThemeProvider>
    </QueryClientProvider>
  );
}

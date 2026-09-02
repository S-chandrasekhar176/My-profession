'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { useSidebar, useStore } from '@/lib/store';
import { useWebSocket } from '@/hooks/useWebSocket';
import { theme } from '@/styles/theme';
import Sidebar from './Sidebar';
import Header from './Header';

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { collapsed } = useSidebar();
  const [isDesktop, setIsDesktop] = useState(false);
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null);

  // Mount persistent real-time WebSocket connection
  useWebSocket({ autoConnect: true });

  // Client-side authentication guard
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const token = localStorage.getItem('ultrabot_token');
      if (!token && pathname !== '/login') {
        setIsAuthenticated(false);
        router.replace('/login');
      } else {
        setIsAuthenticated(true);
      }
    }
  }, [pathname, router]);

  // Detect desktop viewport
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 768px)');
    setIsDesktop(mq.matches);
    const handler = (e: MediaQueryListEvent) => setIsDesktop(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  // Close mobile sidebar on route change
  useEffect(() => {
    useStore.getState().sidebar.setMobileOpen(false);
  }, [pathname]);

  // Skip shell for login page
  if (pathname === '/login') {
    return <>{children}</>;
  }

  // Prevent flash of protected UI if unauthenticated
  if (isAuthenticated === false) {
    return null;
  }

  const sidebarWidth = collapsed ? theme.sidebar.collapsedWidth : theme.sidebar.expandedWidth;

  return (
    <div className="min-h-screen" style={{ backgroundColor: theme.colors.background }}>
      <Sidebar />

      {/* Main content area — offset by sidebar width on desktop only */}
      <div
        className="flex flex-col min-h-screen"
        style={{ marginLeft: isDesktop ? sidebarWidth : 0 }}
      >
        <Header />

        <main className="flex-1 p-4 md:p-6">
          {children}
        </main>

        {/* Footer */}
        <footer
          className="mt-auto px-4 py-3 text-center text-xs border-t"
          style={{
            backgroundColor: theme.colors.surface,
            borderColor: theme.colors.border,
            color: theme.colors.textDisabled,
          }}
        >
          UltraBot Web Trading Terminal · Built with Next.js
        </footer>
      </div>
    </div>
  );
}

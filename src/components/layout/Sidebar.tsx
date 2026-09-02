'use client';

import { useState, useEffect } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import {
  LayoutDashboard,
  Zap,
  ArrowLeftRight,
  BrainCircuit,
  Eye,
  ShieldAlert,
  LineChart,
  Settings,
  AlertTriangle,
  LogOut,
  ChevronLeft,
  ChevronRight,
  Newspaper,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useSidebar, useAuth, useStore, BROKER_LIST } from '@/lib/store';
import { theme } from '@/styles/theme';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Separator } from '@/components/ui/separator';

// ─────────────────────────────────────────────
// Navigation items
// ─────────────────────────────────────────────

const navItems = [
  { label: 'Dashboard', icon: LayoutDashboard, path: '/' },
  { label: 'Opportunities', icon: Zap, path: '/opportunities' },
  { label: 'Trades', icon: ArrowLeftRight, path: '/trades' },
  { label: 'Strategies', icon: BrainCircuit, path: '/strategies' },
  { label: 'Watchlist', icon: Eye, path: '/watchlist' },
  { label: 'Risk', icon: ShieldAlert, path: '/risk' },
  { label: 'Backtest', icon: LineChart, path: '/backtest' },
  { label: 'Settings', icon: Settings, path: '/settings' },
  { label: 'Errors', icon: AlertTriangle, path: '/errors' },
];

// ─────────────────────────────────────────────
// Engine status indicator
// ─────────────────────────────────────────────

function EngineIndicator({ collapsed }: { collapsed: boolean }) {
  const [mounted, setMounted] = useState(false);
  const { status, mode, activeBroker } = useStore((s) => s.engine);

  useEffect(() => {
    setMounted(true);
  }, []);

  const currentStatus = mounted ? status : 'stopped';
  const isRunning = currentStatus === 'running';
  const brokerName = activeBroker ? BROKER_LIST.find((b) => b.id === activeBroker)?.name : null;

  return (
    <div className="flex items-center gap-2 px-3 py-2">
      <span
        className="inline-block h-2.5 w-2.5 rounded-full shrink-0 transition-colors"
        style={{
          backgroundColor: isRunning ? theme.colors.profit : theme.colors.textDisabled,
          boxShadow: isRunning ? `0 0 6px ${theme.colors.profit}` : 'none',
          animation: isRunning ? 'pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite' : 'none',
        }}
      />
      {!collapsed && (
        <div className="flex flex-col min-w-0">
          <span className="text-xs font-medium" style={{ color: isRunning ? theme.colors.profit : theme.colors.textDisabled }}>
            {isRunning ? 'Running' : 'Stopped'}
            {mounted && mode && (
              <span className="ml-1.5 text-[10px] px-1.5 py-0 rounded" style={{ backgroundColor: mode === 'live' ? theme.colors.loss + '15' : theme.colors.accentMuted, color: mode === 'live' ? theme.colors.loss : theme.colors.accent }}>
                {mode}
              </span>
            )}
          </span>
          {isRunning && brokerName && (
            <span className="text-[10px] truncate" style={{ color: theme.colors.textDisabled }}>{brokerName}</span>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// Sidebar component
// ─────────────────────────────────────────────

export default function Sidebar() {
  const pathname = usePathname();
  const { collapsed, mobileOpen, toggle, setMobileOpen } = useSidebar();
  const { logout } = useAuth();
  const router = useRouter();

  const isActive = (path: string) => {
    if (path === '/') return pathname === '/';
    return pathname.startsWith(path);
  };

  const width = collapsed ? theme.sidebar.collapsedWidth : theme.sidebar.expandedWidth;

  return (
    <>
      {/* Mobile backdrop */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 md:hidden"
          style={{ backgroundColor: theme.colors.overlay }}
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={cn(
          'fixed top-0 left-0 z-50 flex flex-col h-full transition-all duration-300 ease-in-out',
          // mobile: slide in/out
          mobileOpen ? 'translate-x-0' : '-translate-x-full',
          // desktop: always visible
          'md:translate-x-0',
        )}
        style={{
          width,
          backgroundColor: theme.colors.surface,
          borderRight: `1px solid ${theme.colors.border}`,
        }}
      >
        {/* ── Brand ── */}
        <div className="flex items-center h-14 px-4 gap-3 shrink-0">
          <div
            className="flex items-center justify-center rounded-lg"
            style={{
              width: 32,
              height: 32,
              backgroundColor: theme.colors.accentMuted,
            }}
          >
            <Zap size={18} style={{ color: theme.colors.accent }} />
          </div>
          {!collapsed && (
            <span className="font-bold text-base tracking-tight" style={{ color: theme.colors.textPrimary }}>
              UltraBot
            </span>
          )}
        </div>

        <Separator style={{ backgroundColor: theme.colors.border }} />

        {/* ── Navigation ── */}
        <nav className="flex-1 overflow-y-auto py-2 px-2 space-y-0.5">
          {navItems.map((item) => {
            const active = isActive(item.path);
            const Icon = item.icon;

            const navButton = (
              <button
                key={item.path}
                onClick={() => {
                  setMobileOpen(false);
                  router.push(item.path);
                }}
                className={cn(
                  'flex items-center w-full rounded-md transition-colors duration-150',
                  collapsed ? 'justify-center px-0 py-2.5' : 'gap-3 px-3 py-2',
                )}
                style={{
                  backgroundColor: active ? theme.colors.accentMuted : 'transparent',
                  color: active ? theme.colors.accent : theme.colors.textMuted,
                }}
                onMouseEnter={(e) => {
                  if (!active) {
                    e.currentTarget.style.backgroundColor = theme.colors.surfaceHover;
                  }
                }}
                onMouseLeave={(e) => {
                  if (!active) {
                    e.currentTarget.style.backgroundColor = 'transparent';
                  }
                }}
              >
                <Icon size={20} />
                {!collapsed && (
                  <span className="text-sm font-medium truncate">{item.label}</span>
                )}
              </button>
            );

            // Tooltip on collapsed state
            if (collapsed) {
              return (
                <Tooltip key={item.path} delayDuration={0}>
                  <TooltipTrigger asChild>{navButton}</TooltipTrigger>
                  <TooltipContent side="right" sideOffset={8}>
                    <p className="text-xs">{item.label}</p>
                  </TooltipContent>
                </Tooltip>
              );
            }

            return navButton;
          })}
        </nav>

        <Separator style={{ backgroundColor: theme.colors.border }} />

        {/* ── Bottom section ── */}
        <div className="shrink-0 py-2">
          <EngineIndicator collapsed={collapsed} />

          {/* Logout */}
          <Tooltip delayDuration={0}>
            <TooltipTrigger asChild>
              <button
                onClick={() => {
                  logout();
                  window.location.href = '/login';
                }}
                className={cn(
                  'flex items-center w-full rounded-md transition-colors duration-150',
                  collapsed ? 'justify-center px-0 py-2.5' : 'gap-3 px-3 py-2',
                )}
                style={{ color: theme.colors.textMuted }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = theme.colors.surfaceHover;
                  e.currentTarget.style.color = theme.colors.loss;
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = 'transparent';
                  e.currentTarget.style.color = theme.colors.textMuted;
                }}
              >
                <LogOut size={20} />
                {!collapsed && (
                  <span className="text-sm font-medium">Logout</span>
                )}
              </button>
            </TooltipTrigger>
            {collapsed && (
              <TooltipContent side="right" sideOffset={8}>
                <p className="text-xs">Logout</p>
              </TooltipContent>
            )}
          </Tooltip>
        </div>

        {/* ── Collapse toggle (desktop only) ── */}
        <button
          onClick={toggle}
          className="hidden md:flex items-center justify-center h-10 shrink-0 border-t transition-colors duration-150"
          style={{
            borderColor: theme.colors.border,
            color: theme.colors.textMuted,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = theme.colors.surfaceHover;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'transparent';
          }}
        >
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </aside>
    </>
  );
}

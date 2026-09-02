// UltraBot Web — Groww-inspired dark trading theme tokens
// All colors are hex strings for maximum portability.

export const theme = {
  colors: {
    // Core backgrounds
    background: '#0a0e17',
    surface: '#111827',
    surfaceHover: '#1a2332',
    surfaceActive: '#1e293b',

    // Borders
    border: '#1e293b',
    borderHover: '#334155',

    // Text
    textPrimary: '#f1f5f9',
    textMuted: '#94a3b8',
    textDisabled: '#64748b',

    // Brand / Accent
    accent: '#00d09c',
    accentHover: '#00e6ad',
    accentMuted: 'rgba(0, 208, 156, 0.15)',

    // Semantic
    profit: '#22c55e',
    loss: '#ef4444',
    warning: '#f59e0b',
    info: '#3b82f6',

    // Market regime
    bull: '#22c55e',
    bear: '#ef4444',
    sideways: '#f59e0b',
    volatile: '#a855f7',

    // Overlays
    overlay: 'rgba(0, 0, 0, 0.5)',
    backdrop: 'rgba(10, 14, 23, 0.8)',
  },

  sidebar: {
    expandedWidth: 240,
    collapsedWidth: 64,
  },

  header: {
    height: 56,
  },
} as const;

export type Theme = typeof theme;

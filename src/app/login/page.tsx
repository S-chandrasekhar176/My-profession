'use client';

import { useState, useEffect, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import { Eye, EyeOff, Bot, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { login as loginApi } from '@/lib/api';
import { useStore } from '@/lib/store';
import { theme } from '@/styles/theme';

export default function LoginPage() {
  const router = useRouter();
  const storeLogin = useStore((s) => s.auth.login);

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Pre-fill from saved credentials if remembered
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const savedUsername = localStorage.getItem('ultrabot_remember_username');
    if (savedUsername) {
      setUsername(savedUsername);
      setRememberMe(true);
    }
  }, []);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);

    try {
      const result = await loginApi(username, password);
      localStorage.setItem('ultrabot_token', result.access_token);
      if (rememberMe) {
        localStorage.setItem('ultrabot_remember_username', username);
      } else {
        localStorage.removeItem('ultrabot_remember_username');
      }
      storeLogin(result.access_token, username);
      router.push('/');
    } catch (err: unknown) {
      const axiosErr = err as { response?: { status?: number; data?: { detail?: string | Array<{ loc?: unknown; msg?: string }> | Record<string, unknown> } } };
      if (axiosErr.response?.status === 401) {
        setError('Invalid username or password. Please try again.');
      } else if (axiosErr.response?.data?.detail) {
        const detail = axiosErr.response.data.detail;
        if (typeof detail === 'string') {
          setError(detail);
        } else if (Array.isArray(detail)) {
          const messages = detail.map((e) => {
            const field = Array.isArray(e.loc) ? (e.loc as string[]).join('.') : '';
            const msg = e.msg || 'Validation error';
            return field ? `${field}: ${msg}` : msg;
          }).join(', ');
          setError(messages || 'Validation error occurred.');
        } else if (typeof detail === 'object' && detail !== null) {
          setError(JSON.stringify(detail));
        } else {
          setError(String(detail));
        }
      } else {
        setError('Unable to connect to the server. Please try again later.');
      }
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div
      className="min-h-screen flex items-center justify-center px-4"
      style={{ backgroundColor: theme.colors.background }}
    >
      {/* Subtle gradient orbs for visual depth */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div
          className="absolute -top-40 -right-40 h-96 w-96 rounded-full opacity-10 blur-3xl"
          style={{ backgroundColor: theme.colors.accent }}
        />
        <div
          className="absolute -bottom-40 -left-40 h-96 w-96 rounded-full opacity-5 blur-3xl"
          style={{ backgroundColor: theme.colors.accent }}
        />
      </div>

      <motion.div
        initial={{ opacity: 0, y: 20, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.5, ease: 'easeOut' }}
        className="relative z-10 w-full max-w-md"
      >
        {/* Card */}
        <div
          className="rounded-xl p-8 shadow-2xl"
          style={{
            backgroundColor: theme.colors.surface,
            border: `1px solid ${theme.colors.border}`,
          }}
        >
          {/* Logo / Brand */}
          <div className="mb-8 text-center">
            <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-xl"
              style={{ backgroundColor: theme.colors.accentMuted }}
            >
              <Bot size={30} style={{ color: theme.colors.accent }} />
            </div>
            <h1 className="text-2xl font-bold tracking-tight" style={{ color: theme.colors.accent }}>
              UltraBot Web
            </h1>
            <p className="mt-1 text-sm" style={{ color: theme.colors.textMuted }}>
              Algorithmic Trading Terminal for NSE
            </p>
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Username */}
            <div className="space-y-2">
              <Label htmlFor="username" className="text-xs font-medium" style={{ color: theme.colors.textMuted }}>
                Username
              </Label>
              <Input
                id="username"
                type="text"
                placeholder="Enter your username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoFocus
                autoComplete="username"
                className="h-10"
                style={{
                  backgroundColor: theme.colors.surfaceActive,
                  border: `1px solid ${theme.colors.border}`,
                  color: theme.colors.textPrimary,
                }}
              />
            </div>

            {/* Password */}
            <div className="space-y-2">
              <Label htmlFor="password" className="text-xs font-medium" style={{ color: theme.colors.textMuted }}>
                Password
              </Label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  placeholder="Enter your password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                  className="h-10 pr-10"
                  style={{
                    backgroundColor: theme.colors.surfaceActive,
                    border: `1px solid ${theme.colors.border}`,
                    color: theme.colors.textPrimary,
                  }}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-ub-text-primary transition-colors"
                  tabIndex={-1}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            {/* Remember me */}
            <div className="flex items-center gap-2">
              <Checkbox
                id="remember"
                checked={rememberMe}
                onCheckedChange={(checked) => setRememberMe(checked === true)}
                className="data-[state=checked]:bg-ub-accent data-[state=checked]:border-ub-accent"
              />
              <Label
                htmlFor="remember"
                className="cursor-pointer text-sm select-none"
                style={{ color: theme.colors.textMuted }}
              >
                Remember me
              </Label>
            </div>

            {/* Error message */}
            {error && (
              <motion.p
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                className="text-sm rounded-md px-3 py-2"
                style={{
                  color: theme.colors.loss,
                  backgroundColor: 'rgba(239, 68, 68, 0.1)',
                  border: `1px solid rgba(239, 68, 68, 0.2)`,
                }}
              >
                {error}
              </motion.p>
            )}

            {/* Login button */}
            <Button
              type="submit"
              disabled={isLoading || !username || !password}
              className="w-full h-11 text-sm font-semibold transition-all duration-200 hover:brightness-110"
              style={{
                backgroundColor: isLoading ? theme.colors.accent + '80' : theme.colors.accent,
                color: theme.colors.background,
              }}
            >
              {isLoading ? (
                <span className="flex items-center gap-2">
                  <Loader2 size={16} className="animate-spin" />
                  Signing in…
                </span>
              ) : (
                'Sign In'
              )}
            </Button>
          </form>
        </div>

        {/* Version footer */}
        <p className="mt-6 text-center text-xs" style={{ color: theme.colors.textDisabled }}>
          v1.0.0 • Paper Trading Mode
        </p>
      </motion.div>
    </div>
  );
}

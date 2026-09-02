'use client';

import { useState, useMemo, useCallback } from 'react';
import { motion } from 'framer-motion';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip as RTooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts';
import {
  AlertTriangle,
  AlertCircle,
  Info,
  CheckCircle2,
  XCircle,
  Loader2,
  RotateCcw,
  Clock,
  Bug,
  ShieldAlert,
  ChevronLeft,
  ChevronRight,
  Activity,
} from 'lucide-react';
import { toast } from 'sonner';
import { useQueryClient } from '@tanstack/react-query';
import { resolveError, type ErrorLogItem } from '@/lib/api';

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────

type Severity = 'Critical' | 'Warning' | 'Info';
type RecoveryStatus = 'Attempting...' | 'Success' | 'Failed' | 'Not Available';

interface ActiveError {
  id: string;
  code: string;
  severity: Severity;
  description: string;
  rootCause: string;
  action: string;
  recoveryStatus: RecoveryStatus;
  timestamp: string;
}

interface ErrorHistoryEntry {
  id: string;
  time: string;
  code: string;
  type: string;
  severity: Severity;
  message: string;
  resolved: boolean;
}

interface ErrorTypeDistribution {
  name: string;
  value: number;
  color: string;
}

interface SeverityDistribution {
  name: string;
  count: number;
  color: string;
}

// ─────────────────────────────────────────────
// Normalization — map real backend rows
// (GET /api/errors → { errors: [...] }) to the
// shapes this page renders. No fabricated data.
// ─────────────────────────────────────────────

function normalizeSeverity(raw: string | undefined): Severity {
  const s = String(raw || '').toLowerCase();
  if (s === 'critical' || s === 'error') return 'Critical';
  if (s === 'warning') return 'Warning';
  return 'Info';
}

function normalizeRecoveryStatus(item: ErrorLogItem): RecoveryStatus {
  const attempted = Boolean(item.auto_recovery_attempted);
  const result = String(item.auto_recovery_result || '').toLowerCase();
  if (!attempted) return 'Not Available';
  if (result.includes('success')) return 'Success';
  if (result.includes('fail')) return 'Failed';
  return 'Attempting...';
}

function formatTimestamp(iso: string | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function toActiveError(item: ErrorLogItem): ActiveError {
  return {
    id: item.id,
    code: item.error_code || '—',
    severity: normalizeSeverity(item.severity),
    description: item.what_happened || item.message || 'No description recorded',
    rootCause: item.why_happened || 'Root cause not recorded',
    action: item.how_to_fix || 'No fix guidance recorded',
    recoveryStatus: normalizeRecoveryStatus(item),
    timestamp: formatTimestamp(item.created_at),
  };
}

function toHistoryEntry(item: ErrorLogItem): ErrorHistoryEntry {
  return {
    id: item.id,
    time: formatTimestamp(item.created_at),
    code: item.error_code || '—',
    type: item.error_type || 'Unknown',
    severity: normalizeSeverity(item.severity),
    message: item.what_happened || item.message || 'No message recorded',
    resolved: Boolean(item.is_resolved),
  };
}

const ERROR_TYPE_COLORS: Record<string, string> = {
  Connection: '#ef4444',
  Order: '#f59e0b',
  Signal: '#3b82f6',
  Risk: '#a855f7',
  Engine: '#22c55e',
};

const SEVERITY_BADGE_STYLES: Record<Severity, string> = {
  Critical: 'bg-ub-loss/15 text-ub-loss border-ub-loss/30',
  Warning: 'bg-ub-warning/15 text-ub-warning border-ub-warning/30',
  Info: 'bg-ub-accent/15 text-ub-accent border-ub-accent/30',
};

const RECOVERY_BADGE_STYLES: Record<RecoveryStatus, string> = {
  'Attempting...': 'bg-ub-warning/15 text-ub-warning border-ub-warning/30',
  Success: 'bg-ub-profit/15 text-ub-profit border-ub-profit/30',
  Failed: 'bg-ub-loss/15 text-ub-loss border-ub-loss/30',
  'Not Available': 'bg-ub-text-muted/15 text-ub-text-muted border-ub-text-muted/30',
};

const SEVERITY_ICONS: Record<Severity, React.ReactNode> = {
  Critical: <AlertCircle className="h-4 w-4" />,
  Warning: <AlertTriangle className="h-4 w-4" />,
  Info: <Info className="h-4 w-4" />,
};

const tooltipStyle = {
  backgroundColor: '#1e293b',
  border: '1px solid #334155',
  borderRadius: '8px',
  color: '#f1f5f9',
  fontSize: '12px',
  padding: '8px 12px',
};

const ITEMS_PER_PAGE = 8;

// ─────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────

export default function ErrorsPage() {
  const queryClient = useQueryClient();
  const { data: apiErrors, isLoading } = useErrors({ limit: 200 });

  // Map the REAL backend rows (snake_case, severity lowercase) into the
  // display shapes this page renders.
  const allErrors = useMemo(() => {
    const raw = apiErrors as unknown;
    const list = Array.isArray(raw)
      ? (raw as ErrorLogItem[])
      : Array.isArray((raw as { errors?: ErrorLogItem[] })?.errors)
        ? ((raw as { errors: ErrorLogItem[] }).errors)
        : [];
    return list.map(toHistoryEntry);
  }, [apiErrors]);

  const activeErrors: ActiveError[] = useMemo(() => {
    const raw = apiErrors as unknown;
    const list = Array.isArray(raw)
      ? (raw as ErrorLogItem[])
      : Array.isArray((raw as { errors?: ErrorLogItem[] })?.errors)
        ? ((raw as { errors: ErrorLogItem[] }).errors)
        : [];
    return list.filter((e) => !e.is_resolved).map(toActiveError);
  }, [apiErrors]);

  const ERROR_HISTORY: ErrorHistoryEntry[] = allErrors;

  // Distinct error types present in the REAL data (for the Type filter).
  const availableTypes = useMemo(() => {
    const set = new Set<string>();
    ERROR_HISTORY.forEach((e) => set.add(e.type));
    return Array.from(set).sort();
  }, [ERROR_HISTORY]);

  const [filterSeverity, setFilterSeverity] = useState<string>('all');
  const [filterType, setFilterType] = useState<string>('all');
  const [filterDateFrom, setFilterDateFrom] = useState<string>('');
  const [filterDateTo, setFilterDateTo] = useState<string>('');
  const [currentPage, setCurrentPage] = useState(1);

  // Derived data for charts
  const errorTypeDistribution: ErrorTypeDistribution[] = useMemo(() => {
    const counts: Record<string, number> = {};
    ERROR_HISTORY.forEach((e) => {
      counts[e.type] = (counts[e.type] || 0) + 1;
    });
    return Object.entries(counts)
      .map(([name, value]) => ({
        name,
        value,
        color: ERROR_TYPE_COLORS[name] || '#94a3b8',
      }))
      .sort((a, b) => b.value - a.value);
  }, [ERROR_HISTORY]);

  const severityDistribution: SeverityDistribution[] = useMemo(() => {
    const counts: Record<string, number> = { Critical: 0, Warning: 0, Info: 0 };
    ERROR_HISTORY.forEach((e) => {
      counts[e.severity]++;
    });
    const colors: Record<string, string> = { Critical: '#ef4444', Warning: '#f59e0b', Info: '#3b82f6' };
    return Object.entries(counts).map(([name, count]) => ({
      name,
      count,
      color: colors[name] || '#94a3b8',
    }));
  }, [ERROR_HISTORY]);

  // Stats
  const stats = useMemo(() => {
    const total = ERROR_HISTORY.length;
    const unresolved = ERROR_HISTORY.filter((e) => !e.resolved).length + activeErrors.length;
    const recovered = ERROR_HISTORY.filter((e) => e.resolved).length;
    const recoveryRate = total > 0 ? Math.round((recovered / total) * 100) : 100;
    const typeCounts: Record<string, number> = {};
    ERROR_HISTORY.forEach((e) => {
      typeCounts[e.type] = (typeCounts[e.type] || 0) + 1;
    });
    const mostCommon = Object.entries(typeCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || 'System Normal';
    return { total: total + activeErrors.length, unresolved, recoveryRate, mostCommon };
  }, [ERROR_HISTORY, activeErrors]);

  // Filtered history
  const filteredHistory = useMemo(() => {
    return ERROR_HISTORY.filter((e) => {
      if (filterSeverity !== 'all' && e.severity !== filterSeverity) return false;
      if (filterType !== 'all' && e.type !== filterType) return false;
      if (filterDateFrom && e.time < filterDateFrom) return false;
      if (filterDateTo && e.time > filterDateTo + 'T23:59:59') return false;
      return true;
    });
  }, [ERROR_HISTORY, filterSeverity, filterType, filterDateFrom, filterDateTo]);

  const totalPages = Math.max(1, Math.ceil(filteredHistory.length / ITEMS_PER_PAGE));
  const paginatedHistory = filteredHistory.slice(
    (currentPage - 1) * ITEMS_PER_PAGE,
    currentPage * ITEMS_PER_PAGE,
  );

  const handleMarkResolved = useCallback(async (id: string) => {
    // Real round-trip: PUT /api/errors/{id}/resolve → refresh from DB.
    try {
      await resolveError(id, 'Resolved from Error Console');
      await queryClient.invalidateQueries({ queryKey: ['errors'] });
      toast.success('Error marked as resolved');
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message || 'Unknown error';
      toast.error(`Failed to resolve error: ${typeof msg === 'string' ? msg : JSON.stringify(msg)}`);
    }
  }, [queryClient]);

  // Reset page on filter change
  const handleFilterChange = useCallback((setter: React.Dispatch<React.SetStateAction<string>>) => (value: string) => {
    setter(value);
    setCurrentPage(1);
  }, []);

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex items-center gap-3">
        <div className="h-10 w-10 rounded-lg bg-ub-warning/10 flex items-center justify-center">
          <AlertTriangle className="h-5 w-5 text-ub-warning" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-ub-text-primary">Error Console</h1>
          <p className="text-sm text-ub-text-muted">Monitor and manage errors, auto-recovery, and system health</p>
        </div>
      </div>

      {/* ── Error Stats ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card className="bg-ub-surface border-ub-border">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-ub-text-muted uppercase tracking-wider">Total Errors</span>
              <Activity className="h-4 w-4 text-ub-accent" />
            </div>
            <p className="text-2xl font-bold text-ub-text-primary">{stats.total}</p>
          </CardContent>
        </Card>
        <Card className="bg-ub-surface border-ub-border">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-ub-text-muted uppercase tracking-wider">Active / Unresolved</span>
              <AlertCircle className="h-4 w-4 text-ub-loss" />
            </div>
            <p className="text-2xl font-bold text-ub-loss">{stats.unresolved}</p>
          </CardContent>
        </Card>
        <Card className="bg-ub-surface border-ub-border">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-ub-text-muted uppercase tracking-wider">Auto-Recovery Rate</span>
              <RotateCcw className="h-4 w-4 text-ub-profit" />
            </div>
            <p className="text-2xl font-bold text-ub-profit">{stats.recoveryRate}%</p>
          </CardContent>
        </Card>
        <Card className="bg-ub-surface border-ub-border">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-ub-text-muted uppercase tracking-wider">Most Common Type</span>
              <Bug className="h-4 w-4 text-ub-volatile" />
            </div>
            <p className="text-lg font-bold text-ub-volatile">{stats.mostCommon}</p>
          </CardContent>
        </Card>
      </div>

      {/* ── Active Errors ── */}
      <div>
        <h2 className="text-lg font-semibold text-ub-text-primary mb-4 flex items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-ub-loss" />
          Active Errors
          {activeErrors.length > 0 && (
            <Badge variant="outline" className="bg-ub-loss/15 text-ub-loss border-ub-loss/30 text-[10px] font-semibold">
              {activeErrors.length}
            </Badge>
          )}
        </h2>

        <div className="space-y-4">
          {isLoading ? (
            <Card className="bg-ub-surface border-ub-border">
              <CardContent className="p-8 text-center">
                <Loader2 className="h-8 w-8 text-ub-accent mx-auto mb-3 animate-spin" />
                <p className="text-sm text-ub-text-muted">Loading errors from engine…</p>
              </CardContent>
            </Card>
          ) : activeErrors.length === 0 ? (
            <Card className="bg-ub-surface border-ub-border">
              <CardContent className="p-8 text-center">
                <CheckCircle2 className="h-10 w-10 text-ub-profit mx-auto mb-3" />
                <p className="text-sm text-ub-text-muted">No active errors. All systems operational.</p>
              </CardContent>
            </Card>
          ) : (
            activeErrors.map((error) => (
              <motion.div
                key={error.id}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.3 }}
              >
                <Card className={`bg-ub-surface border ${
                  error.severity === 'Critical' ? 'border-ub-loss/40' : error.severity === 'Warning' ? 'border-ub-warning/40' : 'border-ub-border'
                }`}>
                  <CardContent className="p-4 space-y-3">
                    {/* Header */}
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge variant="outline" className={`text-[10px] font-semibold ${SEVERITY_BADGE_STYLES[error.severity]}`}>
                          {SEVERITY_ICONS[error.severity]}
                          <span className="ml-1">{error.severity}</span>
                        </Badge>
                        <span className="text-xs font-mono text-ub-text-muted">{error.code}</span>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <Badge variant="outline" className={`text-[10px] font-semibold ${RECOVERY_BADGE_STYLES[error.recoveryStatus]}`}>
                          {error.recoveryStatus === 'Attempting...' && <Loader2 className="h-3 w-3 animate-spin mr-1" />}
                          {error.recoveryStatus === 'Success' && <CheckCircle2 className="h-3 w-3 mr-1" />}
                          {error.recoveryStatus === 'Failed' && <XCircle className="h-3 w-3 mr-1" />}
                          {error.recoveryStatus}
                        </Badge>
                      </div>
                    </div>

                    {/* What happened */}
                    <div className="space-y-1">
                      <div className="flex items-start gap-2">
                        <span className="text-xs font-semibold text-ub-warning mt-0.5 shrink-0">What:</span>
                        <p className="text-sm text-ub-text-primary">{error.description}</p>
                      </div>
                    </div>

                    {/* Why it happened */}
                    <div className="flex items-start gap-2">
                      <span className="text-xs font-semibold text-ub-loss mt-0.5 shrink-0">Why:</span>
                      <p className="text-sm text-ub-text-muted">{error.rootCause}</p>
                    </div>

                    {/* How to fix */}
                    <div className="flex items-start gap-2">
                      <span className="text-xs font-semibold text-ub-profit mt-0.5 shrink-0">Fix:</span>
                      <p className="text-sm text-ub-text-primary">{error.action}</p>
                    </div>

                    {/* Footer */}
                    <div className="flex items-center justify-between pt-2 border-t border-ub-border">
                      <div className="flex items-center gap-1.5 text-ub-text-disabled text-xs">
                        <Clock className="h-3 w-3" />
                        {error.timestamp}
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-ub-accent hover:text-ub-accent-hover hover:bg-ub-accent/10 text-xs h-7 px-3"
                        onClick={() => handleMarkResolved(error.id)}
                      >
                        <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
                        Mark Resolved
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              </motion.div>
            ))
          )}
        </div>
      </div>

      {/* ── Error History + Breakdown ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Error History Table */}
        <div className="lg:col-span-2 space-y-4">
          <Card className="bg-ub-surface border-ub-border">
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-semibold text-ub-text-primary flex items-center gap-2">
                <Clock className="h-4 w-4 text-ub-accent" />
                Error History
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Filters */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="space-y-1.5">
                  <Label className="text-ub-text-muted text-xs">Severity</Label>
                  <Select value={filterSeverity} onValueChange={handleFilterChange(setFilterSeverity)}>
                    <SelectTrigger className="bg-ub-background border-ub-border text-ub-text-primary text-xs h-9">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="bg-ub-surface border-ub-border">
                      <SelectItem value="all" className="text-ub-text-primary">All</SelectItem>
                      <SelectItem value="Critical" className="text-ub-text-primary">Critical</SelectItem>
                      <SelectItem value="Warning" className="text-ub-text-primary">Warning</SelectItem>
                      <SelectItem value="Info" className="text-ub-text-primary">Info</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-ub-text-muted text-xs">Type</Label>
                  <Select value={filterType} onValueChange={handleFilterChange(setFilterType)}>
                    <SelectTrigger className="bg-ub-background border-ub-border text-ub-text-primary text-xs h-9">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="bg-ub-surface border-ub-border">
                      <SelectItem value="all" className="text-ub-text-primary">All</SelectItem>
                      {availableTypes.map((t) => (
                        <SelectItem key={t} value={t} className="text-ub-text-primary">{t}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-ub-text-muted text-xs">From</Label>
                  <Input
                    type="date"
                    value={filterDateFrom}
                    onChange={(e) => handleFilterChange(setFilterDateFrom)(e.target.value)}
                    className="bg-ub-background border-ub-border text-ub-text-primary text-xs h-9"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-ub-text-muted text-xs">To</Label>
                  <Input
                    type="date"
                    value={filterDateTo}
                    onChange={(e) => handleFilterChange(setFilterDateTo)(e.target.value)}
                    className="bg-ub-background border-ub-border text-ub-text-primary text-xs h-9"
                  />
                </div>
              </div>

              {/* Table */}
              <ScrollArea className="max-h-96">
                <Table>
                  <TableHeader>
                    <TableRow className="border-ub-border hover:bg-transparent">
                      <TableHead className="text-ub-text-muted text-xs">Time</TableHead>
                      <TableHead className="text-ub-text-muted text-xs">Code</TableHead>
                      <TableHead className="text-ub-text-muted text-xs">Type</TableHead>
                      <TableHead className="text-ub-text-muted text-xs">Severity</TableHead>
                      <TableHead className="text-ub-text-muted text-xs">Message</TableHead>
                      <TableHead className="text-ub-text-muted text-xs text-right">Resolved?</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {paginatedHistory.length === 0 ? (
                      <TableRow className="border-ub-border">
                        <TableCell colSpan={6} className="text-center text-ub-text-muted text-sm py-8">
                          No errors match the current filters.
                        </TableCell>
                      </TableRow>
                    ) : (
                      paginatedHistory.map((entry) => (
                        <TableRow key={entry.id} className="border-ub-border hover:bg-ub-surface-hover">
                          <TableCell className="text-ub-text-muted text-xs whitespace-nowrap">{entry.time}</TableCell>
                          <TableCell className="text-ub-text-primary text-xs font-mono whitespace-nowrap">{entry.code}</TableCell>
                          <TableCell>
                            <Badge
                              variant="outline"
                              className="text-[10px] font-semibold"
                              style={{
                                borderColor: `${ERROR_TYPE_COLORS[entry.type] || '#94a3b8'}40`,
                                color: ERROR_TYPE_COLORS[entry.type] || '#94a3b8',
                                backgroundColor: `${ERROR_TYPE_COLORS[entry.type] || '#94a3b8'}15`,
                              }}
                            >
                              {entry.type}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline" className={`text-[10px] font-semibold ${SEVERITY_BADGE_STYLES[entry.severity]}`}>
                              {entry.severity}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-ub-text-primary text-xs max-w-[200px] truncate">{entry.message}</TableCell>
                          <TableCell className="text-right">
                            <Badge
                              variant="outline"
                              className={`text-[10px] font-semibold ${
                                entry.resolved
                                  ? 'border-ub-profit/40 text-ub-profit bg-ub-profit/10'
                                  : 'border-ub-loss/40 text-ub-loss bg-ub-loss/10'
                              }`}
                            >
                              {entry.resolved ? (
                                <>
                                  <CheckCircle2 className="h-3 w-3 mr-0.5" />
                                  Yes
                                </>
                              ) : (
                                <>
                                  <XCircle className="h-3 w-3 mr-0.5" />
                                  No
                                </>
                              )}
                            </Badge>
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </ScrollArea>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-between pt-3 border-t border-ub-border">
                  <span className="text-xs text-ub-text-muted">
                    Showing {(currentPage - 1) * ITEMS_PER_PAGE + 1}–{Math.min(currentPage * ITEMS_PER_PAGE, filteredHistory.length)} of {filteredHistory.length}
                  </span>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 w-7 p-0 text-ub-text-muted hover:bg-ub-surface-hover"
                      disabled={currentPage === 1}
                      onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </Button>
                    {Array.from({ length: totalPages }, (_, i) => i + 1).map((page) => (
                      <Button
                        key={page}
                        variant={page === currentPage ? 'default' : 'ghost'}
                        size="sm"
                        className={`h-7 w-7 p-0 text-xs ${
                          page === currentPage
                            ? 'bg-ub-accent text-ub-background font-semibold'
                            : 'text-ub-text-muted hover:bg-ub-surface-hover'
                        }`}
                        onClick={() => setCurrentPage(page)}
                      >
                        {page}
                      </Button>
                    ))}
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 w-7 p-0 text-ub-text-muted hover:bg-ub-surface-hover"
                      disabled={currentPage === totalPages}
                      onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                    >
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Error Breakdown */}
        <div className="space-y-4">
          {/* By Type (PieChart) */}
          <Card className="bg-ub-surface border-ub-border">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold text-ub-text-primary">By Type</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-[200px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={errorTypeDistribution}
                      cx="50%"
                      cy="50%"
                      innerRadius={45}
                      outerRadius={75}
                      paddingAngle={3}
                      dataKey="value"
                      stroke="none"
                    >
                      {errorTypeDistribution.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={entry.color} />
                      ))}
                    </Pie>
                    <RTooltip
                      contentStyle={tooltipStyle}
                      formatter={((value: number, name: string) => [`${value} errors`, name]) as never}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              {/* Legend */}
              <div className="flex flex-wrap gap-3 mt-2 justify-center">
                {errorTypeDistribution.map((item) => (
                  <div key={item.name} className="flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: item.color }} />
                    <span className="text-xs text-ub-text-muted">{item.name}</span>
                    <span className="text-xs font-semibold text-ub-text-primary">{item.value}</span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* By Severity (Horizontal Bars) */}
          <Card className="bg-ub-surface border-ub-border">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold text-ub-text-primary">By Severity</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {severityDistribution.map((item) => {
                  const maxCount = Math.max(...severityDistribution.map((s) => s.count));
                  const pct = Math.round((item.count / maxCount) * 100);
                  return (
                    <div key={item.name} className="space-y-1">
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-ub-text-muted text-xs">{item.name}</span>
                        <span className="text-ub-text-primary text-xs font-semibold">{item.count}</span>
                      </div>
                      <div className="flex h-2 w-full overflow-hidden rounded-full bg-ub-background">
                        <div
                          className="rounded-full transition-all duration-500"
                          style={{ width: `${pct}%`, backgroundColor: item.color }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}import { useErrors } from '@/hooks/useApi';


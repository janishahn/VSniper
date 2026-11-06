import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Fragment, useState } from 'react';
import { Link } from 'react-router-dom';
import { Download, Pause, Play, Upload } from 'lucide-react';

import { api } from '../lib/api';
import { useBuckets } from '../lib/clothingItems';
import { formatBerlinDateTime } from '../lib/datetime';
import { queryKeys } from '../lib/queryKeys';
import { useCurrentUser, useUserId } from '../lib/useUserId';
import { ErrorText } from '../components/ErrorText';
import { ScoreDistributionChart } from '../components/ScoreDistributionChart';
import { Badge, Empty, Loading, PageHeader, Section, Stat, StatusDot, type Tone } from '../components/ui';
import type { OperationsSnapshot, SearchRunRecord } from '../types';

function formatDuration(ms: number | null): string {
  if (ms === null) return '–';
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  return `${m}m ${s}s`;
}

function formatAge(seconds: number | null): string {
  if (seconds === null) return '–';
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

function runTone(status: string): Tone {
  if (status === 'completed') return 'ok';
  if (status === 'running') return 'accent';
  if (status === 'failed') return 'bad';
  return 'neutral';
}

function RunRow({ run, searchName }: { run: SearchRunRecord; searchName: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <Fragment>
      <tr className={expanded ? 'is-open' : undefined}>
        <td>{searchName}</td>
        <td><Badge tone={runTone(run.status)}>{run.status}</Badge></td>
        <td className="muted">{run.mode} · {run.trigger}</td>
        <td className="r">{run.fetched_count}</td>
        <td className="r" title={`${run.new_judged_count} new this run, ${run.judged_count} judged in total`}>
          {run.new_judged_count}<span className="faint"> / {run.judged_count}</span>
        </td>
        <td className="r">{run.alert_count}</td>
        <td className="r nowrap">{formatDuration(run.duration_ms)}</td>
        <td className="r">${run.cost_usd.toFixed(4)}</td>
        <td className="nowrap muted">{formatBerlinDateTime(run.started_at, '–')}</td>
        <td className="r">
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
            {expanded ? 'Hide' : 'Details'}
          </button>
        </td>
      </tr>
      {expanded ? (
        <tr>
          <td colSpan={10} className="expand">
            <dl className="kv">
              {run.judge_model ? <><dt>Judge model</dt><dd>{run.judge_model}</dd></> : null}
              {run.vinted_status ? <><dt>Vinted status</dt><dd>{run.vinted_status}</dd></> : null}
              {run.vinted_detail ? <><dt>Vinted detail</dt><dd>{run.vinted_detail}</dd></> : null}
              <dt>Tokens</dt>
              <dd>{run.input_tokens.toLocaleString()} in · {run.output_tokens.toLocaleString()} out</dd>
              <dt>Queued deliveries</dt>
              <dd>{run.queued_delivery_count}</dd>
              {run.finished_at ? <><dt>Finished</dt><dd>{formatBerlinDateTime(run.finished_at, '–')}</dd></> : null}
              {run.error ? <><dt>Error</dt><dd><code>{run.error}</code></dd></> : null}
              {run.failures_by_reason ? (
                <>
                  <dt>Failure reasons</dt>
                  <dd>
                    {Object.entries(run.failures_by_reason).map(([reason, count]) => `${reason}: ${count}`).join(' · ')}
                  </dd>
                </>
              ) : null}
            </dl>
          </td>
        </tr>
      ) : null}
    </Fragment>
  );
}

function BackupSection() {
  const queryClient = useQueryClient();
  const [includeCache, setIncludeCache] = useState(false);

  const exportMutation = useMutation({
    mutationFn: () => api.exportData(includeCache),
  });

  const importMutation = useMutation({
    mutationFn: (file: File) => api.importData(file),
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const onImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const confirmed = window.confirm(
      `Importing "${file.name}" will OVERWRITE all current data (database, uploads, and cache).\n\n` +
        'A timestamped backup of the previous state is kept in the data directory.\n\n' +
        'Continue?',
    );
    e.target.value = '';
    if (!confirmed) return;
    importMutation.mutate(file);
  };

  const busy = exportMutation.isPending || importMutation.isPending;

  return (
    <Section
      title="Backup"
      sub="Export the database, uploads and settings as one ZIP. Importing replaces everything and pauses the worker while it runs."
    >
      <div className="panel stack">
        <div className="row">
          <button type="button" className="btn" onClick={() => exportMutation.mutate()} disabled={busy}>
            <Download size={15} aria-hidden="true" />
            {exportMutation.isPending ? 'Exporting…' : 'Export backup'}
          </button>
          <label className="btn btn-file" aria-disabled={busy}>
            <Upload size={15} aria-hidden="true" />
            {importMutation.isPending ? 'Importing…' : 'Import backup'}
            <input type="file" accept=".zip,application/zip" onChange={onImportFile} disabled={busy} />
          </label>
          <label className="check small">
            <input type="checkbox" checked={includeCache} onChange={(e) => setIncludeCache(e.target.checked)} />
            Include cached images in the export
          </label>
        </div>
        <ErrorText error={exportMutation.error} prefix="Export failed" />
        <ErrorText error={importMutation.error} prefix="Import failed" />
        {exportMutation.isSuccess ? <p className="success-text">Backup downloaded.</p> : null}
        {importMutation.isSuccess ? (
          <p className={importMutation.data.reloaded ? 'success-text' : 'tone-warn small'}>
            Imported {importMutation.data.restored_files} file{importMutation.data.restored_files === 1 ? '' : 's'}.
            {importMutation.data.reloaded ? ' State reloaded.' : ' Reload failed — restart the container.'}
          </p>
        ) : null}
      </div>
    </Section>
  );
}

function WorkerSection({ ops }: { ops: OperationsSnapshot }) {
  const userId = useUserId();
  const queryClient = useQueryClient();
  const { clothingItemLabel } = useBuckets();
  const isPaused = ops.maintenance_mode !== 'idle';

  const pauseMutation = useMutation({
    mutationFn: (reason: string) => api.maintenancePause(reason),
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.operations }),
  });
  const resumeMutation = useMutation({
    mutationFn: () => api.maintenanceResume(),
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.operations }),
  });
  const cancelMutation = useMutation({
    mutationFn: (searchId: string) => api.cancelSearch(userId, searchId),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.operations });
      queryClient.invalidateQueries({ queryKey: ['search-runs'] });
    },
  });

  const queued = ops.delivery_summary.pending + ops.delivery_summary.processing;

  return (
    <Section
      title="Worker"
      sub={isPaused
        ? `Paused${ops.maintenance_started_at ? ` since ${formatBerlinDateTime(ops.maintenance_started_at, '–')}` : ''}${ops.maintenance_reason ? ` · ${ops.maintenance_reason}` : ''}. Scans and manual runs are blocked.`
        : 'Pausing stops scheduled scans and blocks manual runs. Imports pause the worker on their own.'}
      actions={isPaused ? (
        <button type="button" className="btn btn-primary" onClick={() => resumeMutation.mutate()} disabled={resumeMutation.isPending}>
          <Play size={15} aria-hidden="true" />
          {resumeMutation.isPending ? 'Resuming…' : 'Resume worker'}
        </button>
      ) : (
        <button type="button" className="btn" onClick={() => pauseMutation.mutate('Manual pause from Dashboard')} disabled={pauseMutation.isPending}>
          <Pause size={15} aria-hidden="true" />
          {pauseMutation.isPending ? 'Pausing…' : 'Pause worker'}
        </button>
      )}
    >
      <ErrorText error={pauseMutation.error} prefix="Pause failed" />
      <ErrorText error={resumeMutation.error} prefix="Resume failed" />
      <div className="stats">
        <Stat
          label="Heartbeat"
          value={ops.worker_heartbeat ? formatBerlinDateTime(ops.worker_heartbeat.last_heartbeat_at, 'never') : 'No worker'}
          note={ops.worker_heartbeat ? `Cycle ${ops.worker_heartbeat.cycle_count} · ${ops.worker_heartbeat.phase}` : 'Not running'}
          tone={ops.worker_heartbeat ? undefined : 'warn'}
        />
        <Stat
          label="Active tasks"
          value={ops.active_worker_tasks.length}
          note={ops.active_worker_tasks.map((t) => t.operation).join(', ') || 'Idle'}
        />
        <Stat label="Taste recompute" value={ops.recompute_status} />
        <Stat label="Queued alerts" value={queued} note={`${ops.delivery_summary.sent} sent in total`} />
        <Stat
          label="Failed alerts"
          value={ops.delivery_summary.failed}
          tone={ops.delivery_summary.failed > 0 ? 'bad' : undefined}
          to={ops.delivery_summary.failed > 0 ? 'candidates?delivery_status=failed&feedback=all' : undefined}
        />
      </div>

      {ops.search_claims.some((claim) => claim.run_status === 'running' || claim.is_stale) ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Running search</th>
                <th>Bucket</th>
                <th>Status</th>
                <th className="r">Claim age</th>
                <th>Last run</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {ops.search_claims
                .filter((claim) => claim.run_status === 'running' || claim.is_stale)
                .map((claim) => (
                  <tr key={claim.search_id}>
                    <td>{claim.search_name}</td>
                    <td className="muted">{clothingItemLabel(claim.clothing_item)}</td>
                    <td>
                      <span className="row">
                        <Badge tone={runTone(claim.run_status)}>{claim.run_status}</Badge>
                        {claim.is_stale ? <Badge tone="bad">stale</Badge> : null}
                      </span>
                    </td>
                    <td className="r">{formatAge(claim.claim_age_seconds)}</td>
                    <td className="muted nowrap">{formatBerlinDateTime(claim.last_run_at, 'never')}</td>
                    <td className="r">
                      {claim.run_status === 'running' ? (
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => cancelMutation.mutate(claim.search_id)}
                          disabled={cancelMutation.isPending && cancelMutation.variables === claim.search_id}
                        >
                          {cancelMutation.isPending && cancelMutation.variables === claim.search_id ? 'Cancelling…' : 'Cancel'}
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <ErrorText error={cancelMutation.error} prefix="Cancel failed" />

      {ops.delivery_summary.latest_failures.length > 0 ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Failed alert</th>
                <th>Bucket</th>
                <th className="r">Attempts</th>
                <th>Last error</th>
              </tr>
            </thead>
            <tbody>
              {ops.delivery_summary.latest_failures.map((c) => (
                <tr key={c.id}>
                  <td><a href={c.url} target="_blank" rel="noreferrer">{c.title}</a></td>
                  <td className="muted">{clothingItemLabel(c.clothing_item)}</td>
                  <td className="r">{c.telegram_delivery_attempt_count}</td>
                  <td className="muted">{c.telegram_delivery_last_error ?? '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Section>
  );
}

function RunsSection() {
  const userId = useUserId();
  const searchesQuery = useQuery({ queryKey: queryKeys.searches(userId), queryFn: () => api.getSearches(userId) });
  const [searchFilter, setSearchFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const runsQuery = useQuery({
    queryKey: queryKeys.searchRuns({ search_id: searchFilter, status: statusFilter }),
    queryFn: () => api.getSearchRuns({ search_id: searchFilter || undefined, status: statusFilter || undefined, limit: 50 }),
    refetchInterval: 15_000,
  });
  const searches = searchesQuery.data ?? [];
  const names = new Map(searches.map((search) => [search.id, search.name]));
  const runs = runsQuery.data?.items ?? [];

  return (
    <Section
      title="Scan runs"
      sub={runsQuery.data ? `Latest 50 of ${runsQuery.data.total}` : undefined}
      actions={(
        <>
          <label className="field-inline">
            <span className="visually-hidden">Search</span>
            <select value={searchFilter} onChange={(e) => setSearchFilter(e.target.value)}>
              <option value="">All searches</option>
              {searches.map((search) => (
                <option key={search.id} value={search.id}>{search.name}</option>
              ))}
            </select>
          </label>
          <label className="field-inline">
            <span className="visually-hidden">Status</span>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">Any status</option>
              <option value="completed">Completed</option>
              <option value="running">Running</option>
              <option value="failed">Failed</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </label>
        </>
      )}
    >
      {runsQuery.isLoading ? (
        <Loading label="Loading runs…" />
      ) : runsQuery.isError ? (
        <ErrorText error={runsQuery.error} prefix="Could not load scan runs" />
      ) : runs.length === 0 ? (
        <Empty title={searchFilter || statusFilter ? 'No runs match these filters' : 'No scan runs yet'}>
          {searchFilter || statusFilter ? null : 'The worker records a run each time it scans a search.'}
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Search</th>
                <th>Status</th>
                <th>Mode</th>
                <th className="r">Fetched</th>
                <th className="r">Judged</th>
                <th className="r">Alerts</th>
                <th className="r">Duration</th>
                <th className="r">Cost</th>
                <th>Started</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => <RunRow key={run.id} run={run} searchName={names.get(run.search_id) ?? run.search_id} />)}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

export function DashboardPage() {
  const { userId, user } = useCurrentUser();
  const statsQuery = useQuery({ queryKey: queryKeys.stats(userId), queryFn: () => api.getDashboardStats(userId), refetchInterval: 30_000 });
  const settingsQuery = useQuery({ queryKey: queryKeys.settings, queryFn: api.getSettings });
  const tasteQuery = useQuery({ queryKey: queryKeys.taste(userId), queryFn: () => api.getTaste(userId) });
  const opsQuery = useQuery({ queryKey: queryKeys.operations, queryFn: api.getOperationsStatus, refetchInterval: 10_000 });

  if (statsQuery.isLoading || settingsQuery.isLoading) {
    return <div className="page"><PageHeader title="Dashboard" /><Loading label="Loading dashboard…" /></div>;
  }

  if (statsQuery.isError || settingsQuery.isError || !statsQuery.data || !settingsQuery.data) {
    return (
      <div className="page">
        <PageHeader title="Dashboard" />
        <ErrorText error={statsQuery.error ?? settingsQuery.error ?? 'Could not load dashboard data.'} />
      </div>
    );
  }

  const stats = statsQuery.data;
  const settings = settingsQuery.data;
  const ops = opsQuery.data;


  // Recompute is "due" when training data changed and no run is currently in flight.
  const dirty = tasteQuery.data?.dirty_counts;
  const recomputeStatus = tasteQuery.data?.recompute_state?.status;
  const recomputeDue =
    !!dirty &&
    recomputeStatus !== 'running' &&
    (dirty.new_or_changed_samples > 0 || dirty.manual_note_changed);

  const workerTone: Tone = !ops ? 'neutral' : ops.maintenance_mode !== 'idle' ? 'warn' : ops.worker_heartbeat ? 'ok' : 'bad';
  const workerLabel = !ops
    ? 'Worker'
    : ops.maintenance_mode !== 'idle'
      ? 'Worker paused'
      : ops.worker_heartbeat ? 'Worker running' : 'Worker not running';

  return (
    <div className="page">
      <PageHeader
        title="Dashboard"
        sub={`Last successful scan ${formatBerlinDateTime(stats.last_successful_scan_at, 'not yet')}`}
      />

      {recomputeDue ? (
        <div className="notice notice-warn">
          <span>
            <strong>Taste profile is out of date.</strong>{' '}
            {dirty?.new_or_changed_samples
              ? `${dirty.new_or_changed_samples} sample${dirty.new_or_changed_samples === 1 ? '' : 's'} changed`
              : 'The taste note changed'} since the last recompute.
          </span>
          <span className="spacer" />
          <Link to="my-taste" className="btn btn-sm">Open My Taste</Link>
        </div>
      ) : null}

      <div className="health" aria-label="Connections">
        <div className="health-item">
          <StatusDot tone={settings.telegram_configured ? 'ok' : 'bad'}>Telegram</StatusDot>
          <p>{settings.telegram_configured ? 'Bot token and chat set' : 'Bot token or chat ID missing'}</p>
        </div>
        <div className="health-item">
          <StatusDot tone={settings.ai_configured ? 'ok' : 'bad'}>OpenAI</StatusDot>
          <p>{settings.ai_configured ? 'Key set' : 'Key missing'} · vision {settings.vision_model} · decision {settings.decision_model}</p>
        </div>
        <div className="health-item">
          <StatusDot tone={workerTone}>{workerLabel}</StatusDot>
          <p>
            {ops?.worker_heartbeat
              ? `Last heartbeat ${formatBerlinDateTime(ops.worker_heartbeat.last_heartbeat_at, 'never')}`
              : opsQuery.isError ? 'Status unavailable' : 'No heartbeat recorded'}
          </p>
        </div>
        {!settings.telegram_configured ? (
          <p className="small" style={{ gridColumn: '1 / -1' }}>
            <Link to="/settings">Fix connections in Settings</Link>
          </p>
        ) : null}
      </div>

      <div className="stats">
        <Stat label="Candidates today" value={stats.candidates_today} to="candidates?window=1d&feedback=all" />
        <Stat label="Avg alert score" value={stats.avg_alert_score ? stats.avg_alert_score.toFixed(1) : '–'} to="candidates?decision=alert&feedback=all" />
        <Stat label="Likes" value={stats.likes} to="candidates?feedback=like" />
        <Stat label="Dislikes" value={stats.dislikes} to="candidates?feedback=dislike" />
        <Stat label="Active searches" value={stats.active_searches} to="searches" />
        <Stat label="Pending alerts" value={stats.pending_deliveries} />
        <Stat
          label="Failed alerts"
          value={stats.failed_deliveries}
          tone={stats.failed_deliveries > 0 ? 'bad' : undefined}
          to="candidates?delivery_status=failed&feedback=all"
        />
      </div>

      {user ? <ScoreDistributionChart threshold={user.alert_threshold} /> : null}

      {opsQuery.isLoading ? (
        <Loading label="Loading worker status…" />
      ) : !ops ? (
        <ErrorText error={opsQuery.error ?? 'Could not load operations status.'} />
      ) : (
        <>
          <WorkerSection ops={ops} />
          <RunsSection />
        </>
      )}

      <BackupSection />
    </div>
  );
}

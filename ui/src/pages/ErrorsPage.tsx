import { useEffect } from 'react';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';

import { ErrorText } from '../components/ErrorText';
import { Badge, Empty, Loading, PageHeader, type Tone } from '../components/ui';
import { api } from '../lib/api';
import { formatBerlinDateTime } from '../lib/datetime';
import { queryKeys } from '../lib/queryKeys';
import type {
  ErrorEventRecord,
  ErrorSource,
  ErrorTelegramNotificationStatus,
} from '../types';

const PAGE_SIZE = 50;
const SOURCES: Array<{ value: ErrorSource | ''; label: string }> = [
  { value: '', label: 'All sources' },
  { value: 'api', label: 'API' },
  { value: 'search', label: 'Searches' },
  { value: 'candidate_judgment', label: 'Candidate judging' },
  { value: 'taste_recompute', label: 'Taste recompute' },
  { value: 'telegram', label: 'Telegram' },
  { value: 'worker', label: 'Worker' },
];
const SOURCE_VALUES = new Set<string>(SOURCES.map((source) => source.value));

function notificationLabel(status: ErrorTelegramNotificationStatus): string {
  if (status === 'not_requested') return 'Not requested';
  if (status === 'pending') return 'Waiting';
  if (status === 'processing') return 'Sending';
  if (status === 'sent') return 'Sent';
  return 'Failed';
}

function notificationTone(status: ErrorTelegramNotificationStatus): Tone {
  if (status === 'sent') return 'ok';
  if (status === 'failed') return 'bad';
  if (status === 'pending' || status === 'processing') return 'accent';
  return 'neutral';
}

const SOURCE_LABEL = Object.fromEntries(SOURCES.map((source) => [source.value, source.label]));

function ErrorEntry({ event }: { event: ErrorEventRecord }) {
  const hasDetails = Object.keys(event.details).length > 0;
  return (
    <article className="error-item">
      <div className="row-between">
        <div className="row">
          <Badge tone="bad">{SOURCE_LABEL[event.source] ?? event.source}</Badge>
          <span className="small muted">{event.operation}</span>
        </div>
        <time className="small muted" dateTime={event.occurred_at}>
          {formatBerlinDateTime(event.occurred_at, event.occurred_at)}
        </time>
      </div>
      <h3>{event.summary}</h3>
      <p className="error-msg">{event.message}</p>
      <div className="error-meta">
        <span>#{event.id}</span>
        {event.exception_type ? <span>{event.exception_type}</span> : null}
        {event.related_entity_type && event.related_entity_id ? (
          <span>{event.related_entity_type}: {event.related_entity_id}</span>
        ) : null}
        {event.telegram_notification_status !== 'not_requested' ? (
          <span className="status">
            <Badge tone={notificationTone(event.telegram_notification_status)}>Telegram {notificationLabel(event.telegram_notification_status).toLowerCase()}</Badge>
            {event.telegram_notification_attempt_count > 0
              ? `${event.telegram_notification_attempt_count} attempt${event.telegram_notification_attempt_count === 1 ? '' : 's'}`
              : null}
          </span>
        ) : null}
      </div>
      {event.telegram_notification_last_error ? (
        <ErrorText error={event.telegram_notification_last_error} prefix="Telegram notification failed" />
      ) : null}
      {hasDetails ? (
        <details className="disclosure">
          <summary>Technical details</summary>
          <div className="disclosure-body">
            <pre className="pre">{JSON.stringify(event.details, null, 2)}</pre>
          </div>
        </details>
      ) : null}
    </article>
  );
}

export function ErrorsPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const sourceParam = searchParams.get('source') ?? '';
  const source = SOURCE_VALUES.has(sourceParam) ? (sourceParam as ErrorSource | '') : '';
  const rawPage = Number.parseInt(searchParams.get('page') ?? '0', 10);
  const page = Math.max(0, Number.isFinite(rawPage) ? rawPage : 0);
  const offset = page * PAGE_SIZE;
  const keyParams = { source, offset, limit: PAGE_SIZE };

  const errorsQuery = useQuery({
    queryKey: queryKeys.errors(keyParams),
    queryFn: () => api.getErrors({ source: source || undefined, offset, limit: PAGE_SIZE }),
    refetchInterval: 10_000,
    placeholderData: (previous) => previous,
  });

  const toggleMutation = useMutation({
    mutationFn: api.updateErrorTelegramNotifications,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['errors'] }),
  });

  const setFilter = (nextSource: string) => {
    const next = new URLSearchParams(searchParams);
    if (nextSource) next.set('source', nextSource);
    else next.delete('source');
    next.delete('page');
    setSearchParams(next, { replace: true });
  };

  const setPage = (nextPage: number) => {
    const next = new URLSearchParams(searchParams);
    if (nextPage > 0) next.set('page', String(nextPage));
    else next.delete('page');
    setSearchParams(next, { replace: true });
  };

  const total = errorsQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  useEffect(() => {
    if (errorsQuery.data && page >= totalPages) setPage(totalPages - 1);
  }, [errorsQuery.data, page, totalPages]);

  const notificationsEnabled = errorsQuery.data?.telegram_notifications_enabled ?? false;
  const canEnable = !!errorsQuery.data && (errorsQuery.data.telegram_configured || notificationsEnabled);

  return (
    <div className="page">
      <PageHeader
        title="Errors"
        sub="Failures from scans, judging, recomputes, Telegram, API requests and the worker. Each is recorded once, after retries."
      />

      <div className="panel toggle-row">
        <div>
          <h3>Send errors to Telegram</h3>
          <p className="section-sub">
            {errorsQuery.data && !errorsQuery.data.telegram_configured
              ? 'Set a bot token and chat ID in Settings first.'
              : 'Each new error goes to the alert chat. Delivery is retried three times.'}
          </p>
        </div>
        <label className="switch">
          <input
            type="checkbox"
            checked={notificationsEnabled}
            disabled={toggleMutation.isPending || !canEnable}
            onChange={(event) => toggleMutation.mutate(event.target.checked)}
          />
          <span className="switch-track" aria-hidden="true" />
          {notificationsEnabled ? 'On' : 'Off'}
        </label>
      </div>
      <ErrorText error={toggleMutation.error} prefix="Could not update Telegram error notifications" />

      <div className="stack">
        <div className="toolbar">
          <label className="field-inline">
            <span className="visually-hidden">Source</span>
            <select value={source} onChange={(event) => setFilter(event.target.value)}>
              {SOURCES.map((option) => (
                <option key={option.value || 'all'} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <span className="small muted">{total} error{total === 1 ? '' : 's'}</span>
        </div>

        {errorsQuery.isLoading ? <Loading label="Loading errors…" /> : null}
        {errorsQuery.isError ? <ErrorText error={errorsQuery.error} prefix="Could not load errors" /> : null}
        {errorsQuery.data?.items.length === 0 ? (
          <Empty title={source ? 'No errors from this source' : 'No errors recorded'} />
        ) : null}
        {errorsQuery.data?.items.length ? (
          <div className="error-list">
            {errorsQuery.data.items.map((event) => <ErrorEntry event={event} key={event.id} />)}
          </div>
        ) : null}
      </div>

      {errorsQuery.data && totalPages > 1 ? (
        <nav className="pager" aria-label="Pages">
          <button type="button" className="btn btn-sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</button>
          <span>Page {page + 1} of {totalPages}</span>
          <button type="button" className="btn btn-sm" disabled={page >= totalPages - 1} onClick={() => setPage(page + 1)}>Older</button>
        </nav>
      ) : null}
    </div>
  );
}

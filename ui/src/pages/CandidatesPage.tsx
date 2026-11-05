import { Fragment, useEffect, useState } from 'react';

import { useMutation, useQuery, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { ChevronDown, ChevronRight, ExternalLink, RotateCw, ThumbsDown, ThumbsUp } from 'lucide-react';

import { api } from '../lib/api';
import { useBuckets } from '../lib/clothingItems';
import { formatBerlinDateTime } from '../lib/datetime';
import { queryKeys } from '../lib/queryKeys';
import { ErrorText } from '../components/ErrorText';
import { Badge, Empty, Loading, PageHeader, Score, type Tone } from '../components/ui';
import type { CandidatePage as CandidatePageData, CandidateRecord, ClothingItem } from '../types';

type StageFilter = 'all' | 'vlm_judged' | 'failed';
type ClothingItemFilter = ClothingItem | 'all';
type ViewMode = 'cards' | 'compact';
type TimeWindow = '1h' | '6h' | '12h' | '1d' | '7d' | '30d';
type FeedbackVars = { candidateId: string; verdict: 'like' | 'dislike'; comment: string };
type FeedbackMutation = UseMutationResult<CandidateRecord, Error, FeedbackVars, unknown>;
type RetryMutation = UseMutationResult<void, Error, string, unknown>;

const STAGE_LABELS: Record<StageFilter, string> = {
  all: 'All',
  vlm_judged: 'Judged',
  failed: 'Failed',
};

const DECISION_OPTIONS: Record<string, string> = {
  all: 'Any decision',
  alert: 'Alert',
  review: 'Review',
  discard: 'Discard',
};

const FEEDBACK_OPTIONS: Record<string, string> = {
  all: 'Any feedback',
  unknown: 'No feedback yet',
  like: 'Liked',
  dislike: 'Disliked',
};

const DELIVERY_OPTIONS: Record<string, string> = {
  all: 'Any delivery',
  sent: 'Sent',
  pending: 'Pending',
  failed: 'Failed',
};

const SORT_OPTIONS: Record<string, string> = {
  score_desc: 'Highest score',
  score_asc: 'Lowest score',
  newest: 'Newest',
  oldest: 'Oldest',
  price_asc: 'Cheapest',
  price_desc: 'Most expensive',
};

const TIME_WINDOW_OPTIONS: Record<TimeWindow, string> = {
  '1h': 'Last hour',
  '6h': 'Last 6 hours',
  '12h': 'Last 12 hours',
  '1d': 'Last day',
  '7d': 'Last 7 days',
  '30d': 'Last 30 days',
};

function formatEuroPrice(price: number): string {
  return `${Math.round(price)} €`;
}

function deliveryTone(status: CandidateRecord['telegram_delivery_status']): Tone {
  if (status === 'sent') return 'ok';
  if (status === 'failed') return 'bad';
  if (status === 'pending' || status === 'processing') return 'accent';
  return 'neutral';
}

const DELIVERY_LABEL: Record<CandidateRecord['telegram_delivery_status'], string> = {
  not_queued: 'Not sent',
  pending: 'Queued',
  processing: 'Sending',
  sent: 'Sent',
  failed: 'Send failed',
};

function CandidateGallery({ candidate }: { candidate: CandidateRecord }) {
  const [active, setActive] = useState(0);
  const images = candidate.image_urls;
  if (images.length === 0) return <div className="cand-photo" aria-hidden="true" />;
  const safeActive = Math.min(active, images.length - 1);
  return (
    <>
      <a href={candidate.url} target="_blank" rel="noreferrer" tabIndex={-1}>
        <img className="cand-photo" src={images[safeActive]} alt={candidate.title} loading="lazy" />
      </a>
      {images.length > 1 ? (
        <div className="cand-thumbs">
          {images.map((url, index) => (
            <button
              key={url}
              type="button"
              className="cand-thumb"
              aria-pressed={index === safeActive}
              onClick={() => setActive(index)}
              aria-label={`Photo ${index + 1}`}
            >
              <img src={url} alt="" loading="lazy" />
            </button>
          ))}
        </div>
      ) : null}
    </>
  );
}

function FeedbackControls({
  candidate,
  feedbackMutation,
  compact = false,
}: {
  candidate: CandidateRecord;
  feedbackMutation: FeedbackMutation;
  compact?: boolean;
}) {
  const [comment, setComment] = useState(candidate.feedback_comment ?? '');
  const pending = feedbackMutation.isPending && feedbackMutation.variables?.candidateId === candidate.id;

  useEffect(() => {
    setComment(candidate.feedback_comment ?? '');
  }, [candidate.feedback_comment]);

  const submit = (verdict: 'like' | 'dislike') => feedbackMutation.mutate({ candidateId: candidate.id, verdict, comment });

  return (
    <>
      {!compact ? (
        <input
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          placeholder="Why? (optional, saved with your vote)"
          aria-label="Feedback comment"
        />
      ) : null}
      <button
        type="button"
        className={`btn fb-like${candidate.feedback === 'like' ? ' btn-on' : ''}${compact ? ' btn-sm icon-btn' : ''}`}
        aria-pressed={candidate.feedback === 'like'}
        onClick={() => submit('like')}
        disabled={pending}
        title="Like"
      >
        <ThumbsUp size={15} aria-hidden="true" />
        {compact ? <span className="visually-hidden">Like</span> : candidate.feedback === 'like' ? 'Liked' : 'Like'}
      </button>
      <button
        type="button"
        className={`btn fb-dislike${candidate.feedback === 'dislike' ? ' btn-on' : ''}${compact ? ' btn-sm icon-btn' : ''}`}
        aria-pressed={candidate.feedback === 'dislike'}
        onClick={() => submit('dislike')}
        disabled={pending}
        title="Dislike"
      >
        <ThumbsDown size={15} aria-hidden="true" />
        {compact ? <span className="visually-hidden">Dislike</span> : candidate.feedback === 'dislike' ? 'Disliked' : 'Dislike'}
      </button>
    </>
  );
}

function TechnicalDetails({ candidate, retryMutation }: { candidate: CandidateRecord; retryMutation: RetryMutation }) {
  return (
    <details className="disclosure cand-tech">
      <summary>Technical details</summary>
      <div className="disclosure-body">
        <dl className="kv">
          <dt>Source search</dt>
          <dd>{candidate.source_search_name ?? candidate.source_search_id}</dd>
          <dt>Seen</dt>
          <dd>
            {formatBerlinDateTime(candidate.first_seen_at, '–')}
            {candidate.last_scan_mode ? ` · ${candidate.last_scan_mode} scan` : ''}
            {candidate.source_region ? ` · ${candidate.source_region}` : ''}
          </dd>
          <dt>Delivery</dt>
          <dd>
            {DELIVERY_LABEL[candidate.telegram_delivery_status]} · {candidate.telegram_delivery_attempt_count} attempt
            {candidate.telegram_delivery_attempt_count === 1 ? '' : 's'}
            {candidate.telegram_delivery_status === 'failed' && candidate.telegram_delivery_last_error
              ? ` · ${candidate.telegram_delivery_last_error}`
              : ''}
          </dd>
          <dt>Extraction</dt>
          <dd>{candidate.extraction_status}{candidate.extraction_error ? ` · ${candidate.extraction_error}` : ''}</dd>
          <dt>Judge</dt>
          <dd>
            {candidate.score_trace.model ?? 'unknown model'} · prompt v{candidate.score_trace.prompt_version ?? '?'}
            {candidate.score_trace.judged_at ? ` · ${formatBerlinDateTime(candidate.score_trace.judged_at)}` : ''}
          </dd>
        </dl>
      </div>
      {retryMutation.isError && retryMutation.variables === candidate.id ? (
        <ErrorText error={retryMutation.error} prefix="Retry failed" />
      ) : null}
    </details>
  );
}

function CandidateCard({
  candidate,
  feedbackMutation,
  retryMutation,
}: {
  candidate: CandidateRecord;
  feedbackMutation: FeedbackMutation;
  retryMutation: RetryMutation;
}) {
  const trace = candidate.score_trace;
  const { clothingItemLabel } = useBuckets();
  const meta = [clothingItemLabel(candidate.clothing_item), candidate.brand, candidate.size ? `size ${candidate.size}` : '']
    .filter(Boolean)
    .join(' · ');

  return (
    <article className="cand">
      <div className="cand-media">
        <CandidateGallery candidate={candidate} />
      </div>
      <div className="cand-body">
        <div className="cand-head">
          <div>
            <h2 className="cand-title">
              <a href={candidate.url} target="_blank" rel="noreferrer">{candidate.title}</a>
            </h2>
            <p className="cand-meta">
              <span className="price">{formatEuroPrice(candidate.price_eur)}</span> · {meta}
            </p>
          </div>
          <div className="cand-score">
            <Score value={trace.score_10} decision={trace.decision} size="lg" />
            <span>{trace.decision === 'alert' ? `Alert · ${DELIVERY_LABEL[candidate.telegram_delivery_status].toLowerCase()}` : trace.decision}</span>
          </div>
        </div>

        <p className="cand-why">{trace.explanation || trace.summary}</p>

        {candidate.matched_filters.length || trace.labels.length || trace.concerns.length ? (
          <div className="chips cand-tags">
            {candidate.matched_filters.map((item) => <span className="chip" key={`f-${item}`}><strong>{item}</strong></span>)}
            {trace.labels.map((item) => <span className="chip" key={`l-${item}`}>{item}</span>)}
            {trace.concerns.map((item) => <span className="chip chip-concern" key={`c-${item}`}>{item}</span>)}
          </div>
        ) : null}

        <TechnicalDetails candidate={candidate} retryMutation={retryMutation} />

        <div className="cand-actions">
          <FeedbackControls candidate={candidate} feedbackMutation={feedbackMutation} />
          {candidate.telegram_delivery_status === 'failed' ? (
            <button
              type="button"
              className="btn"
              onClick={() => retryMutation.mutate(candidate.id)}
              disabled={retryMutation.isPending && retryMutation.variables === candidate.id}
            >
              <RotateCw size={15} aria-hidden="true" />
              Resend alert
            </button>
          ) : null}
        </div>
        {feedbackMutation.isError && feedbackMutation.variables?.candidateId === candidate.id ? (
          <ErrorText error={feedbackMutation.error} prefix="Feedback failed" />
        ) : null}
      </div>
    </article>
  );
}

function CompactRow({
  candidate,
  feedbackMutation,
  retryMutation,
}: {
  candidate: CandidateRecord;
  feedbackMutation: FeedbackMutation;
  retryMutation: RetryMutation;
}) {
  const [expanded, setExpanded] = useState(false);
  const thumb = candidate.image_urls[0];
  const trace = candidate.score_trace;
  const { clothingItemLabel } = useBuckets();

  return (
    <Fragment>
      <tr className={expanded ? 'is-open' : undefined}>
        <td>
          {thumb ? <img src={thumb} alt="" className="compact-thumb" loading="lazy" /> : <div className="compact-thumb" />}
        </td>
        <td><Score value={trace.score_10} decision={trace.decision} /></td>
        <td>
          <div className="compact-title">
            <a href={candidate.url} target="_blank" rel="noreferrer" style={{ color: 'inherit' }}>{candidate.title}</a>
          </div>
          <div className="small muted">
            {clothingItemLabel(candidate.clothing_item)} · {candidate.brand || 'no brand'} · {candidate.size}
          </div>
        </td>
        <td className="r nowrap">{formatEuroPrice(candidate.price_eur)}</td>
        <td><Badge tone={deliveryTone(candidate.telegram_delivery_status)}>{DELIVERY_LABEL[candidate.telegram_delivery_status]}</Badge></td>
        <td>
          <div className="row" style={{ flexWrap: 'nowrap', justifyContent: 'flex-end' }}>
            <FeedbackControls candidate={candidate} feedbackMutation={feedbackMutation} compact />
            {candidate.telegram_delivery_status === 'failed' ? (
              <button
                type="button"
                className="btn btn-sm icon-btn"
                title="Resend alert"
                onClick={() => retryMutation.mutate(candidate.id)}
                disabled={retryMutation.isPending && retryMutation.variables === candidate.id}
              >
                <RotateCw size={14} aria-hidden="true" />
                <span className="visually-hidden">Resend alert</span>
              </button>
            ) : null}
            <a href={candidate.url} target="_blank" rel="noreferrer" className="btn btn-sm btn-ghost icon-btn" title="Open on Vinted">
              <ExternalLink size={14} aria-hidden="true" />
              <span className="visually-hidden">Open on Vinted</span>
            </a>
            <button
              type="button"
              className="btn btn-sm btn-ghost icon-btn"
              onClick={() => setExpanded(!expanded)}
              aria-expanded={expanded}
              title="Details"
            >
              {expanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
              <span className="visually-hidden">Details</span>
            </button>
          </div>
        </td>
      </tr>
      {expanded ? (
        <tr>
          <td colSpan={6} className="expand">
            <div className="stack">
              <p className="cand-why">{trace.explanation || trace.summary}</p>
              {trace.labels.length || trace.concerns.length ? (
                <div className="chips">
                  {trace.labels.map((l) => <span className="chip" key={l}>{l}</span>)}
                  {trace.concerns.map((c) => <span className="chip chip-concern" key={c}>{c}</span>)}
                </div>
              ) : null}
              <TechnicalDetails candidate={candidate} retryMutation={retryMutation} />
              <div className="cand-actions">
                <FeedbackControls candidate={candidate} feedbackMutation={feedbackMutation} />
              </div>
              {feedbackMutation.isError && feedbackMutation.variables?.candidateId === candidate.id ? (
                <ErrorText error={feedbackMutation.error} prefix="Feedback failed" />
              ) : null}
            </div>
          </td>
        </tr>
      ) : null}
    </Fragment>
  );
}

const PAGE_SIZE = 50;

export function CandidatesPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const { buckets } = useBuckets();
  const requestedItemFilter = searchParams.get('clothing_item');
  const clothingItemFilter: ClothingItemFilter =
    requestedItemFilter && buckets.some((bucket) => bucket.key === requestedItemFilter)
      ? (requestedItemFilter as ClothingItem)
      : 'all';
  const stageFilter = (searchParams.get('stage') ?? 'all') as StageFilter;
  const decisionFilter = searchParams.get('decision') ?? 'all';
  const feedbackFilter = searchParams.get('feedback') ?? 'unknown';
  const deliveryStatusFilter = searchParams.get('delivery_status') ?? 'all';
  const timeWindow = (searchParams.get('window') ?? '7d') as TimeWindow;
  const sort = searchParams.get('sort') ?? 'score_desc';
  const rawPage = parseInt(searchParams.get('page') ?? '0', 10);
  const page = Math.max(0, Number.isFinite(rawPage) ? rawPage : 0);
  const offset = page * PAGE_SIZE;
  const viewMode: ViewMode = searchParams.get('view') === 'compact' ? 'compact' : 'cards';

  const setParam = (key: string, value: string, defaultValue: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (!value || value === defaultValue) next.delete(key);
      else next.set(key, value);
      if (key !== 'page') next.delete('page');
      return next;
    }, { replace: true });
  };

  const candidatesQuery = useQuery({
    queryKey: queryKeys.candidatesPage({
      clothing_item: clothingItemFilter,
      stage: stageFilter,
      decision: decisionFilter,
      feedback: feedbackFilter,
      delivery_status: deliveryStatusFilter,
      window: timeWindow,
      sort,
      offset,
      limit: PAGE_SIZE,
    }),
    queryFn: () => api.getCandidates({
      clothing_item: clothingItemFilter,
      stage: stageFilter,
      decision: decisionFilter,
      feedback: feedbackFilter,
      delivery_status: deliveryStatusFilter,
      window: timeWindow,
      sort,
      limit: PAGE_SIZE,
      offset,
    }),
    refetchInterval: 30_000,
    placeholderData: (previous) => previous,
  });

  const feedbackMutation = useMutation({
    mutationFn: ({ candidateId, verdict, comment }: FeedbackVars) =>
      api.sendFeedbackWithComment(candidateId, verdict, comment),
    onMutate: async ({ candidateId, verdict, comment }) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.candidates });
      const snapshots = queryClient.getQueriesData<CandidatePageData>({ queryKey: queryKeys.candidates });
      queryClient.setQueriesData<CandidatePageData>({ queryKey: queryKeys.candidates }, (old) =>
        old
          ? { ...old, items: old.items.map((c) => (c.id === candidateId ? { ...c, feedback: verdict, feedback_comment: comment } : c)) }
          : old,
      );
      return { snapshots };
    },
    onError: (_error, _vars, context) => {
      context?.snapshots.forEach(([key, data]) => queryClient.setQueryData(key, data));
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.candidates });
      queryClient.invalidateQueries({ queryKey: queryKeys.taste });
      queryClient.invalidateQueries({ queryKey: queryKeys.stats });
    },
  });

  const retryMutation = useMutation({
    mutationFn: (candidateId: string) => api.retryDelivery(candidateId),
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.candidates }),
  });

  const setPage = (next: number) => setParam('page', String(Math.max(0, next)), '0');
  const total = candidatesQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  useEffect(() => {
    if (candidatesQuery.data && page > totalPages - 1) setPage(totalPages - 1);
  }, [candidatesQuery.data, page, totalPages]);

  const header = (
    <PageHeader
      title="Candidates"
      sub={candidatesQuery.data
        ? `${total} listing${total === 1 ? '' : 's'} match these filters. Votes here feed the next taste recompute, like the Telegram buttons.`
        : undefined}
      actions={(
        <div className="seg" role="group" aria-label="View">
          <button type="button" aria-pressed={viewMode === 'cards'} onClick={() => setParam('view', 'cards', 'cards')}>Cards</button>
          <button type="button" aria-pressed={viewMode === 'compact'} onClick={() => setParam('view', 'compact', 'cards')}>Table</button>
        </div>
      )}
    />
  );

  if (candidatesQuery.isLoading) return <div className="page">{header}<Loading label="Loading candidates…" /></div>;
  if (candidatesQuery.isError || !candidatesQuery.data) {
    return <div className="page">{header}<ErrorText error={candidatesQuery.error ?? 'Could not load candidates.'} /></div>;
  }

  const stageCounts = candidatesQuery.data.stage_counts;
  const itemCounts = candidatesQuery.data.item_counts;
  const visible = candidatesQuery.data.items;
  const filtersNarrowed = decisionFilter !== 'all' || feedbackFilter !== 'unknown' || deliveryStatusFilter !== 'all'
    || timeWindow !== '7d' || clothingItemFilter !== 'all' || stageFilter !== 'all';

  return (
    <div className="page">
      {header}

      <div className="stack" style={{ gap: 'var(--s3)' }}>
        <div className="seg seg-scroll" role="group" aria-label="Clothing bucket">
          <button type="button" aria-pressed={clothingItemFilter === 'all'} onClick={() => setParam('clothing_item', 'all', 'all')}>
            All <span className="count">{itemCounts.all ?? 0}</span>
          </button>
          {/* Archived buckets stay listed while they still have candidates. */}
          {buckets.filter((bucket) => !bucket.archived || itemCounts[bucket.key]).map((bucket) => (
            <button
              key={bucket.key}
              type="button"
              aria-pressed={clothingItemFilter === bucket.key}
              onClick={() => setParam('clothing_item', bucket.key, 'all')}
            >
              {bucket.label} <span className="count">{itemCounts[bucket.key] ?? 0}</span>
            </button>
          ))}
        </div>

        <div className="toolbar">
          <div className="seg" role="group" aria-label="Judging stage">
            {(Object.keys(STAGE_LABELS) as StageFilter[]).map((stage) => (
              <button key={stage} type="button" aria-pressed={stageFilter === stage} onClick={() => setParam('stage', stage, 'all')}>
                {STAGE_LABELS[stage]} <span className="count">{stageCounts[stage] ?? 0}</span>
              </button>
            ))}
          </div>
          <label className="field-inline">
            <span className="visually-hidden">Decision</span>
            <select value={decisionFilter} onChange={(e) => setParam('decision', e.target.value, 'all')}>
              {Object.entries(DECISION_OPTIONS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="field-inline">
            <span className="visually-hidden">Feedback</span>
            <select value={feedbackFilter} onChange={(e) => setParam('feedback', e.target.value, 'unknown')}>
              {Object.entries(FEEDBACK_OPTIONS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="field-inline">
            <span className="visually-hidden">Telegram delivery</span>
            <select value={deliveryStatusFilter} onChange={(e) => setParam('delivery_status', e.target.value, 'all')}>
              {Object.entries(DELIVERY_OPTIONS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="field-inline">
            <span className="visually-hidden">Time range</span>
            <select value={timeWindow} onChange={(e) => setParam('window', e.target.value, '7d')}>
              {Object.entries(TIME_WINDOW_OPTIONS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="field-inline">
            Sort
            <select value={sort} onChange={(e) => setParam('sort', e.target.value, 'score_desc')}>
              {Object.entries(SORT_OPTIONS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
        </div>
      </div>

      <div style={{ opacity: candidatesQuery.isPlaceholderData ? 0.6 : 1, transition: 'opacity 150ms ease' }}>
        {visible.length === 0 ? (
          <Empty title="No candidates match">
            {filtersNarrowed ? (
              <button type="button" className="btn btn-sm" onClick={() => setSearchParams(new URLSearchParams(), { replace: true })}>
                Reset filters
              </button>
            ) : 'Listings appear here once a search has been scanned and judged.'}
          </Empty>
        ) : viewMode === 'cards' ? (
          <div className="cand-list">
            {visible.map((candidate) => (
              <CandidateCard
                key={candidate.id}
                candidate={candidate}
                feedbackMutation={feedbackMutation}
                retryMutation={retryMutation}
              />
            ))}
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: '3.5rem' }}><span className="visually-hidden">Photo</span></th>
                  <th>Score</th>
                  <th>Listing</th>
                  <th className="r">Price</th>
                  <th>Telegram</th>
                  <th className="r">Actions</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((candidate) => (
                  <CompactRow
                    key={candidate.id}
                    candidate={candidate}
                    feedbackMutation={feedbackMutation}
                    retryMutation={retryMutation}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {total > PAGE_SIZE ? (
        <nav className="pager" aria-label="Pages">
          <button type="button" className="btn btn-sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
          <span>Page {page + 1} of {totalPages}</span>
          <button type="button" className="btn btn-sm" disabled={page + 1 >= totalPages} onClick={() => setPage(page + 1)}>Next</button>
        </nav>
      ) : null}
    </div>
  );
}

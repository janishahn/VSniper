import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Trash2 } from 'lucide-react';

import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { ErrorText } from '../components/ErrorText';
import { Loading, PageHeader, Section } from '../components/ui';
import type { StorageCategoryStats } from '../types';

function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

function files(count: number): string {
  return `${count.toLocaleString()} file${count === 1 ? '' : 's'}`;
}

// Kept data in accent shades, re-downloadable cache in neutrals.
const KEPT = ['var(--accent)', 'color-mix(in srgb, var(--accent) 65%, var(--surface))', 'color-mix(in srgb, var(--accent) 35%, var(--surface))'];
const CACHE = ['var(--text-3)', 'var(--line-strong)', 'var(--surface-3)'];

export function StoragePage() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.storage, queryFn: api.getStorageStats });

  const clearCacheMutation = useMutation({
    mutationFn: api.clearCandidateImageCache,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.storage }),
  });

  const header = <PageHeader title="Storage" sub="Disk use of the /data volume." />;

  if (query.isLoading) return <div className="page">{header}<Loading label="Loading storage…" /></div>;
  if (query.isError || !query.data) {
    return <div className="page">{header}<ErrorText error={query.error ?? 'Could not load storage data.'} /></div>;
  }

  const s = query.data;
  const parts: { label: string; note: string; cat: StorageCategoryStats; color: string }[] = [
    { label: 'Database', note: 'Searches, candidates, feedback, settings', cat: s.database, color: KEPT[0] },
    { label: 'Uploads', note: 'Your wardrobe photos', cat: s.uploads, color: KEPT[1] },
    { label: 'Feedback assets', note: 'Photos of rated listings', cat: s.feedback_assets, color: KEPT[2] },
    { label: 'Candidate images', note: 'Cache, can be cleared', cat: s.cache_candidate_images, color: CACHE[0] },
    { label: 'Taste offer images', note: 'Cache, used by recompute', cat: s.cache_taste_offers, color: CACHE[1] },
    { label: 'Other cache', note: 'Cache', cat: s.cache_other, color: CACHE[2] },
  ];

  const onClearCache = () => {
    const confirmed = window.confirm(
      'This clears the cached candidate listing thumbnails. ' +
        "They'll be re-downloaded from Vinted as needed on future scans.\n\n" +
        'Taste offer examples, uploads, and the database are not affected.\n\n' +
        'Continue?',
    );
    if (!confirmed) return;
    clearCacheMutation.mutate();
  };

  return (
    <div className="page">
      {header}

      <div className="panel stack-lg">
        <div>
          <span className="stat-label">Total</span>
          <p className="stat-value" style={{ fontSize: 'var(--fs-xl)' }}>{fmtBytes(s.total_bytes)}</p>
        </div>
        <div className="usage-bar" role="img" aria-label="Disk use by category">
          {parts.map((part) => (
            part.cat.bytes > 0 ? (
              <span
                key={part.label}
                title={`${part.label}: ${fmtBytes(part.cat.bytes)}`}
                style={{ flexGrow: part.cat.bytes, background: part.color }}
              />
            ) : null
          ))}
        </div>
        <div className="usage-legend">
          {parts.map((part) => (
            <div key={part.label}>
              <span className="status">
                <span className="dot" style={{ background: part.color }} aria-hidden="true" />
                {part.label}
              </span>
              <strong>{fmtBytes(part.cat.bytes)}</strong>
              <div className="faint">{files(part.cat.file_count)} · {part.note}</div>
            </div>
          ))}
        </div>
      </div>

      <Section
        title="Candidate image cache"
        sub="Thumbnails of scanned listings. Clearing is safe: they are fetched from Vinted again when needed. Taste offer images, uploads and the database are never touched."
      >
        <div className="row">
          <button type="button" className="btn" onClick={onClearCache} disabled={clearCacheMutation.isPending || s.cache_candidate_images.bytes === 0}>
            <Trash2 size={15} aria-hidden="true" />
            {clearCacheMutation.isPending ? 'Clearing…' : `Clear ${fmtBytes(s.cache_candidate_images.bytes)}`}
          </button>
          {clearCacheMutation.isSuccess ? (
            <span className="success-text">
              Freed {fmtBytes(clearCacheMutation.data.bytes_freed)} ({files(clearCacheMutation.data.files_removed)}).
            </span>
          ) : null}
        </div>
        <ErrorText error={clearCacheMutation.error} prefix="Clear cache failed" />
      </Section>
    </div>
  );
}

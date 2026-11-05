import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Plus, RefreshCw } from 'lucide-react';

import { ErrorText } from '../components/ErrorText';
import { Empty, Loading, Modal, PageHeader } from '../components/ui';
import { api } from '../lib/api';
import { useBuckets } from '../lib/clothingItems';
import { queryKeys } from '../lib/queryKeys';
import type { BucketRecord, VintedCatalogNode } from '../types';

type BucketForm = {
  key: string;
  label: string;
  description: string;
  catalogIds: number[];
  sizeKeywords: string;
};

const KEY_PATTERN = /^[a-z][a-z0-9_]{0,31}$/;

function slugFromLabel(label: string): string {
  return label
    .toLowerCase()
    .replace(/ä/g, 'ae')
    .replace(/ö/g, 'oe')
    .replace(/ü/g, 'ue')
    .replace(/ß/g, 'ss')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^[^a-z]+|_+$/g, '')
    .slice(0, 32);
}

function formFromBucket(bucket: BucketRecord): BucketForm {
  return {
    key: bucket.key,
    label: bucket.label,
    description: bucket.description,
    catalogIds: bucket.categories.map((category) => category.id),
    sizeKeywords: bucket.size_keywords.join(', '),
  };
}

const emptyForm: BucketForm = { key: '', label: '', description: '', catalogIds: [], sizeKeywords: '' };

function pathTail(path: string): string {
  return path.split(' > ').slice(1).join(' › ');
}

function CatalogPicker({
  selected,
  onChange,
}: {
  selected: number[];
  onChange: (ids: number[]) => void;
}) {
  const queryClient = useQueryClient();
  const catalogQuery = useQuery({ queryKey: queryKeys.vintedCatalog, queryFn: api.getVintedCatalog });
  const refreshMutation = useMutation({
    mutationFn: api.refreshVintedCatalog,
    onSuccess: (nodes) => queryClient.setQueryData(queryKeys.vintedCatalog, nodes),
  });
  const [filter, setFilter] = useState('');
  const nodes = catalogQuery.data ?? [];
  const byId = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const needle = filter.trim().toLowerCase();
  // The women's root itself is too broad to be a useful bucket category.
  const visible = nodes.filter((node) => node.parent_id !== null && (!needle || node.path.toLowerCase().includes(needle)));

  const toggle = (node: VintedCatalogNode) =>
    onChange(selected.includes(node.id) ? selected.filter((id) => id !== node.id) : [...selected, node.id]);

  return (
    <div className="stack" style={{ gap: 'var(--s2)' }}>
      <div className="row-between">
        <span className="field">Vinted categories</span>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => refreshMutation.mutate()}
          disabled={refreshMutation.isPending}
          title="Reload the women's category tree from vinted.de"
        >
          <RefreshCw size={14} aria-hidden="true" />
          {refreshMutation.isPending ? 'Refreshing…' : 'Refresh from Vinted'}
        </button>
      </div>
      <ErrorText error={catalogQuery.error ?? refreshMutation.error} prefix="Vinted categories" />
      {selected.length ? (
        <div className="chips">
          {selected.map((id) => (
            <button
              type="button"
              className="chip"
              key={id}
              onClick={() => onChange(selected.filter((value) => value !== id))}
              title={`Remove ${byId.get(id)?.path ?? id}`}
            >
              {byId.get(id) ? pathTail(byId.get(id)!.path) : `#${id} (no longer on Vinted)`}
              <span className="chip-x" aria-hidden="true">×</span>
            </button>
          ))}
        </div>
      ) : (
        <p className="hint">Pick at least one. A parent category includes everything below it.</p>
      )}
      <input
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        placeholder="Filter, e.g. Kleider or Schmuck"
        aria-label="Filter Vinted categories"
      />
      <div className="catalog-tree" role="group" aria-label="Vinted women's categories">
        {catalogQuery.isLoading ? <p className="state-line">Loading categories…</p> : null}
        {visible.map((node) => (
          <label
            key={node.id}
            className="catalog-node"
            style={{ paddingLeft: needle ? undefined : `calc(${node.path.split(' > ').length - 2} * var(--s4))` }}
            title={node.path}
          >
            <input type="checkbox" checked={selected.includes(node.id)} onChange={() => toggle(node)} />
            {needle ? pathTail(node.path) : node.title}
          </label>
        ))}
        {!catalogQuery.isLoading && !visible.length ? <p className="state-line">No category matches.</p> : null}
      </div>
    </div>
  );
}

function BucketModal({
  bucket,
  onClose,
}: {
  bucket: BucketRecord | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<BucketForm>(bucket ? formFromBucket(bucket) : emptyForm);
  const [keyTouched, setKeyTouched] = useState(false);
  const isNew = bucket === null;
  const key = isNew && !keyTouched ? slugFromLabel(form.label) : form.key;
  const keyValid = KEY_PATTERN.test(key);
  const canSave = form.label.trim() !== '' && form.catalogIds.length > 0 && keyValid;

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        label: form.label.trim(),
        description: form.description.trim(),
        catalog_ids: form.catalogIds,
        size_keywords: form.sizeKeywords.split(',').map((value) => value.trim()).filter(Boolean),
      };
      return isNew
        ? api.createBucket({ ...payload, key })
        : api.updateBucket(bucket.key, { ...payload, archived: bucket.archived });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.buckets });
      queryClient.invalidateQueries({ queryKey: queryKeys.searches });
      onClose();
    },
  });

  return (
    <Modal
      label={isNew ? 'New bucket' : `Edit ${bucket.label}`}
      title={isNew ? 'New bucket' : bucket.label}
      sub="Women's items on vinted.de"
      onClose={onClose}
      locked={saveMutation.isPending}
      size="lg"
      error={saveMutation.error}
      footer={(
        <>
          <button type="button" className="btn" onClick={onClose} disabled={saveMutation.isPending}>Cancel</button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => saveMutation.mutate()}
            disabled={!canSave || saveMutation.isPending}
          >
            {saveMutation.isPending ? 'Saving…' : isNew ? 'Create bucket' : 'Save bucket'}
          </button>
        </>
      )}
    >
      <div className="builder-block builder-cols">
        <label className="field">
          Name
          <input
            value={form.label}
            onChange={(event) => setForm({ ...form, label: event.target.value })}
            placeholder="Kleider"
            maxLength={80}
          />
        </label>
        <label className="field">
          Key
          <input
            value={key}
            disabled={!isNew}
            onChange={(event) => { setKeyTouched(true); setForm({ ...form, key: event.target.value }); }}
            aria-invalid={isNew && key !== '' && !keyValid}
          />
          <span className="hint">
            {isNew ? 'Fixed once created. Lowercase letters, digits and _.' : 'Fixed: samples, searches and candidates refer to it.'}
          </span>
        </label>
      </div>
      <div className="builder-block">
        <label className="field">
          Description
          <textarea
            rows={2}
            value={form.description}
            onChange={(event) => setForm({ ...form, description: event.target.value })}
            placeholder="Dresses of any length."
            maxLength={500}
          />
          <span className="hint">The taste model reads this to tell buckets apart. Changing it triggers a new taste profile.</span>
        </label>
      </div>
      <div className="builder-block">
        <CatalogPicker selected={form.catalogIds} onChange={(catalogIds) => setForm({ ...form, catalogIds })} />
      </div>
      <div className="builder-block">
        <label className="field">
          Size keywords
          <input
            value={form.sizeKeywords}
            onChange={(event) => setForm({ ...form, sizeKeywords: event.target.value })}
            placeholder="schuhe"
          />
          <span className="hint">
            “Sync sizes” applies your Vinted account sizes from size groups whose name contains one of these words.
            Leave empty to set sizes by hand.
          </span>
        </label>
      </div>
    </Modal>
  );
}

function BucketRow({
  bucket,
  onEdit,
}: {
  bucket: BucketRecord;
  onEdit: () => void;
}) {
  const queryClient = useQueryClient();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.buckets });
    queryClient.invalidateQueries({ queryKey: queryKeys.searches });
  };
  const deleteMutation = useMutation({ mutationFn: () => api.deleteBucket(bucket.key), onSuccess: invalidate });
  const restoreMutation = useMutation({
    mutationFn: () =>
      api.updateBucket(bucket.key, {
        label: bucket.label,
        description: bucket.description,
        catalog_ids: bucket.categories.map((category) => category.id),
        size_keywords: bucket.size_keywords,
        archived: false,
      }),
    onSuccess: invalidate,
  });

  return (
    <article className={`search-item bucket-item${bucket.archived ? ' is-off' : ''}`}>
      <div>
        <h2 className="search-name">
          {bucket.label}
          <code className="small muted">{bucket.key}</code>
          {bucket.archived ? <span className="badge">Archived</span> : null}
        </h2>
        {bucket.description ? <p className="search-query">{bucket.description}</p> : null}
        <div className="chips">
          {bucket.categories.map((category) => (
            <span className="chip" key={category.id} title={category.path || undefined}>
              {category.path ? pathTail(category.path) : `#${category.id} (no longer on Vinted)`}
            </span>
          ))}
        </div>
      </div>
      <div className="search-buttons">
        {bucket.archived ? (
          <button type="button" className="btn" onClick={() => restoreMutation.mutate()} disabled={restoreMutation.isPending}>
            {restoreMutation.isPending ? 'Restoring…' : 'Restore'}
          </button>
        ) : confirmingDelete ? (
          <>
            <button type="button" className="btn" onClick={() => setConfirmingDelete(false)} disabled={deleteMutation.isPending}>
              Keep
            </button>
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? 'Removing…' : 'Remove'}
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn" onClick={onEdit}>Edit</button>
            <button type="button" className="btn btn-ghost" onClick={() => setConfirmingDelete(true)}>Remove</button>
          </>
        )}
      </div>
      {confirmingDelete && !bucket.archived ? (
        <p className="hint bucket-note">
          A bucket without samples or candidates is deleted with its search. Otherwise it is archived: hidden, its search
          paused, its history kept, and you can restore it.
        </p>
      ) : null}
      <ErrorText error={deleteMutation.error ?? restoreMutation.error} />
    </article>
  );
}

export function BucketsPage() {
  const { query, buckets } = useBuckets();
  const [editing, setEditing] = useState<BucketRecord | 'new' | null>(null);
  const active = buckets.filter((bucket) => !bucket.archived);
  const archived = buckets.filter((bucket) => bucket.archived);

  const header = (
    <PageHeader
      title="Buckets"
      sub="Each bucket has its own search and its own part of the taste profile. New buckets use the overall taste until the next recompute."
      actions={(
        <button type="button" className="btn btn-primary" onClick={() => setEditing('new')}>
          <Plus size={14} aria-hidden="true" />
          New bucket
        </button>
      )}
    />
  );

  if (query.isLoading) return <div className="page">{header}<Loading label="Loading buckets…" /></div>;
  if (query.isError) return <div className="page">{header}<ErrorText error={query.error} /></div>;

  return (
    <div className="page">
      {header}
      {active.length ? (
        <div className="search-list">
          {active.map((bucket) => <BucketRow key={bucket.key} bucket={bucket} onEdit={() => setEditing(bucket)} />)}
        </div>
      ) : (
        <Empty title="No buckets">Create a bucket to start searching.</Empty>
      )}
      {archived.length ? (
        <details className="stack">
          <summary className="muted">{archived.length} archived</summary>
          <div className="search-list">
            {archived.map((bucket) => <BucketRow key={bucket.key} bucket={bucket} onEdit={() => setEditing(bucket)} />)}
          </div>
        </details>
      ) : null}
      {editing ? <BucketModal bucket={editing === 'new' ? null : editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

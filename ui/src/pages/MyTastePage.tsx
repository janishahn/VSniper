import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ImagePlus, Link2, Plus, RefreshCw, ThumbsDown, ThumbsUp, Upload, X } from 'lucide-react';

import { api, ApiError } from '../lib/api';
import { useBuckets } from '../lib/clothingItems';
import { formatBerlinDateTime } from '../lib/datetime';
import { queryKeys } from '../lib/queryKeys';
import { useUnsavedGuard } from '../lib/useUnsavedGuard';
import {
  filterGroupLabel,
  formatSearchFilters,
  groupFilters,
  isPriceCeilingFilter,
  replaceFilterGroup,
} from '../lib/searchFilters';
import { ErrorText } from '../components/ErrorText';
import { Badge, Empty, Loading, Modal, PageHeader, Section, Stat } from '../components/ui';
import type {
  ClothingItem,
  ClothingItemTasteProfile,
  GeneratedSearchDraft,
  LearningSnapshot,
  ReferenceObservation,
  SearchFilter,
  SearchRecord,
  SearchUpdatePayload,
  TasteSample,
  WardrobeZipImportResult,
} from '../types';

type OfferKind = 'offer_like' | 'offer_dislike';

function fmtUsd(value: number): string {
  return `$${value.toFixed(4)}`;
}

// What the VLM read from a sample's photo. Surfaced so a mis-read (e.g. wrong garment type)
// is visible when debugging why the judge keeps missing the user's taste.
const OBSERVATION_FIELDS: { key: keyof ReferenceObservation; label: string }[] = [
  { key: 'garment_type', label: 'Garment' },
  { key: 'silhouette_and_cut', label: 'Silhouette & cut' },
  { key: 'color_palette', label: 'Colour palette' },
  { key: 'fabric_and_texture', label: 'Fabric & texture' },
  { key: 'prints_or_patterns', label: 'Prints / patterns' },
  { key: 'details_and_hardware', label: 'Details & hardware' },
  { key: 'era_or_subculture', label: 'Era / subculture' },
];

function ObservationDetails({ observation }: { observation: ReferenceObservation }) {
  const rows = OBSERVATION_FIELDS
    .map((field) => [field.label, String(observation[field.key] ?? '').trim()] as const)
    .filter(([, value]) => value);
  if (!rows.length && !observation.vibe_keywords?.length) return null;
  return (
    <dl className="kv">
      {rows.map(([label, value]) => (
        <div key={label} style={{ display: 'contents' }}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
      {observation.vibe_keywords?.length ? (
        <>
          <dt>Vibe</dt>
          <dd>
            <div className="chips">
              {observation.vibe_keywords.map((kw) => <span className="chip" key={kw}>{kw}</span>)}
            </div>
          </dd>
        </>
      ) : null}
    </dl>
  );
}

// ─── Upload modal ─────────────────────────────────────────────────────────────

function UploadModal({
  onClose,
  onUploadFile,
  clothingItem,
}: {
  onClose: () => void;
  onUploadFile: (file: File, note: string) => Promise<unknown>;
  clothingItem: ClothingItem;
}) {
  const { clothingItemLabel } = useBuckets();
  const [files, setFiles] = useState<File[]>([]);
  const [note, setNote] = useState('');
  const [dragging, setDragging] = useState(false);
  const [previews, setPreviews] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Build preview object URLs from the current files and revoke them whenever the
  // file list changes or the modal unmounts — otherwise every re-render (e.g. a
  // keystroke in the note field) would leak a fresh blob URL.
  useEffect(() => {
    const urls = files.map((f) => URL.createObjectURL(f));
    setPreviews(urls);
    return () => urls.forEach((url) => URL.revokeObjectURL(url));
  }, [files]);

  const absorb = (fileList: FileList | null) => {
    if (!fileList) return;
    const incoming = Array.from(fileList);
    const accepted = incoming.filter((f) => f.type.startsWith('image/'));
    const rejected = incoming.length - accepted.length;
    setNotice(rejected > 0 ? `${rejected} non-image file${rejected > 1 ? 's were' : ' was'} skipped.` : null);
    // Append to the existing selection, de-duping by name + size so re-picking the same file is a no-op.
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      return [...prev, ...accepted.filter((f) => !seen.has(`${f.name}:${f.size}`))];
    });
  };

  const submit = async () => {
    if (!files.length || submitting) return;
    setSubmitting(true);
    setError(null);
    const remaining = [...files];
    try {
      while (remaining.length) {
        await onUploadFile(remaining[0], note);
        remaining.shift();
        setFiles([...remaining]); // drop succeeded files so a retry can't duplicate them
      }
      onClose();
    } catch (err) {
      setFiles([...remaining]); // keep the failed file plus any not yet attempted
      setError(err);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      label="Add wardrobe photos"
      title={`Add ${clothingItemLabel(clothingItem)} photos`}
      sub="Photos of clothes you own and like. They are described by the vision model on the next recompute."
      onClose={onClose}
      locked={submitting}
      error={error}
      errorPrefix="Upload failed"
      footer={(
        <>
          <button type="button" className="btn" onClick={onClose} disabled={submitting}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={submit} disabled={!files.length || submitting}>
            {submitting
              ? `Uploading… ${files.length} left`
              : files.length > 1 ? `Upload ${files.length} photos` : 'Upload photo'}
          </button>
        </>
      )}
    >
      <div
        className={`drop-zone${dragging ? ' is-dragging' : ''}`}
        role="button"
        tabIndex={0}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); absorb(e.dataTransfer.files); }}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputRef.current?.click(); } }}
      >
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(e) => absorb(e.target.files)}
        />
        {files.length > 0 ? (
          <div className="tiles">
            {files.map((f, i) => (
              <div key={`${f.name}:${f.size}`} className="tile">
                {previews[i] ? <img src={previews[i]} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : null}
              </div>
            ))}
          </div>
        ) : (
          <div className="stack" style={{ justifyItems: 'center', gap: 'var(--s1)' }}>
            <ImagePlus size={22} aria-hidden="true" />
            <span>Drop photos here or click to choose</span>
          </div>
        )}
      </div>

      {files.length > 0 ? (
        <div className="row">
          <span className="small muted">{files.length} photo{files.length > 1 ? 's' : ''} selected</span>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={() => { setFiles([]); setNotice(null); }}
            disabled={submitting}
          >
            Clear
          </button>
        </div>
      ) : null}
      {notice ? <p className="hint">{notice}</p> : null}

      <label className="field">
        Note <span className="hint">Optional, applies to every photo in this upload</span>
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Favourite piece, the fit is why…" />
      </label>
    </Modal>
  );
}

// ─── Add offer by URL modal ─────────────────────────────────────────────────────

function AddOfferModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (payload: {
    vinted_url: string;
    kind: OfferKind;
    clothing_item: ClothingItem;
    note: string;
  }) => Promise<unknown>;
}) {
  const [url, setUrl] = useState('');
  const [kind, setKind] = useState<OfferKind>('offer_like');
  const { clothingItems } = useBuckets();
  const [clothingItem, setClothingItem] = useState<ClothingItem>(() => clothingItems[0]?.value ?? '');
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async () => {
    if (!url.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit({ vinted_url: url.trim(), kind, clothing_item: clothingItem, note: note.trim() });
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      label="Add a Vinted listing"
      title="Add a Vinted listing"
      sub="A listing you'd buy or would never buy. It becomes training data for the next recompute."
      onClose={onClose}
      locked={submitting}
      footer={(
        <>
          <button type="button" className="btn" onClick={onClose} disabled={submitting}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={submit} disabled={!url.trim() || submitting}>
            {submitting ? 'Adding…' : 'Add listing'}
          </button>
        </>
      )}
    >
      <label className="field">
        Listing URL
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void submit(); }}
          placeholder="https://www.vinted.de/items/…"
          inputMode="url"
          autoFocus
        />
      </label>

      <div className="fields-2">
        <div className="field">
          Verdict
          <div className="seg" role="group" aria-label="Verdict">
            <button type="button" aria-pressed={kind === 'offer_like'} onClick={() => setKind('offer_like')}>
              <ThumbsUp size={14} aria-hidden="true" /> Like
            </button>
            <button type="button" aria-pressed={kind === 'offer_dislike'} onClick={() => setKind('offer_dislike')}>
              <ThumbsDown size={14} aria-hidden="true" /> Dislike
            </button>
          </div>
        </div>
        <label className="field">
          Clothing bucket
          <select value={clothingItem} onChange={(e) => setClothingItem(e.target.value as ClothingItem)}>
            {clothingItems.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </label>
      </div>

      <label className="field">
        Note <span className="hint">Optional</span>
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why does this match, or miss?" />
      </label>

      <ErrorText error={error} prefix="Add listing failed" />
    </Modal>
  );
}

// ─── Sample detail modal ───────────────────────────────────────────────────────

function SampleDetailModal({
  sample,
  onClose,
  onUpdate,
  onDelete,
  isUpdating,
  error,
}: {
  sample: TasteSample;
  onClose: () => void;
  onUpdate: (note: string, clothingItem: ClothingItem, kind?: OfferKind) => void;
  onDelete: () => void;
  isUpdating: boolean;
  error: unknown;
}) {
  const [note, setNote] = useState(sample.note);
  const { clothingItems, clothingItemLabel } = useBuckets();
  const [clothingItem, setClothingItem] = useState<ClothingItem>(sample.clothing_item);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const isOffer = sample.kind === 'offer_like' || sample.kind === 'offer_dislike';
  const [offerKind, setOfferKind] = useState<OfferKind>(
    sample.kind === 'offer_like' || sample.kind === 'offer_dislike' ? sample.kind : 'offer_like',
  );
  const image = sample.image_urls[0];
  // The first analysed image's observation is the one matching the displayed photo.
  const observation = sample.image_observations?.[0]?.observation ?? null;
  const facts = [
    sample.brand,
    sample.size ? `size ${sample.size}` : '',
    sample.price_eur != null ? `${sample.price_eur.toFixed(2)} €` : '',
  ].filter(Boolean).join(' · ');

  return (
    <Modal
      label={sample.title || 'Sample'}
      title={sample.title || (sample.kind === 'wardrobe' ? 'Wardrobe photo' : 'Listing')}
      sub={facts || clothingItemLabel(sample.clothing_item)}
      onClose={onClose}
      locked={isUpdating}
      size="lg"
      error={error}
      errorPrefix="Sample update failed"
      footer={confirmingDelete ? (
        <>
          <span className="small muted" style={{ marginRight: 'auto' }}>Remove this sample from your taste data?</span>
          <button type="button" className="btn" onClick={() => setConfirmingDelete(false)} disabled={isUpdating}>Keep</button>
          <button type="button" className="btn btn-danger" onClick={onDelete} disabled={isUpdating}>
            {isUpdating ? 'Removing…' : 'Remove'}
          </button>
        </>
      ) : (
        <>
          <button
            type="button"
            className="btn btn-ghost btn-danger"
            style={{ marginRight: 'auto' }}
            onClick={() => setConfirmingDelete(true)}
            disabled={isUpdating}
          >
            Remove
          </button>
          <button type="button" className="btn" onClick={onClose} disabled={isUpdating}>Cancel</button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onUpdate(note, clothingItem, isOffer ? offerKind : undefined)}
            disabled={isUpdating}
          >
            {isUpdating ? 'Saving…' : 'Save'}
          </button>
        </>
      )}
    >
      <div className="sample-detail">
        {image ? <img src={image} alt="" /> : <div className="tile" />}
        <div className="stack-lg">
          <div className="fields">
            {isOffer ? (
              <div className="field">
                Verdict
                <div className="seg" role="group" aria-label="Verdict">
                  <button type="button" aria-pressed={offerKind === 'offer_like'} onClick={() => setOfferKind('offer_like')}>
                    <ThumbsUp size={14} aria-hidden="true" /> Like
                  </button>
                  <button type="button" aria-pressed={offerKind === 'offer_dislike'} onClick={() => setOfferKind('offer_dislike')}>
                    <ThumbsDown size={14} aria-hidden="true" /> Dislike
                  </button>
                </div>
              </div>
            ) : null}
            <label className="field">
              Clothing bucket
              <select value={clothingItem} onChange={(e) => setClothingItem(e.target.value as ClothingItem)}>
                {clothingItems.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </select>
            </label>
            <label className="field">
              Note
              <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} />
            </label>
            {sample.vinted_url ? (
              <a href={sample.vinted_url} target="_blank" rel="noreferrer" className="small">Open on Vinted</a>
            ) : null}
          </div>
          <div className="stack">
            <h3 style={{ fontSize: 'var(--fs-base)' }}>What the vision model saw</h3>
            {observation
              ? <ObservationDetails observation={observation} />
              : <p className="hint">Not analysed yet. The next recompute describes this photo.</p>}
          </div>
        </div>
      </div>
    </Modal>
  );
}

// ─── Image grid ───────────────────────────────────────────────────────────────

function ImageGrid({
  samples,
  onAdd,
  addLabel,
  emptyLabel,
  onDelete,
  onUpdate,
}: {
  samples: TasteSample[];
  onAdd?: () => void;
  addLabel?: string;
  emptyLabel?: string;
  onDelete: (id: string) => Promise<void> | void;
  onUpdate: (id: string, note: string, clothingItem: ClothingItem, kind?: OfferKind) => Promise<void> | void;
}) {
  const [selected, setSelected] = useState<TasteSample | null>(null);
  const [actionPending, setActionPending] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  const runAction = async (action: () => Promise<void> | void, closeAfterSuccess: boolean) => {
    setActionPending(true);
    setActionError(null);
    try {
      await action();
      if (closeAfterSuccess) setSelected(null);
    } catch (error) {
      setActionError(error);
    } finally {
      setActionPending(false);
    }
  };

  return (
    <>
      {!samples.length && !onAdd ? (
        <p className="hint">{emptyLabel ?? 'None yet.'}</p>
      ) : (
        <div className="tiles">
          {onAdd ? (
            <button type="button" className="tile-add" onClick={onAdd} aria-label={addLabel ?? 'Add photos'} title={addLabel}>
              <Plus size={18} aria-hidden="true" />
            </button>
          ) : null}
          {samples.map((sample) => {
            const image = sample.image_urls[0];
            const name = sample.title || sample.file_name || 'sample';
            return (
              <div key={sample.id} className="tile" title={sample.note || undefined}>
                <button type="button" className="tile-open" onClick={() => setSelected(sample)} aria-label={`Open ${name}`}>
                  {image ? <img src={image} alt="" loading="lazy" /> : <span className="tile-placeholder">?</span>}
                </button>
                {pendingDeleteId === sample.id ? (
                  <div className="tile-confirm">
                    <button
                      type="button"
                      className="yes"
                      onClick={() => { setPendingDeleteId(null); void runAction(() => onDelete(sample.id), false); }}
                      disabled={actionPending}
                    >
                      Remove
                    </button>
                    <button type="button" className="no" onClick={() => setPendingDeleteId(null)} aria-label="Keep">
                      Keep
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="tile-remove"
                    onClick={() => setPendingDeleteId(sample.id)}
                    aria-label={`Remove ${name}`}
                    disabled={actionPending}
                  >
                    <X size={13} aria-hidden="true" />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
      {!selected ? <ErrorText error={actionError} prefix="Sample update failed" /> : null}

      {selected ? (
        <SampleDetailModal
          sample={selected}
          onClose={() => setSelected(null)}
          onUpdate={(note, clothingItem, kind) => {
            void runAction(() => onUpdate(selected.id, note, clothingItem, kind), true);
          }}
          onDelete={() => {
            void runAction(() => onDelete(selected.id), true);
          }}
          isUpdating={actionPending}
          error={actionError}
        />
      ) : null}
    </>
  );
}

// ─── Prompt diff ──────────────────────────────────────────────────────────────

type DiffToken = { value: string; type: 'same' | 'removed' | 'added' };

function wordDiff(oldText: string, newText: string): DiffToken[] {
  // Keep whitespace in the output so the prompts retain their original wrapping,
  // but compare individual non-whitespace chunks instead of whole lines.
  const oldTokens = oldText.match(/\s+|\S+/g) ?? [];
  const newTokens = newText.match(/\s+|\S+/g) ?? [];
  const width = newTokens.length + 1;
  const lengths = new Uint32Array((oldTokens.length + 1) * width);

  for (let oldIndex = oldTokens.length - 1; oldIndex >= 0; oldIndex -= 1) {
    for (let newIndex = newTokens.length - 1; newIndex >= 0; newIndex -= 1) {
      const index = oldIndex * width + newIndex;
      lengths[index] = oldTokens[oldIndex] === newTokens[newIndex]
        ? lengths[(oldIndex + 1) * width + newIndex + 1] + 1
        : Math.max(lengths[(oldIndex + 1) * width + newIndex], lengths[oldIndex * width + newIndex + 1]);
    }
  }

  const result: DiffToken[] = [];
  let oldIndex = 0;
  let newIndex = 0;
  while (oldIndex < oldTokens.length && newIndex < newTokens.length) {
    if (oldTokens[oldIndex] === newTokens[newIndex]) {
      result.push({ value: oldTokens[oldIndex], type: 'same' });
      oldIndex += 1;
      newIndex += 1;
    } else if (lengths[(oldIndex + 1) * width + newIndex] >= lengths[oldIndex * width + newIndex + 1]) {
      result.push({ value: oldTokens[oldIndex], type: 'removed' });
      oldIndex += 1;
    } else {
      result.push({ value: newTokens[newIndex], type: 'added' });
      newIndex += 1;
    }
  }
  while (oldIndex < oldTokens.length) result.push({ value: oldTokens[oldIndex++], type: 'removed' });
  while (newIndex < newTokens.length) result.push({ value: newTokens[newIndex++], type: 'added' });
  return result;
}

type DraftFieldKey = 'query';
type DiffStatus = 'added' | 'changed' | 'removed' | 'unchanged';

type FilterDiff = {
  key: string;
  label: string;
  status: DiffStatus;
  current: SearchFilter[];
  draft: SearchFilter[];
};

function filtersEqual(left: SearchFilter[], right: SearchFilter[]): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function filterDiffs(current: SearchFilter[], draft: SearchFilter[]): FilterDiff[] {
  const currentGroups = groupFilters(current);
  const draftGroups = groupFilters(draft);
  const keys = Array.from(new Set([...currentGroups.keys(), ...draftGroups.keys()]));
  return keys.map((key) => {
    const currentFilters = currentGroups.get(key) ?? [];
    const draftFilters = draftGroups.get(key) ?? [];
    const status: DiffStatus = !currentFilters.length
      ? 'added'
      : !draftFilters.length
        ? 'removed'
        : filtersEqual(currentFilters, draftFilters)
          ? 'unchanged'
          : 'changed';
    return {
      key,
      label: filterGroupLabel(key, draftFilters.length ? draftFilters : currentFilters),
      status,
      current: currentFilters,
      draft: draftFilters,
    };
  });
}

function withoutDraftPriceFilters(filters: SearchFilter[]): SearchFilter[] {
  return filters.filter((filter) => !isPriceCeilingFilter(filter));
}

function defaultDraftSelection(search: SearchRecord, draft: GeneratedSearchDraft): {
  fields: Set<DraftFieldKey>;
  filterGroups: Set<string>;
} {
  const fields = new Set<DraftFieldKey>();
  if (search.query !== draft.query) fields.add('query');
  const filterGroups = new Set(
    filterDiffs(search.filters, withoutDraftPriceFilters(draft.filters))
      .filter((diff) => diff.key !== 'size' && diff.key !== 'price:range')
      .filter((diff) => diff.status === 'added' || diff.status === 'changed')
      .map((diff) => diff.key),
  );
  return { fields, filterGroups };
}

function mergeDraftIntoSearch(
  search: SearchRecord,
  draft: GeneratedSearchDraft,
  selectedFields: Set<DraftFieldKey>,
  selectedFilterGroups: Set<string>,
): SearchUpdatePayload {
  const draftGroups = groupFilters(withoutDraftPriceFilters(draft.filters));
  let filters = [...search.filters];
  for (const key of selectedFilterGroups) {
    filters = replaceFilterGroup(filters, key, draftGroups.get(key) ?? []);
  }
  return {
    clothing_item: search.clothing_item,
    query: selectedFields.has('query') ? draft.query : search.query,
    region: search.region,
    filters,
    alert_threshold: search.alert_threshold,
    enabled: search.enabled,
  };
}

function toggleSetValue<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

function statusLabel(status: DiffStatus): string {
  if (status === 'added') return 'added';
  if (status === 'removed') return 'removed';
  if (status === 'changed') return 'changed';
  return 'same';
}

function PromptDiff({
  oldPrompt,
  newPrompt,
  oldCharacterCount,
  oldTokenCount,
  newCharacterCount,
  newTokenCount,
  tokenizer,
}: {
  oldPrompt: string;
  newPrompt: string;
  oldCharacterCount?: number | null;
  oldTokenCount?: number | null;
  newCharacterCount?: number | null;
  newTokenCount?: number | null;
  tokenizer: string;
}) {
  const tokens = wordDiff(oldPrompt, newPrompt);
  const changedCount = tokens.filter((token) => token.type !== 'same' && /\S/.test(token.value)).length;
  const oldTokens = tokens.filter((token) => token.type !== 'added');
  const newTokens = tokens.filter((token) => token.type !== 'removed');
  return (
    <div className="stack">
      <p className="hint">
        {changedCount === 0 ? 'No changes in the taste prompt.' : `${changedCount} word${changedCount !== 1 ? 's' : ''} changed.`}
        {oldCharacterCount != null && oldTokenCount != null && newCharacterCount != null && newTokenCount != null
          ? ` Before ${oldCharacterCount.toLocaleString()} chars / ${oldTokenCount.toLocaleString()} tokens, after ${newCharacterCount.toLocaleString()} chars / ${newTokenCount.toLocaleString()} tokens (${tokenizer}).`
          : ''}
      </p>
      {changedCount > 0 ? (
        <div className="prompt-diff">
          <pre className="pre" aria-label="Previous prompt">
            {oldTokens.map((token, index) => (
              <span key={index} className={token.type === 'removed' ? 'diff-removed' : undefined}>{token.value}</span>
            ))}
          </pre>
          <pre className="pre" aria-label="New prompt">
            {newTokens.map((token, index) => (
              <span key={index} className={token.type === 'added' ? 'diff-added' : undefined}>{token.value}</span>
            ))}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

// ─── Draft searches ───────────────────────────────────────────────────────────

const diffTone = { added: 'ok', changed: 'accent', removed: 'bad', unchanged: 'neutral' } as const;

function DiffRow({
  label,
  status,
  checked,
  disabled,
  onToggle,
  left,
  right,
  leftLabel = 'Current',
  rightLabel = 'Draft',
}: {
  label: string;
  status: DiffStatus;
  checked: boolean;
  disabled: boolean;
  onToggle: () => void;
  left: ReactNode;
  right: ReactNode;
  leftLabel?: string;
  rightLabel?: string;
}) {
  return (
    <label className={`diff-row${status === 'unchanged' ? ' is-unchanged' : ''}`}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={onToggle} />
      <div>
        <div className="row-between">
          <strong>{label}</strong>
          <Badge tone={diffTone[status]}>{statusLabel(status)}</Badge>
        </div>
        <div className="diff-cols">
          <p><span>{leftLabel}</span>{left}</p>
          <p><span>{rightLabel}</span>{right}</p>
        </div>
      </div>
    </label>
  );
}

function DraftCompareModal({
  draft,
  itemProfile,
  search,
  learningSnapshot,
  onClose,
  onApply,
  isPending,
  error,
}: {
  draft: GeneratedSearchDraft;
  itemProfile: ClothingItemTasteProfile | null;
  search: SearchRecord;
  learningSnapshot: LearningSnapshot | null;
  onClose: () => void;
  onApply: (payload: SearchUpdatePayload) => void;
  isPending: boolean;
  error: unknown;
}) {
  const { clothingItemLabel } = useBuckets();
  const diffs = useMemo(
    () => filterDiffs(search.filters, withoutDraftPriceFilters(draft.filters))
      .filter((diff) => diff.key !== 'size' && diff.key !== 'price:range'),
    [search.filters, draft.filters],
  );
  const [selectedFields, setSelectedFields] = useState<Set<DraftFieldKey>>(
    () => defaultDraftSelection(search, draft).fields,
  );
  const [selectedFilterGroups, setSelectedFilterGroups] = useState<Set<string>>(
    () => defaultDraftSelection(search, draft).filterGroups,
  );

  const hasSelection = selectedFields.size > 0 || selectedFilterGroups.size > 0;
  const queryChanged = search.query !== draft.query;
  const oldItemProfile = learningSnapshot?.old_taste_profile?.item_profiles[draft.clothing_item] ?? null;
  const newItemProfile = learningSnapshot?.new_taste_profile?.item_profiles[draft.clothing_item] ?? itemProfile;
  const profileRows: { label: string; previous: string; current: string; judgeUse: string }[] = newItemProfile ? [
    {
      label: 'Transparency labels',
      previous: oldItemProfile?.transparency_labels.length ? oldItemProfile.transparency_labels.join(' · ') : 'None',
      current: newItemProfile.transparency_labels.length ? newItemProfile.transparency_labels.join(' · ') : 'None',
      judgeUse: 'For you only. Quick labels that summarise the profile; not sent to the judge.',
    },
    {
      label: 'Instant alerts',
      previous: oldItemProfile?.instant_alert_examples.length ? oldItemProfile.instant_alert_examples.join(' · ') : 'None',
      current: newItemProfile.instant_alert_examples.length ? newItemProfile.instant_alert_examples.join(' · ') : 'None',
      judgeUse: 'Positive calibration examples for the judge while there are no real liked listings yet.',
    },
    {
      label: 'Instant rejects',
      previous: oldItemProfile?.instant_reject_examples.length ? oldItemProfile.instant_reject_examples.join(' · ') : 'None',
      current: newItemProfile.instant_reject_examples.length ? newItemProfile.instant_reject_examples.join(' · ') : 'None',
      judgeUse: 'Negative calibration examples for the judge while there are no real disliked listings yet.',
    },
  ] : [];

  return (
    <Modal
      label={`Compare generated draft for ${clothingItemLabel(draft.clothing_item)}`}
      title={`${clothingItemLabel(draft.clothing_item)}: generated search`}
      sub={draft.rationale || 'No rationale provided.'}
      onClose={onClose}
      locked={isPending}
      size="lg"
      error={error}
      errorPrefix="Apply failed"
      footer={(
        <>
          <button type="button" className="btn" onClick={onClose} disabled={isPending}>Cancel</button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onApply(mergeDraftIntoSearch(search, draft, selectedFields, selectedFilterGroups))}
            disabled={isPending || !hasSelection}
          >
            {isPending ? 'Applying…' : 'Apply selected'}
          </button>
        </>
      )}
    >
      <div className="stack">
        <p className="hint">Tick the changes to copy into the saved search. Sizes and price stay as they are.</p>
        <div className="diff-list">
          <DiffRow
            label="Vinted query"
            status={queryChanged ? 'changed' : 'unchanged'}
            checked={selectedFields.has('query')}
            disabled={!queryChanged || isPending}
            onToggle={() => setSelectedFields((current) => toggleSetValue(current, 'query'))}
            left={search.query || '–'}
            right={draft.query || '–'}
          />
          {diffs.map((diff) => (
            <DiffRow
              key={diff.key}
              label={diff.label}
              status={diff.status}
              checked={selectedFilterGroups.has(diff.key)}
              disabled={diff.status === 'unchanged' || isPending}
              onToggle={() => setSelectedFilterGroups((current) => toggleSetValue(current, diff.key))}
              left={formatSearchFilters(diff.current)}
              right={formatSearchFilters(diff.draft)}
            />
          ))}
        </div>
      </div>

      {profileRows.length ? (
        <div className="stack">
          <h3 style={{ fontSize: 'var(--fs-base)' }}>Profile calibration from the last recompute</h3>
          {!oldItemProfile ? <p className="hint">The previous profile wasn't saved, so only the new values are shown.</p> : null}
          <div className="diff-list">
            {profileRows.map((row) => (
              <div className="diff-row is-static" key={row.label} style={{ gridTemplateColumns: 'minmax(0, 1fr)', cursor: 'default' }}>
                <div>
                  <div className="row-between">
                    <strong>{row.label}</strong>
                    <Badge tone={row.previous !== row.current ? 'accent' : 'neutral'}>{row.previous !== row.current ? 'changed' : 'same'}</Badge>
                  </div>
                  <div className="diff-cols">
                    <p><span>Previous</span>{row.previous}</p>
                    <p><span>New</span>{row.current}</p>
                  </div>
                  <p className="hint" style={{ marginTop: 'var(--s2)' }}>{row.judgeUse}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {learningSnapshot?.old_prompt && learningSnapshot?.new_prompt ? (
        <details className="disclosure">
          <summary>Prompt diff from the last recompute ({learningSnapshot.reason})</summary>
          <div className="disclosure-body">
            <PromptDiff
              oldPrompt={learningSnapshot.old_prompt}
              newPrompt={learningSnapshot.new_prompt}
              oldCharacterCount={learningSnapshot.old_prompt_character_count}
              oldTokenCount={learningSnapshot.old_prompt_token_count}
              newCharacterCount={learningSnapshot.new_prompt_character_count}
              newTokenCount={learningSnapshot.new_prompt_token_count}
              tokenizer={learningSnapshot.prompt_tokenizer}
            />
          </div>
        </details>
      ) : null}
    </Modal>
  );
}

function DraftSearches({
  drafts,
  allDrafts,
  itemProfile,
  learningSnapshot,
}: {
  drafts: GeneratedSearchDraft[];
  allDrafts: GeneratedSearchDraft[];
  itemProfile: ClothingItemTasteProfile | null;
  learningSnapshot: LearningSnapshot | null;
}) {
  const queryClient = useQueryClient();
  const searchesQuery = useQuery({ queryKey: queryKeys.searches, queryFn: api.getSearches });
  const [selectedDraft, setSelectedDraft] = useState<GeneratedSearchDraft | null>(null);
  const updateMutation = useMutation({
    mutationFn: ({ searchId, payload }: { searchId: string; payload: SearchUpdatePayload }) => api.updateSearch(searchId, payload),
    onSuccess: () => {
      setSelectedDraft(null);
      queryClient.invalidateQueries({ queryKey: queryKeys.searches });
    },
  });

  const searches = searchesQuery.data ?? [];
  const quickApplyAllMutation = useMutation({
    mutationFn: async () => {
      const updates = allDrafts.flatMap((draft) => {
        const search = searches.find((item) => item.clothing_item === draft.clothing_item);
        if (!search) return [];
        const { fields, filterGroups } = defaultDraftSelection(search, draft);
        if (!fields.size && !filterGroups.size) return [];
        return [{ searchId: search.id, payload: mergeDraftIntoSearch(search, draft, fields, filterGroups) }];
      });
      await Promise.all(updates.map(({ searchId, payload }) => api.updateSearch(searchId, payload)));
      return updates.length;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.searches }),
  });
  const quickApplicableCount = allDrafts.filter((draft) => {
    const search = searches.find((item) => item.clothing_item === draft.clothing_item);
    if (!search) return false;
    const { fields, filterGroups } = defaultDraftSelection(search, draft);
    return fields.size > 0 || filterGroups.size > 0;
  }).length;
  const selectedSearch = selectedDraft
    ? searches.find((search) => search.clothing_item === selectedDraft.clothing_item) ?? null
    : null;

  if (!drafts.length && !allDrafts.length) return null;

  return (
    <div className="panel stack">
      <div className="row-between" style={{ alignItems: 'center' }}>
        <div>
          <h3>Suggested search</h3>
          <p className="hint">The recompute proposes a Vinted search for each bucket. Nothing changes until you apply it.</p>
        </div>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => quickApplyAllMutation.mutate()}
          disabled={
            searchesQuery.isLoading
            || updateMutation.isPending
            || quickApplyAllMutation.isPending
            || quickApplicableCount === 0
          }
          title={quickApplicableCount ? 'Apply the preselected changes for every bucket.' : 'No preselected draft changes to apply.'}
        >
          {quickApplyAllMutation.isPending ? 'Applying all…' : `Apply all buckets${quickApplicableCount ? ` (${quickApplicableCount})` : ''}`}
        </button>
      </div>
      {drafts.length === 0 ? <p className="hint">No suggestion for this bucket.</p> : null}
      {drafts.map((draft) => {
        const existing = searches.find((search) => search.clothing_item === draft.clothing_item);
        const selection = existing ? defaultDraftSelection(existing, draft) : null;
        const quickApplicable = !!selection && (selection.fields.size > 0 || selection.filterGroups.size > 0);
        return (
          <div key={draft.id} className="draft-row">
            <div className="stack" style={{ gap: 'var(--s1)' }}>
              <p>Query <code>{draft.query}</code></p>
              <p className="small muted">{formatSearchFilters(withoutDraftPriceFilters(draft.filters))}</p>
              {draft.rationale ? <p className="small">{draft.rationale}</p> : null}
            </div>
            <div className="row" style={{ flexWrap: 'nowrap' }}>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setSelectedDraft(draft)}
                disabled={searchesQuery.isLoading || !existing || updateMutation.isPending || quickApplyAllMutation.isPending}
                title={existing ? undefined : 'The saved search for this bucket is missing.'}
              >
                Compare
              </button>
              <button
                type="button"
                className="btn btn-sm btn-primary"
                onClick={() => {
                  if (!existing || !selection) return;
                  updateMutation.mutate({
                    searchId: existing.id,
                    payload: mergeDraftIntoSearch(existing, draft, selection.fields, selection.filterGroups),
                  });
                }}
                disabled={searchesQuery.isLoading || !existing || !quickApplicable || updateMutation.isPending || quickApplyAllMutation.isPending}
                title={!existing ? 'The saved search for this bucket is missing.' : !quickApplicable ? 'Already matches the saved search.' : 'Apply the changes preselected in Compare.'}
              >
                {updateMutation.isPending && !selectedDraft ? 'Applying…' : quickApplicable ? 'Apply' : 'Applied'}
              </button>
            </div>
          </div>
        );
      })}
      <ErrorText error={searchesQuery.error} prefix="Searches failed to load" />
      <ErrorText error={selectedDraft ? null : updateMutation.error} prefix="Apply failed" />
      <ErrorText error={quickApplyAllMutation.error} prefix="Apply all failed" />
      {quickApplyAllMutation.isSuccess ? (
        <p className="success-text">Updated {quickApplyAllMutation.data} search{quickApplyAllMutation.data === 1 ? '' : 'es'}.</p>
      ) : null}
      {selectedDraft && selectedSearch ? (
        <DraftCompareModal
          draft={selectedDraft}
          itemProfile={itemProfile}
          search={selectedSearch}
          learningSnapshot={learningSnapshot}
          onClose={() => setSelectedDraft(null)}
          onApply={(payload) => updateMutation.mutate({ searchId: selectedSearch.id, payload })}
          isPending={updateMutation.isPending}
          error={updateMutation.error}
        />
      ) : null}
    </div>
  );
}

// ─── Blocked brands ────────────────────────────────────────────────────────────

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

function BrandSearchInput({
  blockedBrands,
  onAdd,
}: {
  blockedBrands: string[];
  onAdd: (title: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const debouncedQuery = useDebouncedValue(query.trim(), 300);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const suggestionsQuery = useQuery({
    queryKey: queryKeys.vintedBrandSearch(debouncedQuery),
    queryFn: () => api.searchVintedBrands(debouncedQuery),
    enabled: debouncedQuery.length >= 2,
  });

  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const blockedLower = useMemo(() => new Set(blockedBrands.map((b) => b.toLowerCase())), [blockedBrands]);
  const suggestions = (suggestionsQuery.data ?? []).filter((option) => !blockedLower.has(option.title.toLowerCase()));

  const pick = (title: string) => {
    onAdd(title);
    setQuery('');
    setOpen(false);
  };

  return (
    <div className="combo" ref={wrapperRef}>
      <input
        value={query}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            if (suggestions.length) pick(suggestions[0].title);
            else if (query.trim()) pick(query.trim());
          }
          if (e.key === 'Escape') setOpen(false);
        }}
        placeholder="Search Vinted brands to block…"
        aria-label="Block a brand"
        role="combobox"
        aria-expanded={open && debouncedQuery.length >= 2}
        aria-autocomplete="list"
      />
      {open && debouncedQuery.length >= 2 ? (
        <div className="combo-list" role="listbox">
          {suggestionsQuery.isLoading ? (
            <p className="combo-empty">Searching Vinted…</p>
          ) : suggestionsQuery.isError ? (
            <p className="combo-empty">
              Vinted brand lookup failed: {suggestionsQuery.error instanceof Error ? suggestionsQuery.error.message : String(suggestionsQuery.error)}
              {query.trim() ? ` Press Enter to block “${query.trim()}” anyway.` : ''}
            </p>
          ) : suggestions.length ? (
            suggestions.map((option, index) => (
              <button
                type="button"
                role="option"
                aria-selected={index === 0}
                className="combo-option"
                key={option.id}
                onMouseDown={(e) => { e.preventDefault(); pick(option.title); }}
              >
                {option.title}
              </button>
            ))
          ) : (
            <p className="combo-empty">No brand by that name on Vinted. Press Enter to block “{query.trim()}” anyway.</p>
          )}
        </div>
      ) : null}
    </div>
  );
}

// ─── Profile helpers ───────────────────────────────────────────────────────────

function ProfileList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="panel stack">
      <h3>{title}</h3>
      {items.length
        ? <ul className="profile-list">{items.map((item, index) => <li key={index}>{item}</li>)}</ul>
        : <p className="hint">None yet.</p>}
    </div>
  );
}

const SECTIONS = [
  { id: 'wardrobe', label: 'Wardrobe' },
  { id: 'listings', label: 'Rated listings' },
  { id: 'note', label: 'Taste note' },
  { id: 'brands', label: 'Blocked brands' },
  { id: 'profile', label: 'Taste profile' },
];

// ─── Page ─────────────────────────────────────────────────────────────────────

export function MyTastePage() {
  const queryClient = useQueryClient();
  const tasteQuery = useQuery({
    queryKey: queryKeys.taste,
    queryFn: api.getTaste,
    // Poll while a recompute is running (possibly started elsewhere) so the UI
    // reflects completion without a manual refresh.
    refetchInterval: (query) => (query.state.data?.recompute_state?.status === 'running' ? 3000 : false),
  });
  const blockedBrandsQuery = useQuery({ queryKey: queryKeys.blockedBrands, queryFn: api.getBlockedBrands });
  const updateBlockedBrandsMutation = useMutation({
    mutationFn: api.updateBlockedBrands,
    onSuccess: (result) => queryClient.setQueryData(queryKeys.blockedBrands, result),
  });
  const [uploadOpen, setUploadOpen] = useState(false);
  const [offerOpen, setOfferOpen] = useState(false);
  const { query: bucketsQuery, clothingItems, clothingItemLabel } = useBuckets();
  const [uploadTargetItem, setUploadTargetItem] = useState<ClothingItem>('');
  const [zipImportNotice, setZipImportNotice] = useState<string | null>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const [manualNote, setManualNote] = useState('');
  const [lastCost, setLastCost] = useState<number | null>(null);
  const [lastObservationCache, setLastObservationCache] = useState<{
    total_image_inputs: number;
    cached_observations: number;
    fresh_observations: number;
    observation_model: string;
  } | null>(null);
  const manualNoteDirty = useRef(false);
  const [noteDirty, setNoteDirty] = useState(false);
  const setManualNoteDirty = (value: boolean) => { manualNoteDirty.current = value; setNoteDirty(value); };
  useUnsavedGuard(noteDirty);

  useEffect(() => {
    if (!tasteQuery.data || manualNoteDirty.current) return;
    setManualNote(tasteQuery.data.manual_note.text);
  }, [tasteQuery.data?.manual_note.text]);

  const [chosenProfileItem, setSelectedProfileItem] = useState<ClothingItem>('');
  const selectedProfileItem = clothingItems.some((item) => item.value === chosenProfileItem)
    ? chosenProfileItem
    : clothingItems[0]?.value ?? '';

  const judgmentPromptQuery = useQuery({
    queryKey: queryKeys.judgmentPrompt(selectedProfileItem),
    queryFn: () => api.getJudgmentPrompt(selectedProfileItem),
    enabled: !!tasteQuery.data?.taste_profile && !!selectedProfileItem,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.taste });
    queryClient.invalidateQueries({ queryKey: queryKeys.candidates });
  };

  const uploadMutation = useMutation({
    mutationFn: ({ file, note, clothingItem }: { file: File; note: string; clothingItem: ClothingItem }) =>
      api.uploadWardrobeImage(file, note, clothingItem),
    onSuccess: () => invalidate(),
  });

  const zipImportMutation = useMutation({
    mutationFn: (file: File) => api.importWardrobeZip(file),
    onSuccess: (result: WardrobeZipImportResult) => {
      invalidate();
      const n = result.imported.length;
      const s = result.skipped.length;
      setZipImportNotice(s > 0 ? `Imported ${n} photo${n !== 1 ? 's' : ''} (${s} skipped)` : `Imported ${n} photo${n !== 1 ? 's' : ''}`);
      setTimeout(() => setZipImportNotice(null), 5000);
    },
  });

  const updateSampleMutation = useMutation({
    mutationFn: ({ sampleId, note, clothingItem, kind }: { sampleId: string; note: string; clothingItem: ClothingItem; kind?: OfferKind }) =>
      api.updateTasteSample(sampleId, { note, clothing_item: clothingItem, ...(kind ? { kind } : {}) }),
    onSuccess: invalidate,
  });
  const deleteSampleMutation = useMutation({ mutationFn: api.deleteTasteSample, onSuccess: invalidate });
  const addOfferMutation = useMutation({
    mutationFn: (payload: { vinted_url: string; kind: OfferKind; clothing_item: ClothingItem; note: string }) =>
      api.addTasteOffer(payload),
    onSuccess: invalidate,
  });
  const manualNoteMutation = useMutation({
    mutationFn: api.updateTasteManualNote,
    onSuccess: (_result, submittedNote) => {
      if (manualNote === submittedNote) {
        setManualNoteDirty(false);
      }
      invalidate();
    },
  });
  const recomputeMutation = useMutation({
    mutationFn: api.recomputeTaste,
    onSuccess: (result) => {
      setLastCost(result.cost_usd);
      setLastObservationCache(result.observation_cache);
      queryClient.setQueryData(queryKeys.taste, result.snapshot);
      queryClient.invalidateQueries({ queryKey: queryKeys.searches });
      queryClient.invalidateQueries({ queryKey: queryKeys.costs });
    },
    onError: () => {
      // The request may have died client-side (proxy timeout / network blip) while the
      // synchronous server-side recompute keeps running. Refetch so the snapshot's
      // recompute_state takes over driving the polling/banner, instead of leaving the UI
      // stuck on "Recompute failed" until the user re-clicks and hits an opaque 409.
      queryClient.invalidateQueries({ queryKey: queryKeys.taste });
    },
  });
  const cancelRecomputeMutation = useMutation({
    mutationFn: api.cancelTasteRecompute,
    onSuccess: (snapshot) => queryClient.setQueryData(queryKeys.taste, snapshot),
  });

  const grouped = useMemo(() => {
    const samples = tasteQuery.data?.samples ?? [];
    return {
      wardrobe: samples.filter((s) => s.kind === 'wardrobe'),
      liked: samples.filter((s) => s.kind === 'offer_like'),
      disliked: samples.filter((s) => s.kind === 'offer_dislike'),
      noted: samples.filter((s) => s.kind === 'offer_note'),
    };
  }, [tasteQuery.data?.samples]);

  const wardrobeByItem = useMemo(() => {
    const entries = clothingItems.map((item) => [
      item.value,
      grouped.wardrobe.filter((sample) => sample.clothing_item === item.value),
    ] as const);
    return Object.fromEntries(entries) as Record<ClothingItem, TasteSample[]>;
  }, [grouped.wardrobe, bucketsQuery.data]);

  if (tasteQuery.isLoading) return <div className="page"><PageHeader title="My Taste" /><Loading label="Loading taste…" /></div>;
  if (tasteQuery.isError || !tasteQuery.data) {
    return <div className="page"><PageHeader title="My Taste" /><ErrorText error={tasteQuery.error ?? 'Could not load taste.'} /></div>;
  }

  const taste = tasteQuery.data;
  const profile = taste.taste_profile;
  const selectedItemProfile = profile?.item_profiles?.[selectedProfileItem] ?? null;
  // Item-specific only: a tab without its own profile shows empties rather than
  // leaking global / other-bucket signals as if they were item-specific.
  const selectedRubric = Object.entries(selectedItemProfile?.scoring_rubric ?? {});
  const selectedDrafts = selectedItemProfile?.generated_search
    ? [selectedItemProfile.generated_search]
    : (profile?.generated_searches?.filter((draft) => draft.clothing_item === selectedProfileItem) ?? []);
  const allDrafts = profile?.generated_searches ?? [];

  const recomputeState = taste.recompute_state;
  const recomputeRunning = recomputeState?.status === 'running';
  const recomputeBusy = recomputeRunning || recomputeMutation.isPending;
  // Prefer the freshest local result, fall back to the persisted snapshot value
  // (only meaningful once a recompute has actually run).
  const displayCost = lastCost ?? (taste.last_recomputed_at ? recomputeState?.last_cost_usd ?? null : null);
  const recomputeConflict = recomputeMutation.error instanceof ApiError && recomputeMutation.error.status === 409;
  const dirty = taste.dirty_counts;
  const changed = dirty.new_or_changed_samples > 0 || dirty.manual_note_changed;

  const deleteSample = (id: string) => deleteSampleMutation.mutateAsync(id).then(() => undefined);
  const updateSample = (id: string, note: string, clothingItem: ClothingItem, kind?: OfferKind) =>
    updateSampleMutation.mutateAsync({ sampleId: id, note, clothingItem, kind }).then(() => undefined);

  return (
    <div className="page">
      <PageHeader
        title="My Taste"
        sub="Wardrobe photos, rated listings and your note train the profile the judge scores against. Recompute after changing them."
        actions={(
          <>
            {recomputeRunning ? (
              <button
                type="button"
                className="btn"
                onClick={() => cancelRecomputeMutation.mutate()}
                disabled={cancelRecomputeMutation.isPending}
              >
                {cancelRecomputeMutation.isPending ? 'Cancelling…' : 'Cancel'}
              </button>
            ) : null}
            <button
              type="button"
              className={`btn${changed || !profile ? ' btn-primary' : ''}`}
              onClick={() => recomputeMutation.mutate()}
              disabled={recomputeBusy}
            >
              <RefreshCw size={15} aria-hidden="true" />
              {recomputeBusy ? 'Recomputing…' : 'Recompute profile'}
            </button>
          </>
        )}
      />

      {/* While a recompute is running (incl. one the server kept going after a client-side
          request failure), show only the running notice — a stale client error or 409 here
          would falsely read as "failed". Cancel only force-clears the DB claim (see
          cancel_recompute docstring) — if the stuck call is still alive server-side it can still
          take a while to actually stop, but the button unblocks a fresh attempt immediately. */}
      {recomputeRunning ? (
        <div className="notice notice-accent" role="status">
          Recomputing. This page updates when it finishes. Stuck for a long time? Cancel and try again.
        </div>
      ) : recomputeConflict ? (
        <ErrorText error="A recompute is already running." prefix="Recompute failed" />
      ) : recomputeMutation.error ? (
        <ErrorText error={recomputeMutation.error} prefix="Recompute failed" />
      ) : cancelRecomputeMutation.error ? (
        <ErrorText error={cancelRecomputeMutation.error} prefix="Cancel failed" />
      ) : recomputeState?.status === 'failed' ? (
        <ErrorText error={recomputeState.error ?? 'Last recompute failed.'} prefix="Recompute failed" />
      ) : recomputeState?.status === 'cancelled' ? (
        <div className="notice">The last recompute was cancelled.</div>
      ) : null}

      <div className="stats">
        <Stat label="Last recompute" value={formatBerlinDateTime(taste.last_recomputed_at, 'Never')} />
        <Stat
          label="Changed since"
          value={dirty.new_or_changed_samples}
          note={`${dirty.new_or_changed_positive_samples} positive · ${dirty.new_or_changed_negative_samples} negative${dirty.manual_note_changed ? ' · note edited' : ''}`}
          tone={changed ? 'warn' : undefined}
        />
        <Stat label="Last cost" value={displayCost === null ? '–' : fmtUsd(displayCost)} />
        <Stat
          label="Image observations"
          value={lastObservationCache
            ? `${lastObservationCache.cached_observations} cached · ${lastObservationCache.fresh_observations} new`
            : '–'}
          note={lastObservationCache
            ? `${lastObservationCache.total_image_inputs} images · ${lastObservationCache.observation_model || 'n/a'}`
            : 'Shown after a recompute in this tab'}
        />
      </div>

      <div className="taste-layout">
        <nav className="toc" aria-label="On this page">
          {SECTIONS.map((section) => <a key={section.id} href={`#${section.id}`}>{section.label}</a>)}
        </nav>

        <div className="stack-lg" style={{ gap: 'var(--s6)' }}>
          <Section
            id="wardrobe"
            title="Wardrobe"
            sub="Photos of clothes you own and like, per bucket."
            actions={(
              <>
                <input
                  ref={zipInputRef}
                  type="file"
                  accept=".zip"
                  hidden
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) zipImportMutation.mutate(f);
                    e.target.value = '';
                  }}
                />
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => zipInputRef.current?.click()}
                  disabled={zipImportMutation.isPending}
                  title="A ZIP with one folder per bucket"
                >
                  <Upload size={14} aria-hidden="true" />
                  {zipImportMutation.isPending ? 'Importing…' : 'Import ZIP'}
                </button>
              </>
            )}
          >
            {zipImportNotice ? <p className="success-text">{zipImportNotice}</p> : null}
            <ErrorText error={zipImportMutation.error} prefix="Import failed" />
            <div className="bucket-list">
              {clothingItems.map((item) => (
                <div className="bucket" key={item.value}>
                  <div>
                    <h3>{item.label} <span className="faint">{wardrobeByItem[item.value].length}</span></h3>
                    <p>{item.description}</p>
                  </div>
                  <ImageGrid
                    samples={wardrobeByItem[item.value]}
                    onAdd={() => { setUploadTargetItem(item.value); setUploadOpen(true); }}
                    addLabel={`Add ${item.label} photos`}
                    onDelete={deleteSample}
                    onUpdate={(id, note, clothingItem) => updateSample(id, note, clothingItem)}
                  />
                </div>
              ))}
            </div>
          </Section>

          <Section
            id="listings"
            title="Rated listings"
            sub="Vinted listings you liked or disliked, here or with the Telegram buttons."
            actions={(
              <button type="button" className="btn btn-sm" onClick={() => setOfferOpen(true)}>
                <Link2 size={14} aria-hidden="true" />
                Add by URL
              </button>
            )}
          >
            <div className="offer-cols">
              <div className="panel offer-col">
                <h3><ThumbsUp size={15} className="tone-ok" aria-hidden="true" /> Liked <span className="faint">{grouped.liked.length}</span></h3>
                <ImageGrid samples={grouped.liked} emptyLabel="No liked listings yet." onDelete={deleteSample} onUpdate={updateSample} />
              </div>
              <div className="panel offer-col">
                <h3><ThumbsDown size={15} className="tone-bad" aria-hidden="true" /> Disliked <span className="faint">{grouped.disliked.length}</span></h3>
                <ImageGrid samples={grouped.disliked} emptyLabel="No disliked listings yet." onDelete={deleteSample} onUpdate={updateSample} />
              </div>
              {grouped.noted.length ? (
                <div className="panel offer-col">
                  <h3>Comment only <span className="faint">{grouped.noted.length}</span></h3>
                  <ImageGrid
                    samples={grouped.noted}
                    onDelete={deleteSample}
                    onUpdate={(id, note, clothingItem) => updateSample(id, note, clothingItem)}
                  />
                </div>
              ) : null}
            </div>
          </Section>
          <ErrorText error={deleteSampleMutation.error ?? updateSampleMutation.error} prefix="Sample update failed" />

          <Section id="note" title="Taste note" sub="Your own words about style, fit and what to avoid. The recompute reads it alongside the photos.">
            <div className="stack">
              <textarea
                value={manualNote}
                onChange={(e) => { setManualNoteDirty(true); setManualNote(e.target.value); }}
                rows={6}
                placeholder="Baggy workwear and 90s outdoor pieces. No slim fits, no fast fashion logos…"
                aria-label="Taste note"
              />
              <div className="row">
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => manualNoteMutation.mutate(manualNote)}
                  disabled={manualNoteMutation.isPending || !noteDirty}
                >
                  {manualNoteMutation.isPending ? 'Saving…' : 'Save note'}
                </button>
                {noteDirty ? <span className="hint">Unsaved changes</span> : null}
                {!noteDirty && dirty.manual_note_changed ? <span className="hint">Saved. Not in the profile until the next recompute.</span> : null}
              </div>
              <ErrorText error={manualNoteMutation.error} prefix="Save failed" />
            </div>
          </Section>

          <Section
            id="brands"
            title="Blocked brands"
            sub="Listings from these brands are dropped before judging, so they cost nothing and never alert."
          >
            <div className="stack">
              <BrandSearchInput
                blockedBrands={blockedBrandsQuery.data?.brands ?? []}
                onAdd={(title) => {
                  const current = blockedBrandsQuery.data?.brands ?? [];
                  if (current.some((brand) => brand.toLowerCase() === title.toLowerCase())) return;
                  updateBlockedBrandsMutation.mutate([...current, title]);
                }}
              />
              {blockedBrandsQuery.data?.brands.length ? (
                <div className="chips">
                  {blockedBrandsQuery.data.brands.map((brand) => (
                    <button
                      type="button"
                      className="chip"
                      key={brand}
                      onClick={() => {
                        const current = blockedBrandsQuery.data?.brands ?? [];
                        updateBlockedBrandsMutation.mutate(current.filter((b) => b !== brand));
                      }}
                      disabled={updateBlockedBrandsMutation.isPending}
                      title={`Unblock ${brand}`}
                    >
                      {brand} <span className="chip-x" aria-hidden="true">×</span>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="hint">{blockedBrandsQuery.isLoading ? 'Loading…' : 'No brands blocked.'}</p>
              )}
              <ErrorText error={blockedBrandsQuery.error ?? updateBlockedBrandsMutation.error} prefix="Blocked brands update failed" />
            </div>
          </Section>

          <Section
            id="profile"
            title="Taste profile"
            sub={profile
              ? `Version ${profile.version} · ${profile.model ?? 'unknown model'}${profile.reasoning_effort ? ` · ${profile.reasoning_effort} reasoning` : ''}${profile.generated_at ? ` · ${formatBerlinDateTime(profile.generated_at)}` : ''}`
              : undefined}
          >
            {!profile ? (
              <Empty title="No taste profile yet">
                Add wardrobe photos or rated listings, then recompute.
              </Empty>
            ) : (
              <div className="stack-lg">
                <p className="prose">{profile.summary}</p>

                <div className="seg seg-scroll" role="group" aria-label="Clothing bucket">
                  {clothingItems.map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      aria-pressed={selectedProfileItem === item.value}
                      onClick={() => setSelectedProfileItem(item.value)}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>

                {selectedItemProfile?.summary ? <p className="prose">{selectedItemProfile.summary}</p> : null}

                <DraftSearches
                  drafts={selectedDrafts}
                  allDrafts={allDrafts}
                  itemProfile={selectedItemProfile}
                  learningSnapshot={taste.latest_learning_snapshot}
                />

                <div className="profile-grid">
                  <ProfileList title="Likes" items={selectedItemProfile?.likes ?? []} />
                  <ProfileList title="Dislikes and penalties" items={selectedItemProfile?.dislikes_or_penalties ?? []} />
                  <ProfileList title="Instant alert examples" items={selectedItemProfile?.instant_alert_examples ?? []} />
                  <ProfileList title="Instant reject examples" items={selectedItemProfile?.instant_reject_examples ?? []} />
                </div>

                {selectedRubric.length > 0 ? (
                  <div className="panel stack">
                    <h3>Scoring rubric</h3>
                    <dl className="kv">
                      {selectedRubric.map(([band, description]) => (
                        <div key={band} style={{ display: 'contents' }}>
                          <dt className="num">{band}</dt>
                          <dd>{description}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                ) : null}

                {selectedItemProfile?.cross_item_influence?.length ? (
                  <div className="stack" style={{ gap: 'var(--s2)' }}>
                    <span className="hint">Influenced by other buckets</span>
                    <div className="chips">
                      {selectedItemProfile.cross_item_influence.map((item) => <span className="chip" key={item}>{item}</span>)}
                    </div>
                  </div>
                ) : null}

                <div className="stack">
                  {selectedItemProfile?.taste_prompt || profile.taste_prompt ? (
                    <details className="disclosure">
                      <summary>{clothingItemLabel(selectedProfileItem)} taste prompt</summary>
                      <div className="disclosure-body">
                        <pre className="pre">{selectedItemProfile?.taste_prompt ?? profile.taste_prompt}</pre>
                      </div>
                    </details>
                  ) : null}
                  {judgmentPromptQuery.data ? (
                    <details className="disclosure">
                      <summary>Full judge prompt (v{judgmentPromptQuery.data.profile_version})</summary>
                      <div className="disclosure-body stack">
                        <p className="hint">
                          {judgmentPromptQuery.data.character_count.toLocaleString()} characters ·{' '}
                          {judgmentPromptQuery.data.token_count.toLocaleString()} tokens ({judgmentPromptQuery.data.tokenizer})
                        </p>
                        <pre className="pre">{judgmentPromptQuery.data.prompt}</pre>
                      </div>
                    </details>
                  ) : null}
                  <ErrorText error={judgmentPromptQuery.error} prefix="Judge prompt failed to load" />
                </div>
              </div>
            )}
          </Section>
        </div>
      </div>

      {uploadOpen ? (
        <UploadModal
          onClose={() => setUploadOpen(false)}
          onUploadFile={(file, note) => uploadMutation.mutateAsync({ file, note, clothingItem: uploadTargetItem })}
          clothingItem={uploadTargetItem}
        />
      ) : null}
      {offerOpen ? (
        <AddOfferModal
          onClose={() => setOfferOpen(false)}
          onSubmit={(payload) => addOfferMutation.mutateAsync(payload)}
        />
      ) : null}
    </div>
  );
}

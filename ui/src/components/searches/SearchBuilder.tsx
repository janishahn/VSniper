import { type KeyboardEvent, useEffect, useMemo, useState } from 'react';
import { Play, X } from 'lucide-react';

import { DecimalInput } from '../DecimalInput';
import { ErrorText } from '../ErrorText';
import { Modal } from '../ui';
import { clothingItemLabel, clothingItems } from '../../lib/clothingItems';
import { formatBerlinDateTime } from '../../lib/datetime';
import {
  applySizeFilter,
  formatFilter,
  isCategoryFilter,
  isGuidedFilter,
  isSizeFilter,
  isPreferKeywordFilter,
  isAvoidKeywordFilter,
  isPriceCeilingFilter,
  priceCeilingFor,
  replaceFilters,
  unique,
  valuesFor,
} from '../../lib/searchFilters';
import type {
  ClothingItem,
  SearchCategoryOption,
  SearchCategoryOptions,
  SearchFilter,
  SearchRecord,
  SearchRunResult,
  SearchUpdatePayload,
} from '../../types';

export type SearchFormState = {
  name: string;
  clothing_item: ClothingItem;
  query: string;
  region: string;
  enabled: boolean;
  filters: SearchFilter[];
  alert_threshold: number | null;
};

const filterModes: SearchFilter['mode'][] = ['include', 'exclude', 'range', 'exact'];

function setCategoryAliases(form: SearchFormState, aliases: string[]): SearchFormState {
  const values = unique(aliases);
  return {
    ...form,
    filters: replaceFilters(
      form.filters,
      isCategoryFilter,
      values.length ? { field: 'category', label: 'Vinted category', mode: 'include', values } : null,
      'front',
    ),
  };
}

type CategoryChoiceGroup = {
  key: string;
  primaryAlias: string;
  aliases: string[];
  catalogIds: string[];
};

function categoryIdsForAlias(option: SearchCategoryOption, alias: string): string[] {
  return option.alias_catalog_ids?.[alias] ?? [alias];
}

function categoryGroupKey(ids: string[]): string {
  return ids.length ? [...ids].sort().join('|') : '__unresolved__';
}

function groupCategoryAliases(option: SearchCategoryOption): CategoryChoiceGroup[] {
  const groups = new Map<string, CategoryChoiceGroup>();
  for (const alias of option.allowed_aliases) {
    const catalogIds = categoryIdsForAlias(option, alias);
    const key = categoryGroupKey(catalogIds);
    const existing = groups.get(key);
    if (existing) {
      existing.aliases.push(alias);
      continue;
    }
    groups.set(key, { key, primaryAlias: alias, aliases: [alias], catalogIds });
  }
  return Array.from(groups.values());
}

function selectedCategoryGroupKeys(option: SearchCategoryOption, selectedAliases: string[]): Set<string> {
  return new Set(selectedAliases.map((alias) => categoryGroupKey(categoryIdsForAlias(option, alias))));
}

function setDefaultCategoryAliases(
  form: SearchFormState,
  categoryOptions?: SearchCategoryOptions,
  clothingItem: ClothingItem = form.clothing_item,
): SearchFormState {
  const defaults = categoryOptions?.[clothingItem]?.default_aliases ?? [];
  return setCategoryAliases({ ...form, clothing_item: clothingItem }, defaults);
}

function setTokenFilter(
  form: SearchFormState,
  predicate: (filter: SearchFilter) => boolean,
  replacement: SearchFilter | null,
): SearchFormState {
  return { ...form, filters: replaceFilters(form.filters, predicate, replacement) };
}

function normalizePriceCeiling(value: string): string {
  const normalized = value.trim().replace(',', '.');
  if (!normalized) return '';
  const amount = Number(normalized);
  if (!Number.isFinite(amount) || amount <= 0) return '';
  return String(Math.round(amount * 100) / 100);
}

function setPriceCeiling(form: SearchFormState, value: string): SearchFormState {
  const normalized = normalizePriceCeiling(value);
  return {
    ...form,
    filters: replaceFilters(
      form.filters,
      isPriceCeilingFilter,
      normalized ? { field: 'price', label: 'Maximum price', mode: 'range', values: [normalized] } : null,
    ),
  };
}

export function withDefaultCategoryFilter(
  form: SearchFormState,
  categoryOptions?: SearchCategoryOptions,
): SearchFormState {
  return valuesFor(form.filters, isCategoryFilter).length ? form : setDefaultCategoryAliases(form, categoryOptions);
}

export function formFromSearch(search: SearchRecord): SearchFormState {
  return {
    name: search.name,
    clothing_item: search.clothing_item,
    query: search.query,
    region: 'de',
    enabled: search.enabled,
    filters: search.filters,
    alert_threshold: search.alert_threshold,
  };
}

export function payloadFromForm(form: SearchFormState): Omit<SearchUpdatePayload, 'enabled'> {
  const query = form.query.trim();
  const region = form.region.trim().toLowerCase();
  if (region !== 'de') throw new Error('Only the de Vinted region is supported.');
  return {
    clothing_item: form.clothing_item,
    query,
    region,
    filters: form.filters,
    alert_threshold: form.alert_threshold,
  };
}

function validationIssues(form: SearchFormState): string[] {
  const issues: string[] = [];
  if (!form.query.trim()) issues.push('Add a Vinted query, for example “cargo patchwork” or “vintage adidas”.');
  return issues;
}

function ChipEditor({
  values,
  placeholder,
  label,
  onChange,
}: {
  values: string[];
  placeholder: string;
  label: string;
  onChange: (values: string[]) => void;
}) {
  const [draft, setDraft] = useState('');

  const addValues = (raw: string) => {
    const next = unique([...values, ...raw.split(',').map((value) => value.trim())]);
    onChange(next);
    setDraft('');
  };

  const removeValue = (value: string) => onChange(values.filter((item) => item !== value));

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault();
      addValues(draft);
    }
    if (event.key === 'Backspace' && !draft && values.length) {
      removeValue(values[values.length - 1]);
    }
  };

  return (
    <div className="token-input">
      {values.map((value) => (
        <button type="button" className="chip" key={value} onClick={() => removeValue(value)} title={`Remove ${value}`}>
          {value} <span className="chip-x" aria-hidden="true">×</span>
        </button>
      ))}
      <input
        value={draft}
        placeholder={values.length ? '' : placeholder}
        aria-label={label}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => { if (draft.trim()) addValues(draft); }}
        onKeyDown={handleKeyDown}
      />
    </div>
  );
}

function CategoryPicker({
  form,
  categoryOptions,
  onChange,
}: {
  form: SearchFormState;
  categoryOptions?: SearchCategoryOptions;
  onChange: (form: SearchFormState) => void;
}) {
  const option = categoryOptions?.[form.clothing_item];
  const selected = valuesFor(form.filters, isCategoryFilter);
  const visibleSelection = selected.length ? selected : option?.default_aliases ?? [];
  const groupedChoices = option ? groupCategoryAliases(option) : [];
  const selectedGroupKeys = option ? selectedCategoryGroupKeys(option, visibleSelection) : new Set<string>();

  const toggleGroup = (group: CategoryChoiceGroup) => {
    const aliasesInGroup = new Set(group.aliases);
    const active = selectedGroupKeys.has(group.key);
    const next = active
      ? visibleSelection.filter((value) => !aliasesInGroup.has(value))
      : [...visibleSelection.filter((value) => !aliasesInGroup.has(value)), group.primaryAlias];
    onChange(setCategoryAliases(form, next));
  };

  return (
    <div className="builder-block">
      <div className="row-between">
        <div>
          <h3>Vinted category</h3>
          <p className="hint">Limits the search to these catalog sections so Vinted doesn't return other clothing.</p>
        </div>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => onChange(setDefaultCategoryAliases(form, categoryOptions))}
          disabled={!option}
        >
          Use defaults
        </button>
      </div>
      {!option ? (
        <p className="state-line">Loading categories…</p>
      ) : (
        <>
          <div className="chips" role="group" aria-label="Categories">
            {groupedChoices.map((group) => (
              <button
                type="button"
                className="choice"
                aria-pressed={selectedGroupKeys.has(group.key)}
                key={group.key}
                onClick={() => toggleGroup(group)}
                title={group.aliases.length > 1 ? `Vinted catalog ID${group.catalogIds.length > 1 ? 's' : ''} ${group.catalogIds.join(', ')}: ${group.aliases.join(', ')}` : undefined}
              >
                {group.primaryAlias}{group.aliases.length > 1 ? ` +${group.aliases.length - 1}` : ''}
              </button>
            ))}
          </div>
          <p className="hint">
            Defaults: {option.default_aliases.join(', ')} · {option.resolved_catalog_ids.length} Vinted catalog IDs
          </p>
        </>
      )}
    </div>
  );
}

function GuidedFilterEditor({
  form,
  categoryOptions,
  onChange,
  onSyncSizes,
  isSyncingSizes,
  syncSizesError,
}: {
  form: SearchFormState;
  categoryOptions?: SearchCategoryOptions;
  onChange: (form: SearchFormState) => void;
  onSyncSizes: () => void;
  isSyncingSizes: boolean;
  syncSizesError?: unknown;
}) {
  const sizes = valuesFor(form.filters, isSizeFilter);
  const prefer = valuesFor(form.filters, isPreferKeywordFilter);
  const avoid = valuesFor(form.filters, isAvoidKeywordFilter);
  const priceCeiling = priceCeilingFor(form.filters);

  return (
    <>
      <CategoryPicker form={form} categoryOptions={categoryOptions} onChange={onChange} />

      <div className="builder-block builder-cols">
        <div className="stack">
          <div className="row-between">
            <h3>Sizes</h3>
            <button type="button" className="btn btn-sm" onClick={onSyncSizes} disabled={isSyncingSizes}>
              {isSyncingSizes ? 'Syncing…' : 'Sync from Vinted profile'}
            </button>
          </div>
          <ChipEditor
            values={sizes}
            label="Sizes"
            placeholder="M, L, 42…"
            onChange={(values) => onChange({ ...form, filters: applySizeFilter(form.filters, values) })}
          />
          <ErrorText error={syncSizesError} prefix="Size sync failed" />
        </div>

        <div className="stack">
          <div className="row-between">
            <h3>Maximum price</h3>
            {priceCeiling ? (
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => onChange(setPriceCeiling(form, ''))}>Clear</button>
            ) : null}
          </div>
          <label className="affix">
            <DecimalInput
              type="text"
              inputMode="decimal"
              placeholder="No limit"
              aria-label="Maximum price in euro"
              value={priceCeiling}
              normalize={normalizePriceCeiling}
              onCommit={(normalized) => onChange(setPriceCeiling(form, normalized))}
            />
            <span className="muted">€</span>
          </label>
        </div>
      </div>

      <div className="builder-block builder-cols">
        <div className="field">
          Prefer keywords
          <span className="hint">Raise confidence before the vision judge runs.</span>
          <ChipEditor
            values={prefer}
            label="Prefer keywords"
            placeholder="cargo, patchwork, Y2K…"
            onChange={(values) => onChange(setTokenFilter(
              form,
              isPreferKeywordFilter,
              values.length ? { field: 'keywords', label: 'Prefer', mode: 'include', values } : null,
            ))}
          />
        </div>
        <div className="field">
          Avoid keywords
          <span className="hint">Push obvious misses away early.</span>
          <ChipEditor
            values={avoid}
            label="Avoid keywords"
            placeholder="office, plain, logo…"
            onChange={(values) => onChange(setTokenFilter(
              form,
              isAvoidKeywordFilter,
              values.length ? { field: 'keywords', label: 'Avoid', mode: 'exclude', values } : null,
            ))}
          />
        </div>
      </div>
    </>
  );
}

function FilterValuesInput({ values, onCommit }: { values: string[]; onCommit: (values: string[]) => void }) {
  // Keep the raw text locally so typing a comma (or trailing spaces) isn't erased
  // mid-keystroke; only parse into discrete values on blur.
  const [raw, setRaw] = useState(values.join(', '));

  // Resync when the committed values change from outside (e.g. a different filter row).
  useEffect(() => {
    setRaw(values.join(', '));
  }, [values]);

  const commit = () => {
    const parsed = unique(raw.split(','));
    onCommit(parsed);
    setRaw(parsed.join(', '));
  };

  return (
    <input
      placeholder="value1, value2"
      aria-label="Values"
      value={raw}
      onChange={(event) => setRaw(event.target.value)}
      onBlur={commit}
    />
  );
}

function AdvancedFilterEditor({ filters, onChange }: { filters: SearchFilter[]; onChange: (filters: SearchFilter[]) => void }) {
  const update = (index: number, patch: Partial<SearchFilter>) =>
    onChange(filters.map((filter, i) => (i === index ? { ...filter, ...patch } : filter)));
  const remove = (index: number) => onChange(filters.filter((_, i) => i !== index));
  const add = () => onChange([...filters, { field: '', label: '', mode: 'include', values: [] }]);

  return (
    <details className="disclosure builder-block">
      <summary>Advanced filters{filters.length ? ` (${filters.length})` : ''}</summary>
      <div className="disclosure-body stack">
        <p className="hint">Raw Vinted fields for anything the controls above don't cover.</p>
        <div className="filter-grid">
          {filters.length > 0 ? (
            <div className="filter-row filter-row-head" aria-hidden="true">
              <span>Field</span>
              <span>Label</span>
              <span>Mode</span>
              <span>Values</span>
              <span />
            </div>
          ) : null}
          {filters.map((filter, index) => (
            <div className="filter-row" key={index}>
              <input
                placeholder="brand"
                aria-label="Field"
                value={filter.field}
                onChange={(event) => update(index, { field: event.target.value })}
              />
              <input
                placeholder="Brand"
                aria-label="Label"
                value={filter.label}
                onChange={(event) => update(index, { label: event.target.value })}
              />
              <select
                aria-label="Mode"
                value={filter.mode}
                onChange={(event) => update(index, { mode: event.target.value as SearchFilter['mode'] })}
              >
                {filterModes.map((mode) => <option key={mode} value={mode}>{mode}</option>)}
              </select>
              <FilterValuesInput values={filter.values} onCommit={(values) => update(index, { values })} />
              <button type="button" className="btn btn-ghost icon-btn" onClick={() => remove(index)} aria-label="Remove filter">
                <X size={15} aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
        <div>
          <button type="button" className="btn btn-sm" onClick={add}>Add filter</button>
        </div>
      </div>
    </details>
  );
}

export function SearchBuilderModal({
  form,
  categoryOptions,
  onChange,
  onClose,
  onSubmit,
  isPending,
  error,
  onSyncSizes,
  isSyncingSizes,
  syncSizesError,
}: {
  form: SearchFormState;
  categoryOptions?: SearchCategoryOptions;
  onChange: (form: SearchFormState) => void;
  onClose: () => void;
  onSubmit: () => void;
  isPending: boolean;
  error: string | null;
  onSyncSizes: () => void;
  isSyncingSizes: boolean;
  syncSizesError?: unknown;
}) {
  const issues = validationIssues(form);
  const advancedFilters = useMemo(() => form.filters.filter((filter) => !isGuidedFilter(filter)), [form.filters]);
  const selectedItem = clothingItems.find((item) => item.value === form.clothing_item);
  const searchLabel = clothingItemLabel(form.clothing_item);

  const setAdvancedFilters = (nextAdvanced: SearchFilter[]) => {
    onChange({ ...form, filters: [...form.filters.filter(isGuidedFilter), ...nextAdvanced] });
  };

  return (
    <Modal
      label={`Edit ${searchLabel} search`}
      title={`${searchLabel} search`}
      sub={selectedItem ? `${selectedItem.description} · vinted.de` : undefined}
      onClose={onClose}
      locked={isPending}
      size="lg"
      error={error}
      footer={(
        <>
          <button type="button" className="btn" onClick={onClose} disabled={isPending}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={onSubmit} disabled={isPending || issues.length > 0}>
            {isPending ? 'Saving…' : 'Save search'}
          </button>
        </>
      )}
    >
      <div className="builder-block">
        <div className="builder-cols" style={{ alignItems: 'end' }}>
          <label className="field">
            Vinted query
            <input
              value={form.query}
              onChange={(event) => onChange({ ...form, query: event.target.value })}
              placeholder="cargo patchwork"
            />
          </label>
          <label className="switch">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(event) => onChange({ ...form, enabled: event.target.checked })}
            />
            <span className="switch-track" aria-hidden="true" />
            Scan automatically
          </label>
        </div>
        {issues.map((issue) => <p className="tone-warn small" key={issue}>{issue}</p>)}
      </div>

      <GuidedFilterEditor
        form={form}
        categoryOptions={categoryOptions}
        onChange={onChange}
        onSyncSizes={onSyncSizes}
        isSyncingSizes={isSyncingSizes}
        syncSizesError={syncSizesError}
      />

      <AdvancedFilterEditor filters={advancedFilters} onChange={setAdvancedFilters} />
    </Modal>
  );
}

function normalizeThreshold(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return '';
  const value = Math.round(Number(trimmed));
  if (!Number.isFinite(value)) return '';
  return String(Math.max(1, Math.min(100, value)));
}

export function SearchCard({
  search,
  globalAlertThreshold,
  onEdit,
  onToggle,
  onLiveRun,
  onQuickSave,
  runResult,
  errors,
  isBusy,
}: {
  search: SearchRecord;
  globalAlertThreshold: number;
  onEdit: () => void;
  onToggle: () => void;
  onLiveRun: () => void;
  onQuickSave: (payload: SearchUpdatePayload) => void;
  runResult?: SearchRunResult;
  errors: {
    toggle?: unknown;
    live?: unknown;
    quick?: unknown;
  };
  isBusy: {
    live: boolean;
    quick: boolean;
    toggle: boolean;
  };
}) {
  const categoryValues = valuesFor(search.filters, isCategoryFilter);
  const sizeValues = valuesFor(search.filters, isSizeFilter);
  const preferValues = valuesFor(search.filters, isPreferKeywordFilter);
  const avoidValues = valuesFor(search.filters, isAvoidKeywordFilter);
  const priceCeiling = priceCeilingFor(search.filters);
  const advancedCount = search.filters.filter((filter) => !isGuidedFilter(filter)).length;
  const canRun = search.query.trim().length > 0;
  const saveForm = (form: SearchFormState) => {
    const payload = payloadFromForm(form);
    onQuickSave({ ...payload, enabled: form.enabled });
  };
  const savePriceCeiling = (value: string) => {
    if (value === priceCeiling) return;
    saveForm(setPriceCeiling(formFromSearch(search), value));
  };
  const saveAlertThreshold = (value: string) => {
    const parsed = value === '' ? null : Number(value);
    if (parsed === search.alert_threshold) return;
    saveForm({ ...formFromSearch(search), alert_threshold: parsed });
  };

  return (
    <article className={`search-item${search.enabled ? '' : ' is-off'}`}>
      <div>
        <h2 className="search-name">
          {search.name}
          {search.enabled ? null : <span className="badge">Paused</span>}
        </h2>
        <p className="search-query">
          {canRun ? <>Query <code>{search.query}</code></> : <span className="tone-warn">No query yet. Edit the search before it can run.</span>}
        </p>
        <div className="chips">
          {categoryValues.length ? <span className="chip">{categoryValues.join(', ')}</span> : null}
          {sizeValues.length ? <span className="chip">Sizes <strong>{sizeValues.join(', ')}</strong></span> : null}
          {preferValues.length ? <span className="chip">Prefer <strong>{preferValues.join(', ')}</strong></span> : null}
          {avoidValues.length ? <span className="chip chip-avoid">Avoid {avoidValues.join(', ')}</span> : null}
          {advancedCount ? (
            <span className="chip" title={search.filters.filter((filter) => !isGuidedFilter(filter)).map(formatFilter).join(' · ')}>
              +{advancedCount} advanced
            </span>
          ) : null}
          {!search.filters.length ? <span className="hint">Category defaults apply.</span> : null}
        </div>
        <p className="search-last">
          {search.last_run_at
            ? <>Last run {formatBerlinDateTime(search.last_run_at)} · {search.last_fetched_count} fetched · {search.last_judged_count} judged · {search.last_found_count} alert{search.last_found_count === 1 ? '' : 's'}</>
            : 'Never run'}
        </p>
      </div>

      <div className="search-tune">
        <label className="field">
          Max price
          <span className="affix">
            <DecimalInput
              type="text"
              inputMode="decimal"
              placeholder="None"
              value={priceCeiling}
              normalize={normalizePriceCeiling}
              onCommit={savePriceCeiling}
              disabled={isBusy.quick}
            />
            <span className="muted">€</span>
          </span>
        </label>
        <label className="field">
          Alert at
          <DecimalInput
            type="text"
            inputMode="numeric"
            placeholder={`${globalAlertThreshold} (default)`}
            value={search.alert_threshold === null ? '' : String(search.alert_threshold)}
            normalize={normalizeThreshold}
            onCommit={saveAlertThreshold}
            disabled={isBusy.quick}
          />
        </label>
      </div>

      <div className="search-buttons">
        <label className="switch" title={search.enabled ? 'Pause scanning' : 'Resume scanning'}>
          <input type="checkbox" checked={search.enabled} onChange={onToggle} disabled={isBusy.toggle} />
          <span className="switch-track" aria-hidden="true" />
          <span className="visually-hidden">Scan automatically</span>
        </label>
        <button type="button" className="btn" onClick={onEdit}>Edit</button>
        <button
          type="button"
          className="btn"
          onClick={onLiveRun}
          disabled={isBusy.live || !canRun}
          title={canRun ? 'Scan now and send alerts' : 'Add a Vinted query first'}
        >
          <Play size={14} aria-hidden="true" />
          {isBusy.live ? 'Running…' : 'Run now'}
        </button>
      </div>

      {runResult || errors.toggle || errors.live || errors.quick ? (
        <div className="search-feedback">
          {runResult ? <p className="run-result">{runResult.summary}</p> : null}
          <ErrorText error={errors.toggle} prefix="Toggle failed" />
          <ErrorText error={errors.live} prefix="Run failed" />
          <ErrorText error={errors.quick} prefix="Save failed" />
        </div>
      ) : null}
    </article>
  );
}

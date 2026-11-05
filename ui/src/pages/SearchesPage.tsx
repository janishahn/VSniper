import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';

import { Play, RefreshCw } from 'lucide-react';

import { ErrorText } from '../components/ErrorText';
import { Empty, Loading, PageHeader } from '../components/ui';
import {
  formFromSearch,
  payloadFromForm,
  SearchBuilderModal,
  SearchCard,
  type SearchFormState,
  withDefaultCategoryFilter,
} from '../components/searches/SearchBuilder';
import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { applySizeFilter } from '../lib/searchFilters';
import type { SearchRecord, SearchUpdatePayload } from '../types';

type BuilderState = { searchId: string; form: SearchFormState };

export function SearchesPage() {
  const queryClient = useQueryClient();
  const searchesQuery = useQuery({ queryKey: queryKeys.searches, queryFn: api.getSearches, refetchInterval: 30_000 });
  const settingsQuery = useQuery({ queryKey: queryKeys.settings, queryFn: api.getSettings });
  const categoryOptionsQuery = useQuery({ queryKey: queryKeys.searchCategoryOptions, queryFn: api.getSearchCategoryOptions });
  const [builder, setBuilder] = useState<BuilderState | null>(null);
  const [builderError, setBuilderError] = useState<string | null>(null);

  const closeBuilder = () => {
    setBuilder(null);
    setBuilderError(null);
  };

  const openEditBuilder = (search: SearchRecord) => {
    setBuilder({ searchId: search.id, form: withDefaultCategoryFilter(formFromSearch(search), categoryOptionsQuery.data) });
    setBuilderError(null);
  };

  const invalidateSearchRunData = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.searches });
    queryClient.invalidateQueries({ queryKey: queryKeys.candidates });
    queryClient.invalidateQueries({ queryKey: queryKeys.stats });
    queryClient.invalidateQueries({ queryKey: queryKeys.costs });
  };

  const updateMutation = useMutation({
    mutationFn: ({ searchId, payload }: { searchId: string; payload: SearchUpdatePayload }) => api.updateSearch(searchId, payload),
    onSuccess: () => {
      closeBuilder();
      queryClient.invalidateQueries({ queryKey: queryKeys.searches });
    },
    onError: (error) => {
      if (builder) setBuilderError(error.message);
    },
  });

  const toggleMutation = useMutation({
    mutationFn: api.toggleSearch,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.searches }),
  });

  const liveMutation = useMutation({
    mutationFn: api.runSearch,
    onSuccess: invalidateSearchRunData,
  });

  const runAllMutation = useMutation({
    mutationFn: api.runAllSearches,
    onSuccess: invalidateSearchRunData,
  });

  const syncSizesMutation = useMutation({ mutationFn: api.syncVintedSizes });

  const applySizesMutation = useMutation({
    mutationFn: api.applyProfileSizesToAll,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.searches }),
  });

  const updateBuilderForm = (form: SearchFormState) => {
    setBuilder((current) => (current ? { ...current, form } : current));
  };

  const handleSyncSizes = useCallback(() => {
    if (!builder) return;
    syncSizesMutation.mutate(undefined, {
      onSuccess: (result) => {
        setBuilder((current) => (
          current ? { ...current, form: { ...current.form, filters: applySizeFilter(current.form.filters, result.sizes) } } : current
        ));
      },
    });
  }, [builder, syncSizesMutation]);

  const submitBuilder = () => {
    if (!builder) return;
    try {
      setBuilderError(null);
      const payload = payloadFromForm(builder.form);
      updateMutation.mutate({ searchId: builder.searchId, payload: { ...payload, enabled: builder.form.enabled } });
    } catch (error) {
      setBuilderError(error instanceof Error ? error.message : 'Search could not be saved.');
    }
  };

  const header = (
    <PageHeader
      title="Searches"
      sub="One saved Vinted search per clothing bucket. The worker scans each enabled search on the interval set in Settings."
      actions={(
        <>
          <button
            type="button"
            className="btn"
            onClick={() => applySizesMutation.mutate()}
            disabled={applySizesMutation.isPending}
            title="Copy the sizes from your Vinted profile into every search"
          >
            <RefreshCw size={15} aria-hidden="true" />
            {applySizesMutation.isPending ? 'Syncing sizes…' : 'Sync sizes'}
          </button>
          <button type="button" className="btn btn-primary" onClick={() => runAllMutation.mutate()} disabled={runAllMutation.isPending}>
            <Play size={15} aria-hidden="true" />
            {runAllMutation.isPending ? 'Running all…' : 'Run all now'}
          </button>
        </>
      )}
    />
  );

  if (searchesQuery.isLoading) return <div className="page">{header}<Loading label="Loading searches…" /></div>;
  if (searchesQuery.isError || !searchesQuery.data) {
    return <div className="page">{header}<ErrorText error={searchesQuery.error ?? 'Could not load searches.'} /></div>;
  }
  const globalAlertThreshold = settingsQuery.data?.alert_threshold ?? 95;

  return (
    <div className="page">
      {header}

      <ErrorText error={categoryOptionsQuery.error} prefix="Category metadata failed to load" />
      <ErrorText error={runAllMutation.error} prefix="Run all failed" />
      <ErrorText error={applySizesMutation.error} prefix="Size sync failed" />
      {applySizesMutation.isSuccess ? (
        <p className="success-text">Applied sizes {applySizesMutation.data.sizes.join(', ') || '(none found)'} to every search.</p>
      ) : null}

      {searchesQuery.data.length === 0 ? (
        <Empty title="No searches yet">
          The API creates one search per clothing bucket when it starts. Restart the backend to seed them.
        </Empty>
      ) : (
        <div className="search-list">
          {searchesQuery.data.map((search) => {
            const runResult = liveMutation.data?.search_id === search.id
              ? liveMutation.data
              : runAllMutation.data?.find((r) => r.search_id === search.id);
            return (
              <SearchCard
                key={search.id}
                search={search}
                globalAlertThreshold={globalAlertThreshold}
                onEdit={() => openEditBuilder(search)}
                onToggle={() => toggleMutation.mutate(search.id)}
                onLiveRun={() => liveMutation.mutate(search.id)}
                onQuickSave={(payload) => updateMutation.mutate({ searchId: search.id, payload })}
                runResult={runResult}
                errors={{
                  toggle: toggleMutation.variables === search.id ? toggleMutation.error : undefined,
                  live: liveMutation.variables === search.id ? liveMutation.error : undefined,
                  quick: updateMutation.variables?.searchId === search.id && !builder ? updateMutation.error : undefined,
                }}
                isBusy={{
                  live: liveMutation.isPending && liveMutation.variables === search.id,
                  quick: updateMutation.isPending && updateMutation.variables?.searchId === search.id && !builder,
                  toggle: toggleMutation.isPending && toggleMutation.variables === search.id,
                }}
              />
            );
          })}
        </div>
      )}

      {builder ? (
        <SearchBuilderModal
          form={builder.form}
          categoryOptions={categoryOptionsQuery.data}
          onChange={updateBuilderForm}
          onClose={closeBuilder}
          onSubmit={submitBuilder}
          isPending={updateMutation.isPending}
          error={builderError}
          onSyncSizes={handleSyncSizes}
          isSyncingSizes={syncSizesMutation.isPending}
          syncSizesError={syncSizesMutation.error}
        />
      ) : null}
    </div>
  );
}

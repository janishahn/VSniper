import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { Play } from 'lucide-react';

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
import { useBuckets } from '../lib/clothingItems';
import { queryKeys } from '../lib/queryKeys';
import { useCurrentUser } from '../lib/useUserId';
import type { SearchRecord, SearchUpdatePayload } from '../types';

type BuilderState = { searchId: string; form: SearchFormState };

export function SearchesPage() {
  const { userId, user } = useCurrentUser();
  const queryClient = useQueryClient();
  const searchesQuery = useQuery({ queryKey: queryKeys.searches(userId), queryFn: () => api.getSearches(userId), refetchInterval: 30_000 });
  const settingsQuery = useQuery({ queryKey: queryKeys.settings, queryFn: api.getSettings });
  const { query: bucketsQuery, buckets } = useBuckets();
  const bucketFor = (key: string) => buckets.find((bucket) => bucket.key === key);
  const [builder, setBuilder] = useState<BuilderState | null>(null);
  const [builderError, setBuilderError] = useState<string | null>(null);

  const closeBuilder = () => {
    setBuilder(null);
    setBuilderError(null);
  };

  const openEditBuilder = (search: SearchRecord) => {
    setBuilder({ searchId: search.id, form: withDefaultCategoryFilter(formFromSearch(search), bucketFor(search.clothing_item)) });
    setBuilderError(null);
  };

  const invalidateSearchRunData = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.searches(userId) });
    queryClient.invalidateQueries({ queryKey: queryKeys.candidates(userId) });
    queryClient.invalidateQueries({ queryKey: queryKeys.stats(userId) });
    queryClient.invalidateQueries({ queryKey: queryKeys.costs });
  };

  const updateMutation = useMutation({
    mutationFn: ({ searchId, payload }: { searchId: string; payload: SearchUpdatePayload }) => api.updateSearch(userId, searchId, payload),
    onSuccess: () => {
      closeBuilder();
      queryClient.invalidateQueries({ queryKey: queryKeys.searches(userId) });
    },
    onError: (error) => {
      if (builder) setBuilderError(error.message);
    },
  });

  const toggleMutation = useMutation({
    mutationFn: (searchId: string) => api.toggleSearch(userId, searchId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.searches(userId) }),
  });

  const liveMutation = useMutation({
    mutationFn: (searchId: string) => api.runSearch(userId, searchId),
    onSuccess: invalidateSearchRunData,
  });

  const runAllMutation = useMutation({
    mutationFn: () => api.runAllSearches(userId),
    onSuccess: invalidateSearchRunData,
  });

  const updateBuilderForm = (form: SearchFormState) => {
    setBuilder((current) => (current ? { ...current, form } : current));
  };

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
        <button type="button" className="btn btn-primary" onClick={() => runAllMutation.mutate()} disabled={runAllMutation.isPending}>
          <Play size={15} aria-hidden="true" />
          {runAllMutation.isPending ? 'Running all…' : 'Run all now'}
        </button>
      )}
    />
  );

  if (searchesQuery.isLoading) return <div className="page">{header}<Loading label="Loading searches…" /></div>;
  if (searchesQuery.isError || !searchesQuery.data) {
    return <div className="page">{header}<ErrorText error={searchesQuery.error ?? 'Could not load searches.'} /></div>;
  }
  const globalAlertThreshold = user?.alert_threshold ?? 95;

  return (
    <div className="page">
      {header}

      <ErrorText error={bucketsQuery.error} prefix="Buckets failed to load" />
      <ErrorText error={runAllMutation.error} prefix="Run all failed" />

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
          bucket={bucketFor(builder.form.clothing_item)}
          onChange={updateBuilderForm}
          onClose={closeBuilder}
          onSubmit={submitBuilder}
          isPending={updateMutation.isPending}
          error={builderError}
        />
      ) : null}
    </div>
  );
}

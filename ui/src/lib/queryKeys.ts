// Single source of truth for TanStack Query cache keys. Using these instead of
// inline string literals stops a typo in one `invalidateQueries` call from
// silently failing to refresh a cache.
export const queryKeys = {
  users: ['users'] as const,
  // Keys of what a profile owns start with ['users', userId], so one profile's data never
  // answers for another's.
  stats: (userId: number) => ['users', userId, 'stats'] as const,
  costs: ['ai-cost-stats'] as const,
  scoreDistribution: (userId: number, window: string) => ['users', userId, 'score-distribution', window] as const,
  storage: ['storage'] as const,
  errors: (params: { source: string; offset: number; limit: number }) =>
    ['errors', params.source, params.offset, params.limit] as const,
  searches: (userId: number) => ['users', userId, 'searches'] as const,
  buckets: (userId: number) => ['users', userId, 'buckets'] as const,
  vintedCatalog: ['vinted-catalog'] as const,
  vintedSizes: (catalogIds: number[]) => ['vinted-sizes', ...catalogIds] as const,
  taste: (userId: number) => ['users', userId, 'taste'] as const,
  judgmentPrompt: (userId: number, clothingItem: string) => ['users', userId, 'judgment-prompt', clothingItem] as const,
  candidates: (userId: number) => ['users', userId, 'candidates'] as const,
  candidatesPage: (userId: number, params: {
    clothing_item: string;
    stage: string;
    decision: string;
    feedback: string;
    delivery_status: string;
    window: string;
    sort: string;
    offset: number;
    limit: number;
  }) =>
    [
      'users',
      userId,
      'candidates',
      params.clothing_item,
      params.stage,
      params.decision,
      params.feedback,
      params.delivery_status,
      params.window,
      params.sort,
      params.offset,
      params.limit,
    ] as const,
  settings: ['settings'] as const,
  vintedBrandSearch: (query: string) => ['vinted-brand-search', query] as const,
  data: ['data'] as const,
  operations: ['operations'] as const,
  searchRuns: (params: { search_id?: string; status?: string }) =>
    ['search-runs', params.search_id ?? '', params.status ?? ''] as const,
};

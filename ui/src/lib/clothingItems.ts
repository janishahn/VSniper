import { useQuery } from '@tanstack/react-query';

import { api } from './api';
import { queryKeys } from './queryKeys';
import type { BucketRecord, ClothingItem } from '../types';

export type ClothingItemOption = { value: ClothingItem; label: string; description: string };

// Buckets come from /api/buckets. Archived ones keep their label for history but are not offered as choices.
export function useBuckets() {
  const query = useQuery({ queryKey: queryKeys.buckets, queryFn: api.getBuckets });
  const all: BucketRecord[] = query.data ?? [];
  const active = all.filter((bucket) => !bucket.archived);
  const clothingItems: ClothingItemOption[] = active.map((bucket) => ({
    value: bucket.key,
    label: bucket.label,
    description: bucket.description,
  }));
  const clothingItemLabel = (value: ClothingItem) => all.find((bucket) => bucket.key === value)?.label ?? value;
  return { query, buckets: all, activeBuckets: active, clothingItems, clothingItemLabel };
}

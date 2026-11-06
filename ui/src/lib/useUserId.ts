import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';

import { api } from './api';
import { queryKeys } from './queryKeys';

// The profile whose data a page shows, from the /u/:userId route.
export function useUserId(): number {
  const { userId } = useParams();
  return Number(userId);
}

// The profile's own record (name, alert threshold, blocked brands).
export function useCurrentUser() {
  const userId = useUserId();
  const query = useQuery({ queryKey: queryKeys.users, queryFn: api.getUsers });
  return { userId, user: query.data?.find((user) => user.id === userId), query };
}

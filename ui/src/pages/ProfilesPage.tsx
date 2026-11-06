import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Send } from 'lucide-react';

import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import type { UserRecord } from '../types';
import { DecimalInput } from '../components/DecimalInput';
import { ErrorText } from '../components/ErrorText';
import { Badge, Loading, PageHeader } from '../components/ui';

function ProfileRow({ user }: { user: UserRecord }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(user.name);
  const [threshold, setThreshold] = useState(user.alert_threshold);

  useEffect(() => {
    setName(user.name);
    setThreshold(user.alert_threshold);
  }, [user.name, user.alert_threshold]);

  const dirty = name.trim() !== user.name || threshold !== user.alert_threshold;
  const saveMutation = useMutation({
    mutationFn: () => api.updateUser(user.id, { name: name.trim(), alert_threshold: threshold }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.users }),
  });
  const testMutation = useMutation({ mutationFn: () => api.sendTelegramTest(user.id) });

  return (
    <tr>
      <td>
        <input aria-label="Name" value={name} maxLength={80} onChange={(event) => setName(event.target.value)} />
      </td>
      <td className="nowrap">
        {user.telegram_chat_id ? <code>{user.telegram_chat_id}</code> : <span className="muted">none</span>}{' '}
        {user.authorized ? <Badge tone="ok">Allowed</Badge> : <Badge tone="warn">Not in TELEGRAM_CHAT_IDS</Badge>}
      </td>
      <td>
        <span className="affix">
          <DecimalInput
            aria-label="Alert threshold"
            type="text"
            inputMode="numeric"
            value={String(threshold)}
            normalize={(raw) => {
              const parsed = Math.round(Number(raw));
              return String(Number.isFinite(parsed) ? Math.max(1, Math.min(100, parsed)) : threshold);
            }}
            onCommit={(normalized) => setThreshold(Number(normalized))}
          />
          <span className="muted">of 100</span>
        </span>
      </td>
      <td>
        <div className="row">
          <button
            type="button"
            className="btn btn-sm btn-primary"
            onClick={() => saveMutation.mutate()}
            disabled={!dirty || !name.trim() || saveMutation.isPending}
          >
            {saveMutation.isPending ? 'Saving…' : 'Save'}
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => testMutation.mutate()}
            disabled={!user.authorized || testMutation.isPending}
          >
            <Send size={13} aria-hidden="true" />
            {testMutation.isPending ? 'Sending…' : 'Test alert'}
          </button>
          <Link to={`/u/${user.id}`} className="btn btn-sm btn-ghost">Open</Link>
          {testMutation.isSuccess ? <span className="success-text">Sent</span> : null}
        </div>
        <ErrorText error={saveMutation.error} prefix="Save failed" />
        <ErrorText error={testMutation.error} prefix="Test alert failed" />
      </td>
    </tr>
  );
}

export function ProfilesPage() {
  const usersQuery = useQuery({ queryKey: queryKeys.users, queryFn: api.getUsers });
  const header = (
    <PageHeader
      title="Profiles"
      sub="Each chat in TELEGRAM_CHAT_IDS has a profile with its own buckets, searches, taste and alerts. A new chat starts with a copy of the first profile's buckets."
    />
  );

  if (usersQuery.isLoading) return <div className="page">{header}<Loading label="Loading profiles…" /></div>;
  if (usersQuery.isError || !usersQuery.data) {
    return <div className="page">{header}<ErrorText error={usersQuery.error ?? 'Could not load profiles.'} /></div>;
  }

  return (
    <div className="page">
      {header}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Telegram chat</th>
              <th>Alert threshold</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {usersQuery.data.map((user) => <ProfileRow key={user.id} user={user} />)}
          </tbody>
        </table>
      </div>
      <p className="hint">
        A profile whose chat was removed from TELEGRAM_CHAT_IDS keeps its data, but its searches pause and the bot ignores the chat.
      </p>
    </div>
  );
}

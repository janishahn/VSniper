import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { api } from '../lib/api';
import type { SettingsSavePayload } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { useUnsavedGuard } from '../lib/useUnsavedGuard';
import { RefreshCw, Send } from 'lucide-react';

import { DecimalInput } from '../components/DecimalInput';
import { ErrorText } from '../components/ErrorText';
import { Loading, PageHeader } from '../components/ui';

type SettingsFormState = SettingsSavePayload;


function parsePositiveNumber(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function SettingsPage() {
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({ queryKey: queryKeys.settings, queryFn: api.getSettings });
  // `telegramPreview` is a POST, so it must be a mutation rather than a passive
  // query (a query would re-fire the side-effecting endpoint on focus/refetch).
  const previewMutation = useMutation({ mutationFn: api.telegramPreview });
  const previewRequested = useRef(false);

  const [form, setForm] = useState<SettingsFormState>({
    telegram_bot_token: '',
    telegram_chat_id: '',
    alert_threshold: 9,
    scan_interval_seconds: 1800,
  });
  const formDirty = useRef(false);
  const [dirty, setDirty] = useState(false);
  const formRef = useRef(form);
  const setFormDirty = (value: boolean) => { formDirty.current = value; setDirty(value); };
  useUnsavedGuard(dirty);

  useEffect(() => { formRef.current = form; }, [form]);

  useEffect(() => {
    if (saveMutation.isSuccess) saveMutation.reset();
  }, [form]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!settingsQuery.data || formDirty.current) return;
    setForm({
      telegram_bot_token: settingsQuery.data.telegram_bot_token,
      telegram_chat_id: settingsQuery.data.telegram_chat_id,
      alert_threshold: settingsQuery.data.alert_threshold,
      scan_interval_seconds: settingsQuery.data.scan_interval_seconds,
    });
  }, [settingsQuery.data]);

  useEffect(() => {
    // Fetch the alert preview once on mount (guarded against React 18 strict-mode
    // double-invocation). The user can refresh it on demand via the button below.
    if (previewRequested.current) return;
    previewRequested.current = true;
    previewMutation.mutate();
  }, []);

  const saveMutation = useMutation({
    mutationFn: api.saveSettings,
    onSuccess: (_result, submittedForm) => {
      if (JSON.stringify(formRef.current) === JSON.stringify(submittedForm)) {
        setFormDirty(false);
      }
      queryClient.invalidateQueries({ queryKey: queryKeys.settings });
    },
  });

  const sendTestMutation = useMutation({
    mutationFn: async () => {
      const saved = await api.saveSettings(formRef.current);
      const sent = await api.sendTelegramTest();
      return { saved, sent };
    },
    onSuccess: () => {
      setFormDirty(false);
      queryClient.invalidateQueries({ queryKey: queryKeys.settings });
    },
  });

  const header = (
    <PageHeader
      title="Settings"
      sub="Connections and scan behaviour. Changes take effect on the worker's next poll, without a restart."
    />
  );

  // Only the settings form is essential to render. The Telegram preview is
  // supplementary — they must not block editing the config that fixes them.
  if (settingsQuery.isLoading) return <div className="page">{header}<Loading label="Loading settings…" /></div>;
  if (settingsQuery.isError || !settingsQuery.data) {
    return <div className="page">{header}<ErrorText error={settingsQuery.error ?? 'Could not load settings.'} /></div>;
  }


  const set = <K extends keyof SettingsFormState>(key: K, value: SettingsFormState[K]) => {
    setFormDirty(true);
    setForm((f) => ({ ...f, [key]: value }));
  };

  const saveBar = (
    <div className="row">
      <button type="button" className="btn btn-primary" onClick={() => saveMutation.mutate(form)} disabled={saveMutation.isPending || !dirty}>
        {saveMutation.isPending ? 'Saving…' : 'Save changes'}
      </button>
      {saveMutation.isSuccess && !dirty ? <span className="success-text">Saved</span> : null}
      {dirty && !saveMutation.isPending ? <span className="hint">Unsaved changes</span> : null}
    </div>
  );

  return (
    <div className="page">
      {header}

      <div>
        <section className="settings-block" aria-labelledby="scan-title">
          <div>
            <h2 id="scan-title">Scanning and alerts</h2>
            <p className="section-sub">Per-search overrides for the threshold live on the Searches page.</p>
          </div>
          <div className="settings-body">
            <div className="fields-2">
              <label className="field">
                Scan every
                <span className="affix">
                  <DecimalInput
                    type="text"
                    inputMode="numeric"
                    value={String(form.scan_interval_seconds / 60)}
                    normalize={(raw) =>
                      String(Math.max(0.5, Math.min(1440, parsePositiveNumber(raw, form.scan_interval_seconds / 60))))
                    }
                    onCommit={(normalized) => set('scan_interval_seconds', Math.round(Number(normalized) * 60))}
                  />
                  <span className="muted">minutes</span>
                </span>
                <span className="hint">
                  Searches are spread evenly across this window to reduce blocking: 6 searches over 30 minutes means one every 5.
                </span>
              </label>
              <label className="field">
                Alert threshold
                <span className="affix">
                  <DecimalInput
                    type="text"
                    inputMode="numeric"
                    value={String(form.alert_threshold)}
                    normalize={(raw) => String(Math.max(1, Math.min(100, Math.round(parsePositiveNumber(raw, form.alert_threshold)))))}
                    onCommit={(normalized) => set('alert_threshold', Number(normalized))}
                  />
                  <span className="muted">of 100</span>
                </span>
                <span className="hint">Listings scoring this or higher are sent to Telegram.</span>
              </label>
            </div>
          </div>
        </section>

        <section className="settings-block" aria-labelledby="telegram-title">
          <div>
            <h2 id="telegram-title">Telegram</h2>
            <p className="section-sub">The bot that sends alerts and receives the 👍 / 👎 feedback.</p>
          </div>
          <div className="settings-body">
            <div className="fields-2">
              <label className="field">
                Bot token
                <input
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  value={form.telegram_bot_token}
                  onChange={(e) => set('telegram_bot_token', e.target.value)}
                  placeholder="123456789:AA…"
                />
              </label>
              <label className="field">
                Chat ID
                <input
                  value={form.telegram_chat_id}
                  onChange={(e) => set('telegram_chat_id', e.target.value)}
                  placeholder="523167654 or -100…"
                />
              </label>
            </div>
            <div className="row">
              <button
                type="button"
                className="btn"
                onClick={() => sendTestMutation.mutate()}
                disabled={sendTestMutation.isPending || saveMutation.isPending}
              >
                <Send size={15} aria-hidden="true" />
                {sendTestMutation.isPending ? 'Sending…' : dirty ? 'Save and send test alert' : 'Send test alert'}
              </button>
              {sendTestMutation.isSuccess ? (
                <span className="success-text">Sent to chat {sendTestMutation.data.sent.chat_id}</span>
              ) : null}
            </div>
            <ErrorText error={sendTestMutation.error} prefix="Test alert failed" />

            <div className="stack">
              <div className="row-between" style={{ alignItems: 'center' }}>
                <span className="field">Alert preview</span>
                <button
                  type="button"
                  className="btn btn-sm btn-ghost"
                  onClick={() => previewMutation.mutate()}
                  disabled={previewMutation.isPending}
                >
                  <RefreshCw size={13} aria-hidden="true" />
                  {previewMutation.isPending ? 'Refreshing…' : 'Refresh'}
                </button>
              </div>
              {previewMutation.data
                ? <pre className="telegram-preview">{previewMutation.data.preview}</pre>
                : <p className="state-line">{previewMutation.isPending ? 'Loading preview…' : 'No preview available.'}</p>}
              <ErrorText error={previewMutation.error} prefix="Preview failed" />
            </div>
          </div>
        </section>
      </div>

      <div className="settings-save">
        <ErrorText error={saveMutation.error} prefix="Save failed" />
        {saveBar}
      </div>
    </div>
  );
}

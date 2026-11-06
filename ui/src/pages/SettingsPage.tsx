import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { api } from '../lib/api';
import type { SettingsSavePayload } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { useUnsavedGuard } from '../lib/useUnsavedGuard';

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

  const [form, setForm] = useState<SettingsFormState>({
    telegram_bot_token: '',
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
      scan_interval_seconds: settingsQuery.data.scan_interval_seconds,
    });
  }, [settingsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: api.saveSettings,
    onSuccess: (_result, submittedForm) => {
      if (JSON.stringify(formRef.current) === JSON.stringify(submittedForm)) {
        setFormDirty(false);
      }
      queryClient.invalidateQueries({ queryKey: queryKeys.settings });
    },
  });

  const header = (
    <PageHeader
      title="Settings"
      sub="Connections and scan behaviour. Changes take effect on the worker's next poll, without a restart."
    />
  );

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
            <h2 id="scan-title">Scanning</h2>
            <p className="section-sub">Each profile sets its own alert threshold on the Profiles page.</p>
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
            </div>
            <p className="hint">
              The chats allowed to use the bot are listed in TELEGRAM_CHAT_IDS in the .env file. Each one gets its own profile.
            </p>
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

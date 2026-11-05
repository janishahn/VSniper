import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { api } from '../lib/api';
import type { SettingsSavePayload } from '../lib/api';
import { formatBerlinDateTime } from '../lib/datetime';
import { queryKeys } from '../lib/queryKeys';
import { decodeJwtExpiry, decodeRefreshTokenExpiry, extractRefreshToken, formatCookieExpiry } from '../lib/jwt';
import { useUnsavedGuard } from '../lib/useUnsavedGuard';
import { RefreshCw, Send } from 'lucide-react';

import { DecimalInput } from '../components/DecimalInput';
import { ErrorText } from '../components/ErrorText';
import { Loading, PageHeader, StatusDot } from '../components/ui';

type SettingsFormState = SettingsSavePayload;

// noVNC view of the server-side Vinted browser (see README: served under /vinted-browser/).
const VINTED_BROWSER_PATH = '/vinted-browser/vnc.html?autoconnect=1&resize=scale&path=vinted-browser/websockify';
const sessionTone = { healthy: 'ok', warning: 'warn', missing: 'bad' } as const;

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
    vinted_region: 'de',
    vinted_cookie: '',
    vinted_refresh_token: '',
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
      vinted_region: 'de',
      vinted_cookie: settingsQuery.data.vinted_cookie,
      vinted_refresh_token: settingsQuery.data.vinted_refresh_token,
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

  const [cookieValidation, setCookieValidation] = useState<{
    status: 'idle' | 'validating' | 'healthy' | 'warning' | 'missing';
    detail: string;
  }>({ status: 'idle', detail: '' });
  const validateTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cookieExpiry = decodeJwtExpiry(form.vinted_cookie);
  const cookieExpiryText = formatCookieExpiry(cookieExpiry);
  const refreshTokenExpiry = decodeRefreshTokenExpiry(form.vinted_cookie) || decodeRefreshTokenExpiry(form.vinted_refresh_token ? `refresh_token_web=${form.vinted_refresh_token}` : '');
  const refreshTokenExpiryText = formatCookieExpiry(refreshTokenExpiry);

  useEffect(() => {
    if (validateTimeoutRef.current) {
      clearTimeout(validateTimeoutRef.current);
    }

    if (!form.vinted_cookie || form.vinted_cookie.trim() === '' || form.vinted_cookie === 'put-your-vinted-cookie-here') {
      setCookieValidation({ status: 'idle', detail: '' });
      return;
    }

    // Skip live validation when the cookie hasn't changed from the saved value — the
    // settings query hydrating the form on every visit would otherwise fire a network call.
    if (form.vinted_cookie === settingsQuery.data?.vinted_cookie) {
      setCookieValidation({ status: 'idle', detail: '' });
      return;
    }

    setCookieValidation({ status: 'validating', detail: 'Validating...' });

    validateTimeoutRef.current = setTimeout(async () => {
      try {
        const result = await api.validateCookie(form.vinted_cookie);
        setCookieValidation({ status: result.status, detail: result.detail });
      } catch (err) {
        setCookieValidation({ status: 'warning', detail: err instanceof Error ? err.message : 'Validation failed' });
      }
    }, 600);

    return () => {
      if (validateTimeoutRef.current) {
        clearTimeout(validateTimeoutRef.current);
      }
    };
  }, [form.vinted_cookie]);

  const validationTone = {
    idle: undefined,
    validating: undefined,
    healthy: 'ok',
    warning: 'warn',
    missing: 'bad',
  }[cookieValidation.status];

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

  const settings = settingsQuery.data;
  const now = new Date();

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
        <section className="settings-block" aria-labelledby="vinted-title">
          <div>
            <h2 id="vinted-title">Vinted session</h2>
            <p className="section-sub">
              Paste the full cookie from a logged-in vinted.de tab. The refresh token in it keeps the session alive.
            </p>
          </div>
          <div className="settings-body">
            <div className="row">
              <StatusDot tone={sessionTone[settings.session_health.status]}>
                {settings.session_health.status === 'healthy' ? 'Connected' : settings.session_health.status === 'warning' ? 'Needs attention' : 'Not connected'}
              </StatusDot>
              <span className="hint">
                {settings.session_health.detail}
                {settings.session_health.last_validated_at
                  ? ` · checked ${formatBerlinDateTime(settings.session_health.last_validated_at)}`
                  : ''}
              </span>
            </div>
            <label className="field">
              Cookie
              <input
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={form.vinted_cookie}
                onChange={(e) => {
                  const value = e.target.value;
                  const refreshToken = extractRefreshToken(value);
                  set('vinted_cookie', value);
                  if (refreshToken) {
                    set('vinted_refresh_token', refreshToken);
                  }
                }}
                placeholder="Paste your Vinted cookie"
              />
            </label>
            <div className="token-status" aria-live="polite">
              {cookieExpiryText ? (
                <span className={cookieExpiry && cookieExpiry <= now ? 'tone-bad' : 'muted'}>Access token: {cookieExpiryText}</span>
              ) : null}
              {refreshTokenExpiryText ? (
                <span className={refreshTokenExpiry && refreshTokenExpiry <= now ? 'tone-bad' : 'tone-ok'}>Refresh token: {refreshTokenExpiryText}</span>
              ) : null}
              {cookieValidation.status !== 'idle' ? (
                <span className={validationTone ? `tone-${validationTone}` : 'muted'}>
                  {cookieValidation.status === 'validating' ? 'Checking cookie with Vinted…' : `${cookieValidation.status}: ${cookieValidation.detail}`}
                </span>
              ) : null}
            </div>
            <p className="hint">
              Vinted asking for verification? Solve it in the{' '}
              <a href={VINTED_BROWSER_PATH} target="_blank" rel="noreferrer">server's Vinted browser</a>, then save again.
            </p>
          </div>
        </section>

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

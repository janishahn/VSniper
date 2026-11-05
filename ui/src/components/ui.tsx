import { useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { X } from 'lucide-react';

import { useModalDismiss } from '../lib/useModalDismiss';
import { ErrorText } from './ErrorText';

export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {sub ? <p className="page-sub">{sub}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function Section({
  id,
  title,
  sub,
  actions,
  children,
}: {
  id?: string;
  title: string;
  sub?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="section" id={id} aria-labelledby={id ? `${id}-title` : undefined}>
      <div className="section-head">
        <div>
          <h2 id={id ? `${id}-title` : undefined}>{title}</h2>
          {sub ? <p className="section-sub">{sub}</p> : null}
        </div>
        {actions ? <div className="section-actions">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

export type Tone = 'ok' | 'warn' | 'bad' | 'accent' | 'neutral';

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function StatusDot({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className="status">
      <span className={`dot dot-${tone}`} aria-hidden="true" />
      {children}
    </span>
  );
}

export function Stat({
  label,
  value,
  note,
  tone,
  to,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: Tone;
  to?: string;
}) {
  const body = (
    <>
      <span className="stat-label">{label}</span>
      <span className={`stat-value${tone ? ` tone-${tone}` : ''}`}>{value}</span>
      {note ? <span className="stat-note">{note}</span> : null}
    </>
  );
  return to ? <Link className="stat stat-link" to={to}>{body}</Link> : <div className="stat">{body}</div>;
}

export type Decision = 'alert' | 'review' | 'discard';

export function decisionTone(decision: Decision): Tone {
  return decision === 'alert' ? 'accent' : decision === 'review' ? 'warn' : 'neutral';
}

/** The judge score, shown the same way everywhere: the number tinted by its decision. */
export function Score({ value, decision, size = 'md' }: { value: number; decision: Decision; size?: 'md' | 'lg' }) {
  return (
    <span className={`score score-${size} score-${decision}`} title={`Score ${value} of 100 · ${decision}`}>
      {value}
    </span>
  );
}

export function Loading({ label }: { label: string }) {
  return <p className="state-line" role="status">{label}</p>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children ? <div className="empty-body">{children}</div> : null}
    </div>
  );
}

export function Modal({
  label,
  title,
  sub,
  onClose,
  locked = false,
  size = 'md',
  footer,
  error,
  errorPrefix,
  children,
}: {
  label: string;
  title: ReactNode;
  sub?: ReactNode;
  onClose: () => void;
  locked?: boolean;
  size?: 'md' | 'lg';
  footer?: ReactNode;
  error?: unknown;
  errorPrefix?: string;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalDismiss(dialogRef, onClose, locked);

  return createPortal(
    <div className="overlay" onClick={() => { if (!locked) onClose(); }}>
      <div
        className={`modal modal-${size}`}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        ref={dialogRef}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <div>
            <h2>{title}</h2>
            {sub ? <p className="section-sub">{sub}</p> : null}
          </div>
          <button type="button" className="btn btn-ghost icon-btn" onClick={onClose} disabled={locked} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer || error ? (
          <div className="modal-footer">
            <ErrorText error={error} prefix={errorPrefix} />
            {footer ? <div className="row row-end">{footer}</div> : null}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}

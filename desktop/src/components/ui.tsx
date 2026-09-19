import { useCallback, useState, type ReactNode } from "react";

import { errorText } from "../api";

export function Panel(props: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${props.className ?? ""}`}>
      {(props.title || props.actions) && (
        <header className="panel-head">
          {props.title && <h2>{props.title}</h2>}
          {props.actions && <div className="panel-actions">{props.actions}</div>}
        </header>
      )}
      <div className="panel-body">{props.children}</div>
    </section>
  );
}

export function Page(props: { title: string; step?: number; lead?: ReactNode; children: ReactNode }) {
  return (
    <div className="page">
      <header className="page-head">
        <h1>
          {props.step !== undefined && <span className="step-no">{props.step}</span>}
          {props.title}
        </h1>
        {props.lead && <p className="lead">{props.lead}</p>}
      </header>
      {props.children}
    </div>
  );
}

type Tone = "info" | "warn" | "error" | "ok";

export function Notice(props: { tone?: Tone; title?: string; children: ReactNode }) {
  const tone = props.tone ?? "info";
  return (
    <div className={`notice notice-${tone}`} role={tone === "error" ? "alert" : "note"}>
      {props.title && <strong>{props.title}</strong>}
      <div>{props.children}</div>
    </div>
  );
}

export function Button(props: {
  children: ReactNode;
  onClick?: () => void;
  kind?: "primary" | "secondary" | "danger" | "ghost";
  disabled?: boolean;
  busy?: boolean;
  type?: "button" | "submit";
  title?: string;
}) {
  return (
    <button
      type={props.type ?? "button"}
      className={`btn btn-${props.kind ?? "secondary"}`}
      onClick={props.onClick}
      disabled={props.disabled || props.busy}
      title={props.title}
    >
      {props.busy && <span className="spinner" aria-hidden />}
      {props.children}
    </button>
  );
}

export function Field(props: { label: string; hint?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <label className={`field ${props.wide ? "field-wide" : ""}`}>
      <span className="field-label">{props.label}</span>
      {props.children}
      {props.hint && <span className="field-hint">{props.hint}</span>}
    </label>
  );
}

export function Badge(props: { tone?: "neutral" | "ok" | "warn" | "error" | "accent"; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${props.tone ?? "neutral"}`} title={props.title}>
      {props.children}
    </span>
  );
}

const TIER_TITLE: Record<string, string> = {
  T1: "T1 — from the valid index",
  T2: "T2 — orphaned index entry, validated against its block",
  T3: "T3 — signature carve, no index",
  T4: "T4 — carved around unreadable sectors",
};

export function TierBadge(props: { tier: string }) {
  return (
    <span className={`tier tier-${props.tier}`} title={TIER_TITLE[props.tier] ?? props.tier}>
      {props.tier}
    </span>
  );
}

export function SeverityBadge(props: { severity: string }) {
  const tone = props.severity === "serious" ? "error" : props.severity === "attention" ? "warn" : "neutral";
  return <Badge tone={tone}>{props.severity}</Badge>;
}

/** A digest shown in full, monospace, selectable, with a copy button. */
export function Hash(props: { value: string | null | undefined; label?: string }) {
  const [copied, setCopied] = useState(false);
  if (!props.value) return <span className="muted">—</span>;
  const value = props.value;
  return (
    <span className="hash">
      {props.label && <span className="hash-label">{props.label}</span>}
      <code>{value}</code>
      <button
        type="button"
        className="copy"
        onClick={() => {
          void navigator.clipboard.writeText(value).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
          });
        }}
        title="Copy"
      >
        {copied ? "copied" : "copy"}
      </button>
    </span>
  );
}

export function KeyValues(props: { rows: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {props.rows.map(([k, v]) => (
        <div key={k} className="kv-row">
          <dt>{k}</dt>
          <dd>{v === "" || v === null || v === undefined ? <span className="muted">—</span> : v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Loading(props: { what?: string }) {
  return (
    <div className="loading">
      <span className="spinner" aria-hidden /> {props.what ?? "Loading"}…
    </div>
  );
}

export function Empty(props: { children: ReactNode }) {
  return <div className="empty">{props.children}</div>;
}

export function CliHint(props: { children: string }) {
  return (
    <div className="cli-hint" title="The same step from a terminal">
      <span>CLI</span>
      <code>{props.children}</code>
    </div>
  );
}

/** Run a state-changing action with busy and error state; `changed()` refreshes the case. */
export function useAction<A extends unknown[], R>(
  action: (...args: A) => Promise<R>,
  onDone?: (result: R) => void,
): { run: (...args: A) => Promise<R | undefined>; busy: boolean; error: string | null; clear(): void } {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const runAction = useCallback(
    async (...args: A) => {
      setBusy(true);
      setError(null);
      try {
        const result = await action(...args);
        onDone?.(result);
        return result;
      } catch (err) {
        setError(errorText(err));
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [action, onDone],
  );
  return { run: runAction, busy, error, clear: () => setError(null) };
}

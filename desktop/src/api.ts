/**
 * Renderer side of the bridge: run a CLI request, record it in the command log, and turn a
 * failed run into an error carrying the CLI's own message.
 */

import { useEffect, useState, useSyncExternalStore } from "react";

import type { CliRequest, CliResult } from "../shared/api";

export const bridge = window.spectra;

export class CliError extends Error {
  constructor(readonly result: CliResult) {
    super(messageOf(result));
  }
}

function messageOf(result: CliResult): string {
  if (result.cancelled) return "Cancelled.";
  const lines = result.stderr.trim().split(/\r?\n/).filter(Boolean);
  const error = lines.reverse().find((l) => l.startsWith("error:") || l.startsWith("refused"));
  const text = error ?? lines[0] ?? result.stdout.trim().split(/\r?\n/).pop() ?? "";
  return text.replace(/^error:\s*/, "") || `spectra exited with code ${result.exitCode}`;
}

// -- command log ----------------------------------------------------------------------------

export interface LogEntry {
  jobId: number;
  display: string;
  startedAt: number;
  result: CliResult | null;
}

let entries: LogEntry[] = [];
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

function setBusy(delta: number) {
  window.__spectraBusy = Math.max(0, (window.__spectraBusy ?? 0) + delta);
}

bridge.onJobStarted(({ jobId, display }) => {
  entries = [{ jobId, display, startedAt: Date.now(), result: null }, ...entries].slice(0, 200);
  emit();
});

function finished(result: CliResult) {
  const known = entries.some((e) => e.jobId === result.jobId);
  entries = known
    ? entries.map((e) => (e.jobId === result.jobId ? { ...e, result } : e))
    : [{ jobId: result.jobId, display: result.display, startedAt: Date.now(), result }, ...entries];
  emit();
}

export function useCommandLog(): LogEntry[] {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => entries,
  );
}

/** Run a request; resolve with the parsed JSON, or throw CliError with the CLI's message. */
export async function run<T>(request: CliRequest): Promise<T> {
  setBusy(1);
  try {
    const result = await bridge.run(request);
    finished(result);
    if (!result.ok) throw new CliError(result);
    return result.data as T;
  } finally {
    setBusy(-1);
  }
}

/** Like `run`, but a non-zero exit with JSON output is a result, not an error (verify). */
export async function runForResult<T>(request: CliRequest): Promise<{ ok: boolean; data: T | null; message: string }> {
  setBusy(1);
  try {
    const result = await bridge.run(request);
    finished(result);
    return { ok: result.ok, data: result.data as T | null, message: result.ok ? "" : messageOf(result) };
  } finally {
    setBusy(-1);
  }
}

// -- data hook ------------------------------------------------------------------------------

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/**
 * Load `request` whenever `key` changes. A null request loads nothing. `key` should include
 * everything the request depends on plus the case version, so state changes refresh views.
 */
export function useCli<T>(request: CliRequest | null, key: string): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ data: null, error: null, loading: request !== null });
  useEffect(() => {
    if (request === null) {
      setState({ data: null, error: null, loading: false });
      return;
    }
    let live = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    run<T>(request).then(
      (data) => live && setState({ data, error: null, loading: false }),
      (err: unknown) => live && setState({ data: null, error: errorText(err), loading: false }),
    );
    return () => {
      live = false;
    };
    // The request object is rebuilt each render; `key` is what identifies it.
  }, [key]);
  return state;
}

export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

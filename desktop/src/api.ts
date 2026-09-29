/**
 * Renderer side of the bridge: run a CLI request, record it in the command log, and turn a
 * failed run into an error carrying the CLI's own message.
 *
 * Reads are cached by request: a screen opened a second time shows what it showed last
 * time at once and refreshes underneath, instead of blanking to "Loading…" while a Python
 * process starts. The cache is only ever a copy of CLI output; the case is the source.
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

// -- read cache -----------------------------------------------------------------------------

interface CacheEntry {
  data: unknown;
  error: string | null;
  /** The case version the entry was read at. */
  version: string;
}

const cache = new Map<string, CacheEntry>();
const inflight = new Map<string, Promise<CacheEntry>>();

/** Forget every cached read (on closing a case). */
export function clearCache(): void {
  cache.clear();
  inflight.clear();
}

/** One CLI run per distinct request and version, however many views ask at once. */
function fetchShared(request: CliRequest, version: string): Promise<CacheEntry> {
  const id = JSON.stringify(request);
  const flight = `${id}#${version}`;
  let pending = inflight.get(flight);
  if (!pending) {
    pending = run<unknown>(request).then(
      (data): CacheEntry => ({ data, error: null, version }),
      (err: unknown): CacheEntry => ({ data: null, error: errorText(err), version }),
    );
    pending.then((entry) => {
      cache.set(id, entry);
      inflight.delete(flight);
    });
    inflight.set(flight, pending);
  }
  return pending;
}

// -- data hook ------------------------------------------------------------------------------

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  /** Nothing to show yet. */
  loading: boolean;
  /** Showing the last result while a newer one is read. */
  refreshing: boolean;
}

/**
 * Load `request`, and again whenever `version` changes (pass the case version so a state
 * change refreshes every view). A null request loads nothing. A result already read at
 * this version is shown without running anything; an older one is shown until the new
 * one arrives.
 */
export function useCli<T>(request: CliRequest | null, version: string | number): Loaded<T> {
  const id = request === null ? null : JSON.stringify(request);
  const at = String(version);
  const initial = (): Loaded<T> => {
    const hit = id ? cache.get(id) : undefined;
    return hit
      ? { data: hit.data as T | null, error: hit.error, loading: false, refreshing: hit.version !== at }
      : { data: null, error: null, loading: id !== null, refreshing: false };
  };
  const [state, setState] = useState<Loaded<T>>(initial);
  useEffect(() => {
    if (request === null) {
      setState({ data: null, error: null, loading: false, refreshing: false });
      return;
    }
    const now = initial();
    setState(now);
    if (!now.loading && !now.refreshing) return;
    let live = true;
    fetchShared(request, at).then((entry) => {
      if (live) setState({ data: entry.data as T | null, error: entry.error, loading: false, refreshing: false });
    });
    return () => {
      live = false;
    };
    // The request object is rebuilt each render; its JSON is what identifies it.
  }, [id, at]);
  return state;
}

export function errorText(err: unknown): string {
  return (err instanceof Error ? err.message : String(err)).replace(
    /^Error invoking remote method '[^']+': (Error: )?/,
    "",
  );
}

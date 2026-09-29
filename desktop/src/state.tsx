/**
 * The open case: what the CLI says about it (metadata, evidence, progress, audit status),
 * which evidence item the per-item steps are looking at, and a version number that goes
 * up after every state-changing command so every view re-reads what it shows.
 *
 * Also the window's own memory for the case, none of which is evidence: long-running
 * actions keep running and keep their result when the examiner moves to another step,
 * half-filled forms survive navigation, and the steps the examiner chose to skip are
 * remembered so "next step" stops pointing at them.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { bridge, clearCache, errorText, runForResult, useCli } from "./api";
import type { CaseInfo, EvidenceProgress, VerifyResult } from "./types";

// -- tasks ----------------------------------------------------------------------------------

export interface TaskState<R = unknown> {
  busy: boolean;
  error: string | null;
  result: R | null;
  startedAt: number | null;
}

const IDLE: TaskState = { busy: false, error: null, result: null, startedAt: null };

/** Actions keyed by name ("recover:EV-001"), owned by the case rather than by a screen. */
class TaskStore {
  private tasks = new Map<string, TaskState>();
  private listeners = new Set<() => void>();
  private tick = 0;

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  snapshot = () => this.tick;

  get(id: string): TaskState {
    return this.tasks.get(id) ?? IDLE;
  }

  busy(prefix: string): boolean {
    for (const [id, task] of this.tasks) if (task.busy && id.startsWith(prefix)) return true;
    return false;
  }

  set(id: string, next: TaskState): void {
    this.tasks.set(id, next);
    this.tick += 1;
    this.listeners.forEach((l) => l());
  }

  async run<R>(id: string, fn: () => Promise<R>, changed: () => void): Promise<R | undefined> {
    if (this.get(id).busy) return undefined;
    this.set(id, { ...this.get(id), busy: true, error: null, startedAt: Date.now() });
    try {
      const result = await fn();
      this.set(id, { busy: false, error: null, result, startedAt: null });
      return result;
    } catch (err) {
      this.set(id, { ...this.get(id), busy: false, error: errorText(err), startedAt: null });
      return undefined;
    } finally {
      changed();
    }
  }
}

// -- marks ----------------------------------------------------------------------------------

/** Per case, in this window only: steps skipped on purpose and steps looked at. */
export interface Marks {
  skipped: string[];
  visited: string[];
  evidence: string | null;
}

const NO_MARKS: Marks = { skipped: [], visited: [], evidence: null };

function loadMarks(dir: string): Marks {
  try {
    const raw = window.localStorage.getItem(`spectra.marks:${dir}`);
    return raw ? { ...NO_MARKS, ...(JSON.parse(raw) as Partial<Marks>) } : NO_MARKS;
  } catch {
    return NO_MARKS;
  }
}

function saveMarks(dir: string, marks: Marks): void {
  try {
    window.localStorage.setItem(`spectra.marks:${dir}`, JSON.stringify(marks));
  } catch {
    // Convenience only: without storage the step list just forgets skips on restart.
  }
}

// -- case -----------------------------------------------------------------------------------

export interface CaseState {
  dir: string;
  info: CaseInfo | null;
  infoError: string | null;
  verify: VerifyResult | null;
  verifying: boolean;
  evidenceId: string | null;
  /** Progress of the selected evidence item. */
  evidence: EvidenceProgress | null;
  setEvidenceId(id: string): void;
  /** Increases after any change; pass it to useCli. */
  version: number;
  /** Call after a state-changing command. */
  changed(): void;
  close(): void;
  marks: Marks;
  mark(kind: "skipped" | "visited", key: string, on?: boolean): void;
  tasks: TaskStore;
  session: Map<string, unknown>;
}

const CaseContext = createContext<CaseState | null>(null);

export function useCase(): CaseState {
  const value = useContext(CaseContext);
  if (!value) throw new Error("useCase outside an open case");
  return value;
}

export function useOptionalCase(): CaseState | null {
  return useContext(CaseContext);
}

export function CaseProvider(props: { dir: string; onClose(): void; children: ReactNode }) {
  const { dir, onClose } = props;
  const [version, setVersion] = useState(0);
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [verifying, setVerifying] = useState(true);
  const [marks, setMarks] = useState<Marks>(() => loadMarks(dir));
  const tasks = useRef(new TaskStore()).current;
  const session = useRef(new Map<string, unknown>()).current;

  const info = useCli<CaseInfo>({ kind: "caseInfo", case: dir }, version);

  useEffect(() => {
    void bridge.setActiveCase(dir);
    return () => {
      void bridge.setActiveCase(null);
      clearCache();
    };
  }, [dir]);

  useEffect(() => {
    let live = true;
    setVerifying(true);
    // Verification fails with exit 1 and still prints JSON: that is a result to show. The
    // last result stays on screen while the chain is re-read.
    runForResult<VerifyResult>({ kind: "caseVerify", case: dir }).then((r) => {
      if (!live) return;
      setVerifying(false);
      if (r.data) setVerify(r.data);
    });
    return () => {
      live = false;
    };
  }, [dir, version]);

  const evidenceIds = info.data?.evidence.map((e) => e.id) ?? [];
  const evidenceId =
    marks.evidence && evidenceIds.includes(marks.evidence) ? marks.evidence : (evidenceIds[0] ?? null);
  const evidence = info.data?.progress.evidence.find((e) => e.evidence_id === evidenceId) ?? null;

  const updateMarks = useCallback(
    (update: (m: Marks) => Marks) =>
      setMarks((current) => {
        const next = update(current);
        saveMarks(dir, next);
        return next;
      }),
    [dir],
  );
  const setEvidenceId = useCallback((id: string) => updateMarks((m) => ({ ...m, evidence: id })), [updateMarks]);
  const mark = useCallback(
    (kind: "skipped" | "visited", key: string, on = true) =>
      updateMarks((m) => {
        const list = m[kind].filter((k) => k !== key);
        return { ...m, [kind]: on ? [...list, key] : list };
      }),
    [updateMarks],
  );
  const changed = useCallback(() => setVersion((v) => v + 1), []);

  const value = useMemo<CaseState>(
    () => ({
      dir,
      info: info.data,
      infoError: info.error,
      verify,
      verifying,
      evidenceId,
      evidence,
      setEvidenceId,
      version,
      changed,
      close: onClose,
      marks,
      mark,
      tasks,
      session,
    }),
    [dir, info.data, info.error, verify, verifying, evidenceId, evidence, setEvidenceId, version, changed, onClose, marks, mark, tasks, session],
  );
  return <CaseContext.Provider value={value}>{props.children}</CaseContext.Provider>;
}

/** A named action of this case: it keeps running, and keeps its result or error, when the
 * screen that started it is left. `run` refreshes the case when it finishes. */
export function useTask<R>(id: string): TaskState<R> & {
  run(fn: () => Promise<R>): Promise<R | undefined>;
  clear(): void;
} {
  const c = useCase();
  useSyncExternalStore(c.tasks.subscribe, c.tasks.snapshot);
  const state = c.tasks.get(id) as TaskState<R>;
  return {
    ...state,
    run: (fn) => c.tasks.run(id, fn, c.changed),
    clear: () => c.tasks.set(id, IDLE),
  };
}

/** Whether any task whose name starts with `prefix` is running. */
export function useTaskBusy(prefix: string): boolean {
  const c = useCase();
  useSyncExternalStore(c.tasks.subscribe, c.tasks.snapshot);
  return c.tasks.busy(prefix);
}

/** useState that survives leaving and coming back to a screen, for this case. */
export function useSessionState<T>(key: string, initial: T): [T, (next: T) => void] {
  const c = useCase();
  const [value, setValue] = useState<T>(() => (c.session.has(key) ? (c.session.get(key) as T) : initial));
  useEffect(() => {
    setValue(c.session.has(key) ? (c.session.get(key) as T) : initial);
    // Only a different key means different state.
  }, [key]);
  const set = useCallback(
    (next: T) => {
      c.session.set(key, next);
      setValue(next);
    },
    [c.session, key],
  );
  return [value, set];
}

// -- routing -------------------------------------------------------------------------------

export function useRoute(): [string, (route: string) => void] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("?")[0] || "overview";
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const navigate = useCallback((next: string) => {
    window.location.hash = `#/${next}`;
  }, []);
  return [route, navigate];
}

export function go(route: string): void {
  window.location.hash = `#/${route}`;
}

/** A parameter from the hash route, e.g. `rec` in "#/export?rec=REC-0001". */
export function routeParam(name: string): string | null {
  const query = window.location.hash.split("?")[1] ?? "";
  return new URLSearchParams(query).get(name);
}

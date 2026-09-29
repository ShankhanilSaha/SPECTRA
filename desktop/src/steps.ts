/**
 * The examination as one path (doc 7 §4 steps 1–11), and where the case stands on it.
 *
 * Every status here is read from `case info` progress, i.e. from what the case records.
 * The only window-side inputs are the examiner's own "skip this" and "looked at this"
 * marks, which decide what "next step" suggests and nothing else.
 */

import type { CaseProgress, EvidenceProgress } from "./types";
import type { Marks } from "./state";
import { caseGaps, focus } from "./gaps";

export type StepId =
  | "overview" | "documents" | "evidence"
  | "identify" | "parse" | "time" | "recover"
  | "timeline" | "analytics" | "export" | "report";

export type StepGroup = "case" | "evidence" | "findings";

export interface StepDef {
  id: StepId;
  n: number;
  title: string;
  group: StepGroup;
  /** One line: what this step is for. */
  purpose: string;
  /** Label for moving on without completing it, for steps where that is a legitimate choice. */
  skipLabel?: string;
  /** Never suggested as the next step. */
  optional?: boolean;
}

export const STEPS: StepDef[] = [
  { id: "overview", n: 1, title: "Case", group: "case", purpose: "Case record, audit chain and where the examination stands." },
  { id: "documents", n: 2, title: "Scene documents", group: "case", purpose: "Attach and hash the panchnama, Form F-1 and the seizure video.", skipLabel: "Skip for now" },
  { id: "evidence", n: 3, title: "Evidence", group: "case", purpose: "Import the disk image or the export files, and record custody." },
  { id: "identify", n: 4, title: "Identify", group: "evidence", purpose: "Which format family the disk bytes belong to." },
  { id: "parse", n: 5, title: "Parse", group: "evidence", purpose: "Walk the recorder's own index (T1)." },
  { id: "time", n: 6, title: "Time model", group: "evidence", purpose: "Measure the recorder's clock error, or record that it cannot be.", skipLabel: "Continue without an offset" },
  { id: "recover", n: 7, title: "Recover", group: "evidence", purpose: "Find deleted and unindexed footage (T2–T4)." },
  { id: "timeline", n: 8, title: "Timeline", group: "findings", purpose: "Every channel on one axis; gaps are findings." },
  { id: "analytics", n: 9, title: "Analytics", group: "findings", purpose: "Motion gating to cut review time. Leads only.", optional: true },
  { id: "export", n: 10, title: "Review & export", group: "findings", purpose: "Play and export verified evidence copies.", skipLabel: "Skip export" },
  { id: "report", n: 11, title: "Report", group: "findings", purpose: "The twelve-section report and the s. 63(4) certificate." },
];

export const GROUP_TITLE: Record<StepGroup, string> = {
  case: "Case",
  evidence: "Evidence item",
  findings: "Whole case",
};

export function stepDef(id: StepId): StepDef {
  return STEPS.find((s) => s.id === id)!;
}

export function isStep(route: string): route is StepId {
  return STEPS.some((s) => s.id === route);
}

/** done ✓ · todo (to do) · attention (needs a decision) · skipped (by the examiner) ·
 * na (does not apply to this evidence) · locked (an earlier step comes first). */
export type StepState = "done" | "todo" | "attention" | "skipped" | "na" | "locked";

export interface StepStatus {
  state: StepState;
  summary: string;
  /** A completed step whose outcome deserves a second look (an unknown format). */
  warn?: boolean;
}

/** The key a skip or visit is remembered under: per evidence item for steps 4–7. */
export function markKey(id: StepId, evidenceId: string | null): string {
  return stepDef(id).group === "evidence" ? `${id}:${evidenceId ?? ""}` : id;
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

function recovered(ev: EvidenceProgress): number {
  const t = ev.recordings.by_tier;
  return (t.T2 ?? 0) + (t.T3 ?? 0) + (t.T4 ?? 0);
}

export function stepStatus(id: StepId, p: CaseProgress | null, ev: EvidenceProgress | null, marks: Marks): StepStatus {
  const skipped = marks.skipped.includes(markKey(id, ev?.evidence_id ?? null));
  const visited = marks.visited.includes(markKey(id, ev?.evidence_id ?? null));
  if (!p) return { state: "locked", summary: "" };
  const items = p.evidence.length;
  const total = p.evidence.reduce((n, e) => n + e.recordings.total, 0);
  const ident = ev?.identification ?? null;

  switch (id) {
    case "overview":
      return { state: "done", summary: "record & audit chain" };
    case "documents":
      if (p.attachments.length) return { state: "done", summary: plural(p.attachments.length, "document") };
      return skipped ? { state: "skipped", summary: "skipped" } : { state: "todo", summary: "memo, F-1, seizure video" };
    case "evidence":
      return items ? { state: "done", summary: plural(items, "item") } : { state: "todo", summary: "none yet" };
    case "identify":
      if (!ev) return { state: "locked", summary: "add evidence first" };
      if (!ident) return { state: "todo", summary: "not run yet" };
      if (ident.support === "pending_selection") return { state: "attention", summary: "choose a family" };
      // An unknown format is a finding, not a decision the examiner still owes.
      if (ident.support === "none") return { state: "done", summary: "no family matched", warn: true };
      return { state: "done", summary: `${ident.family} · ${ident.support === "parse" ? "full parse" : "carve-only"}` };
    case "parse":
      if (!ev) return { state: "locked", summary: "add evidence first" };
      if (!ident) return { state: "locked", summary: "identify first" };
      if (ident.support === "pending_selection") return { state: "locked", summary: "choose a family first" };
      if (ident.support === "none") return { state: "na", summary: "no parser for this format" };
      if (ident.support !== "parse") return { state: "na", summary: "not applicable · carve-only" };
      return ev.parsed
        ? { state: "done", summary: plural(ev.recordings.by_tier.T1 ?? 0, "recording") }
        : { state: "todo", summary: "not run yet" };
    case "time":
      if (!ev) return { state: "locked", summary: "add evidence first" };
      if (ev.time_observations) return { state: "done", summary: "offset established" };
      if (ident?.support === "none" && !ev.recordings.total) return { state: "na", summary: "no recordings to time" };
      return skipped ? { state: "skipped", summary: "device clock only" } : { state: "todo", summary: "no offset yet" };
    case "recover":
      if (!ev) return { state: "locked", summary: "add evidence first" };
      if (!ident) return { state: "locked", summary: "identify first" };
      if (ident.support === "pending_selection") return { state: "locked", summary: "choose a family first" };
      if (ident.support === "none") return { state: "na", summary: "no family to carve with" };
      return ev.recovered
        ? { state: "done", summary: `${plural(recovered(ev), "recording")} recovered` }
        : { state: "todo", summary: "not run yet" };
    case "timeline":
      if (!total) return { state: "locked", summary: "no recordings yet" };
      return visited ? { state: "done", summary: "reviewed" } : { state: "todo", summary: plural(total, "recording") };
    case "analytics":
      if (!total) return { state: "locked", summary: "no recordings yet" };
      return p.analysed_recordings
        ? { state: "done", summary: `${plural(p.analysed_recordings, "recording")} analysed` }
        : { state: "todo", summary: "optional" };
    case "export":
      if (!total) return { state: "locked", summary: "no recordings yet" };
      if (p.exported_recordings) return { state: "done", summary: `${plural(p.exported_recordings, "recording")} exported` };
      return skipped ? { state: "skipped", summary: "skipped" } : { state: "todo", summary: "nothing exported" };
    case "report":
      return p.reports_generated
        ? { state: "done", summary: p.reports_generated > 1 ? `generated ${p.reports_generated}×` : "generated" }
        : { state: "todo", summary: "not generated" };
  }
}

export interface NextStep {
  step: StepId;
  evidenceId: string | null;
}

const OPEN: StepState[] = ["todo", "attention"];

/** The first step still open, in order; per evidence item for steps 4–7, the selected item
 * first. Optional steps and steps the examiner skipped are passed over. */
export function nextStep(p: CaseProgress | null, marks: Marks, selected: string | null): NextStep | null {
  // Ranked by what each omission costs the report, not by position in a list. The old
  // implementation scanned 1→11 and returned the first open step, which pointed an examiner
  // back to step 2 on a case that was already parsed, timed and recovered. One source of
  // truth now drives the rail's NEXT tag, the footer button and the readiness card, so the
  // three can no longer disagree with each other.
  void marks;
  return focus(caseGaps(p), selected);
}

/** Another evidence item with steps 4–7 still open, for "examine the next item". */
export function nextEvidence(p: CaseProgress | null, marks: Marks, current: string | null): NextStep | null {
  if (!p) return null;
  for (const ev of p.evidence) {
    if (ev.evidence_id === current) continue;
    for (const id of ["identify", "parse", "time", "recover"] as StepId[]) {
      if (OPEN.includes(stepStatus(id, p, ev, marks).state)) return { step: id, evidenceId: ev.evidence_id };
    }
  }
  return null;
}

export function neighbours(id: StepId): { prev: StepDef | null; next: StepDef | null } {
  const i = STEPS.findIndex((s) => s.id === id);
  return { prev: STEPS[i - 1] ?? null, next: STEPS[i + 1] ?? null };
}

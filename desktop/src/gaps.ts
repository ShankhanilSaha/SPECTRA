/**
 * What this case still needs — derived from what would weaken the report, not from a
 * step counter.
 *
 * The old model walked steps 1→11 and pointed at the first one still open, which sends an
 * examiner backwards ("next: scene documents") on a case whose parsing, timing and recovery
 * are long since done. Worse, it answers a question nobody asks. The question an examiner
 * actually has is: *can I sign the report yet, and if not, what is missing?*
 *
 * The engine already answers that. Report §7 negative findings are generated from the case
 * and cannot be deleted — NF-NO-CUSTODY, NF-NO-CLOCK-OFFSET, NF-MISSING-DOCUMENTS,
 * NF-NO-RECOVERY. Those are the obligations. This module reads the same signals from
 * `case info` progress so the window can show them live, before the report is generated,
 * and ranks them by what each one costs the report.
 */

import type { CaseProgress, EvidenceProgress } from "./types";
import type { StepId } from "./steps";

/** How much a missing obligation costs the report. Ordering, not decoration. */
export type Weight = "blocking" | "serious" | "attention";

export interface Gap {
  id: string;
  weight: Weight;
  /** What is missing, as the examiner would say it. */
  title: string;
  /** Why it matters — the sentence that would otherwise appear in report §7. */
  why: string;
  /** Where to go and fix it. */
  step: StepId;
  evidenceId: string | null;
  /** The negative-finding code this becomes if the report is generated as-is. */
  becomes?: string;
}

const RANK: Record<Weight, number> = { blocking: 0, serious: 1, attention: 2 };

function evidenceGaps(ev: EvidenceProgress): Gap[] {
  const out: Gap[] = [];
  const id = ev.evidence_id;
  const ident = ev.identification;

  if (!ident || ident.status !== "identified") {
    out.push({
      id: `${id}:identify`, weight: "blocking", step: "identify", evidenceId: id,
      title: `${id} has not been identified`,
      why: "Nothing can be parsed or carved until the format family is known.",
    });
    return out; // everything downstream depends on this; don't pile on
  }

  if (ident.support === "parse" && !ev.parsed) {
    out.push({
      id: `${id}:parse`, weight: "blocking", step: "parse", evidenceId: id,
      title: `${id} is parseable but has not been parsed`,
      why: "The recorder's own index lists recordings this case has not read.",
    });
  }

  if (!ev.recovered) {
    out.push({
      id: `${id}:recover`, weight: "serious", step: "recover", evidenceId: id,
      title: `Deleted footage has not been looked for on ${id}`,
      why: "Absence of recovered footage is not evidence of absence until recovery has run.",
      becomes: "NF-NO-RECOVERY",
    });
  }

  if (ev.time_observations === 0 && ev.recordings.total > 0) {
    out.push({
      id: `${id}:time`, weight: "serious", step: "time", evidenceId: id,
      title: `No clock offset established for ${id}`,
      why: "Every time in the report stays the recorder's own wall clock, and no absolute time is asserted.",
      becomes: "NF-NO-CLOCK-OFFSET",
    });
  }

  if (ev.custody_entries === 0) {
    out.push({
      id: `${id}:custody`, weight: "serious", step: "evidence", evidenceId: id,
      title: `No custody transfer recorded for ${id}`,
      why: "The case cannot show who held the item between seizure and examination.",
      becomes: "NF-NO-CUSTODY",
    });
  }

  return out;
}

export function caseGaps(p: CaseProgress | null): Gap[] {
  if (!p) return [];
  const out: Gap[] = [];

  if (p.evidence.length === 0) {
    return [{
      id: "case:evidence", weight: "blocking", step: "evidence", evidenceId: null,
      title: "No evidence imported",
      why: "A case with no evidence item has nothing to examine.",
    }];
  }

  for (const ev of p.evidence) out.push(...evidenceGaps(ev));

  if (p.attachments.length === 0) {
    out.push({
      id: "case:documents", weight: "attention", step: "documents", evidenceId: null,
      title: "No scene documents attached",
      why: "The panchnama, Form F-1 and seizure video are what start the chain at the scene.",
      becomes: "NF-MISSING-DOCUMENTS",
    });
  }

  const anyRecordings = p.evidence.some((e) => e.recordings.total > 0);
  if (anyRecordings && p.exported_recordings === 0) {
    out.push({
      id: "case:export", weight: "attention", step: "export", evidenceId: null,
      title: "Nothing has been exported",
      why: "The report tenders artefacts; without an evidence copy there is nothing to tender.",
    });
  }

  if (p.reports_generated === 0) {
    out.push({
      id: "case:report", weight: "attention", step: "report", evidenceId: null,
      title: "Report not generated",
      why: "The examination is not complete until the report and its certificate exist.",
    });
  }

  return out.sort((a, b) => RANK[a.weight] - RANK[b.weight]);
}

/** The one thing to do next: the heaviest open obligation, preferring the item in view. */
export function focus(gaps: Gap[], selected: string | null): Gap | null {
  if (!gaps.length) return null;
  const mine = gaps.filter((g) => g.evidenceId === selected);
  const pool = mine.length ? mine : gaps;
  return pool[0] ?? null;
}

export function readiness(gaps: Gap[]): { label: string; tone: "ok" | "warn" | "error" } {
  if (gaps.some((g) => g.weight === "blocking")) return { label: "blocked", tone: "error" };
  if (gaps.some((g) => g.weight === "serious")) return { label: "incomplete", tone: "warn" };
  if (gaps.length) return { label: "nearly there", tone: "warn" };
  return { label: "ready to report", tone: "ok" };
}

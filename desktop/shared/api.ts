/**
 * The contract between the renderer and the main process.
 *
 * The desktop app is a client of the `spectra` CLI (doc 3 §2: the UI and the CLI are peers
 * over the same services, and the UI holds no logic). The renderer never builds a command
 * line: it sends one of these typed requests, and the main process maps it to a fixed argv
 * for `python -m spectra.cli`. Anything not listed here cannot be run from the window.
 *
 * Type-only module: imported with `import type` on both sides, so nothing here exists at
 * runtime.
 */

export type Provenance = "A" | "B" | "C" | "D";
export type OffsetMethodLetter = "A" | "B" | "C" | "D";
export type Tier = "T2" | "T3" | "T4";

export type CliRequest =
  | {
      kind: "caseNew";
      dir: string;
      id: string;
      title: string;
      agency: string;
      fir: string;
      authority: string;
      examiner: string;
      designation: string;
      s79a: string;
    }
  | { kind: "caseInfo"; case: string }
  | { kind: "caseVerify"; case: string }
  | { kind: "caseChain"; case: string; evidence: string }
  | {
      kind: "caseAttach";
      case: string;
      file: string;
      docKind: string;
      description: string;
      providedBy: string;
      evidence: string | null;
    }
  | {
      kind: "caseCustody";
      case: string;
      evidence: string;
      from: string;
      to: string;
      purpose: string;
      seal: string;
      sealIntact: boolean | null;
      signature: string;
      note: string;
    }
  | { kind: "importImage"; case: string; file: string; provenance: Provenance; label: string; note: string }
  | { kind: "importFiles"; case: string; dir: string; label: string; note: string }
  | { kind: "identify"; case: string; evidence: string }
  | { kind: "identifyShow"; case: string; evidence: string }
  | { kind: "identifySelect"; case: string; evidence: string; family: string; reason: string }
  | { kind: "parse"; case: string; evidence: string }
  | { kind: "listRecordings"; case: string; evidence: string | null }
  | { kind: "listArtifacts"; case: string; recording: string | null }
  | { kind: "coverage"; case: string; evidence: string }
  | { kind: "recover"; case: string; evidence: string; tiers: Tier[]; budgetSeconds: number | null }
  | { kind: "timeShow"; case: string; evidence: string }
  | {
      kind: "timeSet";
      case: string;
      evidence: string;
      method: OffsetMethodLetter;
      deviceTime: string;
      trueTime: string;
      uncertainty: number | null;
      tzOffsetMinutes: number | null;
      validFrom: string | null;
      validTo: string | null;
      note: string;
    }
  | { kind: "timeline"; case: string }
  | { kind: "gaps"; case: string; minGap: number }
  | { kind: "analyzeMotion"; case: string; recording: string; sensitivity: number; minArea: number }
  | { kind: "exportClip"; case: string; recording: string; out: string | null }
  | { kind: "reportFindings"; case: string }
  | { kind: "reportCertificate"; case: string; evidence: string }
  | {
      kind: "reportGenerate";
      case: string;
      out: string;
      agency: string;
      letterhead: string;
      footer: string;
      pdf: boolean;
    }
  | { kind: "version" };

export type CliRequestKind = CliRequest["kind"];

/** One finished CLI invocation, as the command log shows it. */
export interface CliResult {
  jobId: number;
  ok: boolean;
  exitCode: number | null;
  /** The command line, printable and copy-pasteable into a terminal. */
  display: string;
  stdout: string;
  stderr: string;
  /** Parsed stdout when the command was run with --json and succeeded. */
  data: unknown;
  durationMs: number;
  cancelled: boolean;
}

export interface Settings {
  /** Python interpreter that can `import spectra`. */
  python: string;
  /** Directory containing the `spectra` package (the repository root in development). */
  spectraRoot: string;
  /** FFmpeg binary for remux and motion decoding; empty to use PATH. */
  ffmpeg: string;
  /** Recorded as the operator on every audit record. */
  operator: string;
  /** Folder new case directories are created in. */
  caseParent: string;
  /** Recently opened cases that still exist, newest first. */
  recentCases: string[];
}

export interface EnvironmentCheck {
  spectra: { ok: boolean; version: string | null; error: string | null };
  ffmpeg: { ok: boolean; path: string | null };
  /** A fix found on this machine, for the examiner to accept with one click; never applied
   * silently. `python`: an interpreter that can run spectra, when the configured one
   * cannot. `ffmpeg`: an FFmpeg binary, when none is configured or on PATH. */
  suggestion: { python: string | null; version: string | null; ffmpeg: string | null } | null;
}

export interface PickOptions {
  title: string;
  kind: "file" | "directory";
  /** For files: extensions without dots, e.g. ["dd", "img", "E01"]. */
  extensions?: string[];
  /** Offer to create the directory (for a new case or report output). */
  create?: boolean;
  /** Where the dialog opens. */
  defaultPath?: string;
}

export interface SpectraBridge {
  run(request: CliRequest): Promise<CliResult>;
  cancel(jobId: number): Promise<void>;
  onJobStarted(listener: (job: { jobId: number; display: string }) => void): () => void;
  pick(options: PickOptions): Promise<string | null>;
  getSettings(): Promise<Settings>;
  saveSettings(settings: Settings): Promise<Settings>;
  checkEnvironment(): Promise<EnvironmentCheck>;
  /** Tell the main process which case is open, so it can serve that case's artefacts. */
  setActiveCase(dir: string | null): Promise<void>;
  /** URL the <video> element can play for a stored artefact of the active case. */
  artifactUrl(sha256: string): string;
  openReport(htmlPath: string): Promise<void>;
  showInFolder(path: string): Promise<void>;
  onMenu(listener: (action: MenuAction) => void): () => void;
}

export type MenuAction = "newCase" | "openCase" | "closeCase" | "settings" | "log";

/**
 * Runs the `spectra` CLI for the renderer.
 *
 * The window never supplies a command line. Each typed request maps to one fixed argv built
 * here, values are validated before they reach it, and the process is spawned without a
 * shell. Options are passed as `--name=value` so a value that begins with "-" can never be
 * read as an option.
 */

import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";

import type { CliRequest, CliResult, Settings } from "../shared/api.js";

// Leading alphanumeric: an ID is sometimes positional, where "-x" would read as an option.
const ITEM_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const FAMILY = /^[a-z0-9_]{1,40}$/;
const DOC_KINDS = new Set([
  "panchnama", "seizure_video", "authorisation", "seizure_form", "custody_form",
  "acquisition_form", "photograph", "correspondence", "other",
]);

export class RequestError extends Error {}

function id(value: string, what: string): string {
  if (!ITEM_ID.test(value)) throw new RequestError(`invalid ${what}: ${JSON.stringify(value)}`);
  return value;
}

function absolute(value: string, what: string): string {
  if (!value || value.includes("\0") || !path.isAbsolute(value)) {
    throw new RequestError(`${what} must be an absolute path`);
  }
  return value;
}

function text(value: string): string {
  if (value.includes("\0")) throw new RequestError("text contains a NUL byte");
  return value;
}

function finite(value: number, what: string): number {
  if (!Number.isFinite(value)) throw new RequestError(`${what} must be a number`);
  return value;
}

/** `--name=value`, omitted entirely when the value is empty or null. */
function opt(name: string, value: string | number | null | undefined): string[] {
  if (value === null || value === undefined || value === "") return [];
  return [`--${name}=${typeof value === "string" ? text(value) : value}`];
}

export interface Argv {
  args: string[];
  json: boolean;
}

export function argvFor(req: CliRequest): Argv {
  const withCase = (c: string) => opt("case", absolute(c, "case directory"));
  const json = (args: string[]): Argv => ({ args: [...args, "--json"], json: true });
  switch (req.kind) {
    case "version":
      return { args: ["version"], json: false };
    case "caseNew":
      return json([
        "case", "new", ...opt("id", text(req.id)), ...opt("dir", absolute(req.dir, "case directory")),
        ...opt("title", req.title), ...opt("agency", req.agency), ...opt("fir", req.fir),
        ...opt("authority", req.authority), ...opt("examiner", req.examiner),
        ...opt("designation", req.designation), ...opt("s79a", req.s79a),
      ]);
    case "caseInfo":
      return json(["case", "info", ...withCase(req.case)]);
    case "caseVerify":
      return json(["case", "verify", ...withCase(req.case)]);
    case "caseChain":
      return json(["case", "chain", ...opt("evidence", id(req.evidence, "evidence ID")), ...withCase(req.case)]);
    case "caseAttach":
      if (!DOC_KINDS.has(req.docKind)) throw new RequestError(`unknown document kind ${req.docKind}`);
      return json([
        "case", "attach", ...opt("file", absolute(req.file, "document")), ...opt("kind", req.docKind),
        ...opt("description", req.description), ...opt("provided-by", req.providedBy),
        ...opt("evidence", req.evidence === null ? null : id(req.evidence, "evidence ID")),
        ...withCase(req.case),
      ]);
    case "caseCustody":
      return json([
        "case", "custody", ...opt("from", req.from), ...opt("to", req.to), ...opt("purpose", req.purpose),
        ...opt("evidence", id(req.evidence, "evidence ID")), ...opt("seal", req.seal),
        ...(req.sealIntact === null ? [] : [req.sealIntact ? "--seal-intact" : "--seal-broken"]),
        ...opt("signature", req.signature), ...opt("note", req.note), ...withCase(req.case),
      ]);
    case "importImage":
      if (!["A", "B", "C", "D"].includes(req.provenance)) throw new RequestError("provenance must be A-D");
      return json([
        "import", "image", ...opt("file", absolute(req.file, "image")),
        ...opt("provenance", req.provenance), ...opt("label", req.label), ...opt("note", req.note),
        ...withCase(req.case),
      ]);
    case "importFiles":
      return json([
        "import", "files", ...opt("dir", absolute(req.dir, "export directory")),
        ...opt("label", req.label), ...opt("note", req.note), ...withCase(req.case),
      ]);
    case "identify":
      return json(["identify", ...opt("evidence", id(req.evidence, "evidence ID")), ...withCase(req.case)]);
    case "identifyShow":
      return json(["identify", "show", ...opt("evidence", id(req.evidence, "evidence ID")), ...withCase(req.case)]);
    case "identifySelect":
      if (!FAMILY.test(req.family)) throw new RequestError("invalid family");
      if (!req.reason.trim()) throw new RequestError("a reason is required: the selection is audited");
      return json([
        "identify", "select", ...opt("family", req.family), ...opt("reason", req.reason),
        ...opt("evidence", id(req.evidence, "evidence ID")), ...withCase(req.case),
      ]);
    case "parse":
      return json(["parse", ...opt("evidence", id(req.evidence, "evidence ID")), ...withCase(req.case)]);
    case "listRecordings":
      return json([
        "list", "recordings",
        ...opt("evidence", req.evidence === null ? null : id(req.evidence, "evidence ID")),
        ...withCase(req.case),
      ]);
    case "listArtifacts":
      return json([
        "list", "artifacts",
        ...opt("recording", req.recording === null ? null : id(req.recording, "recording ID")),
        ...withCase(req.case),
      ]);
    case "coverage":
      return json(["coverage", ...opt("evidence", id(req.evidence, "evidence ID")), ...withCase(req.case)]);
    case "recover": {
      const tiers = req.tiers.filter((t) => ["T2", "T3", "T4"].includes(t));
      if (!tiers.length) throw new RequestError("choose at least one tier");
      return json([
        "recover", ...opt("tiers", tiers.join(",")),
        ...opt("budget-seconds", req.budgetSeconds === null ? null : finite(req.budgetSeconds, "budget")),
        ...opt("evidence", id(req.evidence, "evidence ID")), ...withCase(req.case),
      ]);
    }
    case "timeShow":
      return json(["time", "show", ...opt("evidence", id(req.evidence, "evidence ID")), ...withCase(req.case)]);
    case "timeSet":
      if (!["A", "B", "C", "D"].includes(req.method)) throw new RequestError("method must be A-D");
      return json([
        "time", "set", ...opt("method", req.method), ...opt("device-time", req.deviceTime),
        ...opt("true-time", req.trueTime),
        ...opt("uncertainty", req.uncertainty === null ? null : finite(req.uncertainty, "uncertainty")),
        ...opt("tz-offset-minutes",
          req.tzOffsetMinutes === null ? null : finite(req.tzOffsetMinutes, "timezone offset")),
        ...opt("valid-from", req.validFrom), ...opt("valid-to", req.validTo), ...opt("note", req.note),
        ...opt("evidence", id(req.evidence, "evidence ID")), ...withCase(req.case),
      ]);
    case "timeline":
      return json(["timeline", ...withCase(req.case)]);
    case "gaps":
      return json(["gaps", ...opt("min-gap", finite(req.minGap, "minimum gap")), ...withCase(req.case)]);
    case "analyzeMotion":
      return json([
        "analyze", "motion", id(req.recording, "recording ID"),
        ...opt("sensitivity", finite(req.sensitivity, "sensitivity")),
        ...opt("min-area", finite(req.minArea, "minimum area")), ...withCase(req.case),
      ]);
    case "exportClip":
      return json([
        "export", "clip", ...opt("recording", id(req.recording, "recording ID")),
        ...opt("out", req.out === null ? null : absolute(req.out, "output directory")),
        ...withCase(req.case),
      ]);
    case "reportFindings":
      return json(["report", "findings", ...withCase(req.case)]);
    case "reportCertificate":
      return json([
        "report", "certificate", ...opt("evidence", id(req.evidence, "evidence ID")), ...withCase(req.case),
      ]);
    case "reportGenerate":
      return json([
        "report", "generate", ...opt("out", absolute(req.out, "report directory")),
        ...opt("agency", req.agency), ...opt("letterhead", req.letterhead), ...opt("footer", req.footer),
        ...(req.pdf ? [] : ["--no-pdf"]), ...withCase(req.case),
      ]);
  }
}

function quote(arg: string): string {
  return /^[A-Za-z0-9_./:=,@+-]+$/.test(arg) ? arg : `"${arg.replace(/(["\\$`])/g, "\\$1")}"`;
}

export function display(args: string[]): string {
  return ["spectra", ...args].map(quote).join(" ");
}

export class CliRunner {
  private readonly running = new Map<number, ChildProcess>();
  private readonly cancelled = new Set<number>();
  private nextJob = 1;

  constructor(
    private readonly settings: () => Settings,
    private readonly started: (job: { jobId: number; display: string }) => void,
  ) {}

  run(request: CliRequest): Promise<CliResult> {
    const jobId = this.nextJob++;
    let argv: Argv;
    try {
      argv = argvFor(request);
    } catch (err) {
      return Promise.resolve(this.refused(jobId, request.kind, err));
    }
    const shown = display(argv.args);
    this.started({ jobId, display: shown });
    const cfg = this.settings();
    const began = Date.now();

    return new Promise((resolve) => {
      const child = spawn(cfg.python, ["-m", "spectra.cli", ...argv.args], {
        cwd: cfg.spectraRoot,
        env: childEnv(cfg),
        shell: false,
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
      });
      this.running.set(jobId, child);
      const out: Buffer[] = [];
      const err: Buffer[] = [];
      child.stdout?.on("data", (chunk: Buffer) => out.push(chunk));
      child.stderr?.on("data", (chunk: Buffer) => err.push(chunk));

      const finish = (exitCode: number | null, spawnError?: Error) => {
        this.running.delete(jobId);
        const cancelled = this.cancelled.delete(jobId);
        const stdout = Buffer.concat(out).toString("utf8");
        let stderr = Buffer.concat(err).toString("utf8");
        if (spawnError) {
          stderr = `could not start ${cfg.python}: ${spawnError.message}. Check Settings → Python.`;
        }
        let data: unknown = null;
        if (argv.json && stdout.trim()) {
          try {
            data = JSON.parse(stdout);
          } catch {
            data = null;
          }
        }
        resolve({
          jobId, ok: exitCode === 0 && !cancelled && !spawnError, exitCode, display: shown,
          stdout, stderr, data, durationMs: Date.now() - began, cancelled,
        });
      };
      child.on("error", (e) => finish(null, e));
      child.on("close", (code) => finish(code));
    });
  }

  cancel(jobId: number): void {
    const child = this.running.get(jobId);
    if (!child) return;
    this.cancelled.add(jobId);
    child.kill();
  }

  private refused(jobId: number, kind: string, err: unknown): CliResult {
    const message = err instanceof Error ? err.message : String(err);
    return {
      jobId, ok: false, exitCode: null, display: `spectra (${kind}) — not run`, stdout: "",
      stderr: `refused before running: ${message}`, data: null, durationMs: 0, cancelled: false,
    };
  }
}

export function childEnv(cfg: Settings): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env };
  // The case is always named explicitly; an inherited SPECTRA_CASE must never pick one.
  delete env.SPECTRA_CASE;
  env.PYTHONPATH = [cfg.spectraRoot, env.PYTHONPATH].filter(Boolean).join(path.delimiter);
  env.PYTHONIOENCODING = "utf-8";
  env.PYTHONUTF8 = "1";
  env.PYTHONDONTWRITEBYTECODE = "1";
  env.SPECTRA_OPERATOR = cfg.operator;
  if (cfg.ffmpeg) env.SPECTRA_FFMPEG = cfg.ffmpeg;
  return env;
}

import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import type { EnvironmentCheck, Settings } from "../shared/api.js";
import { childEnv } from "./cli.js";

const MAX_RECENT = 8;

export class SettingsStore {
  private current: Settings;

  constructor(
    private readonly file: string,
    appDir: string,
    homeDir: string,
  ) {
    this.current = { ...defaults(appDir, homeDir), ...this.load() };
  }

  /** The settings, with recent cases that no longer exist left out of the list shown. */
  get(): Settings {
    return {
      ...this.current,
      recentCases: this.current.recentCases.filter((dir) => isCase(dir)),
    };
  }

  save(next: Settings): Settings {
    this.current = {
      python: next.python.trim() || this.current.python,
      spectraRoot: next.spectraRoot.trim() || this.current.spectraRoot,
      ffmpeg: next.ffmpeg.trim(),
      operator: next.operator.trim() || this.current.operator,
      caseParent: next.caseParent.trim() || this.current.caseParent,
      recentCases: next.recentCases.slice(0, MAX_RECENT),
    };
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    fs.writeFileSync(this.file, JSON.stringify(this.current, null, 2), "utf8");
    return this.get();
  }

  remember(caseDir: string): void {
    const recent = [caseDir, ...this.current.recentCases.filter((c) => c !== caseDir)];
    this.save({ ...this.current, recentCases: recent, caseParent: path.dirname(caseDir) });
  }

  private load(): Partial<Settings> {
    try {
      return JSON.parse(fs.readFileSync(this.file, "utf8")) as Partial<Settings>;
    } catch {
      return {};
    }
  }
}

function isCase(dir: string): boolean {
  try {
    return fs.statSync(path.join(dir, "case.db")).isFile();
  } catch {
    return false;
  }
}

/** The virtual environment the repository's setup instructions create, if there is one. */
function repoPython(spectraRoot: string): string | null {
  const candidate =
    process.platform === "win32"
      ? path.join(spectraRoot, ".venv", "Scripts", "python.exe")
      : path.join(spectraRoot, ".venv", "bin", "python");
  return fs.existsSync(candidate) ? candidate : null;
}

function defaults(appDir: string, homeDir: string): Settings {
  // In development the app lives in <repo>/desktop and the package in <repo>/spectra.
  const spectraRoot = process.env.SPECTRA_ROOT || path.resolve(appDir, "..");
  return {
    python:
      process.env.SPECTRA_PYTHON ||
      repoPython(spectraRoot) ||
      (process.platform === "win32" ? "python" : "python3"),
    spectraRoot,
    ffmpeg: process.env.SPECTRA_FFMPEG || "",
    operator: process.env.SPECTRA_OPERATOR || os.userInfo().username,
    caseParent: path.join(homeDir, "SPECTRA Cases"),
    recentCases: [],
  };
}

function capture(cmd: string, args: string[], cfg: Settings): Promise<{ code: number | null; out: string }> {
  return new Promise((resolve) => {
    let out = "";
    const child = spawn(cmd, args, {
      cwd: cfg.spectraRoot, env: childEnv(cfg), shell: false, windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    child.stdout.on("data", (c: Buffer) => (out += c.toString("utf8")));
    child.stderr.on("data", (c: Buffer) => (out += c.toString("utf8")));
    child.on("error", (e) => resolve({ code: null, out: e.message }));
    child.on("close", (code) => resolve({ code, out }));
  });
}

function spectraVersion(python: string, cfg: Settings) {
  return capture(python, ["-m", "spectra.cli", "version"], { ...cfg, python });
}

/** Can the configured Python run spectra, and which FFmpeg will it find? */
export async function checkEnvironment(cfg: Settings): Promise<EnvironmentCheck> {
  const [version, ffmpeg] = await Promise.all([
    spectraVersion(cfg.python, cfg),
    capture(
      cfg.python,
      ["-c", "from spectra.core.media import locate_ffmpeg; p = locate_ffmpeg(); print(p or '')"],
      cfg,
    ),
  ]);
  const found = ffmpeg.code === 0 ? lastLine(ffmpeg.out) : "";
  const ok = version.code === 0;

  let python: { path: string; version: string } | null = null;
  const local = repoPython(cfg.spectraRoot);
  if (!ok && local && path.resolve(local) !== path.resolve(cfg.python)) {
    const tried = await spectraVersion(local, cfg);
    if (tried.code === 0) python = { path: local, version: tried.out.trim() };
  }
  // The development requirements install an FFmpeg through imageio-ffmpeg; spectra itself
  // only looks at SPECTRA_FFMPEG and PATH, so offer that binary rather than leave export
  // without an MP4.
  let ffmpegPath: string | null = null;
  const pythonForFfmpeg = ok ? cfg.python : python?.path;
  if (!found && pythonForFfmpeg) {
    const bundled = await capture(
      pythonForFfmpeg,
      ["-c", "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"],
      cfg,
    );
    const candidate = bundled.code === 0 ? lastLine(bundled.out) : "";
    if (candidate && fs.existsSync(candidate)) ffmpegPath = candidate;
  }

  return {
    spectra: {
      ok,
      version: ok ? version.out.trim() : null,
      error: ok ? null : version.out.trim().split(/\r?\n/).slice(-3).join("\n"),
    },
    ffmpeg: { ok: Boolean(found), path: found || null },
    suggestion:
      python || ffmpegPath
        ? { python: python?.path ?? null, version: python?.version ?? null, ffmpeg: ffmpegPath }
        : null,
  };
}

function lastLine(text: string): string {
  return text.trim().split(/\r?\n/).pop()?.trim() ?? "";
}

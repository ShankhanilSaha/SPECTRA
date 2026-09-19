import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import type { EnvironmentCheck, Settings } from "../shared/api.js";
import { childEnv } from "./cli.js";

const MAX_RECENT = 8;

export class SettingsStore {
  private current: Settings;

  constructor(private readonly file: string, appDir: string) {
    this.current = { ...defaults(appDir), ...this.load() };
  }

  get(): Settings {
    return { ...this.current, recentCases: [...this.current.recentCases] };
  }

  save(next: Settings): Settings {
    this.current = {
      python: next.python.trim() || this.current.python,
      spectraRoot: next.spectraRoot.trim() || this.current.spectraRoot,
      ffmpeg: next.ffmpeg.trim(),
      operator: next.operator.trim() || this.current.operator,
      recentCases: next.recentCases.slice(0, MAX_RECENT),
    };
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    fs.writeFileSync(this.file, JSON.stringify(this.current, null, 2), "utf8");
    return this.get();
  }

  remember(caseDir: string): void {
    const recent = [caseDir, ...this.current.recentCases.filter((c) => c !== caseDir)];
    this.save({ ...this.current, recentCases: recent });
  }

  private load(): Partial<Settings> {
    try {
      return JSON.parse(fs.readFileSync(this.file, "utf8")) as Partial<Settings>;
    } catch {
      return {};
    }
  }
}

function defaults(appDir: string): Settings {
  return {
    python: process.env.SPECTRA_PYTHON || (process.platform === "win32" ? "python" : "python3"),
    // In development the app lives in <repo>/desktop and the package in <repo>/spectra.
    spectraRoot: process.env.SPECTRA_ROOT || path.resolve(appDir, ".."),
    ffmpeg: process.env.SPECTRA_FFMPEG || "",
    operator: process.env.SPECTRA_OPERATOR || os.userInfo().username,
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

/** Can the configured Python run spectra, and which FFmpeg will it find? */
export async function checkEnvironment(cfg: Settings): Promise<EnvironmentCheck> {
  const version = await capture(cfg.python, ["-m", "spectra.cli", "version"], cfg);
  const ffmpeg = await capture(
    cfg.python,
    ["-c", "from spectra.core.media import locate_ffmpeg; p = locate_ffmpeg(); print(p or '')"],
    cfg,
  );
  const found = ffmpeg.code === 0 ? ffmpeg.out.trim().split(/\r?\n/).pop() || "" : "";
  return {
    spectra: {
      ok: version.code === 0,
      version: version.code === 0 ? version.out.trim() : null,
      error: version.code === 0 ? null : version.out.trim().split(/\r?\n/).slice(-3).join("\n"),
    },
    ffmpeg: { ok: Boolean(found), path: found || null },
  };
}

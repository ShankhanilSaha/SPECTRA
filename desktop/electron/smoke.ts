/**
 * `electron . --smoke`: visit every screen of a prepared case, screenshot each one, and
 * write the renderer console to a log. A development check that the UI renders against real
 * CLI output; not part of the product flow.
 *
 *   SPECTRA_UI_SMOKE_CASE    case directory to open
 *   SPECTRA_UI_SMOKE_OUT     where the PNGs and console.log go
 *   SPECTRA_UI_SMOKE_ROUTES  optional comma-separated list of steps, each a route
 *                            ("identify") or "click:<css selector>" to press a button on
 *                            the current screen and capture what follows
 */

import fs from "node:fs";
import path from "node:path";

import type { BrowserWindow } from "electron";

const ROUTES = [
  "overview", "documents", "evidence", "identify", "parse", "time", "recover",
  "timeline", "analytics", "export", "report", "settings", "log",
];

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function settle(win: BrowserWindow): Promise<void> {
  const deadline = Date.now() + 30_000;
  await sleep(300);
  while (Date.now() < deadline) {
    const busy = (await win.webContents.executeJavaScript("window.__spectraBusy || 0")) as number;
    if (busy === 0) break;
    await sleep(200);
  }
  await sleep(500);
}

export async function runSmoke(win: BrowserWindow, outDir: string): Promise<void> {
  fs.mkdirSync(outDir, { recursive: true });
  const log: string[] = [];
  win.webContents.on("console-message", (...args: unknown[]) => {
    const details = args[0] as { level?: unknown; message?: unknown };
    const level = details.level ?? args[1];
    const message = details.message ?? args[2];
    log.push(`[${String(level)}] ${String(message)}`);
  });
  win.webContents.on("render-process-gone", (_e, details) => log.push(`[crash] ${details.reason}`));
  if (win.webContents.isLoading()) {
    await new Promise<void>((resolve) => win.webContents.once("did-finish-load", () => resolve()));
  }
  await settle(win);

  const routes = process.env.SPECTRA_UI_SMOKE_ROUTES?.split(",").filter(Boolean) ?? ROUTES;
  for (const [i, route] of routes.entries()) {
    if (route.startsWith("click:")) {
      const selector = route.slice("click:".length);
      const clicked = (await win.webContents.executeJavaScript(
        `(() => { const b = document.querySelector(${JSON.stringify(selector)}); if (!b) return "NOT FOUND";` +
          ` const text = b.textContent; const disabled = b.disabled; b.click(); return (disabled ? "DISABLED " : "clicked ") + JSON.stringify(text); })()`,
      )) as string;
      log.push(`[smoke] ${clicked} ${selector}`);
    } else {
      await win.webContents.executeJavaScript(`window.location.hash = ${JSON.stringify(`#/${route}`)}`);
    }
    await settle(win);
    const image = await win.webContents.capturePage();
    const label = route.startsWith("click:") ? "click" : route;
    const name = `${String(i + 1).padStart(2, "0")}-${label}.png`;
    fs.writeFileSync(path.join(outDir, name), image.toPNG());
    log.push(`[smoke] captured ${name} (${image.getSize().width}x${image.getSize().height})`);
  }
  fs.writeFileSync(path.join(outDir, "console.log"), `${log.join("\n")}\n`, "utf8");
}

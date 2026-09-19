/**
 * SPECTRA desktop — main process.
 *
 * A client of the `spectra` CLI (doc 3 §2): every action the window offers is one audited
 * CLI command, run as a child process. There is no server and no listening socket (doc 3 D5,
 * §14), the window loads nothing from the network, and it cannot touch evidence: it hands
 * paths to the CLI, which reads them through EvidenceSource.
 */

import fs from "node:fs";
import path from "node:path";

import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  Menu,
  protocol,
  session,
  shell,
  type IpcMainInvokeEvent,
  type MenuItemConstructorOptions,
} from "electron";

import type { CliRequest, MenuAction, PickOptions, Settings } from "../shared/api.js";
import { CliRunner } from "./cli.js";
import {
  APP_ORIGIN,
  APP_SCHEME,
  MEDIA_SCHEME,
  serveArtifact,
  serveRenderer,
} from "./protocols.js";
import { checkEnvironment, SettingsStore } from "./settings.js";
import { runSmoke } from "./smoke.js";

protocol.registerSchemesAsPrivileged([
  { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true } },
  {
    scheme: MEDIA_SCHEME,
    privileges: { standard: true, secure: true, stream: true, supportFetchAPI: true },
  },
]);
// Offline by construction (rule 6): no hostname can resolve, so nothing can be fetched even
// if something tried. The app's own schemes are served in-process and need no DNS.
app.commandLine.appendSwitch("host-resolver-rules", "MAP * ~NOTFOUND");
app.enableSandbox();

const appDir = app.getAppPath();
const rendererDir = path.join(appDir, "dist", "renderer");
const smoke = process.argv.includes("--smoke");

let settings: SettingsStore;
let runner: CliRunner;
let win: BrowserWindow | null = null;
let activeCase: string | null = null;

function trusted(event: IpcMainInvokeEvent): boolean {
  return event.senderFrame?.url.startsWith(`${APP_ORIGIN}/`) ?? false;
}

function handle<T extends unknown[], R>(
  channel: string,
  fn: (...args: T) => R | Promise<R>,
): void {
  ipcMain.handle(channel, (event, ...args) => {
    if (!trusted(event)) throw new Error("request from an untrusted frame");
    return fn(...(args as T));
  });
}

function hardenSession(): void {
  const ses = session.defaultSession;
  ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  ses.setPermissionCheckHandler(() => false);
  ses.setSpellCheckerEnabled(false);
  ses.webRequest.onBeforeRequest((details, callback) => {
    const allowed =
      details.url.startsWith(`${APP_SCHEME}:`) ||
      details.url.startsWith(`${MEDIA_SCHEME}:`) ||
      details.url.startsWith("devtools:") ||
      details.url.startsWith("data:");
    callback({ cancel: !allowed });
  });
  ses.protocol.handle(APP_SCHEME, (request) => serveRenderer(request, rendererDir));
  ses.protocol.handle(MEDIA_SCHEME, (request) => serveArtifact(request, activeCase));
}

function sendMenu(action: MenuAction): void {
  win?.webContents.send("spectra:menu", action);
}

function buildMenu(): void {
  const template: MenuItemConstructorOptions[] = [
    {
      label: "File",
      submenu: [
        { label: "New Case…", accelerator: "CmdOrCtrl+N", click: () => sendMenu("newCase") },
        { label: "Open Case…", accelerator: "CmdOrCtrl+O", click: () => sendMenu("openCase") },
        { label: "Close Case", click: () => sendMenu("closeCase") },
        { type: "separator" },
        { label: "Settings…", accelerator: "CmdOrCtrl+,", click: () => sendMenu("settings") },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    {
      label: "View",
      submenu: [
        { label: "Command Log", accelerator: "CmdOrCtrl+L", click: () => sendMenu("log") },
        { type: "separator" },
        { role: "reload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow(query: Record<string, string> = {}): BrowserWindow {
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    title: "SPECTRA",
    backgroundColor: "#0f1419",
    show: !smoke,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webviewTag: false,
      spellcheck: false,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(`${APP_ORIGIN}/`)) event.preventDefault();
  });
  const search = new URLSearchParams(query).toString();
  void window.loadURL(`${APP_ORIGIN}/index.html${search ? `?${search}` : ""}`);
  return window;
}

function registerIpc(): void {
  handle("spectra:run", (request: CliRequest) => runner.run(request));
  handle("spectra:cancel", (jobId: number) => runner.cancel(jobId));
  handle("spectra:getSettings", () => settings.get());
  handle("spectra:saveSettings", (next: Settings) => settings.save(next));
  handle("spectra:checkEnvironment", () => checkEnvironment(settings.get()));
  handle("spectra:setActiveCase", (dir: string | null) => {
    if (dir === null) {
      activeCase = null;
      return;
    }
    if (!path.isAbsolute(dir) || !fs.existsSync(path.join(dir, "case.db"))) {
      throw new Error(`${dir} is not a SPECTRA case directory (no case.db)`);
    }
    activeCase = dir;
    settings.remember(dir);
  });
  handle("spectra:pick", async (options: PickOptions) => {
    const properties: Array<"openFile" | "openDirectory" | "createDirectory" | "promptToCreate"> =
      options.kind === "file" ? ["openFile"] : ["openDirectory"];
    if (options.create) properties.push("createDirectory", "promptToCreate");
    const result = await dialog.showOpenDialog(win!, {
      title: options.title,
      properties,
      filters: options.extensions?.length
        ? [{ name: "Files", extensions: options.extensions }, { name: "All files", extensions: ["*"] }]
        : undefined,
    });
    return result.canceled || !result.filePaths.length ? null : result.filePaths[0];
  });
  handle("spectra:openReport", async (htmlPath: string) => {
    if (!htmlPath.toLowerCase().endsWith(".html") || !fs.existsSync(htmlPath)) {
      throw new Error("not a generated report");
    }
    const error = await shell.openPath(htmlPath);
    if (error) throw new Error(error);
  });
  handle("spectra:showInFolder", (target: string) => {
    if (fs.existsSync(target)) shell.showItemInFolder(target);
  });
}

void app.whenReady().then(async () => {
  settings = new SettingsStore(path.join(app.getPath("userData"), "settings.json"), appDir);
  runner = new CliRunner(
    () => settings.get(),
    (job) => win?.webContents.send("spectra:jobStarted", job),
  );
  hardenSession();
  registerIpc();
  buildMenu();

  if (smoke) {
    const caseDir = process.env.SPECTRA_UI_SMOKE_CASE ?? "";
    win = createWindow(caseDir ? { case: caseDir } : {});
    // A hidden window cannot be captured on every platform; show it without taking focus.
    win.showInactive();
    try {
      await runSmoke(win, process.env.SPECTRA_UI_SMOKE_OUT ?? path.join(appDir, "smoke-out"));
    } catch (err) {
      console.error("smoke run failed:", err);
      process.exitCode = 1;
    }
    app.quit();
    return;
  }
  win = createWindow();
  win.on("closed", () => (win = null));
});

app.on("window-all-closed", () => app.quit());

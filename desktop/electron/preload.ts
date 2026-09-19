/**
 * The whole surface the window gets: named calls, no Node, no raw ipcRenderer.
 */

import { contextBridge, ipcRenderer } from "electron";

import type { MenuAction, SpectraBridge } from "../shared/api.js";

const bridge: SpectraBridge = {
  run: (request) => ipcRenderer.invoke("spectra:run", request),
  cancel: (jobId) => ipcRenderer.invoke("spectra:cancel", jobId),
  onJobStarted: (listener) => {
    const wrapped = (_e: unknown, job: { jobId: number; display: string }) => listener(job);
    ipcRenderer.on("spectra:jobStarted", wrapped);
    return () => ipcRenderer.removeListener("spectra:jobStarted", wrapped);
  },
  pick: (options) => ipcRenderer.invoke("spectra:pick", options),
  getSettings: () => ipcRenderer.invoke("spectra:getSettings"),
  saveSettings: (settings) => ipcRenderer.invoke("spectra:saveSettings", settings),
  checkEnvironment: () => ipcRenderer.invoke("spectra:checkEnvironment"),
  setActiveCase: (dir) => ipcRenderer.invoke("spectra:setActiveCase", dir),
  artifactUrl: (sha256) => `spectra-media://artifact/${sha256}`,
  openReport: (htmlPath) => ipcRenderer.invoke("spectra:openReport", htmlPath),
  showInFolder: (target) => ipcRenderer.invoke("spectra:showInFolder", target),
  onMenu: (listener) => {
    const wrapped = (_e: unknown, action: MenuAction) => listener(action);
    ipcRenderer.on("spectra:menu", wrapped);
    return () => ipcRenderer.removeListener("spectra:menu", wrapped);
  },
};

contextBridge.exposeInMainWorld("spectra", bridge);

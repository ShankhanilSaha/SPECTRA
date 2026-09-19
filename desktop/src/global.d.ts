import type { SpectraBridge } from "../shared/api";

declare global {
  interface Window {
    spectra: SpectraBridge;
    /** In-flight CLI requests; read by the smoke run to know when a screen has settled. */
    __spectraBusy?: number;
  }
}

export {};

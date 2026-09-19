/**
 * The open case, the selected evidence item, and a version number that goes up after every
 * state-changing command so every view re-reads what it shows from the CLI.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { bridge, errorText, run, runForResult } from "./api";
import type { CaseInfo, VerifyResult } from "./types";

export interface CaseState {
  dir: string;
  info: CaseInfo | null;
  infoError: string | null;
  verify: VerifyResult | null;
  evidenceId: string | null;
  setEvidenceId(id: string): void;
  /** Increases after any change; include it in data keys. */
  version: number;
  /** Call after a state-changing command succeeded. */
  changed(): void;
  close(): void;
}

const CaseContext = createContext<CaseState | null>(null);

export function useCase(): CaseState {
  const value = useContext(CaseContext);
  if (!value) throw new Error("useCase outside an open case");
  return value;
}

export function useOptionalCase(): CaseState | null {
  return useContext(CaseContext);
}

export function CaseProvider(props: { dir: string; onClose(): void; children: ReactNode }) {
  const { dir, onClose } = props;
  const [info, setInfo] = useState<CaseInfo | null>(null);
  const [infoError, setInfoError] = useState<string | null>(null);
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    void bridge.setActiveCase(dir);
    return () => void bridge.setActiveCase(null);
  }, [dir]);

  useEffect(() => {
    let live = true;
    run<CaseInfo>({ kind: "caseInfo", case: dir }).then(
      (data) => {
        if (!live) return;
        setInfo(data);
        setInfoError(null);
        setEvidenceId((current) =>
          current && data.evidence.some((e) => e.id === current) ? current : (data.evidence[0]?.id ?? null),
        );
      },
      (err: unknown) => live && setInfoError(errorText(err)),
    );
    // Verification fails with exit 1 and still prints JSON: that is a result to show.
    runForResult<VerifyResult>({ kind: "caseVerify", case: dir }).then(
      (r) => live && setVerify(r.data),
    );
    return () => {
      live = false;
    };
  }, [dir, version]);

  const changed = useCallback(() => setVersion((v) => v + 1), []);
  const value = useMemo<CaseState>(
    () => ({ dir, info, infoError, verify, evidenceId, setEvidenceId, version, changed, close: onClose }),
    [dir, info, infoError, verify, evidenceId, version, changed, onClose],
  );
  return <CaseContext.Provider value={value}>{props.children}</CaseContext.Provider>;
}

// -- routing -------------------------------------------------------------------------------

export function useRoute(): [string, (route: string) => void] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("?")[0] || "overview";
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const navigate = useCallback((next: string) => {
    window.location.hash = `#/${next}`;
  }, []);
  return [route, navigate];
}

/** A parameter from the hash route, e.g. `rec` in "#/export?rec=REC-0001". */
export function routeParam(name: string): string | null {
  const query = window.location.hash.split("?")[1] ?? "";
  return new URLSearchParams(query).get(name);
}

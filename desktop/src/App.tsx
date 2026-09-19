import { useCallback, useEffect, useState, type ReactNode } from "react";

import { bridge, errorText, useCommandLog } from "./api";
import { Badge } from "./components/ui";
import { Analytics } from "./screens/Analytics";
import { Documents } from "./screens/Documents";
import { Evidence } from "./screens/Evidence";
import { ExportScreen } from "./screens/Export";
import { Identify } from "./screens/Identify";
import { LogScreen } from "./screens/LogScreen";
import { Overview } from "./screens/Overview";
import { Parse } from "./screens/Parse";
import { Recover } from "./screens/Recover";
import { Report } from "./screens/Report";
import { SettingsScreen } from "./screens/Settings";
import { TimelineScreen } from "./screens/TimelineScreen";
import { TimeModel } from "./screens/TimeModel";
import { Welcome } from "./screens/Welcome";
import { CaseProvider, useCase, useOptionalCase, useRoute } from "./state";

const STEPS: { route: string; label: string; step: number }[] = [
  { route: "overview", label: "Case", step: 1 },
  { route: "documents", label: "Documents & custody", step: 2 },
  { route: "evidence", label: "Evidence", step: 3 },
  { route: "identify", label: "Identify", step: 4 },
  { route: "parse", label: "Parse & recordings", step: 5 },
  { route: "time", label: "Time model", step: 6 },
  { route: "recover", label: "Recover", step: 7 },
  { route: "timeline", label: "Timeline", step: 8 },
  { route: "analytics", label: "Analytics", step: 9 },
  { route: "export", label: "Review & export", step: 10 },
  { route: "report", label: "Report", step: 11 },
];

export function App() {
  const [caseDir, setCaseDir] = useState<string | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const [route, navigate] = useRoute();
  const [welcomeMode, setWelcomeMode] = useState<"open" | "new">("open");

  const openCase = useCallback(
    async (dir: string) => {
      setOpenError(null);
      try {
        await bridge.setActiveCase(dir);
        setCaseDir(dir);
        navigate("overview");
      } catch (err) {
        setOpenError(errorText(err).replace(/^Error invoking remote method '[^']+': (Error: )?/, ""));
      }
    },
    [navigate],
  );
  const closeCase = useCallback(() => {
    setCaseDir(null);
    void bridge.setActiveCase(null);
  }, []);

  useEffect(() => {
    const fromQuery = new URLSearchParams(window.location.search).get("case");
    if (fromQuery) void openCase(fromQuery);
  }, [openCase]);

  useEffect(
    () =>
      bridge.onMenu(async (action) => {
        if (action === "settings") navigate("settings");
        else if (action === "log") navigate("log");
        else if (action === "closeCase") closeCase();
        else if (action === "newCase") {
          closeCase();
          setWelcomeMode("new");
          navigate("welcome");
        } else if (action === "openCase") {
          const dir = await bridge.pick({ title: "Open a SPECTRA case directory", kind: "directory" });
          if (dir) await openCase(dir);
        }
      }),
    [navigate, closeCase, openCase],
  );

  const body = (() => {
    if (route === "settings") return <SettingsScreen />;
    if (route === "log") return <LogScreen />;
    if (!caseDir) return <Welcome mode={welcomeMode} setMode={setWelcomeMode} onOpen={openCase} error={openError} />;
    return <CaseScreen route={route} />;
  })();

  return caseDir ? (
    <CaseProvider dir={caseDir} onClose={closeCase}>
      <Shell route={route} navigate={navigate}>{body}</Shell>
    </CaseProvider>
  ) : (
    <Shell route={route} navigate={navigate}>{body}</Shell>
  );
}

function CaseScreen(props: { route: string }) {
  switch (props.route) {
    case "documents":
      return <Documents />;
    case "evidence":
      return <Evidence />;
    case "identify":
      return <Identify />;
    case "parse":
      return <Parse />;
    case "time":
      return <TimeModel />;
    case "recover":
      return <Recover />;
    case "timeline":
      return <TimelineScreen />;
    case "analytics":
      return <Analytics />;
    case "export":
      return <ExportScreen />;
    case "report":
      return <Report />;
    default:
      return <Overview />;
  }
}

function Shell(props: { route: string; navigate(r: string): void; children: ReactNode }) {
  return (
    <div className="shell">
      <TopBar navigate={props.navigate} />
      <nav className="sidebar" aria-label="Examination steps">
        <CaseNav route={props.route} navigate={props.navigate} />
        <div className="nav-foot">
          <NavLink route="log" label="Command log" active={props.route === "log"} navigate={props.navigate} />
          <NavLink route="settings" label="Settings" active={props.route === "settings"} navigate={props.navigate} />
        </div>
      </nav>
      <main className="content">{props.children}</main>
    </div>
  );
}

function NavLink(props: { route: string; label: string; step?: number; active: boolean; disabled?: boolean; navigate(r: string): void }) {
  return (
    <button
      type="button"
      className={`nav-item ${props.active ? "active" : ""}`}
      onClick={() => props.navigate(props.route)}
      disabled={props.disabled}
      aria-current={props.active ? "page" : undefined}
    >
      {props.step !== undefined && <span className="nav-step">{props.step}</span>}
      <span>{props.label}</span>
    </button>
  );
}

function CaseNav(props: { route: string; navigate(r: string): void }) {
  const open = useOptionalCaseDir();
  return (
    <div className="nav-steps">
      {!open && <NavLink route="welcome" label="Open or create a case" active={props.route !== "settings" && props.route !== "log"} navigate={props.navigate} />}
      {STEPS.map((s) => (
        <NavLink key={s.route} route={s.route} label={s.label} step={s.step} active={Boolean(open) && props.route === s.route} disabled={!open} navigate={props.navigate} />
      ))}
    </div>
  );
}

function useOptionalCaseDir(): string | null {
  return useOptionalCase()?.dir ?? null;
}

function TopBar(props: { navigate(r: string): void }) {
  const current = useOptionalCase();
  const running = useCommandLog().filter((e) => e.result === null).length;
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden>◈</span> SPECTRA
        <span className="brand-sub">offline · prototype</span>
      </div>
      {current ? <CaseBar navigate={props.navigate} /> : <div className="topbar-case muted">No case open</div>}
      <div className="topbar-right">
        {running > 0 && (
          <button type="button" className="running" onClick={() => props.navigate("log")}>
            <span className="spinner" /> {running} running
          </button>
        )}
      </div>
    </header>
  );
}

function CaseBar(props: { navigate(r: string): void }) {
  const c = useCase();
  const meta = c.info?.case;
  return (
    <div className="topbar-case">
      <div className="case-id">
        <strong>{meta?.case_id ?? "…"}</strong>
        {meta?.title && <span className="muted"> · {meta.title}</span>}
      </div>
      {c.info && c.info.evidence.length > 0 && (
        <label className="evidence-select">
          <span>Evidence</span>
          <select value={c.evidenceId ?? ""} onChange={(e) => c.setEvidenceId(e.target.value)}>
            {c.info.evidence.map((e) => (
              <option key={e.id} value={e.id}>
                {e.id} · class {e.provenance_class} · {e.label || e.kind}
              </option>
            ))}
          </select>
        </label>
      )}
      <button type="button" className="audit-chip" onClick={() => props.navigate("overview")} title="Audit chain status — click for details">
        {c.verify === null ? (
          <Badge>audit …</Badge>
        ) : c.verify.ok ? (
          <Badge tone="ok">audit chain verified · {c.verify.records_checked} records</Badge>
        ) : (
          <Badge tone="error">audit chain BROKEN{c.verify.broken_at_seq !== null ? ` at record ${c.verify.broken_at_seq}` : ""}</Badge>
        )}
      </button>
      <button type="button" className="btn btn-ghost" onClick={c.close}>Close case</button>
    </div>
  );
}

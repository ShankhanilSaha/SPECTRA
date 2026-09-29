import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import type { EnvironmentCheck } from "../shared/api";
import { bridge, errorText, useCommandLog } from "./api";
import { Badge } from "./components/ui";
import { Analytics } from "./screens/Analytics";
import { Documents } from "./screens/Documents";
import { EngineSetup, Starting } from "./screens/EngineSetup";
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
import { CaseProvider, useCase, useOptionalCase, useRoute, useTaskBusy } from "./state";
import { GROUP_TITLE, nextStep, STEPS, stepStatus, type StepDef, type StepGroup, type StepStatus } from "./steps";

export function App() {
  const [caseDir, setCaseDir] = useState<string | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const [env, setEnv] = useState<EnvironmentCheck | null>(null);
  const [route, navigate] = useRoute();

  const recheck = useCallback(async () => setEnv(await bridge.checkEnvironment()), []);
  useEffect(() => {
    void recheck();
  }, [recheck]);

  const openCase = useCallback(
    async (dir: string) => {
      setOpenError(null);
      try {
        await bridge.setActiveCase(dir);
        setCaseDir(dir);
        navigate("overview");
      } catch (err) {
        setOpenError(errorText(err));
      }
    },
    [navigate],
  );
  const closeCase = useCallback(() => {
    setCaseDir(null);
    void bridge.setActiveCase(null);
    navigate("welcome");
  }, [navigate]);

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
        else if (action === "newCase") closeCase();
        else if (action === "openCase") {
          const dir = await bridge.pick({ title: "Open a SPECTRA case directory", kind: "directory" });
          if (dir) await openCase(dir);
        }
      }),
    [navigate, closeCase, openCase],
  );

  const body = (() => {
    if (route === "settings") return <SettingsScreen onSaved={recheck} />;
    if (route === "log") return <LogScreen />;
    if (!env) return <Starting />;
    if (!env.spectra.ok) return <EngineSetup env={env} onFixed={recheck} />;
    if (!caseDir) return <Welcome env={env} onOpen={openCase} onFixed={recheck} error={openError} />;
    return <CaseScreen route={route} />;
  })();

  return caseDir ? (
    // Keyed by folder: opening another case starts from nothing, not from this one's tasks.
    <CaseProvider key={caseDir} dir={caseDir} onClose={closeCase}>
      <Shell route={route}>{body}</Shell>
    </CaseProvider>
  ) : (
    <Shell route={route}>{body}</Shell>
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

function Shell(props: { route: string; children: ReactNode }) {
  const main = useRef<HTMLElement>(null);
  // Each step starts at its top, not wherever the previous one was scrolled to.
  // Braces matter: scrollTo returns a promise in current Chromium, and an effect must not
  // return anything but a cleanup function.
  useEffect(() => {
    main.current?.scrollTo(0, 0);
  }, [props.route]);
  return (
    <div className="shell">
      <TopBar />
      <nav className="sidebar" aria-label="Examination steps">
        <Sidebar route={props.route} />
        <div className="nav-foot">
          <a className={`nav-item nav-plain ${props.route === "log" ? "active" : ""}`} href="#/log">Command log</a>
          <a className={`nav-item nav-plain ${props.route === "settings" ? "active" : ""}`} href="#/settings">Settings</a>
        </div>
      </nav>
      <main className="content" ref={main}>{props.children}</main>
    </div>
  );
}

function Sidebar(props: { route: string }) {
  const c = useOptionalCase();
  if (!c) {
    return (
      <div className="nav-steps">
        <a className={`nav-item nav-plain ${props.route !== "settings" && props.route !== "log" ? "active" : ""}`} href="#/welcome">
          Open or create a case
        </a>
        <p className="nav-hint">The examination steps appear here once a case is open.</p>
      </div>
    );
  }
  return <CaseSteps route={props.route} />;
}

const TASK_PREFIX: Partial<Record<string, (ev: string | null) => string>> = {
  evidence: () => "import",
  identify: (ev) => `identify:${ev}`,
  parse: (ev) => `parse:${ev}`,
  recover: (ev) => `recover:${ev}`,
  analytics: () => "motion:",
  export: () => "export:",
  report: () => "report",
};

function CaseSteps(props: { route: string }) {
  const c = useCase();
  const progress = c.info?.progress ?? null;
  const next = nextStep(progress, c.marks, c.evidenceId);
  const groups: StepGroup[] = ["case", "evidence", "findings"];
  return (
    <div className="nav-steps">
      {groups.map((group) => (
        <div key={group} className="nav-group">
          <div className="nav-group-head">
            <span>{GROUP_TITLE[group]}</span>
            {group === "evidence" && <EvidenceSelect />}
          </div>
          {STEPS.filter((s) => s.group === group).map((s) => (
            <StepLink
              key={s.id}
              def={s}
              status={stepStatus(s.id, progress, c.evidence, c.marks)}
              active={props.route === s.id || (s.id === "overview" && !STEPS.some((x) => x.id === props.route) && props.route !== "log" && props.route !== "settings")}
              next={next?.step === s.id && (group !== "evidence" || next.evidenceId === c.evidenceId)}
              task={TASK_PREFIX[s.id]?.(c.evidenceId) ?? null}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

function EvidenceSelect() {
  const c = useCase();
  const items = c.info?.evidence ?? [];
  if (!items.length) return <span className="nav-group-note">none yet</span>;
  if (items.length === 1) return <span className="nav-group-note">{items[0].id}</span>;
  return (
    <select className="nav-evidence" value={c.evidenceId ?? ""} onChange={(e) => c.setEvidenceId(e.target.value)} aria-label="Evidence item for steps 4 to 7">
      {items.map((e) => (
        <option key={e.id} value={e.id}>
          {e.id}{e.label ? ` · ${e.label}` : ""}
        </option>
      ))}
    </select>
  );
}

const GLYPH: Record<StepStatus["state"], string> = {
  done: "✓",
  todo: "",
  attention: "!",
  skipped: "–",
  na: "–",
  locked: "",
};

function StepLink(props: { def: StepDef; status: StepStatus; active: boolean; next: boolean; task: string | null }) {
  const { def, status } = props;
  const running = useTaskBusy(props.task ?? "\0");
  const state = running ? "running" : status.state;
  return (
    <a
      className={`nav-item step-${state} ${status.warn ? "step-warn" : ""} ${props.active ? "active" : ""} ${props.next ? "is-next" : ""}`}
      href={`#/${def.id}`}
      aria-current={props.active ? "page" : undefined}
    >
      <span className="nav-step" aria-hidden>
        {running ? <span className="spinner" /> : GLYPH[status.state] || def.n}
      </span>
      <span className="nav-text">
        <span className="nav-title">
          {def.title}
          {props.next && !props.active && <span className="next-tag">next</span>}
        </span>
        <span className="nav-summary">{running ? "running…" : status.summary}</span>
      </span>
    </a>
  );
}

function TopBar() {
  const current = useOptionalCase();
  const running = useCommandLog().filter((e) => e.result === null).length;
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden>◈</span> SPECTRA
        <span className="brand-sub">offline · prototype</span>
      </div>
      {current ? <CaseBar /> : <div className="topbar-case muted">No case open</div>}
      <div className="topbar-right">
        {running > 0 && (
          <a className="running" href="#/log" title="Open the command log">
            <span className="spinner" /> {running} running
          </a>
        )}
        {current && <button type="button" className="btn btn-ghost" onClick={current.close}>Close case</button>}
      </div>
    </header>
  );
}

function CaseBar() {
  const c = useCase();
  const meta = c.info?.case;
  return (
    <div className="topbar-case">
      <div className="case-id">
        <strong>{meta?.case_id ?? "…"}</strong>
        {meta?.title && <span className="muted"> · {meta.title}</span>}
      </div>
      <a className="audit-chip" href="#/overview" title="Audit chain status — open the case overview for details">
        {c.verify === null ? (
          <Badge>audit chain · checking</Badge>
        ) : c.verify.ok ? (
          <Badge tone="ok">audit chain verified · {c.verify.records_checked} records{c.verifying ? " · rechecking" : ""}</Badge>
        ) : (
          <Badge tone="error">audit chain BROKEN{c.verify.broken_at_seq !== null ? ` at record ${c.verify.broken_at_seq}` : ""}</Badge>
        )}
      </a>
    </div>
  );
}

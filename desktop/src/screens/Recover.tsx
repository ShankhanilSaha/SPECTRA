import { useEffect, useState } from "react";

import type { Tier } from "../../shared/api";
import { run, useCli } from "../api";
import { CoverageBar } from "../components/CoverageBar";
import { RecordingTable } from "../components/RecordingTable";
import { StepPage } from "../components/StepNav";
import { Badge, Button, CliHint, Empty, KeyValues, Notice, Panel, TierBadge } from "../components/ui";
import { duration } from "../format";
import type { CoverageRow, Recording, RecoverSummary } from "../types";
import { useCase, useSessionState, useTask } from "../state";

const TIERS: { id: Tier; title: string; text: string }[] = [
  { id: "T2", title: "T2 — orphan index entries", text: "Entries the recorder marked free whose blocks are still intact, validated against the block before being trusted. Fast." },
  { id: "T3", title: "T3 — full signature carve", text: "Frames found by their own signatures, with no index. Slow on a large disk — and where the footage the vendor tool cannot see comes from." },
  { id: "T4", title: "T4 — bad-sector tolerant", text: "Carves around unreadable ranges. Run it if ingest reported read errors." },
];

export function Recover() {
  const c = useCase();
  const ev = c.evidenceId;
  const support = c.evidence?.identification?.support;
  const carveOnly = support === "carve_only";
  const task = useTask<RecoverSummary>(`recover:${ev}`);
  const [tiers, setTiers] = useSessionState<Tier[]>(`recover:tiers:${ev}`, carveOnly ? ["T3"] : ["T2", "T3"]);
  const toggle = (t: Tier) => setTiers(tiers.includes(t) ? tiers.filter((x) => x !== t) : [...tiers, t].sort());
  const summary = task.result ?? c.evidence?.last_recover ?? null;
  const recovered = Boolean(c.evidence?.recovered) || task.result !== null;
  const recordings = useCli<Recording[]>(ev && recovered ? { kind: "listRecordings", case: c.dir, evidence: ev } : null, c.version);
  const coverage = useCli<CoverageRow[]>(ev && recovered ? { kind: "coverage", case: c.dir, evidence: ev } : null, c.version);
  const found = (recordings.data ?? []).filter((r) => r.recovery_tier !== "T1");

  if (!support || support === "pending_selection" || support === "none") {
    return (
      <StepPage step="recover">
        {!support ? (
          <Notice>Identify {ev} first (<a href="#/identify">step 4</a>): recovery uses the format plugin identification selects.</Notice>
        ) : support === "pending_selection" ? (
          <Notice tone="warn">Identification is ambiguous: choose a candidate on the <a href="#/identify">Identify</a> step first.</Notice>
        ) : (
          <Notice title="Not applicable">No format family matched, so there are no signatures to carve with.</Notice>
        )}
      </StepPage>
    );
  }

  return (
    <StepPage
      step="recover"
      lead="A recorder frees an index entry long before it overwrites the block. On a disk pulled from service that window never closes, and this is where the footage vendor tools cannot see comes from."
    >
      <Panel title={recovered ? "Run recovery again" : "Choose the tiers"}>
        <div className="choice-list">
          {TIERS.map((t) => {
            const unavailable = t.id === "T2" && carveOnly;
            return (
              <label key={t.id} className={`choice ${tiers.includes(t.id) && !unavailable ? "active" : ""} ${unavailable ? "disabled" : ""}`}>
                <span className="choice-row">
                  <input type="checkbox" checked={tiers.includes(t.id) && !unavailable} disabled={unavailable || task.busy} onChange={() => toggle(t.id)} />
                  <strong>{t.title}</strong>
                </span>
                <span>{unavailable ? "Needs a parseable index; this evidence is carve-only." : t.text}</span>
              </label>
            );
          })}
        </div>
        {task.error && <Notice tone="error">{task.error}</Notice>}
        <div className="form-actions">
          <Button
            kind={recovered ? "secondary" : "primary"}
            onClick={() => task.run(() => run<RecoverSummary>({ kind: "recover", case: c.dir, evidence: ev!, tiers: tiers.filter((t) => !(t === "T2" && carveOnly)), budgetSeconds: null }))}
            busy={task.busy}
            disabled={!tiers.filter((t) => !(t === "T2" && carveOnly)).length}
          >
            {task.busy ? "Recovering…" : `Run ${tiers.filter((t) => !(t === "T2" && carveOnly)).join(" + ") || "…"}`}
          </Button>
          {task.busy && task.startedAt && <Elapsed since={task.startedAt} />}
        </div>
        <CliHint>{`spectra recover --tiers ${tiers.filter((t) => !(t === "T2" && carveOnly)).join(",") || "T3"} --evidence ${ev}`}</CliHint>
      </Panel>

      {summary && <SummaryPanel s={summary} fresh={Boolean(task.result)} />}

      {recovered && (
        <Panel title={`Recovered recordings (${found.length})`} actions={<span className="muted small">T2–T4 only; the index's own recordings are in step 5</span>}>
          {found.length ? <RecordingTable rows={found} /> : <Empty>Nothing beyond what the index already lists.</Empty>}
        </Panel>
      )}
      {coverage.data?.length ? (
        <Panel title="Coverage map" actions={<span className="muted small">every byte of the image in exactly one bucket (FR-29)</span>}>
          <CoverageBar rows={coverage.data} />
        </Panel>
      ) : null}
    </StepPage>
  );
}

function Elapsed(props: { since: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <span className="muted small">
      {duration((now - props.since) / 1000)} so far · it keeps running if you move to another step
    </span>
  );
}

function SummaryPanel(props: { s: RecoverSummary; fresh: boolean }) {
  const { s } = props;
  return (
    <Panel title={props.fresh ? "Result" : "Last run"}>
      <div className="stat-row">
        <div className="stat"><div className="stat-value">{s.added}</div><div className="stat-label">new recordings</div></div>
        {Object.entries(s.by_tier).map(([tier, n]) => (
          <div key={tier} className="stat"><div className="stat-value">{n}</div><div className="stat-label"><TierBadge tier={tier} /></div></div>
        ))}
        <div className="stat">
          <div className="stat-value">{s.gain_pct === null ? "—" : `+${s.gain_pct}%`}</div>
          <div className="stat-label">recording-minutes over T1 {s.gain_pct === null ? "(no T1 baseline)" : ""}</div>
        </div>
      </div>
      <KeyValues
        rows={[
          ["Tiers run", s.tiers_run.join(", ") || "none"],
          ["T1 baseline", `${s.t1_minutes.toFixed(1)} min`],
          ["Recovered", `${s.recovered_minutes.toFixed(1)} min`],
          ["Merged duplicates", String(s.duplicates_merged)],
          ["Items with no time", s.items_without_time ? <Badge tone="warn">{s.items_without_time} — shown as “time unknown”, ordered by position</Badge> : "0"],
        ]}
      />
      {s.skipped.map((x) => <Notice key={x}>Skipped {x}</Notice>)}
      {s.notes.map((n) => <p key={n} className="muted small">{n}</p>)}
    </Panel>
  );
}

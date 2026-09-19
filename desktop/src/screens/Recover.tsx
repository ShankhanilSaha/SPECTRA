import { useState } from "react";

import type { Tier } from "../../shared/api";
import { run, useCli } from "../api";
import { CoverageBar } from "../components/CoverageBar";
import { Badge, Button, CliHint, Empty, KeyValues, Notice, Page, Panel, TierBadge, useAction } from "../components/ui";
import type { CoverageRow, RecoverSummary } from "../types";
import { useCase } from "../state";

const TIERS: { id: Tier; title: string; text: string }[] = [
  { id: "T2", title: "T2 — orphan index entries", text: "Entries the recorder marked free whose blocks are still intact, validated against the block before being trusted. Fast." },
  { id: "T3", title: "T3 — full signature carve", text: "Frames found by their own signatures, with no index. Slow on a large disk — and where the footage the vendor tool cannot see comes from." },
  { id: "T4", title: "T4 — bad-sector tolerant", text: "Carves around unreadable ranges. Run it if ingest reported read errors." },
];

export function Recover() {
  const c = useCase();
  const [tiers, setTiers] = useState<Tier[]>(["T2", "T3"]);
  const [summary, setSummary] = useState<RecoverSummary | null>(null);
  const coverage = useCli<CoverageRow[]>(c.evidenceId ? { kind: "coverage", case: c.dir, evidence: c.evidenceId } : null, `${c.dir}|${c.evidenceId}|${c.version}`);
  const action = useAction(
    () => run<RecoverSummary>({ kind: "recover", case: c.dir, evidence: c.evidenceId!, tiers, budgetSeconds: null }),
    (s) => {
      setSummary(s);
      c.changed();
    },
  );
  const toggle = (t: Tier) => setTiers(tiers.includes(t) ? tiers.filter((x) => x !== t) : [...tiers, t].sort());

  return (
    <Page step={7} title="Recover deleted footage" lead="A recorder frees an index entry long before it overwrites the block. On a disk pulled from service that window never closes.">
      {!c.evidenceId ? (
        <Empty>Add evidence first.</Empty>
      ) : (
        <>
          <Panel
            title={`Recovery tiers · ${c.evidenceId}`}
            actions={<Button kind="primary" onClick={action.run} busy={action.busy} disabled={!tiers.length}>Run {tiers.join(" + ")}</Button>}
          >
            <div className="choice-list">
              {TIERS.map((t) => (
                <label key={t.id} className={`choice ${tiers.includes(t.id) ? "active" : ""}`}>
                  <span className="choice-row">
                    <input type="checkbox" checked={tiers.includes(t.id)} onChange={() => toggle(t.id)} />
                    <strong>{t.title}</strong>
                  </span>
                  <span>{t.text}</span>
                </label>
              ))}
            </div>
            {action.error && <Notice tone="error">{action.error}</Notice>}
            <CliHint>{`spectra recover --tiers ${tiers.join(",") || "T2,T3"} --evidence ${c.evidenceId}`}</CliHint>
          </Panel>
          {summary && <SummaryPanel s={summary} />}
          <Panel title="Coverage map" actions={<span className="muted small">recomputed on every recovery run</span>}>
            {coverage.data?.length ? <CoverageBar rows={coverage.data} /> : <Empty>No coverage map yet — run recovery.</Empty>}
          </Panel>
        </>
      )}
    </Page>
  );
}

function SummaryPanel(props: { s: RecoverSummary }) {
  const { s } = props;
  return (
    <Panel title="Result">
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
      <p><a href="#/parse">See them in the recording inventory →</a></p>
    </Panel>
  );
}

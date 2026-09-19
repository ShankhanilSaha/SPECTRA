import { useState } from "react";

import { run, useCli } from "../api";
import { HexBytes } from "../components/HexBytes";
import { Badge, Button, CliHint, Empty, Field, Loading, Notice, Page, Panel, useAction } from "../components/ui";
import { hex } from "../format";
import type { Candidate, IdentificationView, Support } from "../types";
import { useCase } from "../state";

const SUPPORT: Record<Support, { label: string; tone: "ok" | "warn" | "error" | "neutral"; text: string }> = {
  parse: { label: "full parse", tone: "ok", text: "The layout is understood: parse the index next." },
  carve_only: {
    label: "carve-only",
    tone: "warn",
    text: "The family is known but this layout is not verified, so it is not parsed. Signature carving (Recover, T3) still works — that is where the footage comes from.",
  },
  pending_selection: {
    label: "choose a candidate",
    tone: "warn",
    text: "More than one family matched. Nothing was selected for you: review the matched bytes and choose deliberately. Your choice and reason are audited.",
  },
  none: {
    label: "unknown",
    tone: "neutral",
    text: "No format family matched. That is a finding, not a failure: carving is still available, and nothing is guessed.",
  },
};

export function Identify() {
  const c = useCase();
  const shown = useCli<IdentificationView>(
    c.evidenceId ? { kind: "identifyShow", case: c.dir, evidence: c.evidenceId } : null,
    `${c.dir}|${c.evidenceId}|${c.version}`,
  );
  const identify = useAction(() => run({ kind: "identify", case: c.dir, evidence: c.evidenceId! }), c.changed);
  const notYet = shown.error?.includes("has not been identified");

  return (
    <Page
      step={4}
      title="Identify"
      lead="The parser is chosen from the bytes on the disk, never the badge on the chassis. Rebrands are most of the market."
    >
      {!c.evidenceId ? (
        <Empty>Add evidence first.</Empty>
      ) : (
        <>
          <Panel
            title={`Identification · ${c.evidenceId}`}
            actions={<Button kind={shown.data ? "secondary" : "primary"} onClick={identify.run} busy={identify.busy}>{shown.data ? "Re-run identification" : "Identify"}</Button>}
          >
            {identify.error && <Notice tone="error">{identify.error}</Notice>}
            {shown.loading && !shown.data ? (
              <Loading />
            ) : notYet ? (
              <Empty>Not identified yet. Identification probes every registered format plugin at fixed offsets and reports every match with the bytes it matched.</Empty>
            ) : shown.error ? (
              <Notice tone="error">{shown.error}</Notice>
            ) : shown.data ? (
              <Result view={shown.data} />
            ) : null}
            <CliHint>{`spectra identify --evidence ${c.evidenceId}`}</CliHint>
          </Panel>
          {shown.data?.support === "pending_selection" && <SelectCandidate view={shown.data} />}
        </>
      )}
    </Page>
  );
}

function Result(props: { view: IdentificationView }) {
  const v = props.view;
  const support = SUPPORT[v.support];
  return (
    <div className="identify">
      <div className="identify-head">
        <div>
          <div className="big-label">Format family</div>
          <div className="big-value">{v.selected_family ?? (v.status === "ambiguous" ? "ambiguous" : "unknown")}</div>
          {v.selected_layout_version && <div className="muted">layout {v.selected_layout_version}</div>}
        </div>
        <div>
          <div className="big-label">Support</div>
          <Badge tone={support.tone}>{support.label}</Badge>
          {v.selection === "operator" && <Badge tone="accent">selected by examiner</Badge>}
        </div>
        <p className="identify-text">{support.text}</p>
      </div>
      {v.candidates.length > 0 && <h3>Candidates ({v.candidates.length})</h3>}
      {v.candidates.map((cand) => <CandidateCard key={cand.family} cand={cand} selected={cand.family === v.selected_family} />)}
      {v.observations.length > 0 && (
        <>
          <h3>Other observations</h3>
          {v.observations.map((o) => (
            <div key={o.offset} className="observation">
              <span className="muted">{hex(o.offset)} — {o.description}</span>
              <HexBytes offset={o.offset} hex={o.hex} />
            </div>
          ))}
        </>
      )}
      {v.errors.map((e) => (
        <Notice key={e.family} tone="error" title={`Probe error in ${e.family} ${e.plugin_version}`}>{e.error_type}: {e.message}</Notice>
      ))}
      <p className="muted small">Brand inference from firmware (FR-06) is not in this prototype; the family above comes from the disk bytes alone.</p>
    </div>
  );
}

function CandidateCard(props: { cand: Candidate; selected: boolean }) {
  const { cand } = props;
  return (
    <div className={`candidate ${props.selected ? "selected" : ""}`}>
      <div className="candidate-head">
        <strong>{cand.family}</strong>
        <span className="muted">layout {cand.layout_version ?? "unrecognised"} · plugin {cand.plugin_version}</span>
        <div className="confidence" title="Confidence">
          <div className="confidence-bar"><div style={{ width: `${Math.round(cand.confidence * 100)}%` }} /></div>
          <span>{cand.confidence.toFixed(2)}</span>
        </div>
        <Badge tone={cand.parse_supported ? "ok" : "warn"}>{cand.parse_supported ? "parse supported" : "carve-only"}</Badge>
      </div>
      {cand.matches.map((m, i) => (
        <div key={i} className="match">
          <div className="match-desc">@ {hex(m.offset)} — {m.description}</div>
          <HexBytes offset={m.offset} hex={m.hex} />
        </div>
      ))}
      {cand.note && <p className="small muted">{cand.note}</p>}
    </div>
  );
}

function SelectCandidate(props: { view: IdentificationView }) {
  const c = useCase();
  const [family, setFamily] = useState(props.view.candidates[0]?.family ?? "");
  const [reason, setReason] = useState("");
  const action = useAction(
    () => run({ kind: "identifySelect", case: c.dir, evidence: c.evidenceId!, family, reason }),
    c.changed,
  );
  return (
    <Panel title="Choose a candidate (audited)">
      <div className="form-grid">
        <Field label="Family">
          <select value={family} onChange={(e) => setFamily(e.target.value)}>
            {props.view.candidates.map((cand) => <option key={cand.family} value={cand.family}>{cand.family} ({cand.confidence.toFixed(2)})</option>)}
          </select>
        </Field>
        <Field label="Reason" hint="Recorded in the audit chain and the report. Say what in the matched bytes decided it." wide>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} />
        </Field>
      </div>
      {action.error && <Notice tone="error">{action.error}</Notice>}
      <div className="form-actions">
        <Button kind="primary" onClick={action.run} busy={action.busy} disabled={!reason.trim()}>Select {family}</Button>
      </div>
    </Panel>
  );
}

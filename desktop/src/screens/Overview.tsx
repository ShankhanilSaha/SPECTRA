import { bytes } from "../format";
import { Badge, Button, CliHint, Hash, KeyValues, Loading, Notice, Panel } from "../components/ui";
import { StepPage } from "../components/StepNav";
import { go, useCase } from "../state";
import { nextStep, stepDef, stepStatus, type StepId } from "../steps";
import type { EvidenceProgress } from "../types";

export function Overview() {
  const c = useCase();
  const meta = c.info?.case;
  return (
    <StepPage step="overview" lead={<span className="path">{c.dir}</span>}>
      <NextCard />
      <Panel title="Evidence items">
        {!c.info ? (
          <Loading />
        ) : c.info.evidence.length === 0 ? (
          <p className="muted">No evidence yet.</p>
        ) : (
          <table className="table">
            <thead>
              <tr><th>ID</th><th>Label</th><th>Provenance</th><th className="num">Size</th><th>Identify</th><th>Parse</th><th>Time</th><th>Recover</th></tr>
            </thead>
            <tbody>
              {c.info.evidence.map((e) => {
                const p = c.info!.progress.evidence.find((x) => x.evidence_id === e.id) ?? null;
                return (
                  <tr key={e.id} className={`clickable ${e.id === c.evidenceId ? "row-selected" : ""}`} onClick={() => c.setEvidenceId(e.id)}>
                    <td><strong>{e.id}</strong><div className="muted small">{e.kind}</div></td>
                    <td>{e.label || <span className="muted">—</span>}</td>
                    <td><Badge tone="accent">class {e.provenance_class}</Badge></td>
                    <td className="num">{bytes(e.capacity_bytes)}</td>
                    {(["identify", "parse", "time", "recover"] as StepId[]).map((s) => (
                      <td key={s}><MiniStatus step={s} ev={p} evidenceId={e.id} /></td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>
      <div className="grid-2">
        <Panel title="Case record">
          {meta ? (
            <KeyValues
              rows={[
                ["Case ID", meta.case_id],
                ["Title", meta.title],
                ["Agency", meta.agency],
                ["FIR / authority", [meta.fir_ref, meta.authority_ref].filter(Boolean).join(" · ")],
                ["Examiner", [meta.examiner_name, meta.examiner_designation].filter(Boolean).join(", ")],
                ["s. 79A reference", meta.examiner_s79a_ref],
                ["Created (UTC)", meta.created_utc],
              ]}
            />
          ) : (
            <Loading />
          )}
        </Panel>
        <AuditPanel />
      </div>
    </StepPage>
  );
}

/** Where the examination stands, and the one thing to do next. */
function NextCard() {
  const c = useCase();
  if (!c.info) return null;
  const next = nextStep(c.info.progress, c.marks, c.evidenceId);
  if (!next) {
    return (
      <div className="next-card done">
        <div>
          <div className="eyebrow">Every step has been run</div>
          <h2>Read the report's §7 negative findings before you sign.</h2>
        </div>
        <Button kind="primary" onClick={() => go("report")}>Open the report step →</Button>
      </div>
    );
  }
  const def = stepDef(next.step);
  const ev = c.info.progress.evidence.find((e) => e.evidence_id === next.evidenceId) ?? null;
  const status = stepStatus(next.step, c.info.progress, ev, c.marks);
  return (
    <div className="next-card">
      <div>
        <div className="eyebrow">
          Next · step {def.n}
          {next.evidenceId ? ` · ${next.evidenceId}` : ""}
        </div>
        <h2>{def.title}</h2>
        <p>{def.purpose} <span className="muted">({status.summary})</span></p>
      </div>
      <Button
        kind="primary"
        onClick={() => {
          if (next.evidenceId) c.setEvidenceId(next.evidenceId);
          go(next.step);
        }}
      >
        Go to {def.title} →
      </Button>
    </div>
  );
}

function MiniStatus(props: { step: StepId; ev: EvidenceProgress | null; evidenceId: string }) {
  const c = useCase();
  const s = stepStatus(props.step, c.info?.progress ?? null, props.ev, c.marks);
  // "Done" is not "good". A step can complete and find nothing, and on this evidence that
  // absence may be the most important thing in the case — so a completed step reads neutral
  // and the tick in the rail carries completion. Colour is reserved for what needs attention.
  const tone = s.warn || s.state === "attention" ? "warn" : "neutral";
  return (
    <button
      type="button"
      className="mini-status"
      onClick={(e) => {
        e.stopPropagation();
        c.setEvidenceId(props.evidenceId);
        go(props.step);
      }}
      title={`Open ${stepDef(props.step).title} for ${props.evidenceId}`}
    >
      <Badge tone={tone}>{s.summary}</Badge>
    </button>
  );
}

function AuditPanel() {
  const c = useCase();
  const v = c.verify;
  return (
    <Panel title="Audit chain" actions={<Button onClick={c.changed} busy={c.verifying}>Verify now</Button>}>
      {!v ? (
        <Loading what="Verifying" />
      ) : (
        <>
          {v.ok ? (
            <Notice tone="ok" title="VERIFIED">
              Every record links to the one before it, the manifest head matches, and every stored artefact still hashes to its recorded digest.
            </Notice>
          ) : (
            <Notice tone="error" title="VERIFICATION FAILED">
              {v.broken_at_seq !== null && <p>The chain breaks at record <strong>{v.broken_at_seq}</strong>.</p>}
              <ul>{v.problems.map((p) => <li key={p}>{p}</li>)}</ul>
            </Notice>
          )}
          {v.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
          <KeyValues
            rows={[
              ["Records checked", String(v.records_checked)],
              ["Head digest", <Hash value={v.head_digest} />],
              ["Artefacts checked", String(v.artifacts_checked)],
            ]}
          />
          <CliHint>{`spectra case verify --case "${c.dir}"`}</CliHint>
        </>
      )}
    </Panel>
  );
}

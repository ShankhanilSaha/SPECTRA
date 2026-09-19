import { bytes } from "../format";
import { Badge, Button, CliHint, Empty, Hash, KeyValues, Loading, Notice, Page, Panel } from "../components/ui";
import { useCase } from "../state";

export function Overview() {
  const c = useCase();
  const meta = c.info?.case;
  return (
    <Page step={1} title="Case" lead={c.dir}>
      {c.infoError && <Notice tone="error">{c.infoError}</Notice>}
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
      <Panel title="Evidence items">
        {!c.info ? (
          <Loading />
        ) : c.info.evidence.length === 0 ? (
          <Empty>
            No evidence yet. <a href="#/evidence">Add evidence</a> — and attach the scene documents first (<a href="#/documents">step 2</a>).
          </Empty>
        ) : (
          <table className="table">
            <thead>
              <tr><th>ID</th><th>Kind</th><th>Provenance</th><th>Label</th><th>Size</th><th>SHA-256</th></tr>
            </thead>
            <tbody>
              {c.info.evidence.map((e) => (
                <tr key={e.id} className={e.id === c.evidenceId ? "row-selected" : ""} onClick={() => c.setEvidenceId(e.id)}>
                  <td><strong>{e.id}</strong></td>
                  <td>{e.kind}</td>
                  <td><Badge tone="accent">class {e.provenance_class}</Badge></td>
                  <td>{e.label || <span className="muted">—</span>}</td>
                  <td className="num">{bytes(e.capacity_bytes)}</td>
                  <td><Hash value={e.sha256} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </Page>
  );
}

function AuditPanel() {
  const c = useCase();
  const v = c.verify;
  return (
    <Panel title="Audit chain" actions={<Button onClick={c.changed}>Verify now</Button>}>
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
              ["Head record", String(v.head_seq)],
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

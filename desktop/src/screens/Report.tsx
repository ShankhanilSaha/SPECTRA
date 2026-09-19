import { useState } from "react";

import { bridge, run, useCli } from "../api";
import { Badge, Button, CliHint, Empty, Field, Hash, KeyValues, Loading, Notice, Page, Panel, SeverityBadge, useAction } from "../components/ui";
import type { Certificate, Findings, ReportResult } from "../types";
import { useCase } from "../state";

export function Report() {
  const c = useCase();
  const findings = useCli<Findings>({ kind: "reportFindings", case: c.dir }, `${c.dir}|${c.version}`);
  return (
    <Page step={11} title="Report" lead="Twelve sections, generated from the case. Read §7 first: it says what the tool could not read.">
      <Generate />
      <CertificatePanel />
      <Panel
        title="§7 Negative findings"
        actions={<span className="muted small">generated, not written — you can add to it, never remove from it (FR-81)</span>}
      >
        {findings.loading && !findings.data ? (
          <Loading what="Building findings" />
        ) : findings.error ? (
          <Notice tone="error">{findings.error}</Notice>
        ) : findings.data ? (
          <>
            {!findings.data.integrity.audit_ok && (
              <Notice tone="error" title="The audit chain does not verify">The report says so on its first page.</Notice>
            )}
            <div className="stat-row">
              {(["serious", "attention", "info"] as const).map((s) => (
                <div key={s} className="stat">
                  <div className="stat-value">{findings.data!.negative_summary.by_severity[s] ?? 0}</div>
                  <div className="stat-label"><SeverityBadge severity={s} /></div>
                </div>
              ))}
            </div>
            {findings.data.negative_findings.length === 0 ? (
              <Notice tone="warn">No negative findings. That is unusual for a forensic examination — check the coverage map and the time model.</Notice>
            ) : (
              <ul className="findings">
                {findings.data.negative_findings.map((f, i) => (
                  <li key={`${f.code}-${i}`} className={`finding finding-${f.severity}`}>
                    <div className="finding-head">
                      <SeverityBadge severity={f.severity} />
                      <strong>{f.title}</strong>
                      <code className="muted">{f.code}</code>
                      {f.evidence_id && <Badge>{f.evidence_id}</Badge>}
                    </div>
                    <p>{f.detail}</p>
                  </li>
                ))}
              </ul>
            )}
            <KeyValues rows={[["Conclusions digest", <Hash value={findings.data.conclusions_digest} />]]} />
            <p className="muted small">The conclusions digest covers what was found on the disk: a second examiner running the same version on the same image reproduces it (AC-11).</p>
          </>
        ) : null}
      </Panel>
    </Page>
  );
}

function Generate() {
  const c = useCase();
  const [out, setOut] = useState("");
  const [agency, setAgency] = useState(c.info?.case.agency ?? "");
  const [letterhead, setLetterhead] = useState("");
  const [footer, setFooter] = useState("");
  const [pdf, setPdf] = useState(false);
  const [result, setResult] = useState<ReportResult | null>(null);
  const action = useAction(
    () => run<ReportResult>({ kind: "reportGenerate", case: c.dir, out, agency, letterhead, footer, pdf }),
    (r) => {
      setResult(r);
      c.changed();
    },
  );
  return (
    <div className="grid-2">
      <Panel title="Generate the report">
        <div className="form-grid">
          <Field label="Output folder" wide>
            <div className="path-row">
              <input value={out} readOnly placeholder="Choose a folder" />
              <Button onClick={async () => setOut((await bridge.pick({ title: "Folder for the report", kind: "directory", create: true })) ?? out)}>Choose…</Button>
            </div>
          </Field>
          <Field label="Agency (letterhead)"><input value={agency} onChange={(e) => setAgency(e.target.value)} /></Field>
          <Field label="Letterhead line"><input value={letterhead} onChange={(e) => setLetterhead(e.target.value)} /></Field>
          <Field label="Footer note" wide><input value={footer} onChange={(e) => setFooter(e.target.value)} /></Field>
          <label className="check">
            <input type="checkbox" checked={pdf} onChange={(e) => setPdf(e.target.checked)} /> Also render PDF (needs WeasyPrint)
          </label>
        </div>
        {action.error && <Notice tone="error">{action.error}</Notice>}
        <div className="form-actions">
          <Button kind="primary" onClick={action.run} busy={action.busy} disabled={!out}>Generate report</Button>
        </div>
        <CliHint>{`spectra report generate --out <folder>${pdf ? "" : " --no-pdf"}`}</CliHint>
      </Panel>
      <Panel title="Generated">
        {result ? (
          <>
            {!result.audit_ok && <Notice tone="error">The audit chain did not verify; the report states it on page one.</Notice>}
            {result.pdf_error && <Notice tone="warn">{result.pdf_error}</Notice>}
            <KeyValues
              rows={[
                ["Report (HTML)", result.html],
                ["HTML SHA-256", <Hash value={result.html_sha256} />],
                ["PDF", result.pdf ?? "not rendered"],
                ["findings.json", <Hash value={result.findings_digest} />],
                ["Negative findings", `${result.negative_summary.total} (${result.negative_summary.by_severity.serious ?? 0} serious)`],
              ]}
            />
            <div className="form-actions">
              <Button kind="primary" onClick={() => void bridge.openReport(result.html)}>Open report</Button>
              <Button onClick={() => void bridge.showInFolder(result.html)}>Show in folder</Button>
            </div>
            <p className="muted small">The BSA s. 63(4) certificate is a separate document enclosed with the report — prepare it below.</p>
          </>
        ) : (
          <Empty>Not generated in this session yet.</Empty>
        )}
      </Panel>
    </div>
  );
}

function CertificatePanel() {
  const c = useCase();
  const [cert, setCert] = useState<Certificate | null>(null);
  const action = useAction(() => run<Certificate>({ kind: "reportCertificate", case: c.dir, evidence: c.evidenceId! }), setCert);
  const body = cert?.certificate;
  return (
    <Panel
      title={`BSA s. 63(4) certificate${c.evidenceId ? ` · ${c.evidenceId}` : ""}`}
      actions={<Button onClick={action.run} busy={action.busy} disabled={!c.evidenceId}>{cert ? "Refresh" : "Prepare certificate"}</Button>}
    >
      {action.error && <Notice tone="error">{action.error}</Notice>}
      {!body ? (
        <Empty>Pre-fills the Schedule to s. 63(4) with the record, the device and the hash values. Signatures are left blank: it is a sworn statement by two named people, required at each instance of submission.</Empty>
      ) : (
        <div className="certificate">
          <div className="cert-heading">{body.heading}</div>
          <div className="muted small">{body.authority} · case {body.case_id} · instance {body.instance}</div>
          <KeyValues
            rows={[
              ["Record", body.record_description],
              ["Source type", body.device.source_type],
              ["Device", body.device.other_information],
              ["Hash boxes ticked", body.hashes.ticked.join(", ") || "none"],
              ["SHA-256", <Hash value={body.hashes.SHA256} />],
              ["MD5", <Hash value={body.hashes.MD5} />],
              ["Enclosure", body.hash_report_ref],
            ]}
          />
          {body.unsigned.map((u) => <Badge key={u} tone="warn">UNSIGNED: {u}</Badge>)}
          {cert!.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
          <CliHint>{`spectra report certificate --evidence ${c.evidenceId} --out <folder>`}</CliHint>
        </div>
      )}
    </Panel>
  );
}

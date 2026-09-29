import { useState } from "react";

import { bridge, errorText, run, useCli } from "../api";
import { StepPage } from "../components/StepNav";
import { Badge, Button, CliHint, Empty, Field, Hash, KeyValues, Loading, Notice, Panel, SeverityBadge } from "../components/ui";
import type { Certificate, Findings, ReportResult } from "../types";
import { useCase, useSessionState, useTask } from "../state";

export function Report() {
  const c = useCase();
  const findings = useCli<Findings>({ kind: "reportFindings", case: c.dir }, c.version);
  return (
    <StepPage step="report" lead="Twelve sections, generated from the case. Read §7 first: it says what the tool could not read, and you can add to it but never remove from it.">
      <Generate />
      <Panel
        title="§7 Negative findings"
        actions={<span className="muted small">generated, not written (FR-81)</span>}
      >
        {findings.loading ? (
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
      <CertificatePanel />
    </StepPage>
  );
}

function Generate() {
  const c = useCase();
  const sep = c.dir.includes("\\") ? "\\" : "/";
  const [form, setForm] = useSessionState("report:form", {
    out: `${c.dir.replace(/[\\/]+$/, "")}${sep}reports`,
    agency: c.info?.case.agency ?? "",
    letterhead: "",
    footer: "",
    pdf: false,
  });
  const task = useTask<ReportResult>("report");
  const [openError, setOpenError] = useState<string | null>(null);
  const result = task.result;
  const generated = c.info?.progress.reports_generated ?? 0;
  const lastHtml = result?.html ?? (generated ? `${form.out}${sep}report-${c.info?.case.case_id}.html` : null);

  const open = async (html: string) => {
    setOpenError(null);
    try {
      await bridge.openReport(html);
    } catch (err) {
      setOpenError(`${errorText(err)}: ${html}`);
    }
  };

  return (
    <div className="grid-2">
      <Panel title={generated ? "Generate again" : "Generate the report"}>
        <div className="form-grid">
          <Field label="Output folder" hint="Inside the case folder by default, beside everything it describes" wide>
            <div className="path-row">
              <input value={form.out} readOnly />
              <Button onClick={async () => {
                const out = await bridge.pick({ title: "Folder for the report", kind: "directory", create: true, defaultPath: form.out });
                if (out) setForm({ ...form, out });
              }}>Change…</Button>
            </div>
          </Field>
          <Field label="Agency (letterhead)"><input value={form.agency} onChange={(e) => setForm({ ...form, agency: e.target.value })} /></Field>
          <Field label="Letterhead line"><input value={form.letterhead} onChange={(e) => setForm({ ...form, letterhead: e.target.value })} /></Field>
          <Field label="Footer note" wide><input value={form.footer} onChange={(e) => setForm({ ...form, footer: e.target.value })} /></Field>
          <label className="check">
            <input type="checkbox" checked={form.pdf} onChange={(e) => setForm({ ...form, pdf: e.target.checked })} /> Also render PDF (needs WeasyPrint)
          </label>
        </div>
        {task.error && <Notice tone="error">{task.error}</Notice>}
        <div className="form-actions">
          <Button
            kind={generated ? "secondary" : "primary"}
            onClick={() => task.run(() => run<ReportResult>({ kind: "reportGenerate", case: c.dir, ...form }))}
            busy={task.busy}
          >
            {task.busy ? "Generating…" : generated ? "Generate again" : "Generate report"}
          </Button>
        </div>
        <CliHint>{`spectra report generate --out "${form.out}"${form.pdf ? "" : " --no-pdf"}`}</CliHint>
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
          </>
        ) : generated ? (
          <p className="muted">Generated {generated === 1 ? "once" : `${generated} times`}, last at {c.info?.progress.last_report_utc}.</p>
        ) : (
          <Empty>Not generated yet.</Empty>
        )}
        {openError && <Notice tone="error">{openError}</Notice>}
        {lastHtml && (
          <div className="form-actions">
            <Button kind="primary" onClick={() => void open(lastHtml)}>Open report</Button>
            <Button onClick={() => void bridge.showInFolder(lastHtml)}>Show in folder</Button>
          </div>
        )}
        <p className="muted small">The BSA s. 63(4) certificate is a separate document enclosed with the report — prepare it below.</p>
      </Panel>
    </div>
  );
}

function CertificatePanel() {
  const c = useCase();
  const ev = c.evidenceId;
  const task = useTask<Certificate>(`certificate:${ev}`);
  const cert = task.result;
  const body = cert?.certificate;
  const items = c.info?.evidence ?? [];
  return (
    <Panel
      title="BSA s. 63(4) certificate"
      actions={
        <>
          {items.length > 1 && (
            <select value={ev ?? ""} onChange={(e) => c.setEvidenceId(e.target.value)} aria-label="Evidence item">
              {items.map((e) => <option key={e.id} value={e.id}>{e.id}{e.label ? ` · ${e.label}` : ""}</option>)}
            </select>
          )}
          <Button
            onClick={() => task.run(() => run<Certificate>({ kind: "reportCertificate", case: c.dir, evidence: ev! }))}
            busy={task.busy}
            disabled={!ev}
          >
            {cert ? "Refresh" : `Prepare for ${ev ?? "…"}`}
          </Button>
        </>
      }
    >
      {task.error && <Notice tone="error">{task.error}</Notice>}
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
          <CliHint>{`spectra report certificate --evidence ${ev} --out <folder>`}</CliHint>
        </div>
      )}
    </Panel>
  );
}

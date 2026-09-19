import { run, useCli } from "../api";
import { CoverageBar } from "../components/CoverageBar";
import { RecordingTable } from "../components/RecordingTable";
import { Button, CliHint, Empty, Loading, Notice, Page, Panel, useAction } from "../components/ui";
import type { CoverageRow, IdentificationView, Recording } from "../types";
import { useCase } from "../state";

export function Parse() {
  const c = useCase();
  const key = `${c.dir}|${c.evidenceId}|${c.version}`;
  const ident = useCli<IdentificationView>(c.evidenceId ? { kind: "identifyShow", case: c.dir, evidence: c.evidenceId } : null, key);
  const recordings = useCli<Recording[]>(c.evidenceId ? { kind: "listRecordings", case: c.dir, evidence: c.evidenceId } : null, key);
  const coverage = useCli<CoverageRow[]>(c.evidenceId ? { kind: "coverage", case: c.dir, evidence: c.evidenceId } : null, key);
  const parse = useAction(() => run({ kind: "parse", case: c.dir, evidence: c.evidenceId! }), c.changed);
  const support = ident.data?.support;

  return (
    <Page step={5} title="Parse & recordings" lead="Walk the recorder's own index through the selected plugin (T1). Recovered items join this inventory, badged by tier.">
      {!c.evidenceId ? (
        <Empty>Add evidence first.</Empty>
      ) : (
        <>
          <Panel
            title="Parse the index"
            actions={<Button kind="primary" onClick={parse.run} busy={parse.busy} disabled={support !== "parse"}>Parse {c.evidenceId}</Button>}
          >
            {ident.error ? (
              <Notice>Identify this evidence first (<a href="#/identify">step 4</a>).</Notice>
            ) : support === "carve_only" ? (
              <Notice tone="warn" title="Carve-only">
                The layout of this {ident.data?.selected_family} evidence is not verified, so it is not parsed — a mis-parse would produce a confident wrong timeline. Go to <a href="#/recover">Recover</a> and run T3: the frame headers carry channel and time.
              </Notice>
            ) : support === "pending_selection" ? (
              <Notice tone="warn">Identification is ambiguous: choose a candidate on the <a href="#/identify">Identify</a> page first.</Notice>
            ) : support === "none" ? (
              <Notice>No format family matched, so there is no index to parse. Carving may still recover footage.</Notice>
            ) : (
              <p className="muted">Parsing reads the layout and enumerates the recordings the index describes. Times stay device-local until you set the time model (step 6).</p>
            )}
            {parse.error && <Notice tone="error">{parse.error}</Notice>}
            <CliHint>{`spectra parse --evidence ${c.evidenceId}`}</CliHint>
          </Panel>
          <Panel title={`Recording inventory · ${c.evidenceId}`}>
            {recordings.loading && !recordings.data ? (
              <Loading />
            ) : recordings.error ? (
              <Notice tone="error">{recordings.error}</Notice>
            ) : recordings.data?.length ? (
              <RecordingTable rows={recordings.data} />
            ) : (
              <Empty>No recordings yet.</Empty>
            )}
          </Panel>
          <Panel title="Coverage map" actions={<span className="muted small">every byte of the image in exactly one bucket (FR-29)</span>}>
            {coverage.data?.length ? (
              <CoverageBar rows={coverage.data} />
            ) : (
              <Empty>No coverage map yet — it is written by Recover (step 7).</Empty>
            )}
          </Panel>
        </>
      )}
    </Page>
  );
}

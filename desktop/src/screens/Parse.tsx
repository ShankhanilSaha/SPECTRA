import { run, useCli } from "../api";
import { CoverageBar } from "../components/CoverageBar";
import { RecordingTable } from "../components/RecordingTable";
import { StepPage } from "../components/StepNav";
import { Button, CliHint, Empty, Loading, Notice, Panel } from "../components/ui";
import type { CoverageRow, Recording } from "../types";
import { useCase, useTask } from "../state";

export function Parse() {
  const c = useCase();
  const ev = c.evidenceId;
  const ident = c.evidence?.identification ?? null;
  const support = ident?.support;
  const task = useTask(`parse:${ev}`);
  // The finished task counts until the case's progress has caught up with it.
  const parsed = Boolean(c.evidence?.parsed) || task.result !== null;
  const recordings = useCli<Recording[]>(ev && parsed ? { kind: "listRecordings", case: c.dir, evidence: ev } : null, c.version);
  const coverage = useCli<CoverageRow[]>(ev && parsed ? { kind: "coverage", case: c.dir, evidence: ev } : null, c.version);

  let body;
  if (!ident) {
    body = <Notice>Identify {ev} first (<a href="#/identify">step 4</a>): the parser is chosen from what identification finds.</Notice>;
  } else if (support === "pending_selection") {
    body = <Notice tone="warn">Identification is ambiguous: choose a candidate on the <a href="#/identify">Identify</a> step first.</Notice>;
  } else if (support === "carve_only") {
    body = (
      <Notice title="Not applicable to this evidence — continue to the time model">
        {ident.family} is a known family, but this layout is not verified, so there is no index SPECTRA will trust. That is deliberate: a mis-parse
        would produce a confident wrong timeline. The frame headers still carry channel and time, so Recover (step 7) carves the footage out
        by signature instead.
      </Notice>
    );
  } else if (support === "none") {
    body = <Notice title="Not applicable">No format family matched, so there is no index to parse.</Notice>;
  } else if (!parsed) {
    body = (
      <Panel title="Parse the index">
        {task.error && <Notice tone="error">{task.error}</Notice>}
        <div className="cta">
          <p>
            Reads the {ident.family} layout ({ident.layout_version}) and lists every recording the recorder's own index describes (T1).
            Times stay device-local until the time model is set in step 6.
          </p>
          <Button kind="primary" onClick={() => task.run(() => run({ kind: "parse", case: c.dir, evidence: ev! }))} busy={task.busy}>
            {task.busy ? "Parsing…" : `Parse ${ev}`}
          </Button>
        </div>
        <CliHint>{`spectra parse --evidence ${ev}`}</CliHint>
      </Panel>
    );
  } else {
    body = (
      <>
        <Panel title={`Recordings from the index · ${ev}`}>
          {recordings.loading ? (
            <Loading />
          ) : recordings.error ? (
            <Notice tone="error">{recordings.error}</Notice>
          ) : recordings.data?.length ? (
            <RecordingTable rows={recordings.data} />
          ) : (
            <Empty>The index lists no recordings.</Empty>
          )}
        </Panel>
        {coverage.data?.length ? (
          <Panel title="Coverage map" actions={<span className="muted small">every byte of the image in exactly one bucket (FR-29)</span>}>
            <CoverageBar rows={coverage.data} />
          </Panel>
        ) : null}
      </>
    );
  }

  return <StepPage step="parse">{body}</StepPage>;
}

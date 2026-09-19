import { useState } from "react";

import { bridge, run, useCli } from "../api";
import { RecordingPicker } from "../components/RecordingPicker";
import { Badge, Button, CliHint, Empty, Hash, KeyValues, Loading, Notice, Page, Panel, useAction } from "../components/ui";
import { bytes } from "../format";
import type { ArtifactRow, ExportResult, Recording } from "../types";
import { routeParam, useCase } from "../state";

export function ExportScreen() {
  const c = useCase();
  const recordings = useCli<Recording[]>({ kind: "listRecordings", case: c.dir, evidence: null }, `${c.dir}|${c.version}`);
  const [rec, setRec] = useState<string | null>(routeParam("rec"));
  const artifacts = useCli<ArtifactRow[]>(rec ? { kind: "listArtifacts", case: c.dir, recording: rec } : null, `${c.dir}|${rec}|${c.version}`);
  const [result, setResult] = useState<ExportResult | null>(null);
  const exportAction = useAction(
    (out: string | null) => run<ExportResult>({ kind: "exportClip", case: c.dir, recording: rec!, out }),
    (r) => {
      setResult(r);
      c.changed();
    },
  );
  const mp4 = [...(artifacts.data ?? [])].reverse().find((a) => a.kind === "mp4_evidence");

  return (
    <Page step={10} title="Review & export" lead="The disk image is the evidence. The MP4 is a verified extraction of it: remuxed, never transcoded, with every coded picture checked identical to the elementary stream.">
      <Panel title="Recording">
        {recordings.loading && !recordings.data ? (
          <Loading />
        ) : !recordings.data?.length ? (
          <Empty>No recordings yet.</Empty>
        ) : (
          <div className="path-row">
            <RecordingPicker rows={recordings.data} value={rec} onChange={(id) => { setRec(id); setResult(null); }} />
            <Button kind="primary" onClick={() => exportAction.run(null)} busy={exportAction.busy} disabled={!rec}>Export evidence copy</Button>
            <Button
              onClick={async () => {
                const out = await bridge.pick({ title: "Copy the clip and its manifest to…", kind: "directory", create: true });
                if (out) await exportAction.run(out);
              }}
              disabled={!rec || exportAction.busy}
            >
              Export and copy out…
            </Button>
          </div>
        )}
        {exportAction.error && <Notice tone="error">{exportAction.error}</Notice>}
        {rec && <CliHint>{`spectra export clip --recording ${rec}`}</CliHint>}
      </Panel>

      <div className="grid-2">
        <Panel title="Player">
          {mp4 ? (
            <>
              <video key={mp4.sha256} className="player" controls src={bridge.artifactUrl(mp4.sha256)} />
              <div className="player-caption">
                <Badge tone="ok">evidence copy</Badge> remuxed MP4 · VCL NAL units verified identical to the ES · <Hash label="SHA-256" value={mp4.sha256} />
              </div>
            </>
          ) : result && !result.mp4 ? (
            <Notice tone="warn" title="No MP4 for this recording">{result.mp4_skipped_reason} The elementary stream was still extracted and hashed.</Notice>
          ) : (
            <Empty>Export the recording to play its evidence copy here.</Empty>
          )}
        </Panel>
        <Panel title="Result">
          {result ? (
            <>
              <KeyValues
                rows={[
                  ["Frames", String(result.frames)],
                  ["Elementary stream", <Hash value={result.es.sha256} />],
                  ["ES MD5", <Hash value={result.es.md5} />],
                  ["Evidence MP4", result.mp4 ? <Hash value={result.mp4.sha256} /> : <Badge tone="warn">not produced</Badge>],
                  ["Verification", result.vcl ? `${result.vcl.result} — ${result.vcl.vcl_units} coded pictures` : "—"],
                ]}
              />
              {result.vcl && <p className="small muted">{result.vcl.method}</p>}
              {result.copied_to.length > 0 && (
                <div className="copied">
                  {result.copied_to.map((p) => (
                    <div key={p}><button type="button" className="link" onClick={() => void bridge.showInFolder(p)}>{p}</button></div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <Empty>Nothing exported in this session yet.</Empty>
          )}
        </Panel>
      </div>

      <Panel title="Stored artefacts for this recording">
        {artifacts.data?.length ? (
          <table className="table">
            <thead><tr><th>Kind</th><th>Created (UTC)</th><th className="num">Size</th><th>SHA-256</th></tr></thead>
            <tbody>
              {artifacts.data.map((a) => (
                <tr key={a.sha256}>
                  <td>{a.kind}{a.is_derivative && <Badge tone="warn">derivative</Badge>}</td>
                  <td>{a.created_utc}</td>
                  <td className="num">{bytes(a.size_bytes)}</td>
                  <td><Hash value={a.sha256} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No artefacts yet.</Empty>
        )}
      </Panel>
    </Page>
  );
}

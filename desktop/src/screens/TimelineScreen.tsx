import { useMemo, useState } from "react";

import { useCli } from "../api";
import { Timeline, type TLLane, type TLSyncGap } from "../components/Timeline";
import { Badge, Button, Empty, KeyValues, Loading, Notice, Page, Panel, TierBadge } from "../components/ui";
import { channelLabel, deviceTime, duration, notesOf, toMs, utcTime } from "../format";
import type { GapReport, Recording, TimelineData } from "../types";
import { useCase } from "../state";

type Axis = "reference" | "device";

function laneKey(evidence: string, channel: number | null): string {
  return `${evidence}|${channel ?? "?"}`;
}

export function TimelineScreen() {
  const c = useCase();
  const key = `${c.dir}|${c.version}`;
  const timeline = useCli<TimelineData>({ kind: "timeline", case: c.dir }, key);
  const gaps = useCli<GapReport>({ kind: "gaps", case: c.dir, minGap: 1 }, key);
  const recordings = useCli<Recording[]>({ kind: "listRecordings", case: c.dir, evidence: null }, key);
  const hasReference = Boolean(timeline.data?.lanes.length);
  const [axisChoice, setAxis] = useState<Axis | null>(null);
  const axis: Axis = axisChoice ?? (hasReference ? "reference" : "device");
  const [selected, setSelected] = useState<string | null>(null);

  const byId = useMemo(() => new Map((recordings.data ?? []).map((r) => [r.id, r])), [recordings.data]);

  const built = useMemo(() => {
    if (!timeline.data || !gaps.data || !recordings.data) return null;
    if (axis === "reference") return referenceLanes(timeline.data, gaps.data, byId);
    return deviceLanes(c.evidenceId, recordings.data, gaps.data);
  }, [axis, timeline.data, gaps.data, recordings.data, byId, c.evidenceId]);

  const loading = timeline.loading || gaps.loading || recordings.loading;
  const error = timeline.error || gaps.error || recordings.error;
  const picked = selected ? byId.get(selected) : undefined;

  return (
    <Page step={8} title="Timeline" lead="Look at the gaps first. A gap on every channel at once usually means a power event or a deliberate interruption — often the most important finding in the case.">
      <Panel
        title="Channels on one axis"
        actions={
          <div className="segmented" role="radiogroup" aria-label="Time axis">
            <button type="button" className={axis === "reference" ? "active" : ""} onClick={() => setAxis("reference")} disabled={!hasReference}>
              Reference (UTC) · all devices
            </button>
            <button type="button" className={axis === "device" ? "active" : ""} onClick={() => setAxis("device")} disabled={!c.evidenceId}>
              Device clock · {c.evidenceId ?? "—"}
            </button>
          </div>
        }
      >
        {axis === "device" && (
          <Notice tone="warn" title="Absolute time not established on this axis">
            This is the recorder's own wall clock for {c.evidenceId}. Only relative order and gaps within this one device are meaningful here; it cannot be compared with another device.
          </Notice>
        )}
        {!hasReference && axis === "reference" && <Notice>No evidence has a clock offset yet — set one in the Time model (step 6).</Notice>}
        {error && <Notice tone="error">{error}</Notice>}
        {loading && !built ? (
          <Loading />
        ) : built && built.lanes.length ? (
          <Timeline lanes={built.lanes} syncGaps={built.syncGaps} selected={selected} onSelect={setSelected} axisLabel={built.axisLabel} />
        ) : (
          <Empty>No recordings with a time on this axis.</Empty>
        )}
      </Panel>
      <div className="grid-2">
        <Panel title="Selected recording">
          {picked ? <RecordingDetail r={picked} /> : <Empty>Click a recording on the timeline.</Empty>}
        </Panel>
        <Panel title="Not on this axis" actions={<span className="muted small">never positioned by guesswork</span>}>
          {built && built.unplaced.length ? (
            <table className="table">
              <thead><tr><th>Recording</th><th>Channel</th><th>Why</th></tr></thead>
              <tbody>
                {built.unplaced.map((u) => (
                  <tr key={u.recording_id} className="clickable" onClick={() => setSelected(u.recording_id)}>
                    <td className="nowrap">{u.recording_id}</td><td className="nowrap">{channelLabel(u.channel)}</td><td className="small">{u.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>Every recording is placed.</Empty>
          )}
        </Panel>
      </div>
    </Page>
  );
}

interface Built {
  lanes: TLLane[];
  syncGaps: TLSyncGap[];
  unplaced: { recording_id: string; channel: number | null; reason: string }[];
  axisLabel: string;
}

function gapTitle(g: { start: string; end: string; duration_s: number; uncertainty_s: number }, all: boolean, fmt: (s: string) => string) {
  return `${all ? "No recording on ANY channel" : "No recording"} · ${duration(g.duration_s)} · ${fmt(g.start)} → ${fmt(g.end)} · ±${g.uncertainty_s} s`;
}

function referenceLanes(t: TimelineData, gaps: GapReport, byId: Map<string, Recording>): Built {
  const lanes: TLLane[] = t.lanes.map((lane) => ({
    key: laneKey(lane.evidence_id, lane.channel),
    label: `${lane.evidence_id} · ${channelLabel(lane.channel)}`,
    sub: `±${Math.max(0, ...lane.segments.map((s) => s.uncertainty_s))} s`,
    segments: lane.segments.map((s) => {
      const rec = byId.get(s.recording_id);
      return {
        id: s.recording_id, start: toMs(s.start), end: toMs(s.end), uncertainty: s.uncertainty_s,
        tier: rec?.recovery_tier ?? null,
        title: `${s.recording_id} · ${rec?.recovery_tier ?? ""} · ${utcTime(s.start)} → ${utcTime(s.end)} · ±${s.uncertainty_s} s`,
      };
    }),
    gaps: [],
  }));
  const syncGaps: TLSyncGap[] = [];
  for (const entry of gaps.evidence.filter((e) => e.basis === "reference")) {
    for (const g of entry.channel_gaps) {
      lanes.find((l) => l.key === laneKey(entry.evidence_id, g.channel))?.gaps.push({ start: toMs(g.start), end: toMs(g.end), title: gapTitle(g, false, utcTime) });
    }
    const own = lanes.filter((l) => l.key.startsWith(`${entry.evidence_id}|`)).map((l) => l.key);
    for (const g of entry.synchronised_gaps) {
      syncGaps.push({ start: toMs(g.start), end: toMs(g.end), title: gapTitle(g, true, utcTime), laneKeys: own });
    }
  }
  return { lanes, syncGaps, unplaced: t.unplaced, axisLabel: "Reference axis · UTC · per-device uncertainty applied" };
}

function deviceLanes(evidence: string | null, recs: Recording[], gaps: GapReport): Built {
  const mine = recs.filter((r) => r.evidence_id === evidence);
  const lanes = new Map<string, TLLane>();
  const unplaced: Built["unplaced"] = [];
  for (const r of mine) {
    if (!r.t_local_start || !r.t_local_end || toMs(r.t_local_end) < toMs(r.t_local_start)) {
      unplaced.push({
        recording_id: r.id, channel: r.channel,
        reason: !r.t_local_start ? "time unknown — ordered by physical position only" : "the recorded end is earlier than the start; reported as a time anomaly",
      });
      continue;
    }
    const k = laneKey(r.evidence_id, r.channel);
    if (!lanes.has(k)) lanes.set(k, { key: k, label: `${r.evidence_id} · ${channelLabel(r.channel)}`, sub: "device clock", segments: [], gaps: [] });
    lanes.get(k)!.segments.push({
      id: r.id, start: toMs(r.t_local_start), end: toMs(r.t_local_end), uncertainty: 0, tier: r.recovery_tier,
      title: `${r.id} · ${r.recovery_tier} · ${deviceTime(r.t_local_start)} → ${deviceTime(r.t_local_end)} (device clock)`,
    });
  }
  const ordered = [...lanes.values()].sort((a, b) => a.key.localeCompare(b.key, undefined, { numeric: true }));
  const syncGaps: TLSyncGap[] = [];
  const entry = gaps.evidence.find((e) => e.evidence_id === evidence && e.basis === "device-local");
  if (entry) {
    for (const g of entry.channel_gaps) {
      lanes.get(laneKey(entry.evidence_id, g.channel))?.gaps.push({ start: toMs(g.start), end: toMs(g.end), title: gapTitle(g, false, deviceTime) });
    }
    for (const g of entry.synchronised_gaps) {
      syncGaps.push({ start: toMs(g.start), end: toMs(g.end), title: gapTitle(g, true, deviceTime), laneKeys: ordered.map((l) => l.key) });
    }
  }
  return { lanes: ordered, syncGaps, unplaced, axisLabel: `Device clock of ${evidence} · not UTC · absolute time not established` };
}

function RecordingDetail(props: { r: Recording }) {
  const { r } = props;
  const notes = notesOf(r.notes_json);
  return (
    <>
      <KeyValues
        rows={[
          ["Recording", <strong>{r.id}</strong>],
          ["Evidence · channel", `${r.evidence_id} · ${channelLabel(r.channel)}`],
          ["Tier · confidence", <><TierBadge tier={r.recovery_tier} /> {r.confidence.toFixed(2)}</>],
          ["Device clock", `${deviceTime(r.t_local_start)} → ${deviceTime(r.t_local_end)}`],
          ["Reference", r.t_ref_start ? `${utcTime(r.t_ref_start)} → ${utcTime(r.t_ref_end)} ±${r.t_uncertainty_s} s` : <Badge tone="warn">not established</Badge>],
          ["Frames · codec", `${r.frame_count ?? "—"} · ${r.codec ?? "—"}`],
        ]}
      />
      {notes.length > 0 && <ul className="small notes">{notes.map((n) => <li key={n}>{n}</li>)}</ul>}
      <div className="form-actions">
        <Button kind="primary" onClick={() => (window.location.hash = `#/export?rec=${r.id}`)}>Export & play →</Button>
        <Button onClick={() => (window.location.hash = `#/analytics?rec=${r.id}`)}>Motion analysis →</Button>
      </div>
    </>
  );
}

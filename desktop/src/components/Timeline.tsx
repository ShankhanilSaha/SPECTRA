/**
 * Multi-channel timeline (FR-57, doc 7 §4 step 8). Lanes on one axis; the axis is either the
 * reference (UTC) axis or, where no offset is established, the device's own clock — and the
 * caller labels which. Nothing here computes a time: it draws what the CLI returned.
 */

import { useEffect, useMemo, useRef, useState, type PointerEvent, type WheelEvent } from "react";

import { duration, stamp } from "../format";

export interface TLSegment {
  id: string;
  start: number;
  end: number;
  uncertainty: number;
  tier: string | null;
  title: string;
}

export interface TLSpan {
  start: number;
  end: number;
  title: string;
}

/** A gap on every channel of one recorder: drawn across that recorder's lanes only. */
export interface TLSyncGap extends TLSpan {
  laneKeys: string[];
}

export interface TLLane {
  key: string;
  label: string;
  sub?: string;
  segments: TLSegment[];
  gaps: TLSpan[];
}

const LABEL_W = 170;
const AXIS_H = 34;
const LANE_H = 38;
const STEPS_S = [
  1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400,
  172800, 604800,
];

function niceStep(spanMs: number, width: number): number {
  const target = spanMs / Math.max(1, width / 110);
  for (const s of STEPS_S) if (s * 1000 >= target) return s * 1000;
  return STEPS_S[STEPS_S.length - 1] * 1000;
}

export function Timeline(props: {
  lanes: TLLane[];
  syncGaps: TLSyncGap[];
  selected: string | null;
  onSelect(id: string): void;
  axisLabel: string;
}) {
  const { lanes, syncGaps } = props;
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);
  const [hover, setHover] = useState<{ x: number; y: number; text: string } | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(400, entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const extent = useMemo(() => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const lane of lanes) {
      for (const s of lane.segments) {
        lo = Math.min(lo, s.start - s.uncertainty * 1000);
        hi = Math.max(hi, s.end + s.uncertainty * 1000);
      }
      for (const g of lane.gaps) {
        lo = Math.min(lo, g.start);
        hi = Math.max(hi, g.end);
      }
    }
    for (const g of syncGaps) {
      lo = Math.min(lo, g.start);
      hi = Math.max(hi, g.end);
    }
    if (!Number.isFinite(lo)) return null;
    const pad = Math.max(1000, (hi - lo) * 0.03);
    return [lo - pad, hi + pad] as [number, number];
  }, [lanes, syncGaps]);

  const [view, setView] = useState<[number, number] | null>(extent);
  const extentKey = extent ? `${extent[0]}:${extent[1]}` : "";
  // Re-fit when the data changes, not when the user zooms.
  useEffect(() => setView(extent), [extentKey]);

  const drag = useRef<{ x: number; view: [number, number] } | null>(null);

  if (!extent || !view) return <div className="empty">Nothing to place on this axis.</div>;

  const plotW = width - LABEL_W - 12;
  const height = AXIS_H + lanes.length * LANE_H + 8;
  const [v0, v1] = view;
  const span = v1 - v0;
  const x = (t: number) => LABEL_W + ((t - v0) / span) * plotW;
  const step = niceStep(span, plotW);
  const ticks: number[] = [];
  for (let t = Math.ceil(v0 / step) * step; t <= v1; t += step) ticks.push(t);
  const showSeconds = step < 60_000;

  const zoom = (factor: number, centre = (v0 + v1) / 2) => {
    const next = Math.min(Math.max(span * factor, 2000), (extent[1] - extent[0]) * 20);
    const ratio = (centre - v0) / span;
    setView([centre - next * ratio, centre - next * ratio + next]);
  };
  const onWheel = (e: WheelEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left - LABEL_W;
    if (px < 0) return;
    zoom(e.deltaY > 0 ? 1.25 : 0.8, v0 + (px / plotW) * span);
  };
  const onDown = (e: PointerEvent<SVGSVGElement>) => {
    if ((e.target as Element).closest(".tl-seg")) return;
    drag.current = { x: e.clientX, view };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    if (!drag.current) return;
    const dt = ((e.clientX - drag.current.x) / plotW) * span;
    setView([drag.current.view[0] - dt, drag.current.view[1] - dt]);
  };
  const onUp = () => {
    drag.current = null;
  };
  const tip = (e: PointerEvent, text: string) => {
    const rect = box.current!.getBoundingClientRect();
    setHover({ x: e.clientX - rect.left + 12, y: e.clientY - rect.top + 12, text });
  };

  return (
    <div className="timeline" ref={box}>
      <div className="tl-toolbar">
        <span className="tl-axis">{props.axisLabel}</span>
        <span className="tl-range">
          {stamp(v0)} → {stamp(v1)} · {duration(span / 1000)} shown
        </span>
        <button type="button" className="btn btn-ghost" onClick={() => zoom(0.5)}>Zoom in</button>
        <button type="button" className="btn btn-ghost" onClick={() => zoom(2)}>Zoom out</button>
        <button type="button" className="btn btn-ghost" onClick={() => setView(extent)}>Fit</button>
      </div>
      <svg
        width={width}
        height={height}
        className="tl-svg"
        onWheel={onWheel}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerLeave={() => {
          onUp();
          setHover(null);
        }}
        onDoubleClick={() => setView(extent)}
        role="img"
        aria-label="Recording timeline"
      >
        <defs>
          <pattern id="tl-hatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="7" className="hatch-line" />
          </pattern>
          <pattern id="tl-dots" width="6" height="6" patternUnits="userSpaceOnUse">
            <circle cx="3" cy="3" r="1.1" className="pattern-mark" />
          </pattern>
          <pattern id="tl-cross" width="6" height="6" patternUnits="userSpaceOnUse">
            <path d="M0 0L6 6M6 0L0 6" className="pattern-line" />
          </pattern>
          <clipPath id="tl-plot">
            <rect x={LABEL_W} y={0} width={plotW} height={height} />
          </clipPath>
        </defs>

        {lanes.map((lane, i) => (
          <g key={lane.key}>
            <rect x={0} y={AXIS_H + i * LANE_H} width={width} height={LANE_H} className={i % 2 ? "lane-odd" : "lane-even"} />
            <text x={10} y={AXIS_H + i * LANE_H + 16} className="lane-label">{lane.label}</text>
            {lane.sub && <text x={10} y={AXIS_H + i * LANE_H + 30} className="lane-sub">{lane.sub}</text>}
          </g>
        ))}

        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={AXIS_H - 6} y2={height} className="tick-line" />
            <text x={x(t) + 3} y={AXIS_H - 12} className="tick-label">
              {stamp(t, false).slice(0, showSeconds ? 8 : 5)}
            </text>
            {new Date(t).getUTCHours() === 0 && new Date(t).getUTCMinutes() === 0 && (
              <text x={x(t) + 3} y={12} className="tick-date">{stamp(t).slice(0, 10)}</text>
            )}
          </g>
        ))}
        <text x={LABEL_W + 3} y={12} className="tick-date">{stamp(v0).slice(0, 10)}</text>

        <g clipPath="url(#tl-plot)">
          {syncGaps.map((g, i) => {
            const rows = g.laneKeys.map((k) => lanes.findIndex((l) => l.key === k)).filter((r) => r >= 0);
            if (!rows.length) return null;
            const top = AXIS_H + Math.min(...rows) * LANE_H;
            const bottom = AXIS_H + (Math.max(...rows) + 1) * LANE_H;
            return (
              <g key={`sync-${i}`}>
                <rect
                  x={x(g.start)}
                  y={top}
                  width={Math.max(2, x(g.end) - x(g.start))}
                  height={bottom - top}
                  className="sync-gap"
                  onPointerMove={(e) => tip(e, g.title)}
                  onPointerLeave={() => setHover(null)}
                />
                {x(g.end) - x(g.start) > 150 && (
                  <text x={x(g.start) + 6} y={top + 14} className="sync-gap-label">
                    no recording on any channel · {duration((g.end - g.start) / 1000)}
                  </text>
                )}
              </g>
            );
          })}

          {lanes.map((lane, i) => {
            const top = AXIS_H + i * LANE_H;
            return (
              <g key={lane.key}>
                {lane.gaps.map((g, j) => (
                  <rect
                    key={j}
                    x={x(g.start)}
                    y={top + 6}
                    width={Math.max(2, x(g.end) - x(g.start))}
                    height={LANE_H - 12}
                    className="lane-gap"
                    onPointerMove={(e) => tip(e, g.title)}
                    onPointerLeave={() => setHover(null)}
                  />
                ))}
                {lane.segments.map((s) => {
                  const x0 = x(s.start);
                  const w = Math.max(3, x(s.end) - x0);
                  const u = s.uncertainty * 1000;
                  const tier = s.tier ?? "T1";
                  return (
                    <g
                      key={s.id}
                      className={`tl-seg ${props.selected === s.id ? "selected" : ""}`}
                      onClick={() => props.onSelect(s.id)}
                      onPointerMove={(e) => tip(e, s.title)}
                      onPointerLeave={() => setHover(null)}
                    >
                      {u > 0 && (
                        <rect x={x(s.start - u)} y={top + 9} width={Math.max(3, x(s.end + u) - x(s.start - u))} height={LANE_H - 18} className="unc-band" />
                      )}
                      <rect x={x0} y={top + 11} width={w} height={LANE_H - 22} rx={2} className={`seg seg-${tier}`} />
                      {tier === "T3" && <rect x={x0} y={top + 11} width={w} height={LANE_H - 22} fill="url(#tl-dots)" />}
                      {tier === "T4" && <rect x={x0} y={top + 11} width={w} height={LANE_H - 22} fill="url(#tl-cross)" />}
                      {w > 26 && (
                        <text x={x0 + 4} y={top + LANE_H / 2 + 4} className="seg-label">
                          {w > 90 ? `${tier} ${s.id}` : tier}
                        </text>
                      )}
                    </g>
                  );
                })}
              </g>
            );
          })}
        </g>
      </svg>
      {hover && (
        <div className="tl-tip" style={{ left: hover.x, top: hover.y }}>
          {hover.text}
        </div>
      )}
      <div className="tl-legend">
        <span><i className="swatch seg-T1" /> T1 indexed</span>
        <span><i className="swatch seg-T2" /> T2 orphan entry</span>
        <span><i className="swatch seg-T3 dots" /> T3 carved</span>
        <span><i className="swatch seg-T4 cross" /> T4 bad-sector carve</span>
        <span><i className="swatch unc" /> time uncertainty</span>
        <span><i className="swatch gap" /> gap on a channel</span>
        <span><i className="swatch sync" /> gap on all channels</span>
        <span className="muted">Scroll to zoom · drag to pan · double-click to fit · click a recording to select it</span>
      </div>
    </div>
  );
}

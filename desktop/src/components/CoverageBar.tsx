import { bytes, pct } from "../format";
import type { Bucket, CoverageRow } from "../types";

const ORDER: Bucket[] = ["structural", "parsed", "carved", "unreadable", "unaccounted"];

const MEANING: Record<Bucket, string> = {
  structural: "Superblock, index, logs — understood metadata",
  parsed: "Claimed by a T1/T2 recording",
  carved: "Recovered at T3/T4",
  unreadable: "Bad sectors, zero-filled with the gap recorded",
  unaccounted: "Bytes the tool could not explain — a negative finding",
};

/** Every byte of the image in exactly one bucket (FR-29), as one bar with a legend. */
export function CoverageBar(props: { rows: CoverageRow[] }) {
  const totals = new Map<Bucket, number>();
  for (const row of props.rows) totals.set(row.bucket, (totals.get(row.bucket) ?? 0) + row.length);
  const total = [...totals.values()].reduce((a, b) => a + b, 0);
  return (
    <div className="coverage">
      <div className="coverage-bar" role="img" aria-label="Coverage map">
        {ORDER.filter((b) => totals.get(b)).map((bucket) => (
          <div
            key={bucket}
            className={`cov cov-${bucket}`}
            style={{ flexGrow: totals.get(bucket) ?? 0 }}
            title={`${bucket}: ${bytes(totals.get(bucket))} (${pct(totals.get(bucket) ?? 0, total)})`}
          />
        ))}
      </div>
      <table className="coverage-legend">
        <tbody>
          {ORDER.map((bucket) => (
            <tr key={bucket} className={bucket === "unaccounted" && totals.get(bucket) ? "row-flag" : ""}>
              <td>
                <span className={`swatch cov-${bucket}`} aria-hidden /> {bucket}
              </td>
              <td className="num">{bytes(totals.get(bucket) ?? 0)}</td>
              <td className="num">{pct(totals.get(bucket) ?? 0, total)}</td>
              <td className="muted">{MEANING[bucket]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

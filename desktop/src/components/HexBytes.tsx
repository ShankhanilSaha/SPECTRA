import { hex } from "../format";

/** The matched bytes exactly as read from the evidence, at their absolute offset. */
export function HexBytes(props: { offset: number; hex: string }) {
  const data = props.hex.match(/../g)?.map((h) => parseInt(h, 16)) ?? [];
  const rows: number[][] = [];
  for (let i = 0; i < data.length; i += 16) rows.push(data.slice(i, i + 16));
  return (
    <pre className="hexdump">
      {rows.map((row, i) => {
        const bytesText = row.map((b) => b.toString(16).padStart(2, "0")).join(" ");
        const ascii = row.map((b) => (b >= 0x20 && b < 0x7f ? String.fromCharCode(b) : "·")).join("");
        return (
          <div key={i}>
            <span className="hex-off">{hex(props.offset + i * 16).padEnd(12)}</span>
            <span className="hex-bytes">{bytesText.padEnd(47)}</span>
            <span className="hex-ascii">{ascii}</span>
          </div>
        );
      })}
    </pre>
  );
}

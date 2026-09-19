/**
 * Display formatting. Times are never converted to the workstation's timezone: a
 * device-local time is shown exactly as the recorder wrote it, and a reference time is
 * shown in UTC and labelled so. Letting the browser "helpfully" localise either would put a
 * third, unexplained time in front of the examiner.
 */

const HAS_ZONE = /(Z|[+-]\d\d:?\d\d)$/;

/** Milliseconds for an ISO string. A naive (device-local) string is read on a UTC grid so
 * no host offset is applied; the number is only ever used for layout and difference. */
export function toMs(iso: string): number {
  return Date.parse(HAS_ZONE.test(iso) ? iso : `${iso}Z`);
}

function pad(n: number, width = 2): string {
  return String(n).padStart(width, "0");
}

/** "2026-03-05 14:04:18" from ms on the UTC grid. */
export function stamp(ms: number, withDate = true): string {
  const d = new Date(ms);
  const time = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
  if (!withDate) return time;
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${time}`;
}

/** A device-local ISO string as the recorder wrote it, or "time unknown". */
export function deviceTime(iso: string | null): string {
  return iso ? iso.replace("T", " ") : "time unknown";
}

/** A reference (UTC) ISO string as "… UTC". */
export function utcTime(iso: string | null): string {
  if (!iso) return "not established";
  return `${stamp(toMs(iso))} UTC`;
}

export function duration(seconds: number): string {
  const s = Math.round(Math.abs(seconds));
  if (s < 60) return `${s} s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = s % 60;
  if (h) return `${h} h ${pad(m)} min`;
  return rest ? `${m} min ${pad(rest)} s` : `${m} min`;
}

/** Signed offset as "+00:17:42". */
export function offset(seconds: number): string {
  const sign = seconds < 0 ? "−" : "+";
  const s = Math.round(Math.abs(seconds));
  return `${sign}${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
}

export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = n;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${n} B` : `${value.toFixed(value < 10 ? 2 : 1)} ${units[unit]}`;
}

export function hex(n: number): string {
  return `0x${n.toString(16).toUpperCase()}`;
}

export function pct(part: number, whole: number): string {
  if (!whole) return "0.0 %";
  return `${((100 * part) / whole).toFixed(1)} %`;
}

export function channelLabel(channel: number | null): string {
  return channel === null ? "ch ?" : `ch ${channel}`;
}

export function notesOf(json: string | null): string[] {
  if (!json) return [];
  try {
    const parsed = JSON.parse(json) as unknown;
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

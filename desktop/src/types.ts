/** Shapes of the JSON the `spectra` CLI prints with --json. Only the fields the UI reads. */

export interface CaseMeta {
  case_id: string;
  title: string;
  agency: string;
  fir_ref: string;
  authority_ref: string;
  examiner_name: string;
  examiner_designation: string;
  examiner_s79a_ref: string;
  created_utc: string;
}

export interface EvidenceItem {
  id: string;
  kind: string;
  provenance_class: string;
  label: string;
  capacity_bytes: number | null;
  sha256: string | null;
  source_path: string | null;
}

export interface CaseInfo {
  case: CaseMeta;
  evidence: EvidenceItem[];
  audit_head: { seq: number; digest: string };
}

export interface VerifyResult {
  ok: boolean;
  records_checked: number;
  head_seq: number;
  head_digest: string;
  broken_at_seq: number | null;
  artifacts_checked: number;
  problems: string[];
  warnings: string[];
}

export interface CustodyEntry {
  seq: number;
  evidence_id: string;
  ts_utc: string;
  from_holder: string;
  to_holder: string;
  purpose: string;
  seal_number: string;
  seal_intact: boolean | null;
  signature_ref: string;
  note: string;
}

export interface Attachment {
  id: string;
  kind: string;
  description: string;
  sha256: string;
  md5: string;
  size_bytes: number;
  filename: string;
  statutory_ref: string;
}

export interface CaseChain {
  evidence_id: string;
  custody: CustodyEntry[];
  attachments: Attachment[];
  chain_breaks: string[];
}

export interface Ingest {
  evidence_id: string;
  size: number;
  md5: string;
  sha256: string;
  gaps: [number, number][];
  embedded_hash_check: { status?: string };
  source_format: string;
  notes: string;
}

export interface Match {
  offset: number;
  hex: string;
  description: string;
}

export interface Candidate {
  family: string;
  layout_version: string | null;
  confidence: number;
  parse_supported: boolean;
  matches: Match[];
  plugin_version: string;
  note: string;
}

export type Support = "parse" | "carve_only" | "pending_selection" | "none";

export interface IdentificationView {
  evidence_id: string;
  status: "identified" | "ambiguous" | "unknown";
  support: Support;
  selected_family: string | null;
  selected_layout_version: string | null;
  selection: string | null;
  candidates: Candidate[];
  observations: Match[];
  errors: { family: string; plugin_version: string; error_type: string; message: string }[];
}

export interface Recording {
  id: string;
  evidence_id: string;
  channel: number | null;
  stream: string | null;
  codec: string | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  t_local_start: string | null;
  t_local_end: string | null;
  t_ref_start: string | null;
  t_ref_end: string | null;
  t_uncertainty_s: number | null;
  t_method: string | null;
  size_bytes: number | null;
  recovery_tier: string;
  confidence: number;
  source_note: string | null;
  frame_count: number | null;
  notes_json: string | null;
}

export type Bucket = "structural" | "parsed" | "carved" | "unreadable" | "unaccounted";

export interface CoverageRow {
  evidence_id: string;
  offset: number;
  length: number;
  bucket: Bucket;
}

export interface RecoverSummary {
  evidence_id: string;
  tiers_requested: string[];
  tiers_run: string[];
  skipped: string[];
  added: number;
  by_tier: Record<string, number>;
  duplicates_merged: number;
  coverage: Record<string, number>;
  t1_minutes: number;
  recovered_minutes: number;
  items_without_time: number;
  gain_pct: number | null;
  notes: string[];
}

export interface TimeShow {
  evidence_id: string;
  tz_offset_s: number | null;
  observations: {
    id: string;
    method: string;
    device_local: string;
    true_utc: string;
    uncertainty_s: number;
    valid_from: string | null;
    valid_to: string | null;
    note: string | null;
  }[];
  segments: {
    valid_from: string | null;
    valid_to: string | null;
    total_offset_s: number;
    uncertainty_s: number;
    method: string;
    note: string;
  }[];
}

export interface TimelineData {
  basis: "reference";
  lanes: {
    label: string;
    evidence_id: string;
    channel: number | null;
    segments: { recording_id: string; start: string; end: string; uncertainty_s: number }[];
  }[];
  unplaced: { evidence_id: string; recording_id: string; channel: number | null; reason: string }[];
}

export interface Gap {
  channel: number | null;
  start: string;
  end: string;
  duration_s: number;
  uncertainty_s: number;
  synchronised: boolean;
  after_recording: string | null;
  before_recording: string | null;
}

export interface GapReport {
  min_gap_s: number;
  evidence: {
    evidence_id: string;
    basis: "reference" | "device-local";
    banner?: string;
    channel_gaps: Gap[];
    synchronised_gaps: Gap[];
    not_placed: { recording_id: string; channel: number | null; reason: string }[];
  }[];
}

export interface MotionResult {
  recording_id: string;
  total_frames: number;
  motion_frames: number;
  segments: {
    start_frame: number;
    end_frame: number;
    start_pts_ms: number;
    end_pts_ms: number;
    peak_score: number;
    motion_frame_count: number;
  }[];
  annotations_count: number;
  sampled_fps: number;
  decode_note: string;
}

export interface ArtifactRef {
  sha256: string;
  md5: string;
  size: number;
  kind: string;
  path: string;
}

export interface ExportResult {
  recording_id: string;
  frames: number;
  es: ArtifactRef;
  frame_index: ArtifactRef;
  mp4: ArtifactRef | null;
  mp4_skipped_reason: string | null;
  vcl: { method: string; vcl_digest: string; vcl_units: number; result: string } | null;
  copied_to: string[];
}

export interface ArtifactRow {
  sha256: string;
  md5: string;
  kind: string;
  recording_id: string | null;
  is_derivative: boolean;
  created_utc: string;
  size_bytes: number;
  path: string;
}

export interface NegativeFinding {
  code: string;
  severity: "serious" | "attention" | "info";
  title: string;
  detail: string;
  evidence_id: string | null;
  numbers: Record<string, unknown>;
}

export interface Findings {
  conclusions_digest: string;
  generated_utc: string;
  negative_findings: NegativeFinding[];
  negative_summary: { total: number; by_severity: Record<string, number> };
  integrity: { audit_ok: boolean; audit_broken_at_seq: number | null; audit_head_digest: string };
  tool_versions: Record<string, unknown>;
}

export interface ReportResult {
  html: string;
  html_sha256: string;
  pdf: string | null;
  pdf_error: string | null;
  findings_digest: string;
  conclusions_digest: string;
  negative_summary: { total: number; by_severity: Record<string, number> };
  audit_ok: boolean;
}

export interface Certificate {
  certificate: {
    heading: string;
    authority: string;
    case_id: string;
    instance: number;
    record_description: string;
    device: { source_type: string; other_information: string; make_and_model: string; serial_number: string };
    hashes: { MD5: string; SHA1: string; SHA256: string; ticked: string[] };
    hash_report_ref: string;
    unsigned: string[];
  };
  hash_report: { artefact: string; kind: string; SHA256: string; MD5: string; size_bytes: number; note: string }[];
  warnings: string[];
}

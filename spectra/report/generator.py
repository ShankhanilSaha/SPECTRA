"""The twelve-section examination report (FR-80, FR-87, AC-09, doc 3 §10).

A rendering of `findings.json` and nothing more. The template adds layout, headings and
the fixed statutory language; it adds no facts. If a number appears in the report it came
out of the findings document, which ships beside it, so any reader can check the rendering
against the data without trusting this module.

## Determinism (NFR-08, AC-11)

The report inherits the findings document's determinism and must not spend it. Three
things would, and none of them happen here:

* **No clock reads.** Every time printed comes from the document.
* **No iteration over unordered containers.** The document arrives sorted; the template
  walks it in the order given.
* **No environment leakage.** No locale-dependent formatting, no host name, no paths
  outside the case.

The consequence is that the HTML for a given findings document is byte-stable, and the
report prints its own findings digest so a reader can confirm which data produced it.

## PDF/A

`render_pdf` needs WeasyPrint, which needs cairo and pango. It is an optional extra, and
when it is absent the HTML is still written and the caller is told the PDF step was
skipped. A forensic tool may not quietly hand over a lesser artefact than the one asked
for (rule 7).

## Section 7

Negative findings are rendered from `negative_findings` with no filter, no threshold and
no collapse-by-default. The section that a reader most wants shortened is the one that
must not be (FR-81, rule 11).
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined

#: Section titles, in the order doc 3 §10 fixes them. The numbering is cited in SOPs and
#: in the validation plan, so it is data here rather than prose in a template.
SECTIONS: tuple[str, ...] = (
    "Case and authority",
    "Evidence received",
    "Acquisition",
    "Identification",
    "Filesystem findings",
    "Recordings",
    "Negative findings",
    "Timestamp analysis",
    "Timeline and correlation",
    "Analytics",
    "Integrity",
    "Appendices",
)

#: Printed verbatim beside every analytics hit (FR-96). Not configurable.
ANALYTICS_DISCLAIMER = (
    "Machine-generated detection. Requires human verification against the source frame. "
    "Not an identification."
)

_SEVERITY_LABEL = {
    "serious": "Serious",
    "attention": "Attention",
    "info": "Information",
}


@dataclass(frozen=True, slots=True)
class Branding:
    """Agency-specific presentation (FR-87). Presentation only — never content.

    Nothing here can add, remove or reword a finding. An agency may put its letterhead on
    the report; it may not put its house style on what the tool could not read.
    """

    agency_name: str = ""
    letterhead_line: str = ""
    footer_note: str = ""
    language: str = "en"


def _fmt_bytes(value: Any) -> str:
    """Byte counts with both the exact figure and a readable one.

    The exact number is what a reader checks against `findings.json`; the readable one is
    what makes a coverage table legible. Printing only the rounded figure would make the
    report and its own data appear to disagree.
    """
    if value is None:
        return "not recorded"
    n = int(value)
    for unit, scale in (("TB", 1 << 40), ("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= scale:
            return f"{n:,} bytes ({n / scale:.2f} {unit})"
    return f"{n:,} bytes"


def _fmt(value: Any, blank: str = "not recorded") -> str:
    """Render a scalar, distinguishing 'absent' from 'empty' in words rather than silence."""
    if value is None or value == "":
        return blank
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


_ENV = Environment(  # noqa: S701 — autoescape is set below; see the comment.
    autoescape=True,
    undefined=StrictUndefined,  # a missing key must fail loudly, not render as blank
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_ENV.filters["bytes"] = _fmt_bytes
_ENV.filters["f"] = _fmt

_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ branding.language }}">
<head>
<meta charset="utf-8">
<title>Examination report — {{ doc.case.case_id | f }}</title>
<style>
  @page { size: A4; margin: 18mm 16mm; }
  body { font-family: "DejaVu Serif", Georgia, serif; font-size: 10.5pt; line-height: 1.45;
         color: #111; }
  h1 { font-size: 17pt; margin: 0 0 2mm; }
  h2 { font-size: 13pt; margin: 9mm 0 2mm; border-bottom: 1.5px solid #111;
       padding-bottom: 1mm; page-break-after: avoid; }
  h3 { font-size: 11pt; margin: 5mm 0 1.5mm; page-break-after: avoid; }
  table { border-collapse: collapse; width: 100%; margin: 2mm 0 4mm; font-size: 9.5pt; }
  th, td { border: 1px solid #999; padding: 1.4mm 2mm; text-align: left;
           vertical-align: top; }
  th { background: #eee; font-weight: bold; }
  .letterhead { border-bottom: 2.5px solid #111; padding-bottom: 3mm; margin-bottom: 5mm; }
  .agency { font-size: 14pt; font-weight: bold; }
  .mono { font-family: "DejaVu Sans Mono", Consolas, monospace; font-size: 8.5pt;
          word-break: break-all; }
  .finding { border-left: 4px solid #999; padding: 1.5mm 0 1.5mm 3mm; margin: 3mm 0;
             page-break-inside: avoid; }
  .serious { border-left-color: #000; }
  .attention { border-left-color: #666; }
  .info { border-left-color: #bbb; }
  .sev { font-weight: bold; text-transform: uppercase; font-size: 8.5pt;
         letter-spacing: 0.5px; }
  .banner { border: 2px solid #000; padding: 3mm; margin: 4mm 0; font-weight: bold; }
  .muted { color: #555; }
  .disclaimer { font-style: italic; font-size: 9pt; }
  footer { margin-top: 8mm; border-top: 1px solid #999; padding-top: 2mm; font-size: 8.5pt;
           color: #555; }
</style>
</head>
<body>

<div class="letterhead">
  {% if branding.agency_name %}<div class="agency">{{ branding.agency_name }}</div>{% endif %}
  {% if branding.letterhead_line %}<div>{{ branding.letterhead_line }}</div>{% endif %}
  <h1>Digital evidence examination report</h1>
  <div class="muted">Case {{ doc.case.case_id | f }} &middot; generated
    {{ doc.generated_utc | f }} &middot; SPECTRA {{ tool_version }}</div>
</div>

{% if not doc.time.absolute_established %}
<div class="banner">
  ABSOLUTE TIME NOT ESTABLISHED. No clock offset has been measured for this evidence, so
  every time in this report is the recorder's own clock as it was found. It may be wrong
  by an unknown amount. No time in this report may be treated as UTC or as local wall-clock
  time without independent corroboration.
</div>
{% endif %}
{% if not doc.integrity.audit_ok %}
<div class="banner">
  AUDIT CHAIN VERIFICATION FAILED at sequence {{ doc.integrity.audit_broken_at_seq | f }}.
  {{ doc.integrity.audit_reason | f }} This report is produced from a case whose own
  integrity record does not verify, and should not be relied upon until that is explained.
</div>
{% endif %}

<h2>1. {{ sections[0] }}</h2>
<table>
  <tr><th>Case number</th><td>{{ doc.case.case_id | f }}</td></tr>
  <tr><th>Title</th><td>{{ doc.case.title | f }}</td></tr>
  <tr><th>Agency</th><td>{{ doc.case.agency | f }}</td></tr>
  <tr><th>FIR reference</th><td>{{ doc.case.fir_ref | f }}</td></tr>
  <tr><th>Authority reference</th><td>{{ doc.case.authority_ref | f }}</td></tr>
  <tr><th>Examiner</th><td>{{ doc.case.examiner_name | f }}</td></tr>
  <tr><th>Designation</th><td>{{ doc.case.examiner_designation | f }}</td></tr>
  <tr><th>IT Act s. 79A notification</th><td>{{ doc.case.examiner_s79a_ref | f }}</td></tr>
</table>

<h2>2. {{ sections[1] }}</h2>
{% for item in doc.evidence %}
<h3>{{ item.id }} — {{ item.label | f('no label') }}</h3>
<table>
  <tr><th>Kind</th><td>{{ item.kind | f }}</td>
      <th>Provenance class</th><td>{{ item.provenance_class | f }}</td></tr>
  <tr><th>Device</th><td>{{ item.device_make | f }} {{ item.device_model | f('') }}</td>
      <th>Device serial</th><td>{{ item.device_serial | f }}</td></tr>
  <tr><th>Disk</th><td>{{ item.disk_make | f }} {{ item.disk_model | f('') }}</td>
      <th>Disk serial</th><td>{{ item.disk_serial | f }}</td></tr>
  <tr><th>Capacity</th><td colspan="3">{{ item.capacity_bytes | bytes }}</td></tr>
  <tr><th>MD5</th><td colspan="3" class="mono">{{ item.md5 | f }}</td></tr>
  <tr><th>SHA-256</th><td colspan="3" class="mono">{{ item.sha256 | f }}</td></tr>
</table>
{% if item.notes %}<p class="muted">{{ item.notes }}</p>{% endif %}
{% else %}
<p>No evidence has been ingested into this case.</p>
{% endfor %}

<h3>Chain of custody</h3>
{% if doc.custody %}
<table>
  <tr><th>#</th><th>Recorded</th><th>Released by</th><th>Received by</th><th>Purpose</th>
      <th>Seal</th></tr>
  {% for row in doc.custody %}
  <tr><td>{{ row.seq }}</td><td>{{ row.ts_utc | f }}</td><td>{{ row.from_holder | f }}</td>
      <td>{{ row.to_holder | f }}</td><td>{{ row.purpose | f }}</td>
      <td>{% if row.seal_intact is none %}no seal recorded
          {% elif row.seal_intact %}intact{{ ' (' ~ row.seal_number ~ ')'
            if row.seal_number else '' }}
          {% else %}NOT INTACT{% endif %}</td></tr>
  {% endfor %}
</table>
{% else %}
<p>No custody transfers are recorded. The chain of custody is not established by this
case file.</p>
{% endif %}

<h3>Documents attached to this case</h3>
{% if doc.attachments %}
<table>
  <tr><th>ID</th><th>Kind</th><th>File</th><th>Under</th><th>SHA-256</th></tr>
  {% for row in doc.attachments %}
  <tr><td>{{ row.id }}</td><td>{{ row.kind | f }}</td><td>{{ row.filename | f }}</td>
      <td>{{ row.statutory_ref | f('—') }}</td>
      <td class="mono">{{ row.sha256 | f }}</td></tr>
  {% endfor %}
</table>
{% else %}
<p>No external case documents are attached.</p>
{% endif %}

<h2>3. {{ sections[2] }}</h2>
{% for item in doc.evidence %}
<table>
  <tr><th>Evidence</th><td>{{ item.id }}</td>
      <th>Provenance class</th><td>{{ item.provenance_class | f }}</td></tr>
  <tr><th>Acquired</th><td>{{ item.acquired_utc | f }}</td>
      <th>By</th><td>{{ item.acquired_by | f }}</td></tr>
  <tr><th>Write blocker</th><td>{{ item.write_blocker | f }}</td>
      <th>Source format</th><td>{{ item.source_format | f }}</td></tr>
  <tr><th>HPA present</th><td>{{ item.hpa_present | f }}</td>
      <th>DCO present</th><td>{{ item.dco_present | f }}</td></tr>
  <tr><th>Hidden sectors</th><td colspan="3">{{ item.hidden_sectors | f }}</td></tr>
</table>
{% if item.gaps %}
<p><strong>Unreadable regions recorded during acquisition:</strong>
   {{ item.gaps | length }}. These were zero-filled in the image and are reported as
   unreadable in the coverage map, not as recorded zeros.</p>
{% endif %}
{% endfor %}

<h2>4. {{ sections[3] }}</h2>
{% for row in doc.identification %}
<table>
  <tr><th>Evidence</th><td>{{ row.evidence_id }}</td>
      <th>Format family</th><td>{{ row.family | f }}</td></tr>
  <tr><th>Layout version</th><td>{{ row.layout_version | f }}</td>
      <th>Confidence</th><td>{{ row.confidence | f }}</td></tr>
  <tr><th>Parse supported</th><td>{{ row.parse_supported | f }}</td>
      <th>Status</th><td>{{ row.status | f }}</td></tr>
  <tr><th>Brand inferred</th><td colspan="3">{{ row.brand_inferred | f('not inferred') }}
      <span class="muted">— the parser is chosen from the bytes on the disk, never from
      the badge on the chassis.</span></td></tr>
  <tr><th>Matched bytes</th><td colspan="3" class="mono">{{ row.matched_signature_hex |
      f('none recorded') }} at offset(s) {{ row.matched_offsets | f('—') }}</td></tr>
</table>
{% if row.candidates | length > 1 %}
<p><strong>More than one family matched.</strong> All candidates are listed; the selection
   and its reason are in the audit log.</p>
<table>
  <tr><th>Family</th><th>Layout</th><th>Confidence</th><th>Parse supported</th></tr>
  {% for candidate in row.candidates %}
  <tr><td>{{ candidate.family }}</td><td>{{ candidate.layout_version | f }}</td>
      <td>{{ candidate.confidence | f }}</td><td>{{ candidate.parse_supported | f }}</td></tr>
  {% endfor %}
</table>
{% endif %}
{% else %}
<p>No evidence item has been identified.</p>
{% endfor %}

<h2>5. {{ sections[4] }}</h2>
{% for row in doc.disk_layout %}
<table>
  <tr><th>Evidence</th><td>{{ row.evidence_id }}</td>
      <th>Family / layout</th><td>{{ row.family | f }} / {{ row.layout_version | f }}</td></tr>
  <tr><th>Block size</th><td>{{ row.block_size | bytes }}</td>
      <th>Block count</th><td>{{ row.block_count | f }}</td></tr>
  <tr><th>Blocks used</th><td>{{ row.blocks_used | f }}</td>
      <th>Index extents</th><td>{{ row.index_extents | length }}</td></tr>
  <tr><th>Volume formatted (device clock)</th><td colspan="3">
      {{ row.format_t_local | f('not recorded') }}
      {% if row.format_t_local %}<span class="muted">— as the recorder's own clock read it;
      not corroborated against any external time source.</span>{% endif %}</td></tr>
</table>
{% if row.note %}<p class="muted">{{ row.note }}</p>{% endif %}
{% else %}
<p>No filesystem layout was parsed.</p>
{% endfor %}

<h3>Coverage of the image</h3>
{% for entry in doc.coverage %}
<p>Evidence {{ entry.evidence_id }} — {{ entry.image_size_bytes | bytes }}
   {% if not entry.tiles_image %}<strong>(the buckets below do not account for the whole
   image; this is a defect and is reported as a finding)</strong>{% endif %}</p>
<table>
  <tr><th>Bucket</th><th>Bytes</th><th>Share</th><th>Meaning</th></tr>
  {% for bucket, total in entry.totals_bytes.items() %}
  <tr><td>{{ bucket }}</td><td>{{ total | bytes }}</td>
      <td>{{ '%.1f' % (100.0 * total / entry.image_size_bytes)
             if entry.image_size_bytes else '—' }}%</td>
      <td>{{ bucket_meaning[bucket] }}</td></tr>
  {% endfor %}
</table>
{% else %}
<p>No coverage map was produced.</p>
{% endfor %}

<h2>6. {{ sections[5] }}</h2>
{% if doc.recordings %}
<p>{{ doc.recordings | length }} recording(s). Times shown are the recorder's own clock
   unless section 8 states that an offset was established.</p>
<table>
  <tr><th>ID</th><th>Ch</th><th>Stream</th><th>Start (device)</th><th>End (device)</th>
      <th>Codec</th><th>Size</th><th>Tier</th><th>Conf.</th></tr>
  {% for rec in doc.recordings %}
  <tr><td>{{ rec.id }}</td><td>{{ rec.channel | f('?') }}</td>
      <td>{{ rec.stream | f }}</td>
      <td>{{ rec.t_local_start | f('time unknown') }}</td>
      <td>{{ rec.t_local_end | f('time unknown') }}</td>
      <td>{{ rec.codec | f }}</td><td>{{ rec.size_bytes | bytes }}</td>
      <td>{{ rec.recovery_tier | f }}</td><td>{{ rec.confidence | f }}</td></tr>
  {% endfor %}
</table>
<p class="muted">Recovery tiers: T1 index walk &middot; T2 orphaned index entries
   &middot; T3 signature carving &middot; T4 carving across unreadable sectors.</p>
{% else %}
<p>No recordings were enumerated or recovered.</p>
{% endif %}

<h2>7. {{ sections[6] }}</h2>
<p>This section is generated from the case and cannot be edited or removed. It states what
   the tool could not do. An empty section below would mean the tool found nothing it
   failed at — it does not mean the evidence is complete.</p>
{% if doc.negative_findings %}
<p>{{ doc.negative_summary.total }} finding(s):
   {{ doc.negative_summary.by_severity.serious }} serious,
   {{ doc.negative_summary.by_severity.attention }} requiring attention,
   {{ doc.negative_summary.by_severity.info }} informational.</p>
{% for finding in doc.negative_findings %}
<div class="finding {{ finding.severity }}">
  <div class="sev">{{ severity_label[finding.severity] }} &middot; {{ finding.code }}
    {% if finding.evidence_id %}&middot; {{ finding.evidence_id }}{% endif %}</div>
  <div><strong>{{ finding.title }}</strong></div>
  <div>{{ finding.detail }}</div>
  {% if finding.numbers %}
  <div class="muted">{% for key, value in finding.numbers.items() %}{{ key }}={{ value }}{{
    ", " if not loop.last }}{% endfor %}</div>
  {% endif %}
</div>
{% endfor %}
{% else %}
<p>No negative findings were generated. This is unusual and should itself be questioned.</p>
{% endif %}

<h2>8. {{ sections[7] }}</h2>
<table>
  <tr><th>Absolute time established</th>
      <td>{{ doc.time.absolute_established | f }}</td></tr>
  <tr><th>Offset methods used</th>
      <td>{{ doc.time.methods_used | join(', ') if doc.time.methods_used else 'none' }}</td></tr>
  <tr><th>Recordings with a reference time</th>
      <td>{{ doc.time.recordings_with_reference_time }} of
          {{ doc.time.recordings_total }}</td></tr>
</table>
{% if doc.time.observations %}
<table>
  <tr><th>Method</th><th>Device local</th><th>True UTC</th><th>Uncertainty</th>
      <th>Note</th></tr>
  {% for obs in doc.time.observations %}
  <tr><td>{{ obs.method | f }}</td><td>{{ obs.device_local | f }}</td>
      <td>{{ obs.true_utc | f }}</td><td>&plusmn;{{ obs.uncertainty_s | f }} s</td>
      <td>{{ obs.note | f('—') }}</td></tr>
  {% endfor %}
</table>
{% else %}
<p>No clock-offset observation was recorded. Under FR-53 no absolute time is asserted
   anywhere in this report.</p>
{% endif %}

<h2>9. {{ sections[8] }}</h2>
{% if doc.device_events %}
<table>
  <tr><th>Device time</th><th>Kind</th><th>Detail</th></tr>
  {% for row in doc.device_events %}
  <tr><td>{{ row.t_device | f }}</td><td>{{ row.kind | f }}</td>
      <td>{{ row.detail | f }}</td></tr>
  {% endfor %}
</table>
{% else %}
<p>No device system-log events were read from this evidence.</p>
{% endif %}
<p class="muted">Absence of footage in a period is reported as a gap, not as evidence that
   nothing happened.</p>

<h2>10. {{ sections[9] }}</h2>
{% if doc.annotations %}
<p class="disclaimer">{{ disclaimer }}</p>
<table>
  <tr><th>Recording</th><th>Frame</th><th>Label</th><th>Score</th><th>Model</th>
      <th>Model SHA-256</th></tr>
  {% for row in doc.annotations %}
  <tr><td>{{ row.recording_id | f }}</td><td>{{ row.frame_no | f }}</td>
      <td>{{ row.label | f }}</td><td>{{ row.score | f }}</td>
      <td>{{ row.model_name | f('—') }}</td>
      <td class="mono">{{ row.model_sha256 | f('—') }}</td></tr>
  {% endfor %}
</table>
{% else %}
<p>No analytics were run on this evidence.</p>
{% endif %}

<h2>11. {{ sections[10] }}</h2>
<table>
  <tr><th>Audit chain verifies</th><td>{{ doc.integrity.audit_ok | f }}</td></tr>
  <tr><th>Audit records</th><td>{{ doc.integrity.audit_records }}</td></tr>
  <tr><th>Audit head sequence</th><td>{{ doc.integrity.audit_head_seq | f }}</td></tr>
  <tr><th>Audit head digest</th>
      <td class="mono">{{ doc.integrity.audit_head_digest | f }}</td></tr>
  <tr><th>Findings digest</th><td class="mono">{{ findings_digest }}</td></tr>
  <tr><th>Conclusions digest</th>
      <td class="mono">{{ doc.conclusions_digest }}</td></tr>
</table>
<h3>Artefacts produced</h3>
<p class="muted">These are the artefacts that existed when this report's data was
   assembled. This report, and the findings document it was rendered from, are created
   after that point and so are not listed below — a document cannot carry its own
   digest. Their hashes are in the case manifest and in the hash report enclosed with
   the s. 63(4) certificate.</p>
{% if doc.artifacts %}
<table>
  <tr><th>Kind</th><th>Path</th><th>Size</th><th>SHA-256</th></tr>
  {% for row in doc.artifacts %}
  <tr><td>{{ row.kind | f }}</td><td>{{ row.path | f }}</td>
      <td>{{ row.size_bytes | bytes }}</td><td class="mono">{{ row.sha256 }}</td></tr>
  {% endfor %}
</table>
{% else %}
<p>No artefacts have been exported from this case.</p>
{% endif %}
<h3>How to verify this report independently</h3>
<p>These commands need only this case directory and a SPECTRA installation. They do not
   need the examiner, the original device, or network access.</p>
<pre class="mono">spectra verify chain --case &lt;case directory&gt;
spectra report findings --case &lt;case directory&gt;</pre>
<p>The audit head digest printed by the first command must equal
   <span class="mono">{{ doc.integrity.audit_head_digest | f }}</span>. The conclusions
   digest printed by the second must equal
   <span class="mono">{{ doc.conclusions_digest }}</span>; it covers what was found on the
   disk, and is reproducible by any examiner working from the same image. The findings
   digest above additionally covers this examination's own history, so it is expected to
   differ between examinations.</p>

<h2>12. {{ sections[11] }}</h2>
<h3>Statutory basis</h3>
<p>Electronic records are admissible under s. 61 of the Bharatiya Sakshya Adhiniyam 2023,
   subject to s. 63. A certificate in the form of the Schedule to s. 63(4) is required at
   each instance at which the record is submitted for admission, and is enclosed
   separately.</p>
<h3>Known limitations of this tool</h3>
<ul>
  <li>Support level differs by format family. Families supported only at carving level
      cannot be enumerated from an index, and completeness is not claimed for them.</li>
  <li>An on-disk layout version the tool does not recognise is not parsed. It is reported
      as unparsed rather than guessed at.</li>
  <li>Carved material with no header timestamp has no absolute time and is presented in
      physical order only.</li>
  <li>Vendor codec variants may not decode in all players.</li>
  <li>Analytics produce leads for human review and never identifications.</li>
  <li>Encrypted vendor storage is detected and reported, not defeated.</li>
</ul>
<h3>Examiner's declaration</h3>
<p>To be completed and signed by the examiner named in section 1.</p>
<table>
  <tr><th>Signature</th><td style="height:14mm"></td></tr>
  <tr><th>Name and designation</th><td>{{ doc.case.examiner_name | f }},
      {{ doc.case.examiner_designation | f }}</td></tr>
  <tr><th>Date and place</th><td></td></tr>
</table>

<footer>
  Report generated by SPECTRA {{ tool_version }} from findings digest
  <span class="mono">{{ findings_digest }}</span>.
  {% if branding.footer_note %}{{ branding.footer_note }}{% endif %}
</footer>
</body>
</html>
"""

_BUCKET_MEANING = {
    "parsed": "Claimed by a recording read from the filesystem index.",
    "carved": "Recovered by signature carving rather than from an index.",
    "structural": "Superblock, index and log areas — understood metadata.",
    "unreadable": "Sectors that could not be read from the source.",
    "unaccounted": "Bytes the tool could not explain. Data may remain here.",
}


def render_html(
    doc: dict[str, Any],
    findings_digest: str,
    *,
    branding: Branding | None = None,
    tool_version: str = "",
) -> str:
    """Render the twelve-section report. Pure function of its arguments (NFR-08)."""
    template = _ENV.from_string(_TEMPLATE)
    return template.render(
        doc=doc,
        findings_digest=findings_digest,
        branding=branding or Branding(),
        tool_version=tool_version,
        sections=SECTIONS,
        severity_label=_SEVERITY_LABEL,
        bucket_meaning=_BUCKET_MEANING,
        disclaimer=ANALYTICS_DISCLAIMER,
    )


class PdfUnavailable(RuntimeError):
    """WeasyPrint is not installed, so the PDF/A step cannot run."""


def render_pdf(html_text: str, out: Path, base_url: str | None = None) -> Path:
    """Render the HTML to PDF. Raises `PdfUnavailable` rather than degrading quietly."""
    try:
        from weasyprint import HTML  # noqa: PLC0415 — optional extra, imported on use
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PdfUnavailable(
            "PDF output needs WeasyPrint and its native cairo/pango libraries. "
            "Install it with `pip install 'spectra[pdf]'`. The HTML report has still "
            "been written."
        ) from exc
    out.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_text, base_url=base_url).write_pdf(str(out))
    return out


def escape(value: str) -> str:
    """Escape a value for inclusion in a report fragment built outside the template."""
    return html.escape(value, quote=True)

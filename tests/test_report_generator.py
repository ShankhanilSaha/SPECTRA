"""The twelve-section report (FR-80, FR-87, AC-09, TC-RP-01, TC-RP-05).

The report is a rendering of `findings.json`. These tests pin that it renders all twelve
sections, that it cannot quietly drop the parts a reader would most like shortened, and
that it does not spend the determinism the findings document paid for.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest

from spectra.core.casestore import CaseMeta, CaseStore
from spectra.report import findings as fnd
from spectra.report import generator as gen
from spectra.services import custody as cs
from spectra.services import evidence as ev
from spectra.services import identify as ident
from spectra.services import parse as ps
from spectra.services import recover as rc
from spectra.services import report as rp
from tests import hikgen
from tests.test_audit import fixed_clock

RECS = [
    (1, datetime(2026, 3, 5, 14, 0, 0), datetime(2026, 3, 5, 14, 30, 0)),
    (2, datetime(2026, 3, 5, 14, 5, 0), datetime(2026, 3, 5, 14, 35, 0)),
]


def seeded(tmp_path: Path, name: str = "case") -> tuple[CaseStore, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"{name}.raw"
    path.write_bytes(hikgen.volume(recordings=RECS, orphan_indices=(1,)))
    store = CaseStore.create(
        tmp_path / name,
        CaseMeta("CASE-G-1", title="generator", agency="State FSL",
                 examiner_name="R. Examiner", examiner_designation="Assistant Director"),
        "examiner-1", clock=fixed_clock(),
    )
    ingest = ev.import_image(store, path, "A", label="disk 1")
    ident.run_identify(store, ingest.evidence_id)
    ps.parse(store, ingest.evidence_id)
    rc.recover(store, ingest.evidence_id, ("T2", "T3"))
    return store, ingest.evidence_id


def render(store: CaseStore, **kwargs: object) -> str:
    doc = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    return gen.render_html(doc, fnd.digest(doc), **kwargs)  # type: ignore[arg-type]


def text_of(markup: str) -> str:
    """Visible text only, so assertions do not accidentally match CSS or attributes."""
    body = markup.split("</style>", 1)[1]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


# --- all twelve sections (AC-09) --------------------------------------------------------


def test_ac_09_every_section_is_present_and_numbered(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    visible = text_of(render(store))
    assert len(gen.SECTIONS) == 12
    for number, title in enumerate(gen.SECTIONS, start=1):
        assert f"{number}. {title}" in visible, f"section {number} is missing"
    store.close()


def test_the_certificate_is_referenced_in_the_appendices(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    visible = text_of(render(store))
    assert "63(4)" in visible and "Schedule" in visible
    store.close()


# --- section 7 cannot be softened (FR-81, rule 11) --------------------------------------


def test_ac_09_negative_findings_are_populated_and_rendered(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    doc = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    visible = text_of(gen.render_html(doc, fnd.digest(doc)))
    assert doc["negative_findings"], "the fixture should produce findings"
    for finding in doc["negative_findings"]:
        assert finding["code"] in visible, f"{finding['code']} was not rendered"
        assert finding["title"] in visible
    store.close()


def test_the_template_offers_no_way_to_filter_findings() -> None:
    """A severity threshold or a collapse-by-default would defeat FR-81 in presentation."""
    source = Path("spectra/report/generator.py").read_text(encoding="utf-8")
    template = source.split('_TEMPLATE = """', 1)[1]
    for forbidden in ("selectattr", "rejectattr", "[:5]", "truncate", "<details"):
        assert forbidden not in template, f"{forbidden} could hide a finding"


def test_an_empty_section_seven_says_that_is_unusual(tmp_path: Path) -> None:
    """Silence would read as a clean bill of health, which is not what it means."""
    store = CaseStore.create(
        tmp_path / "empty", CaseMeta("CASE-E"), "examiner-1", clock=fixed_clock()
    )
    visible = text_of(render(store))
    assert "unusual" in visible
    store.close()


# --- refusals are stated, not implied ---------------------------------------------------


def test_a_case_with_no_offset_carries_the_not_established_banner(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    visible = text_of(render(store))
    assert "ABSOLUTE TIME NOT ESTABLISHED" in visible
    assert "FR-53" in visible
    store.close()


def test_a_broken_audit_chain_is_declared_on_the_first_page(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    store.conn.execute("DROP TRIGGER audit_no_update")
    store.conn.execute("UPDATE audit SET operator = 'someone else' WHERE seq = 1")
    store.conn.commit()
    visible = text_of(render(store))
    assert "AUDIT CHAIN VERIFICATION FAILED" in visible
    store.close()


def test_the_volume_format_time_is_labelled_as_the_device_clock(tmp_path: Path) -> None:
    """It comes off the recorder's own clock and must never read as corroborated."""
    store, _ = seeded(tmp_path)
    visible = text_of(render(store))
    assert "Volume formatted (device clock)" in visible
    store.close()


def test_analytics_hits_carry_the_fixed_disclaimer(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    rec = store.conn.execute(
        "SELECT id FROM recording WHERE evidence_id = ? ORDER BY id", (ev_id,)
    ).fetchone()[0]
    store.conn.execute(
        "INSERT INTO annotation (id, recording_id, frame_no, label, score, source,"
        " model_name, model_sha256) VALUES (?,?,?,?,?,?,?,?)",
        ("ANN-001", rec, 12, "person", 0.81, "machine", "yolox-s", "a" * 64),
    )
    store.conn.commit()
    visible = text_of(render(store))
    assert "Not an identification" in visible
    assert "a" * 64 in visible, "the model hash must be printed for reproducibility"
    store.close()


# --- branding is presentation only (FR-87) ----------------------------------------------


def test_branding_reaches_the_letterhead(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    visible = text_of(render(store, branding=gen.Branding(
        agency_name="Central Forensic Science Laboratory", footer_note="Copy 1 of 3")))
    assert "Central Forensic Science Laboratory" in visible
    assert "Copy 1 of 3" in visible
    store.close()


def test_branding_cannot_change_a_finding(tmp_path: Path) -> None:
    """An agency may put its letterhead on the report, not its house style on the gaps."""
    store, _ = seeded(tmp_path)
    plain = text_of(render(store))
    branded = text_of(render(store, branding=gen.Branding(agency_name="Some Agency")))
    doc = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    for finding in doc["negative_findings"]:
        assert finding["detail"][:60] in plain
        assert finding["detail"][:60] in branded
    store.close()


def test_branding_is_escaped_rather_than_injected(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    markup = render(store, branding=gen.Branding(agency_name="<script>alert(1)</script>"))
    assert "<script>alert(1)</script>" not in markup
    assert "&lt;script&gt;" in markup
    store.close()


# --- determinism (NFR-08) ---------------------------------------------------------------


def test_the_same_document_renders_byte_identically(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    doc = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    first = gen.render_html(doc, fnd.digest(doc))
    second = gen.render_html(doc, fnd.digest(doc))
    assert first == second
    store.close()


def test_the_generator_never_reads_a_clock() -> None:
    import ast

    tree = ast.parse(Path("spectra/report/generator.py").read_text(encoding="utf-8"))
    called = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for forbidden in ("datetime.now", "datetime.utcnow", "utc_now", "time.time"):
        assert forbidden not in called


def test_a_missing_key_fails_loudly_rather_than_rendering_blank(tmp_path: Path) -> None:
    """StrictUndefined: a silently empty field in a forensic report is a wrong report."""
    from jinja2 import UndefinedError

    store, _ = seeded(tmp_path)
    doc = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    del doc["integrity"]
    with pytest.raises(UndefinedError):
        gen.render_html(doc, "deadbeef")
    store.close()


# --- the service ------------------------------------------------------------------------


def test_the_service_writes_the_report_and_stores_it_hashed(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    result = rp.generate_report(store, out_dir=tmp_path / "out", pdf=False)
    assert result["html"].is_file()
    row = store.conn.execute(
        "SELECT sha256 FROM artifact WHERE kind = 'report'"
    ).fetchone()
    assert row is not None and row[0] == result["html_sha256"]
    store.close()


def test_a_missing_pdf_backend_is_reported_not_hidden(tmp_path: Path) -> None:
    """Handing over a lesser artefact than the one requested, silently, is the failure."""
    store, _ = seeded(tmp_path)
    result = rp.generate_report(store, out_dir=tmp_path / "out", pdf=True)
    if result["pdf"] is None:
        assert "WeasyPrint" in result["pdf_error"]
        assert result["html"].is_file(), "the HTML is still produced"
    else:
        assert result["pdf"].is_file()
    store.close()


def test_the_report_records_the_findings_it_was_rendered_from(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    result = rp.generate_report(store, out_dir=tmp_path / "out", pdf=False)
    markup = result["html"].read_text(encoding="utf-8")
    assert result["findings_digest"] in markup
    assert result["conclusions_digest"] in markup
    store.close()


def test_custody_and_documents_appear_in_section_two(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    cs.record_transfer(store, ev_id, from_holder="SI Rao", to_holder="Malkhana",
                       purpose="storage", seal_number="S-1", seal_intact=True)
    panchnama = tmp_path / "panchnama.pdf"
    panchnama.write_bytes(b"memo")
    cs.attach_document(store, panchnama, "panchnama", evidence_id=ev_id)
    visible = text_of(render(store))
    assert "SI Rao" in visible and "Malkhana" in visible and "S-1" in visible
    assert "panchnama.pdf" in visible
    assert "s. 103" in visible, "the statutory hook travels with the document"
    store.close()


def test_a_case_with_no_custody_says_so_in_words(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    visible = text_of(render(store))
    assert "chain of custody is not established" in visible
    store.close()

"""BSA s. 63(4) certificate — the Schedule form, pre-filled (FR-83, doc 1 §5.1).

The Bharatiya Sakshya Adhiniyam 2023 replaced the Indian Evidence Act on 1 July 2024.
Section 63(4) requires a certificate **in the form prescribed by the Schedule**, signed by
two people, submitted *"at each instance where it is being submitted for admission"*.

Three things about that form drive this module's shape:

**It has two parts with different content.** Part A is completed by the party — the person
with lawful control of the device — and carries the s. 63(2) recital and a statement of
how the device was held (owned / maintained / managed / operated). Part B is completed by
an expert, repeats the device particulars, omits the recital and the control block, and
adds the expert's **designation** beside the signature. The asymmetry is in the statute;
do not tidy it away.

**It names three algorithms and only three.** SHA1, SHA256, MD5, plus a free-text "Other
(legally acceptable standard)". SHA-512 is not listed and would have to go under Other.
This is why `core/hashing.py` computes all three.

**The hash values do not fit in it.** The form has one line for a hash and a mandatory
instruction that a **hash report is enclosed**. That enclosure is where the per-artefact
detail lives, and it is generated alongside the certificate.

## What this module will not do

It leaves the affirmation identities and the signatures blank. A certificate is a sworn
statement by two named people; pre-filling the parts only they can attest to would be
forging the very thing the section exists to obtain. The tool fills in what it measured.

**Transition.** Which form applies turns on the date the *proceeding* was instituted, not
when the footage was made. A case begun before 1 July 2024 still takes the old IEA
s. 65B(4) certificate, so `Statute` selects the heading and the citations; the device and
hash particulars are the same either way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Statute = Literal["BSA_63", "IEA_65B"]

#: The Schedule's own source-type checkboxes, in the order the form prints them.
#: "DVR" is a named option — the statute contemplates this device class explicitly.
SOURCE_TYPES: tuple[str, ...] = (
    "Computer / Storage Media",
    "DVR",
    "Mobile",
    "Flash Drive",
    "CD/DVD",
    "Server",
    "Cloud",
    "Other",
)

#: The control statement in Part A, multi-select.
CONTROL_MODES: tuple[str, ...] = ("Owned", "Maintained", "Managed", "Operated")

#: Exactly the algorithms the Schedule names, in its order. Anything else is "Other".
HASH_ALGORITHMS: tuple[str, ...] = ("SHA1", "SHA256", "MD5")

_RECITAL = (
    "The digital device or the digital record source was under the lawful control for "
    "regularly creating, storing or processing information for the purposes of carrying "
    "out regular activities and during this period, the computer or the communication "
    "device was working properly and the relevant information was regularly fed into the "
    "computer during the ordinary course of business. If the computer/digital device at "
    "any point of time was not working properly or out of operation, then it has not "
    "affected the electronic/digital record or its accuracy."
)

_HEADINGS: dict[Statute, tuple[str, str]] = {
    "BSA_63": (
        "THE SCHEDULE [See section 63(4)(c)] — CERTIFICATE",
        "Bharatiya Sakshya Adhiniyam, 2023 (47 of 2023), section 63(4)",
    ),
    "IEA_65B": (
        "CERTIFICATE UNDER SECTION 65B(4)",
        "Indian Evidence Act, 1872, section 65B(4) — for proceedings instituted before "
        "1 July 2024",
    ),
}


@dataclass(frozen=True, slots=True)
class DeviceParticulars:
    """The device block, printed identically in Part A and Part B."""

    source_type: str = "DVR"
    source_type_other: str = ""
    make_and_model: str = ""
    colour: str = ""
    serial_number: str = ""
    identifier: str = ""          # IMEI / UIN / UID / MAC / Cloud ID, as applicable
    other_information: str = ""

    def __post_init__(self) -> None:
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(
                f"source type {self.source_type!r} is not one the Schedule lists; "
                f"use one of {', '.join(SOURCE_TYPES)}"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_type_other": self.source_type_other,
            "make_and_model": self.make_and_model,
            "colour": self.colour,
            "serial_number": self.serial_number,
            "identifier": self.identifier,
            "other_information": self.other_information,
        }


@dataclass(frozen=True, slots=True)
class HashBlock:
    """The Schedule's hash statement. Only the algorithms it names may be ticked."""

    sha1: str = ""
    sha256: str = ""
    md5: str = ""
    other_algorithm: str = ""
    other_value: str = ""

    @property
    def ticked(self) -> tuple[str, ...]:
        out = [name for name, value in
               (("SHA1", self.sha1), ("SHA256", self.sha256), ("MD5", self.md5)) if value]
        if self.other_value:
            out.append(f"Other ({self.other_algorithm or 'unspecified'})")
        return tuple(out)

    def to_json(self) -> dict[str, Any]:
        return {
            "SHA1": self.sha1,
            "SHA256": self.sha256,
            "MD5": self.md5,
            "other_algorithm": self.other_algorithm,
            "other_value": self.other_value,
            "ticked": list(self.ticked),
        }


@dataclass(frozen=True, slots=True)
class Signatory:
    """A person who must sign. Left blank by the tool — see the module docstring."""

    name: str = ""
    parentage: str = ""           # son / daughter / spouse of
    address_or_employer: str = ""
    designation: str = ""         # Part B only
    place: str = ""
    signed_on: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parentage": self.parentage,
            "address_or_employer": self.address_or_employer,
            "designation": self.designation,
            "place": self.place,
            "date": self.signed_on.strftime("%d/%m/%Y") if self.signed_on else "",
            "time_ist": self.signed_on.strftime("%H:%M") if self.signed_on else "",
        }


@dataclass(frozen=True, slots=True)
class Certificate:
    """One certificate, for one instance of submission."""

    case_id: str
    instance: int
    statute: Statute
    device: DeviceParticulars
    hashes: HashBlock
    record_description: str
    hash_report_ref: str
    control_modes: tuple[str, ...] = ()
    part_a: Signatory = field(default_factory=Signatory)
    part_b: Signatory = field(default_factory=Signatory)
    generated_utc: str = ""

    def __post_init__(self) -> None:
        unknown = [m for m in self.control_modes if m not in CONTROL_MODES]
        if unknown:
            raise ValueError(
                f"control mode(s) {', '.join(unknown)} are not on the form; "
                f"use {', '.join(CONTROL_MODES)}"
            )
        if self.instance < 1:
            raise ValueError("instance numbering starts at 1 — one per submission")

    @property
    def heading(self) -> str:
        return _HEADINGS[self.statute][0]

    @property
    def authority(self) -> str:
        return _HEADINGS[self.statute][1]

    @property
    def recital(self) -> str:
        return _RECITAL

    @property
    def unsigned(self) -> tuple[str, ...]:
        """Which parts still need a human. Printed on the form so it cannot be missed."""
        missing = []
        if not self.part_a.name:
            missing.append("Part A (person in lawful control of the device)")
        if not self.part_b.name:
            missing.append("Part B (expert)")
        return tuple(missing)

    def to_json(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "instance": self.instance,
            "statute": self.statute,
            "heading": self.heading,
            "authority": self.authority,
            "record_description": self.record_description,
            "device": self.device.to_json(),
            "control_modes": list(self.control_modes),
            "hashes": self.hashes.to_json(),
            "hash_report_ref": self.hash_report_ref,
            "part_a": self.part_a.to_json(),
            "part_b": self.part_b.to_json(),
            "unsigned": list(self.unsigned),
            "generated_utc": self.generated_utc,
        }


def statute_for(instituted_on: datetime | None) -> Statute:
    """Which certificate applies, by the date the proceeding was instituted.

    The BSA commenced on 1 July 2024. The transition turns on when the *case* began, not
    when the footage was recorded, so an older proceeding still takes the IEA form. With
    no date supplied the current statute is assumed and the report says so.
    """
    if instituted_on is None:
        return "BSA_63"
    return "BSA_63" if instituted_on >= datetime(2024, 7, 1) else "IEA_65B"


def hash_report_rows(artefacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The enclosure the Schedule mandates: one row per artefact, all three digests.

    The form itself has room for a single hash. Everything a reader actually needs —
    which file, how big, which digests — lives here, and the certificate references it.

    `note` carries anything that qualifies a row. An item the case knows about but cannot
    tender appears here with empty hash columns and a note saying why, rather than being
    dropped: an enclosure quietly shorter than the case record is the first thing an
    adversarial reader tries to find.
    """
    rows = []
    for art in artefacts:
        rows.append(
            {
                "artefact": art.get("path") or art.get("kind") or "",
                "kind": art.get("kind") or "",
                "size_bytes": art.get("size_bytes"),
                "MD5": art.get("md5") or "",
                "SHA1": art.get("sha1") or "",
                "SHA256": art.get("sha256") or "",
                "note": art.get("note") or "",
            }
        )
    return sorted(rows, key=lambda r: (str(r["kind"]), str(r["artefact"])))


def tendered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows that are actually being handed over — those with a digest to assert."""
    return [r for r in rows if r.get("SHA256")]


def withheld(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The disclosed-but-not-tendered rows. Printed under their own heading on the form."""
    return [r for r in rows if not r.get("SHA256")]

"""Idempotent demo seed.

    python -m seed.seed              # insert anything missing
    python -m seed.seed --reset      # wipe seeded data first
    python -m seed.seed --quiet      # summary only

Running it twice is a no-op: every row gets a UUID derived from its fixture key, so a
second run finds the same primary keys already present and inserts nothing. That is
also what keeps the dataset reproducible across machines (NFR-4).

The run finishes by *verifying* the fixtures it just wrote — that the duplicate images
really are within the pHash threshold, that the corroboration cluster really is inside
the radius and window, and so on. A failed check exits non-zero, because a seed that
silently stops exercising a later phase's acceptance criterion is worse than no seed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from sqlalchemy import delete
from sqlmodel import Session, select

from app.config import settings
from app.core.geo import haversine_m, offset_metres
from app.core.time import minutes_between, utcnow
from app.db import engine, ensure_storage_paths, init_db
from app.models import Assignment, ProcessEvent, Report, ReportStatus, Responder
from app.services.pipeline import process_pending
from seed import images
from seed.fixtures.reports import (
    EXPECTED_REPORT_COUNT,
    EXPECTED_ZONE_COUNTS,
    REPORT_SPECS,
    ReportSpec,
)
from seed.fixtures.responders import RESPONDER_SPECS
from seed.fixtures.zones import ANOMALOUS, ZONES

# Fixed namespace → the same fixture key always yields the same UUID, on any machine.
SEED_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "rescuenet.ai/seed")

IDEMPOTENCY_PREFIX = "seed-"
SEED_IMAGE_DIR = "seed"


def stable_id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"{kind}:{key}")


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class SeedSummary:
    reports_created: int = 0
    reports_existing: int = 0
    responders_created: int = 0
    responders_existing: int = 0
    images_written: int = 0
    reports_scored: int = 0
    reports_assessed: int = 0
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.passed for check in self.checks)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(Check(name, passed, detail))


def resolve_coordinates(spec: ReportSpec) -> tuple[float, float]:
    if spec.lat is not None and spec.lng is not None:
        return spec.lat, spec.lng
    zone = ZONES[spec.zone]
    return offset_metres(zone.lat, zone.lng, spec.north_m, spec.east_m)


def _wipe(session: Session) -> None:
    """Clear seeded data in foreign-key-safe order."""
    session.exec(delete(ProcessEvent))  # type: ignore[call-overload]
    session.exec(delete(Assignment))  # type: ignore[call-overload]
    session.exec(delete(Report))  # type: ignore[call-overload]
    session.exec(delete(Responder))  # type: ignore[call-overload]
    session.commit()


def _seed_responders(session: Session, summary: SeedSummary) -> None:
    for spec in RESPONDER_SPECS:
        responder_id = stable_id("responder", spec.key)
        if session.get(Responder, responder_id) is not None:
            summary.responders_existing += 1
            continue

        session.add(
            Responder(
                id=responder_id,
                name=spec.name,
                skill=spec.skill,
                lat=spec.lat,
                lng=spec.lng,
                capacity=spec.capacity,
                active_count=spec.active_count,
                status=spec.status,
            )
        )
        summary.responders_created += 1

    session.commit()


# Somewhere clearly not Bengaluru, for the deliberate EXIF mismatch.
_MISMATCH_GPS = (28.6139, 77.2090)  # New Delhi


def _seed_images(summary: SeedSummary, now: datetime) -> dict[str, tuple[str, str]]:
    """Write every referenced image. Returns {image_id: (relative_path, phash)}."""
    directory = settings.media_dir / SEED_IMAGE_DIR
    catalogue: dict[str, tuple[str, str]] = {}

    specs_by_image = {spec.image: spec for spec in REPORT_SPECS if spec.image}

    for image_id in sorted(specs_by_image):
        spec = specs_by_image[image_id]

        gps: tuple[float, float] | None = None
        if spec.image_gps == "match":
            gps = resolve_coordinates(spec)
        elif spec.image_gps == "mismatch":
            gps = _MISMATCH_GPS

        path = images.write(
            image_id,
            directory,
            gps=gps,
            captured_at=now - timedelta(hours=spec.client_hours_ago),
        )
        summary.images_written += 1
        relative = f"{SEED_IMAGE_DIR}/{path.name}"
        catalogue[image_id] = (relative, images.phash_of(path))

    return catalogue


def _seed_reports(
    session: Session,
    catalogue: dict[str, tuple[str, str]],
    now: datetime,
    summary: SeedSummary,
) -> None:
    for spec in REPORT_SPECS:
        report_id = stable_id("report", spec.key)
        if session.get(Report, report_id) is not None:
            summary.reports_existing += 1
            continue

        lat, lng = resolve_coordinates(spec)
        received_hours = (
            spec.client_hours_ago
            if spec.received_hours_ago is None
            else spec.received_hours_ago
        )
        image_path, image_phash = catalogue.get(spec.image or "", (None, None))

        session.add(
            Report(
                id=report_id,
                idempotency_key=f"{IDEMPOTENCY_PREFIX}{spec.key}",
                text=spec.text,
                image_path=image_path,
                image_phash=image_phash,
                lat=lat,
                lng=lng,
                client_created_at=now - timedelta(hours=spec.client_hours_ago),
                received_at=now - timedelta(hours=received_hours),
                reporter_pseudonym=spec.pseudonym,
                # Deliberately unscored: Phases 4 and 5 own these fields.
            )
        )
        summary.reports_created += 1

    session.commit()


def _by_key(session: Session, key: str) -> Report | None:
    return session.exec(
        select(Report).where(Report.idempotency_key == f"{IDEMPOTENCY_PREFIX}{key}")
    ).first()


def _verify(session: Session, summary: SeedSummary) -> None:
    report_count = len(session.exec(select(Report)).all())
    summary.add(
        "report count",
        report_count == EXPECTED_REPORT_COUNT,
        f"{report_count} reports (expected {EXPECTED_REPORT_COUNT})",
    )

    responder_count = len(session.exec(select(Responder)).all())
    summary.add(
        "responder count",
        responder_count == len(RESPONDER_SPECS),
        f"{responder_count} responders (expected {len(RESPONDER_SPECS)})",
    )

    zone_counts: dict[str, int] = {}
    for spec in REPORT_SPECS:
        zone_counts[spec.zone] = zone_counts.get(spec.zone, 0) + 1
    summary.add(
        "zone distribution",
        zone_counts == EXPECTED_ZONE_COUNTS,
        f"{zone_counts}",
    )

    keys = [report.idempotency_key for report in session.exec(select(Report)).all()]
    summary.add(
        "idempotency keys unique",
        len(keys) == len(set(keys)),
        f"{len(set(keys))} distinct keys across {len(keys)} reports",
    )

    # --- Duplicate image pair (Phase 5) ---
    dup_a, dup_b = _by_key(session, "dup-image-a"), _by_key(session, "dup-image-b")
    if dup_a and dup_b and dup_a.image_phash and dup_b.image_phash:
        distance = images.hamming(dup_a.image_phash, dup_b.image_phash)
        threshold = settings.phash_duplicate_distance
        summary.add(
            "duplicate image pair detectable",
            distance <= threshold,
            f"pHash Hamming distance {distance} <= threshold {threshold}",
        )
    else:
        summary.add("duplicate image pair detectable", False, "pair or hashes missing")

    # --- No accidental duplicates among the rest ---
    hashed = [
        report
        for report in session.exec(select(Report)).all()
        if report.image_phash is not None
    ]
    collisions: list[str] = []
    for index, left in enumerate(hashed):
        for right in hashed[index + 1 :]:
            pair = {left.idempotency_key, right.idempotency_key}
            if pair == {f"{IDEMPOTENCY_PREFIX}dup-image-a", f"{IDEMPOTENCY_PREFIX}dup-image-b"}:
                continue
            distance = images.hamming(left.image_phash or "", right.image_phash or "")
            if distance <= settings.phash_duplicate_distance:
                collisions.append(
                    f"{left.idempotency_key}~{right.idempotency_key}({distance})"
                )
    summary.add(
        "no accidental image duplicates",
        not collisions,
        "all other image pairs beyond threshold"
        if not collisions
        else f"unexpected near-duplicates: {', '.join(collisions)}",
    )

    # --- Stale timestamp (Phase 5) ---
    stale = _by_key(session, "stale-timestamp")
    if stale:
        gap_hours = minutes_between(stale.client_created_at, stale.received_at) / 60
        summary.add(
            "stale report penalised",
            gap_hours > 6,
            f"client clock {gap_hours:.1f} h before receipt (threshold 6 h)",
        )
    else:
        summary.add("stale report penalised", False, "fixture missing")

    # --- Newest report is also the most severe (Phase 6) ---
    latest_by_clock = session.exec(
        select(Report).order_by(Report.client_created_at.desc())  # type: ignore[union-attr]
    ).first()
    summary.add(
        "newest report is the critical one",
        latest_by_clock is not None
        and latest_by_clock.idempotency_key == f"{IDEMPOTENCY_PREFIX}latest-critical",
        f"newest = {latest_by_clock.idempotency_key if latest_by_clock else 'none'}",
    )

    # --- Corroboration cluster (Phase 5) ---
    cluster = [
        _by_key(session, "corroborated-1"),
        _by_key(session, "corroborated-2"),
        _by_key(session, "corroborated-3"),
    ]
    if all(cluster):
        radius = settings.corroboration_radius_m
        window = settings.corroboration_window_min
        worst_distance = max(
            haversine_m(a.lat, a.lng, b.lat, b.lng)  # type: ignore[union-attr]
            for i, a in enumerate(cluster)
            for b in cluster[i + 1 :]
        )
        worst_gap = max(
            abs(minutes_between(a.client_created_at, b.client_created_at))  # type: ignore[union-attr]
            for i, a in enumerate(cluster)
            for b in cluster[i + 1 :]
        )
        summary.add(
            "corroboration cluster inside window",
            worst_distance <= radius and worst_gap <= window,
            f"max separation {worst_distance:.0f} m / {worst_gap:.0f} min "
            f"(limits {radius} m / {window} min)",
        )
    else:
        summary.add("corroboration cluster inside window", False, "cluster incomplete")

    # --- Impossible movement (Phase 5) ---
    move_a, move_b = _by_key(session, "impossible-move-a"), _by_key(session, "impossible-move-b")
    if move_a and move_b:
        km = haversine_m(move_a.lat, move_a.lng, move_b.lat, move_b.lng) / 1000
        gap = abs(minutes_between(move_a.client_created_at, move_b.client_created_at))
        summary.add(
            "impossible movement detectable",
            km > 100 and gap <= 10 and move_a.reporter_pseudonym == move_b.reporter_pseudonym,
            f"{km:.0f} km apart in {gap:.0f} min, same pseudonym "
            f"'{move_a.reporter_pseudonym}'",
        )
    else:
        summary.add("impossible movement detectable", False, "pair missing")

    # --- Every report scored, with reasons (Phase 4 acceptance) ---
    all_reports = session.exec(select(Report)).all()
    unscored = [r.idempotency_key for r in all_reports if r.severity_score is None]
    summary.add(
        "every report scored",
        not unscored,
        f"{len(all_reports) - len(unscored)}/{len(all_reports)} reports scored"
        + ("" if not unscored else f"; missing: {', '.join(unscored[:3])}"),
    )

    unexplained = [r.idempotency_key for r in all_reports if not r.severity_reasons]
    summary.add(
        "every score explained",
        not unexplained,
        "all scores carry reason codes (FR-8)"
        if not unexplained
        else f"{len(unexplained)} scores have no reasons",
    )

    # Reason weights must add up to the score, or "explainable" is only decorative.
    mismatched = [
        r.idempotency_key
        for r in all_reports
        if r.severity_score is not None
        and sum(item.get("weight", 0) for item in r.severity_reasons) != r.severity_score
    ]
    summary.add(
        "reasons sum to the score",
        not mismatched,
        "reason weights reconcile with every severity score"
        if not mismatched
        else f"mismatch on: {', '.join(mismatched[:3])}",
    )

    providers = {r.scoring_provider for r in all_reports if r.scoring_provider}
    summary.add(
        "scoring provider recorded",
        bool(providers) and all(providers),
        f"providers in use: {', '.join(sorted(providers)) or 'none'}",
    )

    # --- Authenticity (Phase 5 acceptance) ---
    unassessed = [r.idempotency_key for r in all_reports if r.authenticity_score is None]
    summary.add(
        "every report trust-scored",
        not unassessed,
        f"{len(all_reports) - len(unassessed)}/{len(all_reports)} reports assessed",
    )

    auth_mismatched = [
        r.idempotency_key
        for r in all_reports
        if r.authenticity_score is not None
        and sum(item.get("weight", 0) for item in r.authenticity_reasons) != r.authenticity_score
    ]
    summary.add(
        "trust reasons sum to the score",
        not auth_mismatched,
        "authenticity reasons reconcile with every score"
        if not auth_mismatched
        else f"mismatch on: {', '.join(auth_mismatched[:3])}",
    )

    def _codes(report: Report | None) -> set[str]:
        return {item["code"] for item in report.authenticity_reasons} if report else set()

    dup_b = _by_key(session, "dup-image-b")
    summary.add(
        "duplicate image caught and flagged",
        dup_b is not None
        and "DUPLICATE_IMAGE" in _codes(dup_b)
        and dup_b.status == ReportStatus.FLAGGED,
        f"dup-image-b scored {dup_b.authenticity_score if dup_b else '?'} "
        f"({dup_b.status.value if dup_b else 'missing'})",
    )

    # The original must not be punished for being first.
    dup_a = _by_key(session, "dup-image-a")
    summary.add(
        "original of the pair not penalised",
        dup_a is not None and "DUPLICATE_IMAGE" not in _codes(dup_a),
        f"dup-image-a scored {dup_a.authenticity_score if dup_a else '?'} "
        f"({dup_a.status.value if dup_a else 'missing'})",
    )

    stale_report = _by_key(session, "stale-timestamp")
    summary.add(
        "stale report loses trust",
        stale_report is not None and "STALE_REPORT" in _codes(stale_report),
        f"stale-timestamp scored {stale_report.authenticity_score if stale_report else '?'}",
    )

    cluster_scores = [
        r.authenticity_score
        for key in ("corroborated-1", "corroborated-2", "corroborated-3")
        if (r := _by_key(session, key)) and r.authenticity_score is not None
    ]
    baseline_reports = [
        r.authenticity_score
        for r in all_reports
        if r.authenticity_score is not None and not r.authenticity_reasons[1:]
    ]
    summary.add(
        "corroborated cluster scores higher",
        len(cluster_scores) == 3
        and bool(baseline_reports)
        and min(cluster_scores) > max(baseline_reports),
        f"cluster {cluster_scores} vs uncorroborated baseline {max(baseline_reports, default=0)}",
    )

    exif_match = _by_key(session, "filler-08")
    exif_mismatch = _by_key(session, "filler-10")
    summary.add(
        "EXIF consistency rewarded, mismatch not",
        exif_match is not None
        and "EXIF_CONSISTENT" in _codes(exif_match)
        and exif_mismatch is not None
        and "EXIF_CONSISTENT" not in _codes(exif_mismatch),
        "matching EXIF GPS earns the bonus; out-of-town EXIF does not",
    )

    # FR-15 / TRD §10: no automated path may reject or delete a report.
    rejected = [r.idempotency_key for r in all_reports if r.status == ReportStatus.REJECTED]
    summary.add(
        "nothing auto-rejected",
        not rejected,
        "no report rejected without a human decision"
        if not rejected
        else f"auto-rejected: {', '.join(rejected[:3])}",
    )

    flagged = [r for r in all_reports if r.status == ReportStatus.FLAGGED]
    summary.add(
        "review queue is populated",
        bool(flagged),
        f"{len(flagged)} flagged for human review: "
        f"{', '.join(sorted(r.idempotency_key for r in flagged))}",
    )

    # The demo hinges on this ordering (Phase 6): the newest report is also the worst.
    critical = _by_key(session, "latest-critical")
    aged = _by_key(session, "aged-low-severity")
    if critical and aged and critical.severity_score and aged.severity_score:
        summary.add(
            "severity separates the fixtures",
            critical.severity_score > aged.severity_score,
            f"latest-critical {critical.severity_score} vs aged-low {aged.severity_score}",
        )
    else:
        summary.add("severity separates the fixtures", False, "fixtures unscored")


def run(reset: bool = False, echo: Callable[[str], None] = print) -> SeedSummary:
    summary = SeedSummary()
    now = utcnow()

    ensure_storage_paths()
    init_db()

    with Session(engine) as session:
        if reset:
            _wipe(session)
            echo("Wiped existing reports, responders, assignments and events.")

        _seed_responders(session, summary)
        catalogue = _seed_images(summary, now)
        _seed_reports(session, catalogue, now, summary)

    # Scoring runs after the reports are committed, through exactly the same pipeline
    # ingestion uses — triage, then authenticity. With no API keys configured the remote
    # providers are skipped and the local scorer answers, so a re-seed with the network
    # off still scores everything (Phase 4 acceptance, NFR-2).
    summary.reports_scored, summary.reports_assessed = asyncio.run(process_pending())

    with Session(engine) as session:
        _verify(session, summary)

    _report(summary, echo)
    return summary


def _report(summary: SeedSummary, echo: Callable[[str], None]) -> None:
    echo("")
    echo("Seed summary")
    echo(f"  reports    {summary.reports_created:>3} created, {summary.reports_existing:>3} already present")
    echo(f"  responders {summary.responders_created:>3} created, {summary.responders_existing:>3} already present")
    echo(f"  images     {summary.images_written:>3} written to {settings.media_dir / SEED_IMAGE_DIR}")
    echo(f"  triaged    {summary.reports_scored:>3} newly scored")
    echo(f"  assessed   {summary.reports_assessed:>3} newly authenticity-scored")
    echo("")
    echo("Fixture checks")
    for check in summary.checks:
        echo(f"  {'PASS' if check.passed else 'FAIL'}  {check.name:<34} {check.detail}")
    echo("")
    echo("All checks passed." if summary.ok else "SEED VERIFICATION FAILED.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the RescueNet demo dataset.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete existing reports, responders, assignments and events first",
    )
    parser.add_argument("--quiet", action="store_true", help="print the summary only")
    args = parser.parse_args(argv)

    lines: list[str] = []
    echo: Callable[[str], None] = lines.append if args.quiet else print
    summary = run(reset=args.reset, echo=echo)

    if args.quiet:
        status = "ok" if summary.ok else "FAILED"
        print(
            f"seed {status}: {summary.reports_created} reports created, "
            f"{summary.reports_existing} existing, "
            f"{sum(1 for c in summary.checks if c.passed)}/{len(summary.checks)} checks passed"
        )
        if not summary.ok:
            for line in lines:
                print(line)

    return 0 if summary.ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Validate the seeded demo dataset against real RescueNet behavior."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATASET_PATH = ROOT / "data" / "incidents.json"
IMAGES_DIR = ROOT / "data" / "images"
DEMO_STATE_PATH = ROOT / "data" / "demo_state.json"
FIXED_SERVER_TIMESTAMP = "2026-01-01T12:00:00Z"

os.environ.setdefault("AI_TIMEOUT_SECONDS", "0.25")
os.environ.setdefault("VISION_TIMEOUT_SECONDS", "0.25")
os.environ.setdefault("VISION_MODEL", "__disabled_demo_vision__")
os.environ["AUTHENTICITY_STATE_FILE"] = str(DEMO_STATE_PATH)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_engine.analyze import analyze_report
from authenticity import reset_state


def _wipe_demo_state() -> None:
    if DEMO_STATE_PATH.exists():
        DEMO_STATE_PATH.unlink()
    try:
        reset_state()
    except Exception:
        pass


def _load_records() -> list[dict[str, Any]]:
    with DATASET_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _normalize_record(raw: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "report_id": raw["report_id"],
        "description": raw["description"],
        "latitude": raw.get("latitude"),
        "longitude": raw.get("longitude"),
        "client_timestamp": raw.get("client_timestamp"),
        "server_timestamp": FIXED_SERVER_TIMESTAMP,
        "reporter_pseudonym": raw.get("reporter_pseudonym"),
    }
    image_path = raw.get("image_path")
    if image_path:
        payload["image"] = base64.b64encode((IMAGES_DIR / image_path).read_bytes()).decode("utf-8")
    else:
        payload["image"] = None
    return payload


def _flatten_reason_codes(groups: Any) -> list[str]:
    codes: list[str] = []
    if not groups:
        return codes
    if isinstance(groups, list):
        for item in groups:
            if item is not None:
                codes.append(str(item))
    else:
        codes.append(str(groups))
    return sorted(set(codes))


async def _run_once() -> list[dict[str, Any]]:
    _wipe_demo_state()
    results: list[dict[str, Any]] = []
    for record in _load_records():
        results.append(await analyze_report(_normalize_record(record)))
    return results


def _compare_run_results(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    if len(a) != len(b):
        return False
    for left, right in zip(a, b):
        if left.get("report_id") != right.get("report_id"):
            return False
        left_sig = {
            "incident_type": left.get("incident_type"),
            "severity_band": left.get("severity", {}).get("band"),
            "severity_score": left.get("severity", {}).get("score"),
            "authenticity_score": left.get("authenticity", {}).get("score"),
            "verification_status": left.get("verification_status"),
            "reason_codes": _flatten_reason_codes(left.get("authenticity", {}).get("reason_codes", []))
            + _flatten_reason_codes(left.get("severity", {}).get("reason_codes", [])),
        }
        right_sig = {
            "incident_type": right.get("incident_type"),
            "severity_band": right.get("severity", {}).get("band"),
            "severity_score": right.get("severity", {}).get("score"),
            "authenticity_score": right.get("authenticity", {}).get("score"),
            "verification_status": right.get("verification_status"),
            "reason_codes": _flatten_reason_codes(right.get("authenticity", {}).get("reason_codes", []))
            + _flatten_reason_codes(right.get("severity", {}).get("reason_codes", [])),
        }
        if left_sig != right_sig:
            return False
    return True


def _scenario_checks(results: list[dict[str, Any]]) -> dict[str, bool]:
    result_map = {item["report_id"]: item for item in results}
    checks: dict[str, bool] = {}

    def ok(report_id: str, label: str, condition: bool) -> None:
        checks[f"{report_id}:{label}"] = bool(condition)

    for key in ["RPT-001", "RPT-005", "RPT-006", "RPT-007", "RPT-008", "RPT-009", "RPT-010", "RPT-011", "RPT-012"]:
        item = result_map.get(key)
        if item is None:
            ok(key, "exists", False)
            continue
        codes = _flatten_reason_codes(item.get("authenticity", {}).get("reason_codes", [])) + _flatten_reason_codes(item.get("severity", {}).get("reason_codes", []))
        status = item.get("verification_status")

        if key == "RPT-001":
            ok(key, "severity_high_or_critical", item.get("severity", {}).get("band") in {"HIGH", "CRITICAL"})
        elif key == "RPT-005":
            ok(key, "duplicate_and_flagged", "IMAGE_EXACT_DUPLICATE" in codes and status == "FLAGGED")
        elif key == "RPT-006":
            ok(key, "invalid_geo", "GEO_INVALID" in codes)
        elif key == "RPT-007":
            ok(key, "timestamp_implausible", "TIME_IMPLAUSIBLE" in codes)
        elif key == "RPT-008":
            ok(key, "impossible_movement", "IMPOSSIBLE_MOVEMENT" in codes)
        elif key in {"RPT-009", "RPT-010", "RPT-011"}:
            ok(key, "corroboration", "CORROBORATED" in codes or "WEAK_CORROBORATION" in codes)
        elif key == "RPT-012":
            ok(key, "text_only", item.get("incident_type") is not None and item.get("severity", {}).get("band") in {"LOW", "MEDIUM", "HIGH", "CRITICAL"})

    return checks


async def main() -> int:
    runs: list[list[dict[str, Any]]] = [await _run_once() for _ in range(3)]
    if not all(_compare_run_results(runs[0], run) for run in runs[1:]):
        print("Two- and three-run reproducibility: FAIL")
        return 1

    first = runs[0]
    checks = _scenario_checks(first)
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("Scenario assertions: FAIL")
        for name in failed:
            print(f"  - {name}")
        return 1

    print("Scenario assertions: PASS")
    print("Three-run reproducibility: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

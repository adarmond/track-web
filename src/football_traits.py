from __future__ import annotations

import re
from typing import Any

import pandas as pd


TRAIT_COLUMNS = [
    "Athlete ID", "Name", "School", "Position", "State",
    "Trait", "Evidence Status", "Evidence Events", "Best Marks",
    "Verified Performances", "Development", "Data Depth", "Evidence Note",
]


def clean(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _event_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", clean(value).lower())


def trait_for_event(event: Any) -> str:
    key = _event_key(event)
    if not key:
        return "OTHER TRACK EVIDENCE"

    if any(x in key for x in ("longjump", "triplejump", "highjump", "polevault")):
        return "EXPLOSIVENESS"
    if any(x in key for x in ("shotput", "shot", "discus", "javelin", "hammer", "weightthrow")):
        return "POWER / THROWS"
    if any(x in key for x in ("400m", "400meter", "400metre", "800m", "800meter", "800metre", "300mhurdles", "400mhurdles")):
        return "SPEED ENDURANCE"
    if any(x in key for x in ("100m", "100meter", "100metre", "200m", "200meter", "200metre", "100mhurdles", "110mhurdles")):
        return "SPEED"
    return "OTHER TRACK EVIDENCE"


def _depth(count: int) -> str:
    if count <= 0:
        return "NO DATA"
    if count == 1:
        return "SINGLE VERIFIED MARK"
    if count <= 3:
        return "MULTIPLE VERIFIED MARKS"
    return "DEEP VERIFIED HISTORY"


def _development(rows: pd.DataFrame) -> str:
    values = pd.to_numeric(rows.get("Improvement", pd.Series(dtype=float)), errors="coerce").dropna()
    if values.empty:
        return "NO TREND YET"
    best = float(values.max())
    if best > 1e-9:
        return "IMPROVING"
    if (values.abs() <= 1e-9).all():
        return "STABLE / NO MEASURED IMPROVEMENT"
    return "MIXED / LIMITED TREND"


def build_football_traits(
    profiles: pd.DataFrame,
    event_bests: pd.DataFrame,
) -> pd.DataFrame:
    profiles = profiles.copy() if isinstance(profiles, pd.DataFrame) else pd.DataFrame()
    event_bests = event_bests.copy() if isinstance(event_bests, pd.DataFrame) else pd.DataFrame()

    rows: list[dict[str, Any]] = []

    for _, profile in profiles.iterrows():
        aid = clean(profile.get("Athlete ID", ""))
        name = clean(profile.get("Name", ""))
        school = clean(profile.get("School", ""))
        position = clean(profile.get("Position", ""))
        state = clean(profile.get("State", ""))

        athlete_events = event_bests[event_bests["Athlete ID"].fillna("").astype(str) == aid].copy() if not event_bests.empty else pd.DataFrame()

        if athlete_events.empty:
            rows.append({
                "Athlete ID": aid, "Name": name, "School": school,
                "Position": position, "State": state, "Trait": "DATA COVERAGE",
                "Evidence Status": "NO VERIFIED TRACK EVIDENCE", "Evidence Events": "",
                "Best Marks": "", "Verified Performances": 0,
                "Development": "NO TREND YET", "Data Depth": "NO DATA",
                "Evidence Note": "No verified track performances are currently stored for this athlete.",
            })
            continue

        athlete_events["__trait"] = athlete_events["Event"].map(trait_for_event)

        for trait, group in athlete_events.groupby("__trait", sort=False):
            performances = int(pd.to_numeric(group["Performances"], errors="coerce").fillna(0).sum())
            evidence_events = []
            best_marks = []
            for _, event_row in group.iterrows():
                event = clean(event_row.get("Event", ""))
                pb = clean(event_row.get("PB", ""))
                if event:
                    evidence_events.append(event)
                    best_marks.append(f"{event}: {pb}" if pb else event)

            rows.append({
                "Athlete ID": aid,
                "Name": name,
                "School": school,
                "Position": position,
                "State": state,
                "Trait": trait,
                "Evidence Status": "VERIFIED TRACK EVIDENCE",
                "Evidence Events": ", ".join(evidence_events),
                "Best Marks": " | ".join(best_marks),
                "Verified Performances": performances,
                "Development": _development(group),
                "Data Depth": _depth(performances),
                "Evidence Note": "Descriptive track evidence only; use alongside football film, testing, size, production and position context.",
            })

    result = pd.DataFrame(rows, columns=TRAIT_COLUMNS)
    if not result.empty:
        result = result.sort_values(["Position", "Name", "Trait"], kind="stable").reset_index(drop=True)
    return result


def trait_summary(traits: pd.DataFrame) -> dict[str, int]:
    traits = traits if isinstance(traits, pd.DataFrame) else pd.DataFrame()
    summary = {
        "athletes_with_speed_evidence": 0,
        "athletes_with_explosiveness_evidence": 0,
        "athletes_with_power_evidence": 0,
        "athletes_with_speed_endurance_evidence": 0,
        "athletes_without_verified_evidence": 0,
    }
    if traits.empty:
        return summary

    verified = traits[traits["Evidence Status"] == "VERIFIED TRACK EVIDENCE"]
    mapping = {
        "SPEED": "athletes_with_speed_evidence",
        "EXPLOSIVENESS": "athletes_with_explosiveness_evidence",
        "POWER / THROWS": "athletes_with_power_evidence",
        "SPEED ENDURANCE": "athletes_with_speed_endurance_evidence",
    }
    for trait, key in mapping.items():
        summary[key] = int(verified.loc[verified["Trait"] == trait, "Athlete ID"].nunique())

    no_data = traits[traits["Evidence Status"] == "NO VERIFIED TRACK EVIDENCE"]
    summary["athletes_without_verified_evidence"] = int(no_data["Athlete ID"].nunique())
    return summary

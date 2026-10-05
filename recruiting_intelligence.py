from __future__ import annotations

import re
from typing import Any

import pandas as pd


PROFILE_COLUMNS = [
    "Athlete ID", "Name", "School", "Position", "State",
    "Verified Performances", "Events", "Latest Meet Date", "Latest Meet",
    "Primary Source", "Data Coverage",
]

EVENT_COLUMNS = [
    "Athlete ID", "Name", "School", "Position", "State", "Event",
    "Performances", "First Date", "First Mark", "Latest Date", "Latest Mark",
    "PB", "SB", "PB Date", "Improvement", "Improvement Display",
    "Legal Performances", "Wind Aided Performances", "Source Count",
]


def clean(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def canonical_id(value: Any) -> str:
    return clean(value)[:12]


def _column(df: pd.DataFrame, *names: str) -> str | None:
    lookup = {re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in df.columns}
    for name in names:
        key = re.sub(r"[^a-z0-9]", "", name.lower())
        if key in lookup:
            return lookup[key]
    return None


def normalize_event(value: Any) -> str:
    raw = clean(value)
    compact = re.sub(r"\s+", " ", raw).strip()
    return compact


def _event_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_event(value).lower())


def event_is_time_based(event: Any) -> bool:
    key = _event_key(event)
    if not key:
        return False
    field_words = (
        "longjump", "triplejump", "highjump", "polevault", "shot",
        "discus", "javelin", "hammer", "weightthrow",
    )
    if any(word in key for word in field_words):
        return False
    return bool(re.search(r"\d", key)) or any(x in key for x in ("mile", "relay", "hurdle"))


def _numeric_series(group: pd.DataFrame) -> pd.Series:
    numeric_col = _column(group, "Mark Numeric")
    if numeric_col:
        return pd.to_numeric(group[numeric_col], errors="coerce")

    mark_col = _column(group, "Mark")
    if not mark_col:
        return pd.Series(index=group.index, dtype=float)

    def parse_mark(value: Any):
        text = clean(value)
        try:
            return float(text)
        except Exception:
            pass
        # Feet-inches, e.g. 21-10 or 46-02.25. Convert to inches for comparisons.
        m = re.fullmatch(r"\s*(\d+)\s*[-']\s*(\d+(?:\.\d+)?)\s*(?:\"|in)?\s*", text)
        if m:
            return float(m.group(1)) * 12.0 + float(m.group(2))
        # Metric value such as 5.77m.
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*m\s*", text.lower())
        if m:
            return float(m.group(1))
        return None

    return group[mark_col].map(parse_mark)


def _format_improvement(event: str, first_numeric: float | None, best_numeric: float | None) -> tuple[float | None, str]:
    if first_numeric is None or best_numeric is None or pd.isna(first_numeric) or pd.isna(best_numeric):
        return None, ""
    if event_is_time_based(event):
        delta = float(first_numeric) - float(best_numeric)
        return delta, f"{delta:+.2f} sec" if abs(delta) > 1e-12 else "0.00 sec"
    delta = float(best_numeric) - float(first_numeric)
    return delta, f"{delta:+.2f}" if abs(delta) > 1e-12 else "0.00"


def build_recruiting_intelligence(
    athletes: pd.DataFrame,
    history: pd.DataFrame,
    current_season: int | None = None,
) -> dict[str, pd.DataFrame]:
    athletes = athletes.copy() if isinstance(athletes, pd.DataFrame) else pd.DataFrame()
    history = history.copy() if isinstance(history, pd.DataFrame) else pd.DataFrame()

    athlete_rows = []
    event_rows = []

    aid_col_a = _column(athletes, "Athlete ID")
    name_col_a = _column(athletes, "Name", "Athlete")
    school_col_a = _column(athletes, "School")
    pos_col_a = _column(athletes, "Position", "Pos")
    state_col_a = _column(athletes, "State")

    aid_col_h = _column(history, "Athlete ID")
    name_col_h = _column(history, "Name", "Athlete")
    school_col_h = _column(history, "School")
    event_col = _column(history, "Event")
    date_col = _column(history, "Meet Date")
    meet_col = _column(history, "Meet")
    mark_col = _column(history, "Mark")
    season_col = _column(history, "Season")
    source_col = _column(history, "Source")
    legal_col = _column(history, "Legal Mark")
    wind_aided_col = _column(history, "Wind Aided")

    if date_col:
        history["__date"] = pd.to_datetime(history[date_col], errors="coerce")
    else:
        history["__date"] = pd.NaT

    for _, athlete in athletes.iterrows():
        aid = canonical_id(athlete.get(aid_col_a, "") if aid_col_a else "")
        name = clean(athlete.get(name_col_a, "") if name_col_a else "")
        school = clean(athlete.get(school_col_a, "") if school_col_a else "")
        position = clean(athlete.get(pos_col_a, "") if pos_col_a else "")
        state = clean(athlete.get(state_col_a, "") if state_col_a else "")

        if history.empty:
            athlete_history = history.copy()
        elif aid and aid_col_h:
            athlete_history = history[history[aid_col_h].map(canonical_id) == aid].copy()
        elif name_col_h:
            mask = history[name_col_h].fillna("").astype(str).str.strip().str.casefold() == name.casefold()
            if school and school_col_h:
                mask &= history[school_col_h].fillna("").astype(str).str.strip().str.casefold() == school.casefold()
            athlete_history = history[mask].copy()
        else:
            athlete_history = history.iloc[0:0].copy()

        athlete_history = athlete_history.sort_values("__date", kind="stable") if not athlete_history.empty else athlete_history
        latest = athlete_history.iloc[-1] if not athlete_history.empty else None
        sources = []
        if source_col and not athlete_history.empty:
            sources = sorted({clean(v) for v in athlete_history[source_col] if clean(v)})
        events = []
        if event_col and not athlete_history.empty:
            events = sorted({normalize_event(v) for v in athlete_history[event_col] if normalize_event(v)})

        athlete_rows.append({
            "Athlete ID": aid,
            "Name": name,
            "School": school,
            "Position": position,
            "State": state,
            "Verified Performances": int(len(athlete_history)),
            "Events": ", ".join(events),
            "Latest Meet Date": clean(latest.get(date_col, "")) if latest is not None and date_col else "",
            "Latest Meet": clean(latest.get(meet_col, "")) if latest is not None and meet_col else "",
            "Primary Source": sources[0] if len(sources) == 1 else ("Multiple" if sources else ""),
            "Data Coverage": "VERIFIED RESULTS" if len(athlete_history) else "NO VERIFIED PERFORMANCES",
        })

        if athlete_history.empty or not event_col:
            continue

        for event, group in athlete_history.groupby(athlete_history[event_col].map(normalize_event), dropna=False):
            event = normalize_event(event)
            if not event:
                continue
            group = group.sort_values("__date", kind="stable").copy()
            numeric = _numeric_series(group)
            valid_numeric = numeric.dropna()
            time_based = event_is_time_based(event)

            best_idx = None
            if not valid_numeric.empty:
                best_idx = valid_numeric.idxmin() if time_based else valid_numeric.idxmax()

            first = group.iloc[0]
            latest_event = group.iloc[-1]
            best = group.loc[best_idx] if best_idx is not None else None

            first_num = numeric.loc[first.name] if first.name in numeric.index else None
            best_num = numeric.loc[best_idx] if best_idx is not None else None
            improvement, improvement_display = _format_improvement(event, first_num, best_num)

            season_group = group
            if current_season is not None and season_col:
                season_values = pd.to_numeric(group[season_col], errors="coerce")
                current = group[season_values == int(current_season)]
                if not current.empty:
                    season_group = current
            season_numeric = _numeric_series(season_group).dropna()
            sb_idx = None
            if not season_numeric.empty:
                sb_idx = season_numeric.idxmin() if time_based else season_numeric.idxmax()
            sb_row = season_group.loc[sb_idx] if sb_idx is not None else None

            legal_count = 0
            if legal_col:
                legal_count = int(group[legal_col].fillna("").astype(str).str.upper().isin(["YES", "Y", "TRUE", "LEGAL", "1"]).sum())
            wind_aided_count = 0
            if wind_aided_col:
                wind_aided_count = int(group[wind_aided_col].fillna("").astype(str).str.upper().isin(["YES", "Y", "TRUE", "WIND AIDED", "1"]).sum())

            event_sources = set()
            if source_col:
                event_sources = {clean(v) for v in group[source_col] if clean(v)}

            event_rows.append({
                "Athlete ID": aid,
                "Name": name,
                "School": school,
                "Position": position,
                "State": state,
                "Event": event,
                "Performances": int(len(group)),
                "First Date": clean(first.get(date_col, "")) if date_col else "",
                "First Mark": clean(first.get(mark_col, "")) if mark_col else "",
                "Latest Date": clean(latest_event.get(date_col, "")) if date_col else "",
                "Latest Mark": clean(latest_event.get(mark_col, "")) if mark_col else "",
                "PB": clean(best.get(mark_col, "")) if best is not None and mark_col else "",
                "SB": clean(sb_row.get(mark_col, "")) if sb_row is not None and mark_col else "",
                "PB Date": clean(best.get(date_col, "")) if best is not None and date_col else "",
                "Improvement": improvement,
                "Improvement Display": improvement_display,
                "Legal Performances": legal_count,
                "Wind Aided Performances": wind_aided_count,
                "Source Count": len(event_sources),
            })

    profiles = pd.DataFrame(athlete_rows, columns=PROFILE_COLUMNS)
    event_bests = pd.DataFrame(event_rows, columns=EVENT_COLUMNS)

    if not event_bests.empty:
        event_bests = event_bests.sort_values(["Name", "Event"], kind="stable").reset_index(drop=True)
    if not profiles.empty:
        profiles = profiles.sort_values(["Position", "Name"], kind="stable").reset_index(drop=True)

    return {"profiles": profiles, "event_bests": event_bests}

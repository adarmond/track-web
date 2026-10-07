from __future__ import annotations

import json
import hmac
import hashlib
import unicodedata
from io import BytesIO
import requests
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.recruiting_intelligence import build_recruiting_intelligence
from src.football_traits import build_football_traits

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
HISTORY_FILE = DATA_DIR / "performance_history.csv"
SNAPSHOT_FILE = DATA_DIR / "recruiting_intelligence_snapshot.json"
WEEKLY_REPORT = REPORTS_DIR / "weekly_track_report.xlsx"


st.set_page_config(
    page_title="Nevada Football Recruiting Intelligence",
    page_icon="🏈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Step 9.2.1: public code repository, private data.
# Secrets live in Streamlit Community Cloud settings, never in GitHub.
def require_access() -> None:
    try:
        expected = str(st.secrets["APP_PASSWORD"])
    except Exception:
        st.error("APP_PASSWORD is not configured in Streamlit Secrets.")
        st.stop()

    if st.session_state.get("_site_authenticated") is True:
        return

    st.title("Nevada Football Recruiting Intelligence")
    st.caption("Internal recruiting operations")
    supplied = st.text_input("Access password", type="password")
    if st.button("Sign in", type="primary"):
        if supplied and hmac.compare_digest(supplied, expected):
            st.session_state["_site_authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()


def supabase_settings() -> tuple[str, str, str]:
    try:
        url = str(st.secrets["SUPABASE_URL"]).rstrip("/")
        key = str(st.secrets["SUPABASE_SERVICE_ROLE_KEY"])
        bucket = str(st.secrets.get("SUPABASE_BUCKET", "recruiting-web"))
        return url, key, bucket
    except Exception as exc:
        st.error(f"Supabase secrets are not configured: {exc}")
        st.stop()


def storage_headers() -> dict[str, str]:
    _, key, _ = supabase_settings()
    return {"Authorization": f"Bearer {key}", "apikey": key}


def download_private_object(object_path: str) -> bytes:
    url, _, bucket = supabase_settings()
    endpoint = f"{url}/storage/v1/object/authenticated/{bucket}/{object_path}"
    response = requests.get(endpoint, headers=storage_headers(), timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"{object_path} returned HTTP {response.status_code}")
    return response.content


def upload_private_object(object_path: str, payload: bytes, content_type: str) -> None:
    url, _, bucket = supabase_settings()
    endpoint = f"{url}/storage/v1/object/{bucket}/{object_path}"
    headers = storage_headers() | {
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    response = requests.post(endpoint, headers=headers, data=payload, timeout=30)
    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Could not save {object_path} to private storage "
            f"(HTTP {response.status_code}): {response.text[:300]}"
        )


def sync_private_web_data() -> bool:
    files = {
        "data/athletes.csv": ROOT / "data" / "athletes.csv",
        "data/performance_history.csv": ROOT / "data" / "performance_history.csv",
        "data/recruiting_intelligence_snapshot.json": ROOT / "data" / "recruiting_intelligence_snapshot.json",
        "reports/weekly_track_report.xlsx": ROOT / "reports" / "weekly_track_report.xlsx",
    }
    for object_path, local_path in files.items():
        try:
            payload = download_private_object(object_path)
        except Exception as exc:
            st.error(f"Could not load private recruiting data: {exc}")
            st.stop()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(payload)
    return True


def refresh_private_web_data() -> None:
    load_history.clear()
    load_athletes.clear()
    sync_private_web_data()


require_access()
sync_private_web_data()

st.markdown("""
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1500px;}
[data-testid="stSidebar"] {border-right: 1px solid rgba(128,128,128,.22);}
.nv-title {font-size:2.05rem; font-weight:800; letter-spacing:-.035em; margin-bottom:.05rem;}
.nv-sub {opacity:.68; margin-bottom:1rem;}
.nv-card {border:1px solid rgba(128,128,128,.24); border-radius:14px; padding:14px 16px; margin-bottom:10px;}
.nv-kicker {font-size:.76rem; font-weight:700; opacity:.62; text-transform:uppercase; letter-spacing:.08em;}
.nv-value {font-size:1.1rem; font-weight:750; margin-top:3px;}
.status-ok {font-weight:750;}
.small-note {font-size:.84rem; opacity:.68;}\n.hero {border:1px solid rgba(128,128,128,.20);border-radius:18px;padding:18px 20px;margin:4px 0 18px}.hero-name{font-size:1.65rem;font-weight:850}.hero-meta{opacity:.68}.section-kicker{font-size:.74rem;font-weight:800;opacity:.60;text-transform:uppercase;letter-spacing:.10em}[data-testid="stMetric"]{border:1px solid rgba(128,128,128,.18);border-radius:14px;padding:10px 14px}
</style>
""", unsafe_allow_html=True)


def clean(v: Any) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass
    return str(v).strip()


def normalize_header(value: Any) -> str:
    return clean(value).casefold().replace("_", " ").strip()


def find_upload_column(df: pd.DataFrame, names: list[str]) -> str | None:
    lookup = {normalize_header(c): c for c in df.columns}
    for name in names:
        hit = lookup.get(normalize_header(name))
        if hit is not None:
            return hit
    return None


def stable_web_athlete_id(name: str, school: str) -> str:
    raw = f"{name.strip().casefold()}|{school.strip().casefold()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def parse_roster_excel(file_bytes: bytes) -> pd.DataFrame:
    raw = pd.read_excel(BytesIO(file_bytes), dtype=str).fillna("")
    name_col = find_upload_column(raw, ["Name", "Athlete", "Player", "Player Name", "Athlete Name"])
    if name_col is None:
        raise ValueError("The workbook needs a Name column.")

    school_col = find_upload_column(raw, ["School", "High School", "HS"])
    position_col = find_upload_column(raw, ["Position", "Pos"])
    state_col = find_upload_column(raw, ["State", "ST"])

    out = pd.DataFrame()
    out["Name"] = raw[name_col].astype(str).str.strip()
    out["School"] = raw[school_col].astype(str).str.strip() if school_col else ""
    out["Position"] = raw[position_col].astype(str).str.strip() if position_col else ""
    out["State"] = raw[state_col].astype(str).str.strip() if state_col else ""
    out = out[out["Name"] != ""].copy()
    out = out.drop_duplicates(subset=["Name", "School"], keep="first").reset_index(drop=True)
    return out


def merge_uploaded_roster(existing_raw: pd.DataFrame, incoming: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    existing = normalize_athletes(existing_raw)
    existing_lookup: dict[tuple[str, str], str] = {}
    for _, row in existing.iterrows():
        key = (clean(row["Name"]).casefold(), clean(row["School"]).casefold())
        if key[0]:
            existing_lookup[key] = clean(row["Athlete ID"])

    rows = []
    new_count = 0
    existing_count = 0
    for _, row in incoming.iterrows():
        name = clean(row["Name"])
        school = clean(row["School"])
        key = (name.casefold(), school.casefold())
        athlete_id = existing_lookup.get(key, "")
        if athlete_id:
            existing_count += 1
        else:
            athlete_id = stable_web_athlete_id(name, school)
            new_count += 1
        rows.append({
            "Athlete ID": athlete_id,
            "Name": name,
            "School": school,
            "Position": clean(row["Position"]),
            "State": clean(row["State"]),
        })

    # Step 9.3 treats the uploaded workbook as the current recruiting roster.
    merged = pd.DataFrame(rows, columns=["Athlete ID", "Name", "School", "Position", "State"])
    return merged, new_count, existing_count


def roster_master_xlsx(roster: pd.DataFrame) -> bytes:
    output = BytesIO()
    master = roster[["Position", "Name", "School", "State"]].copy()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        master.to_excel(writer, index=False, sheet_name="Sheet1")
    return output.getvalue()


def save_roster_to_private_storage(roster: pd.DataFrame) -> None:
    csv_bytes = roster.to_csv(index=False).encode("utf-8")
    xlsx_bytes = roster_master_xlsx(roster)
    upload_private_object("data/athletes.csv", csv_bytes, "text/csv")
    upload_private_object(
        "inputs/2026 Nevada Track.xlsx",
        xlsx_bytes,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    (DATA_DIR / "athletes.csv").write_bytes(csv_bytes)
    load_athletes.clear()

def github_tracker_settings() -> tuple[str, str, str, str]:
    try:
        owner = str(st.secrets.get("GITHUB_TRACKER_OWNER", "adarmond"))
        repo = str(st.secrets.get("GITHUB_TRACKER_REPO", "track"))
        workflow = str(st.secrets.get("GITHUB_TRACKER_WORKFLOW", "run-tracker.yml"))
        token = str(st.secrets["GITHUB_TRACKER_TOKEN"])
        return owner, repo, workflow, token
    except Exception as exc:
        raise RuntimeError(f"GitHub tracker control is not configured: {exc}")


def github_headers() -> dict[str, str]:
    _, _, _, token = github_tracker_settings()
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def request_tracker_run() -> None:
    owner, repo, workflow, _ = github_tracker_settings()
    endpoint = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches"
    response = requests.post(
        endpoint,
        headers=github_headers(),
        json={"ref": "main"},
        timeout=30,
    )
    if response.status_code != 204:
        raise RuntimeError(
            f"GitHub did not accept the run request (HTTP {response.status_code}): "
            f"{response.text[:300]}"
        )


def latest_tracker_run() -> dict[str, Any] | None:
    owner, repo, workflow, _ = github_tracker_settings()
    endpoint = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow}/runs"
    response = requests.get(
        endpoint,
        headers=github_headers(),
        params={"branch": "main", "per_page": 1},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Could not read tracker status (HTTP {response.status_code}): "
            f"{response.text[:300]}"
        )
    runs = response.json().get("workflow_runs", [])
    return runs[0] if runs else None


def github_run_is_active(run: dict[str, Any] | None) -> bool:
    return bool(run and run.get("status") in {"queued", "in_progress", "waiting", "pending"})


def format_github_time(value: Any) -> str:
    raw = clean(value)
    if not raw:
        return "—"
    try:
        return pd.to_datetime(raw, utc=True).tz_convert("US/Pacific").strftime("%Y-%m-%d %I:%M %p PT")
    except Exception:
        return raw

def find_athlete_file() -> Path | None:
    candidates = [
        DATA_DIR / "athletes.csv",
        ROOT / "athletes.csv",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


@st.cache_data(show_spinner=False)
def load_history(path: str, mtime: float) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str).fillna("")
    return df


@st.cache_data(show_spinner=False)
def load_athletes(path: str, mtime: float) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str).fillna("")
    return df


def normalize_athletes(df: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "Athlete ID": ["Athlete ID", "athlete_id", "id"],
        "Name": ["Name", "name", "Athlete"],
        "School": ["School", "school"],
        "Position": ["Position", "position", "Pos"],
        "State": ["State", "state"],
    }
    out = pd.DataFrame(index=df.index)
    for target, options in aliases.items():
        found = next((c for c in options if c in df.columns), None)
        out[target] = df[found].astype(str).str.strip() if found else ""
    return out


def snapshot_payload() -> dict:
    if not SNAPSHOT_FILE.exists():
        return {}
    try:
        return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def snapshot_alerts(payload: dict) -> list[dict]:
    for key in ("alerts", "weekly_alerts", "Weekly Alerts"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def fmt_latest(row: pd.Series) -> str:
    mark = clean(row.get("Latest Mark", ""))
    event = clean(row.get("Event", ""))
    date = clean(row.get("Latest Date", ""))
    if not mark and not event:
        return "—"
    bits = [x for x in [event, mark, date] if x]
    return " · ".join(bits)


def best_marks(event_rows: pd.DataFrame) -> str:
    if event_rows.empty:
        return "—"
    bits = []
    for _, r in event_rows.iterrows():
        event = clean(r.get("Event", ""))
        pb = clean(r.get("PB", ""))
        if event and pb:
            bits.append(f"{event}: {pb}")
    return " | ".join(bits) if bits else "—"


def trait_summary(traits: pd.DataFrame, athlete_id: str) -> str:
    if traits is None or traits.empty:
        return "No verified trait evidence"
    aid_col = "Athlete ID" if "Athlete ID" in traits.columns else None
    subset = traits[traits[aid_col].astype(str) == athlete_id] if aid_col else pd.DataFrame()
    if subset.empty:
        return "No verified trait evidence"
    parts = []
    for col in ["Speed", "Explosiveness", "Power", "Speed Endurance"]:
        if col in subset.columns:
            val = clean(subset.iloc[0].get(col, ""))
            if val and "NO VERIFIED" not in val.upper():
                parts.append(f"{col}: {val}")
    return " | ".join(parts) if parts else "No verified trait evidence"


def athlete_history(history: pd.DataFrame, athlete_id: str, name: str) -> pd.DataFrame:
    if history.empty:
        return history
    if "Athlete ID" in history.columns and athlete_id:
        hit = history[history["Athlete ID"].astype(str) == athlete_id]
        if not hit.empty:
            return hit.copy()
    if "Name" in history.columns:
        return history[history["Name"].astype(str).str.casefold() == name.casefold()].copy()
    return pd.DataFrame()



def normalized_identity_text(value: Any) -> str:
    text = clean(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


def athlete_identity_keys(row: pd.Series) -> set[str]:
    keys: set[str] = set()
    aid = normalized_identity_text(row.get("Athlete ID", ""))
    name = normalized_identity_text(row.get("Name", ""))
    school = normalized_identity_text(row.get("School", ""))
    if aid:
        keys.add(f"id:{aid}")
    if name and school:
        keys.add(f"name_school:{name}|{school}")
    if name:
        keys.add(f"name:{name}")
    return keys


def verified_identity_keys(history_df: pd.DataFrame) -> set[str]:
    keys: set[str] = set()
    for _, row in history_df.iterrows():
        keys.update(athlete_identity_keys(row))
    return keys


def athlete_has_verified_history(row: pd.Series, history_keys: set[str]) -> bool:
    keys = athlete_identity_keys(row)
    strong = {k for k in keys if k.startswith("id:") or k.startswith("name_school:")}
    if strong & history_keys:
        return True
    return bool({k for k in keys if k.startswith("name:")} & history_keys)


def verified_athlete_ids(athlete_df: pd.DataFrame, history_df: pd.DataFrame) -> set[str]:
    history_keys = verified_identity_keys(history_df)
    return {clean(row.get("Athlete ID", "")) for _, row in athlete_df.iterrows()
            if clean(row.get("Athlete ID", "")) and athlete_has_verified_history(row, history_keys)}


def history_with_dates(history_df: pd.DataFrame) -> pd.DataFrame:
    out = history_df.copy()
    if out.empty:
        out["_Date"] = pd.Series(dtype="datetime64[ns]")
        return out
    if "Meet Date" in out.columns:
        out["_Date"] = pd.to_datetime(out["Meet Date"], errors="coerce")
    else:
        out["_Date"] = pd.NaT
    return out


def truthy_flag(value: Any) -> bool:
    return clean(value).casefold() in {"yes", "true", "1", "y", "pb", "sb"}


def recent_verified_results(history_df: pd.DataFrame, limit: int = 8) -> pd.DataFrame:
    out = history_with_dates(history_df)
    if out.empty:
        return out
    return out.sort_values("_Date", ascending=False, na_position="last").head(limit)


def recent_standouts(history_df: pd.DataFrame, limit: int = 6) -> pd.DataFrame:
    """Recent verified PB/SB performances. No synthetic player grade is created."""
    out = history_with_dates(history_df)
    if out.empty:
        return out
    pb = out["PB"].map(truthy_flag) if "PB" in out.columns else pd.Series(False, index=out.index)
    sb = out["SB"].map(truthy_flag) if "SB" in out.columns else pd.Series(False, index=out.index)
    flagged = out[pb | sb].copy()
    if flagged.empty:
        return flagged
    return flagged.sort_values("_Date", ascending=False, na_position="last").head(limit)


def athlete_last_result_map(history_df: pd.DataFrame) -> dict[str, pd.Timestamp]:
    out = history_with_dates(history_df)
    result: dict[str, pd.Timestamp] = {}
    if out.empty:
        return result
    for _, row in out.dropna(subset=["_Date"]).iterrows():
        aid = clean(row.get("Athlete ID", ""))
        name = clean(row.get("Name", "")).casefold()
        key = aid or name
        if not key:
            continue
        date = row["_Date"]
        if key not in result or date > result[key]:
            result[key] = date
    return result


def athlete_attention_rows(athlete_df: pd.DataFrame, history_df: pd.DataFrame) -> list[dict[str, str]]:
    history_keys = verified_identity_keys(history_df)
    dated = history_with_dates(history_df)
    latest_date = dated["_Date"].max() if not dated.empty else pd.NaT
    rows: list[dict[str, str]] = []
    for _, athlete in athlete_df.iterrows():
        aid = clean(athlete.get("Athlete ID", ""))
        name = clean(athlete.get("Name", ""))
        name_norm = normalized_identity_text(name)
        school_norm = normalized_identity_text(athlete.get("School", ""))
        matches = dated.iloc[0:0]
        if not dated.empty:
            if aid and "Athlete ID" in dated.columns:
                matches = dated[dated["Athlete ID"].map(normalized_identity_text) == normalized_identity_text(aid)]
            if matches.empty and name_norm and "Name" in dated.columns:
                nm = dated["Name"].map(normalized_identity_text) == name_norm
                if school_norm and "School" in dated.columns:
                    sm = dated["School"].map(normalized_identity_text) == school_norm
                    strong = dated[nm & sm]
                    if not strong.empty:
                        matches = strong
                if matches.empty:
                    matches = dated[nm]
        valid_dates = matches["_Date"].dropna() if not matches.empty else pd.Series(dtype="datetime64[ns]")
        last = valid_dates.max() if not valid_dates.empty else pd.NaT
        if not athlete_has_verified_history(athlete, history_keys):
            rows.append({"Athlete ID": aid, "Name": name, "Position": clean(athlete.get("Position", "")),
                         "Reason": "No verified track evidence", "Last Result": "—"})
        elif pd.notna(last) and pd.notna(latest_date) and (latest_date - last).days >= 30:
            rows.append({"Athlete ID": aid, "Name": name, "Position": clean(athlete.get("Position", "")),
                         "Reason": "No verified result in 30+ days", "Last Result": last.strftime("%Y-%m-%d")})
    return rows


def matching_history_for_athlete(athlete: pd.Series, history_df: pd.DataFrame) -> pd.DataFrame:
    if history_df.empty:
        return history_df.copy()
    aid = normalized_identity_text(athlete.get("Athlete ID", ""))
    name = normalized_identity_text(athlete.get("Name", ""))
    school = normalized_identity_text(athlete.get("School", ""))
    if aid and "Athlete ID" in history_df.columns:
        matched = history_df[history_df["Athlete ID"].map(normalized_identity_text) == aid]
        if not matched.empty:
            return matched.copy()
    if name and "Name" in history_df.columns:
        name_mask = history_df["Name"].map(normalized_identity_text) == name
        if school and "School" in history_df.columns:
            strong = history_df[name_mask & (history_df["School"].map(normalized_identity_text) == school)]
            if not strong.empty:
                return strong.copy()
        return history_df[name_mask].copy()
    return history_df.iloc[0:0].copy()

def best_mark_summary(athlete_history: pd.DataFrame, limit: int = 4) -> list[str]:
    if athlete_history.empty or "Event" not in athlete_history.columns:
        return []
    rows=[]
    for event, group in athlete_history.groupby("Event", dropna=True):
        event_name=clean(event)
        if not event_name: continue
        pb_group=group[group["PB"].map(truthy_flag)] if "PB" in group.columns else group.iloc[0:0]
        candidate=pb_group.iloc[-1] if not pb_group.empty else group.iloc[-1]
        mark=clean(candidate.get("Mark",""))
        if mark: rows.append(f"{event_name}: {mark}")
        if len(rows)>=limit: break
    return rows

def football_trait_summary(athlete: pd.Series, traits_df: pd.DataFrame) -> str:
    if traits_df.empty: return "No verified football-trait evidence yet"
    aid=normalized_identity_text(athlete.get("Athlete ID",""))
    name=normalized_identity_text(athlete.get("Name",""))
    matches=traits_df.iloc[0:0]
    if aid and "Athlete ID" in traits_df.columns:
        matches=traits_df[traits_df["Athlete ID"].map(normalized_identity_text)==aid]
    if matches.empty and name and "Name" in traits_df.columns:
        matches=traits_df[traits_df["Name"].map(normalized_identity_text)==name]
    if matches.empty: return "No verified football-trait evidence yet"
    row=matches.iloc[0]
    pieces=[]
    for col in ["Primary Trait","Football Trait","Trait","Trait Summary","Speed","Explosiveness","Power","Speed Endurance"]:
        if col in matches.columns:
            value=clean(row.get(col,""))
            if value and value.casefold() not in {"nan","none","0","false"}:
                pieces.append(f"{col}: {value}")
    return " · ".join(pieces[:3]) if pieces else "Verified track evidence on file"

def evidence_freshness(athlete_history: pd.DataFrame, reference_date: pd.Timestamp) -> tuple[str,str]:
    dated=history_with_dates(athlete_history)
    valid=dated["_Date"].dropna() if not dated.empty else pd.Series(dtype="datetime64[ns]")
    if valid.empty: return "NO EVIDENCE","No verified result"
    last=valid.max()
    age=(reference_date-last).days if pd.notna(reference_date) else 0
    status="CURRENT" if age<=14 else ("WATCH" if age<=30 else "STALE")
    return status,f"Last verified: {last.strftime('%Y-%m-%d')}"

def render_position_room_card(athlete: pd.Series, history_df: pd.DataFrame, traits_df: pd.DataFrame, reference_date: pd.Timestamp, key_prefix: str) -> None:
    aid=clean(athlete.get("Athlete ID","")); name=clean(athlete.get("Name",""))
    school=clean(athlete.get("School","")); state=clean(athlete.get("State","")); pos=clean(athlete.get("Position",""))
    ah=matching_history_for_athlete(athlete,history_df)
    freshness,last_text=evidence_freshness(ah,reference_date)
    marks=best_mark_summary(ah); trait_text=football_trait_summary(athlete,traits_df)
    with st.container(border=True):
        top,action=st.columns([5,1.5])
        with top:
            st.markdown(f"### {name}")
            location=" · ".join([x for x in [pos,school,state] if x])
            if location: st.caption(location)
        with action:
            if aid and st.button("Open Profile",key=f"{key_prefix}_{aid}",use_container_width=True):
                open_profile(aid); st.rerun()
        m1,m2,m3=st.columns(3)
        m1.metric("Evidence",len(ah))
        m2.metric("PB Marks",int(ah["PB"].map(truthy_flag).sum()) if "PB" in ah.columns else 0)
        m3.metric("Freshness",freshness)
        st.caption(last_text)
        st.markdown("**Verified bests:** "+" · ".join(marks) if marks else "**Verified bests:** No verified marks stored yet.")
        st.markdown(f"**Football translation:** {trait_text}")


def render_compact_result(row: pd.Series, key_prefix: str) -> None:
    aid = clean(row.get("Athlete ID", ""))
    name = clean(row.get("Name", ""))
    event = clean(row.get("Event", ""))
    mark = clean(row.get("Mark", ""))
    meet = clean(row.get("Meet", ""))
    date = clean(row.get("Meet Date", ""))
    tags = []
    if truthy_flag(row.get("PB", "")):
        tags.append("PB")
    if truthy_flag(row.get("SB", "")):
        tags.append("SB")
    tag_text = " · ".join(tags)
    with st.container(border=True):
        left, right = st.columns([5, 1])
        with left:
            headline = " · ".join([x for x in [name, event, mark] if x])
            st.markdown(f"**{headline or name}**")
            details = " · ".join([x for x in [tag_text, meet, date] if x])
            if details:
                st.caption(details)
        with right:
            if aid and st.button("Open Profile", key=f"{key_prefix}_{aid}_{row.name}", use_container_width=True):
                open_profile(aid)
                st.rerun()


def open_profile(athlete_id: str) -> None:
    st.session_state["selected_athlete_id"] = athlete_id
    st.session_state["_next_workspace"] = "Player Profiles"


athlete_file = find_athlete_file()
if athlete_file is None:
    st.error("Step 9.0 cannot find data/athletes.csv. Run your existing tracker first so the athlete database is available.")
    st.stop()
if not HISTORY_FILE.exists():
    st.error("Step 9.0 cannot find data/performance_history.csv. Run your existing tracker first.")
    st.stop()

athletes_raw = load_athletes(str(athlete_file), athlete_file.stat().st_mtime)
athletes = normalize_athletes(athletes_raw)
history = load_history(str(HISTORY_FILE), HISTORY_FILE.stat().st_mtime)

# Step 8.4 API: recruiting intelligence returns a dictionary containing
# the two DataFrames used by the report layer.
intelligence = build_recruiting_intelligence(athletes_raw, history, 2026)
if not isinstance(intelligence, dict):
    raise RuntimeError("Unexpected recruiting intelligence result; expected a dictionary.")

profiles = intelligence.get("profiles", pd.DataFrame())
event_bests = intelligence.get("event_bests", pd.DataFrame())

if not isinstance(profiles, pd.DataFrame):
    profiles = pd.DataFrame()
if not isinstance(event_bests, pd.DataFrame):
    event_bests = pd.DataFrame()

# Step 8.4 API: football traits consumes the intelligence DataFrames and
# returns one DataFrame.
trait_rows = build_football_traits(profiles, event_bests)
if not isinstance(trait_rows, pd.DataFrame):
    trait_rows = pd.DataFrame()

payload = snapshot_payload()
alerts = snapshot_alerts(payload)

with st.sidebar:
    st.markdown("### Nevada Football")
    st.caption("Recruiting Intelligence · Step 9.6.2")
    if "_next_workspace" in st.session_state:
        st.session_state["workspace"] = st.session_state.pop("_next_workspace")
    if "workspace" not in st.session_state:
        st.session_state["workspace"] = "Operations"
    page = st.radio(
        "Workspace",
        ["Operations", "Recruiting Board", "Player Profiles", "Position Rooms", "Weekly Alerts", "Performance History", "Manage Athletes", "Tracker Control"],
        label_visibility="collapsed", key="workspace",
    )
    st.divider()
    positions = sorted([x for x in athletes["Position"].unique().tolist() if clean(x)])
    position_filter = st.multiselect("Position filter", positions)
    states = sorted([x for x in athletes["State"].unique().tolist() if clean(x)])
    state_filter = st.multiselect("State filter", states)
    st.caption("Step 9.6.2 recruiting operations. Verified performance history remains protected by the stable tracker pipeline.")

filtered = athletes.copy()
if position_filter:
    filtered = filtered[filtered["Position"].isin(position_filter)]
if state_filter:
    filtered = filtered[filtered["State"].isin(state_filter)]

verified_ids = verified_athlete_ids(athletes, history)

st.markdown('<div class="nv-title">Nevada Football Recruiting Intelligence</div>', unsafe_allow_html=True)
st.markdown('<div class="nv-sub">Track evidence, development and weekly recruiting operations.</div>', unsafe_allow_html=True)

if page == "Operations":
    dated_history = history_with_dates(history)
    latest_verified_date = dated_history["_Date"].max() if not dated_history.empty else pd.NaT
    standouts = recent_standouts(history, limit=6)
    recent_results = recent_verified_results(history, limit=8)
    attention = athlete_attention_rows(filtered, history)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tracked Athletes", len(athletes))
    c2.metric("Verified Athletes", len(set(athletes["Athlete ID"]) & verified_ids))
    c3.metric("Verified Performances", len(history))
    c4.metric("Current Alerts", len(alerts))

    st.subheader("Recruiting Operations")
    st.caption(
        "A staff-first view of verified track evidence. PB/SB flags and tracker alerts come from the existing "
        "verified pipeline; this dashboard does not create an overall player grade."
    )

    left, right = st.columns([1.35, 1])

    with left:
        st.markdown("### Recent Standouts")
        if standouts.empty:
            st.info("No verified PB/SB performances are available yet.")
        else:
            for _, result in standouts.iterrows():
                render_compact_result(result, "standout")

    with right:
        st.markdown("### Staff Attention")
        if not attention:
            st.success("Every athlete in the current view has recent verified track evidence.")
        else:
            for item in attention[:6]:
                with st.container(border=True):
                    x, y = st.columns([4, 1.6])
                    with x:
                        st.markdown(f"**{item['Name']}** · {item['Position']}")
                        st.caption(f"{item['Reason']} · Last result: {item['Last Result']}")
                    with y:
                        if item["Athlete ID"] and st.button(
                            "Open Profile",
                            key=f"attention_{item['Athlete ID']}",
                            use_container_width=True,
                        ):
                            open_profile(item["Athlete ID"])
                            st.rerun()
            if len(attention) > 6:
                st.caption(f"+ {len(attention) - 6} more athletes need attention.")

    st.markdown("### Position Rooms")
    room_positions = sorted([x for x in filtered["Position"].unique().tolist() if clean(x)])
    if not room_positions:
        st.info("No position groups match the current filters.")
    else:
        room_cols = st.columns(min(4, len(room_positions)))
        for i, pos in enumerate(room_positions):
            room = filtered[filtered["Position"] == pos]
            room_ids = set(room["Athlete ID"].astype(str))
            verified_count = len(room_ids & verified_ids)
            with room_cols[i % len(room_cols)]:
                with st.container(border=True):
                    st.markdown(f"### {pos}")
                    st.metric("Recruits", len(room))
                    st.caption(f"{verified_count} with verified track evidence")
                    names = ", ".join(room["Name"].astype(str).tolist()[:4])
                    if names:
                        st.write(names)
                    if len(room) > 4:
                        st.caption(f"+ {len(room) - 4} more")
                    if st.button("Open Position Room", key=f"open_room_{pos}", use_container_width=True):
                        st.session_state["_position_room"] = pos
                        st.session_state["_next_workspace"] = "Position Rooms"
                        st.rerun()

    st.markdown("### What Changed")
    st.caption(
        "Tracker-generated alerts from the persistent recruiting-intelligence snapshot. "
        "This is the primary run-to-run change feed."
    )
    if alerts:
        st.dataframe(pd.DataFrame(alerts), use_container_width=True, hide_index=True)
    else:
        st.info("No new recruiting alerts since the current intelligence snapshot.")

    st.markdown("### Latest Verified Results")
    if recent_results.empty:
        st.info("No verified performances are stored yet.")
    else:
        cols = [c for c in ["Meet Date", "Name", "Position", "Event", "Mark", "PB", "SB", "Meet"] if c in recent_results.columns]
        st.dataframe(recent_results[cols], use_container_width=True, hide_index=True)

    if pd.notna(latest_verified_date):
        st.caption(f"Latest verified performance in the database: {latest_verified_date.strftime('%Y-%m-%d')}")

    if WEEKLY_REPORT.exists():
        st.download_button(
            "Download weekly Excel report",
            WEEKLY_REPORT.read_bytes(),
            "weekly_track_report.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

elif page == "Recruiting Board":
    st.subheader("Recruiting Board")
    st.caption("Open any recruit to move directly into the full player profile.")
    for _,r in filtered.iterrows():
        aid=clean(r["Athlete ID"]); ev=event_bests[event_bests["Athlete ID"].astype(str)==aid] if "Athlete ID" in event_bests.columns else pd.DataFrame()
        with st.container(border=True):
            x,y=st.columns([5,1])
            with x:
                st.markdown(f"### {clean(r['Name'])} · {clean(r['Position'])}")
                st.caption(" · ".join([v for v in [clean(r["School"]),clean(r["State"])] if v]))
                st.write(("VERIFIED TRACK EVIDENCE · "+best_marks(ev)) if aid in verified_ids else "NO VERIFIED TRACK EVIDENCE")
            with y:
                if st.button("Open Profile",key=f"board_{aid}",use_container_width=True):
                    open_profile(aid); st.rerun()

elif page == "Player Profiles":
    if filtered.empty:
        st.warning("No athletes match the current sidebar filters.")
    else:
        ids=filtered["Athlete ID"].astype(str).tolist()
        names=dict(zip(ids,filtered["Name"].astype(str)))
        wanted=clean(st.session_state.get("selected_athlete_id",""))
        idx=ids.index(wanted) if wanted in ids else 0
        aid=st.selectbox("Player",ids,index=idx,format_func=lambda x:names.get(x,x))
        st.session_state["selected_athlete_id"]=aid
        r=filtered[filtered["Athlete ID"].astype(str)==aid].iloc[0]; name=clean(r["Name"])
        ah=athlete_history(history,aid,name)
        ev=event_bests[event_bests["Athlete ID"].astype(str)==aid] if "Athlete ID" in event_bests.columns else pd.DataFrame()
        st.markdown(f'<div class="hero"><div class="section-kicker">PLAYER PROFILE</div><div class="hero-name">{name}</div><div class="hero-meta">{clean(r["Position"])} · {clean(r["School"])} · {clean(r["State"])}</div></div>',unsafe_allow_html=True)
        c1,c2,c3,c4=st.columns(4)
        c1.metric("Verified Performances",len(ah)); c2.metric("Verified Events",len(ev)); c3.metric("Evidence Status","VERIFIED" if len(ah) else "NO EVIDENCE")
        c4.metric("Latest Result",clean(ah["Meet Date"].max()) if len(ah) and "Meet Date" in ah.columns else "—")
        st.subheader("Best Marks")
        if ev.empty: st.info("No verified track evidence is stored for this athlete.")
        else:
            cols=[c for c in ["Event","PB","SB","PB Date","Latest Date","Latest Mark","Improvement Display"] if c in ev.columns]
            st.dataframe(ev[cols],use_container_width=True,hide_index=True)
        st.subheader("Football Trait Evidence"); st.info(trait_summary(trait_rows,aid))
        st.subheader("Development")
        if len(ah) and "Event" in ah.columns and "Mark Numeric" in ah.columns:
            events=[x for x in ah["Event"].astype(str).unique() if x]
            if events:
                event=st.selectbox("Event",events,key=f"chart_{aid}")
                ch=ah[ah["Event"].astype(str)==event].copy()
                ch["Date"]=pd.to_datetime(ch["Meet Date"],errors="coerce"); ch["Performance"]=pd.to_numeric(ch["Mark Numeric"],errors="coerce")
                ch=ch.dropna(subset=["Date","Performance"]).sort_values("Date")
                if len(ch):
                    st.line_chart(ch.set_index("Date")[["Performance"]],use_container_width=True)
                    st.caption("Running events: lower is better. Field events: higher is better.")
                else: st.caption("No numeric marks are available for this event.")
        else: st.caption("Not enough verified numeric history to chart development.")
        st.subheader("Verified Performance History")
        if ah.empty: st.caption("No verified performances.")
        else:
            cols=[c for c in ["Meet Date","Meet","Event","Mark","Place","Wind","PB","SB","Source"] if c in ah.columns]
            view=ah[cols]
            if "Meet Date" in view.columns:view=view.sort_values("Meet Date",ascending=False)
            st.dataframe(view,use_container_width=True,hide_index=True)

elif page == "Position Rooms":
    st.subheader("Position Room Recruiting Boards")
    st.caption("Review each football position as a recruiting room: verified track evidence, best marks, evidence freshness, football-trait translation and direct player navigation.")

    room_positions = sorted([x for x in filtered["Position"].unique().tolist() if clean(x)])
    if not room_positions:
        st.info("No position groups match the current filters.")
    else:
        default_pos = st.session_state.get("_position_room", room_positions[0])
        if default_pos not in room_positions:
            default_pos = room_positions[0]
        selected_pos = st.selectbox("Position room", room_positions, index=room_positions.index(default_pos))
        st.session_state["_position_room"] = selected_pos

        room = filtered[filtered["Position"] == selected_pos].copy()
        room_verified = verified_athlete_ids(room, history)
        dated_all = history_with_dates(history)
        reference_date = dated_all["_Date"].max() if not dated_all.empty else pd.NaT

        a, b, c = st.columns(3)
        a.metric(f"{selected_pos} Recruits", len(room))
        b.metric("With Verified Evidence", len(room_verified))
        c.metric("Need Evidence", max(len(room) - len(room_verified), 0))

        st.markdown(f"### {selected_pos} Room")
        for _, athlete in room.sort_values(["Name"]).iterrows():
            render_position_room_card(athlete, history, traits, reference_date, f"room_{selected_pos}")


elif page == "Weekly Alerts":
    st.subheader("Weekly Alerts")
    st.caption("Changes are compared against the persistent Step 8.3+ intelligence snapshot.")
    if alerts:
        st.dataframe(pd.DataFrame(alerts), use_container_width=True, hide_index=True)
    else:
        st.success("No new recruiting alerts in the current snapshot.")

elif page == "Tracker Control":
    st.subheader("Tracker Control")
    st.caption(
        "Run the private recruiting tracker in GitHub Actions. "
        "The cloud runner pulls the private Supabase roster/history, runs the existing Step 8.4 engine, "
        "and publishes refreshed results back to Supabase."
    )

    try:
        run = latest_tracker_run()
        active = github_run_is_active(run)

        if run:
            status = clean(run.get("status")).replace("_", " ").title()
            conclusion = clean(run.get("conclusion")).replace("_", " ").title()
            display_status = conclusion if status == "Completed" and conclusion else status

            c1, c2, c3 = st.columns(3)
            c1.metric("Latest run", display_status or "Unknown")
            c2.metric("Started", format_github_time(run.get("run_started_at") or run.get("created_at")))
            c3.metric("Run number", f"#{run.get('run_number', '—')}")

            if active:
                st.info("A tracker run is currently active. A second run is disabled until it finishes.")
            elif clean(run.get("conclusion")) == "success":
                st.success("The latest cloud tracker run completed successfully.")
            elif clean(run.get("conclusion")):
                st.error(f"The latest cloud tracker run ended with: {clean(run.get('conclusion'))}")

        else:
            active = False
            st.info("No Step 9.5 cloud tracker runs have been recorded yet.")

        if st.button(
            "Run Recruiting Tracker",
            type="primary",
            disabled=active,
            use_container_width=True,
        ):
            request_tracker_run()
            st.session_state["_tracker_requested"] = True
            st.rerun()

        if st.session_state.pop("_tracker_requested", False):
            st.success("Tracker run requested. Use Refresh status below in a few seconds.")

        refresh_col, data_col = st.columns(2)
        with refresh_col:
            if st.button("Refresh Run Status", use_container_width=True):
                st.rerun()
        with data_col:
            if st.button("Refresh Website Data", use_container_width=True):
                refresh_private_web_data()
                st.session_state["_website_data_refreshed"] = True
                st.rerun()

        if st.session_state.pop("_website_data_refreshed", False):
            st.success("Website data refreshed from private Supabase storage.")

        if run and clean(run.get("status")) == "completed" and clean(run.get("conclusion")) == "success":
            st.caption(
                "After a successful run, click Refresh Website Data to immediately reload the newly "
                "published roster, history, intelligence snapshot, and weekly report."
            )

        st.caption(
            "The website only requests and monitors the private GitHub Actions job. "
            "The GitHub token and Supabase credentials stay in server-side secrets and are not sent to the browser."
        )

    except Exception as exc:
        st.error(str(exc))
        st.info(
            "Step 9.5 requires GITHUB_TRACKER_TOKEN in Streamlit Secrets and the private track repository "
            "to contain the Step 9.5 workflow."
        )

elif page == "Manage Athletes":
    st.subheader("Manage Athletes")
    st.caption(
        "Upload an Excel recruiting roster. The workbook is stored only in private Supabase storage; "
        "it is never committed to the public GitHub repository."
    )

    with st.container(border=True):
        st.markdown("### Upload recruiting roster")
        st.write("Required: **Name**. Recommended for reliable result matching: **School, Position, State**.")
        st.caption(
            "Accepted column names include Name/Athlete/Player, School/High School, Position/Pos, and State/ST."
        )
        uploaded = st.file_uploader("Excel roster", type=["xlsx"], accept_multiple_files=False)

        if uploaded is not None:
            try:
                incoming = parse_roster_excel(uploaded.getvalue())
                proposed, new_count, existing_count = merge_uploaded_roster(athletes_raw, incoming)

                missing_school = int((proposed["School"].astype(str).str.strip() == "").sum())
                missing_position = int((proposed["Position"].astype(str).str.strip() == "").sum())
                missing_state = int((proposed["State"].astype(str).str.strip() == "").sum())

                c1, c2, c3 = st.columns(3)
                c1.metric("Athletes detected", len(proposed))
                c2.metric("New athletes", new_count)
                c3.metric("Already tracked", existing_count)

                if missing_school or missing_position or missing_state:
                    st.warning(
                        f"Missing fields — School: {missing_school}, Position: {missing_position}, "
                        f"State: {missing_state}. Names-only uploads are allowed, but result discovery "
                        "is much more reliable when School and State are included."
                    )

                st.dataframe(
                    proposed[["Name", "School", "Position", "State"]],
                    use_container_width=True,
                    hide_index=True,
                )

                st.warning(
                    "Importing replaces the CURRENT recruiting roster with this workbook. "
                    "Verified performance history is not deleted."
                )
                confirm = st.checkbox(
                    "I reviewed the roster and want this workbook to become the current recruiting roster."
                )
                if st.button(
                    "Import Athletes",
                    type="primary",
                    disabled=not confirm or proposed.empty,
                    use_container_width=True,
                ):
                    save_roster_to_private_storage(proposed)
                    st.session_state["_roster_import_success"] = (
                        f"Imported {len(proposed)} athletes. "
                        f"{new_count} new, {existing_count} already tracked."
                    )
                    st.rerun()
            except Exception as exc:
                st.error(f"Could not read/import this workbook: {exc}")

    if "_roster_import_success" in st.session_state:
        st.success(st.session_state.pop("_roster_import_success"))

    st.divider()
    st.markdown("### Current private roster")
    st.dataframe(
        athletes[["Name", "School", "Position", "State"]],
        use_container_width=True,
        hide_index=True,
    )
    st.download_button(
        "Download tracker master workbook",
        data=roster_master_xlsx(athletes),
        file_name="2026 Nevada Track.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        help="Download a copy of the current private recruiting roster.",
    )
    st.info(
        "The private web roster updates immediately. When you are ready, open Tracker Control and click "
        "Run Recruiting Tracker; the cloud runner will use this roster automatically."
    )

elif page == "Performance History":
    st.subheader("Verified Performance History")
    view = history.copy()
    if position_filter and "Position" in view.columns:
        view = view[view["Position"].isin(position_filter)]
    if state_filter and "State" in view.columns:
        view = view[view["State"].isin(state_filter)]
    st.dataframe(view, use_container_width=True, hide_index=True)
    st.download_button(
        "Download performance history CSV",
        data=view.to_csv(index=False).encode("utf-8"),
        file_name="performance_history.csv",
        mime="text/csv",
    )

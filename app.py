from __future__ import annotations

import json
import hmac
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


@st.cache_resource(show_spinner=False)
def sync_private_web_data() -> bool:
    try:
        url = str(st.secrets["SUPABASE_URL"]).rstrip("/")
        key = str(st.secrets["SUPABASE_SERVICE_ROLE_KEY"])
        bucket = str(st.secrets.get("SUPABASE_BUCKET", "recruiting-web"))
    except Exception as exc:
        st.error(f"Supabase secrets are not configured: {exc}")
        st.stop()

    files = {
        "data/athletes.csv": ROOT / "data" / "athletes.csv",
        "data/performance_history.csv": ROOT / "data" / "performance_history.csv",
        "data/recruiting_intelligence_snapshot.json": ROOT / "data" / "recruiting_intelligence_snapshot.json",
        "reports/weekly_track_report.xlsx": ROOT / "reports" / "weekly_track_report.xlsx",
    }
    headers = {"Authorization": f"Bearer {key}", "apikey": key}
    for object_path, local_path in files.items():
        endpoint = f"{url}/storage/v1/object/authenticated/{bucket}/{object_path}"
        response = requests.get(endpoint, headers=headers, timeout=30)
        if response.status_code != 200:
            st.error(f"Could not load private recruiting data: {object_path} ({response.status_code})")
            st.stop()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(response.content)
    return True


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
    st.caption("Recruiting Intelligence · Step 9.1")
    if "_next_workspace" in st.session_state:
        st.session_state["workspace"] = st.session_state.pop("_next_workspace")
    if "workspace" not in st.session_state:
        st.session_state["workspace"] = "Operations"
    page = st.radio(
        "Workspace",
        ["Operations", "Recruiting Board", "Player Profiles", "Position Rooms", "Weekly Alerts", "Performance History"],
        label_visibility="collapsed", key="workspace",
    )
    st.divider()
    positions = sorted([x for x in athletes["Position"].unique().tolist() if clean(x)])
    position_filter = st.multiselect("Position filter", positions)
    states = sorted([x for x in athletes["State"].unique().tolist() if clean(x)])
    state_filter = st.multiselect("State filter", states)
    st.caption("Read-only web layer. The Step 8.4 tracker remains the source of truth.")

filtered = athletes.copy()
if position_filter:
    filtered = filtered[filtered["Position"].isin(position_filter)]
if state_filter:
    filtered = filtered[filtered["State"].isin(state_filter)]

verified_ids = set()
if "Athlete ID" in history.columns:
    verified_ids = set(history["Athlete ID"].astype(str).tolist())
elif "Name" in history.columns:
    verified_names = set(history["Name"].astype(str).str.casefold().tolist())
    verified_ids = set(athletes.loc[athletes["Name"].str.casefold().isin(verified_names), "Athlete ID"])

st.markdown('<div class="nv-title">Nevada Football Recruiting Intelligence</div>', unsafe_allow_html=True)
st.markdown('<div class="nv-sub">Track evidence, development and weekly recruiting operations.</div>', unsafe_allow_html=True)

if page == "Operations":
    c1,c2,c3,c4=st.columns(4)
    c1.metric("Tracked Athletes",len(athletes)); c2.metric("Verified Athletes",len(set(athletes["Athlete ID"]) & verified_ids))
    c3.metric("Verified Performances",len(history)); c4.metric("Current Alerts",len(alerts))
    st.subheader("Recruiting Operations Board")
    st.caption("Verified track evidence is supporting recruiting context—not an overall player grade.")
    for i,(_,r) in enumerate(filtered.iterrows()):
        aid=clean(r["Athlete ID"])
        ev=event_bests[event_bests["Athlete ID"].astype(str)==aid] if "Athlete ID" in event_bests.columns else pd.DataFrame()
        with st.container(border=True):
            x,y=st.columns([5,1])
            with x:
                st.markdown(f"**{clean(r['Name'])}** · {clean(r['Position'])} — {clean(r['School'])}, {clean(r['State'])}")
                st.caption(("VERIFIED · "+best_marks(ev)) if aid in verified_ids else "NO VERIFIED TRACK EVIDENCE")
                st.write(trait_summary(trait_rows,aid))
            with y:
                if st.button("Open Profile",key=f"ops_{aid}",use_container_width=True):
                    open_profile(aid); st.rerun()
    st.subheader("Weekly Change")
    if alerts: st.dataframe(pd.DataFrame(alerts),use_container_width=True,hide_index=True)
    else: st.info("No new recruiting alerts since the current intelligence snapshot.")
    if WEEKLY_REPORT.exists():
        st.download_button("Download weekly Excel report",WEEKLY_REPORT.read_bytes(),"weekly_track_report.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

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
    st.subheader("Position Rooms")
    room_positions = sorted([x for x in filtered["Position"].unique().tolist() if clean(x)])
    if not room_positions:
        st.info("No position groups match the current filters.")
    for pos in room_positions:
        room = filtered[filtered["Position"] == pos]
        st.markdown(f"### {pos} · {len(room)}")
        for _, a in room.iterrows():
            aid = clean(a["Athlete ID"])
            ev = event_bests[event_bests["Athlete ID"].astype(str) == aid] if "Athlete ID" in event_bests.columns else pd.DataFrame()
            with st.container(border=True):
                st.markdown(f"**{clean(a['Name'])}** — {clean(a['School'])}, {clean(a['State'])}")
                st.caption(("VERIFIED · " + best_marks(ev)) if aid in verified_ids else "NO VERIFIED TRACK EVIDENCE")
                st.write(trait_summary(trait_rows, aid))

elif page == "Weekly Alerts":
    st.subheader("Weekly Alerts")
    st.caption("Changes are compared against the persistent Step 8.3+ intelligence snapshot.")
    if alerts:
        st.dataframe(pd.DataFrame(alerts), use_container_width=True, hide_index=True)
    else:
        st.success("No new recruiting alerts in the current snapshot.")

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

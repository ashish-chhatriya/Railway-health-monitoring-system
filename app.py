import math
import os
import time
from datetime import datetime

import firebase_admin
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from firebase_admin import credentials, db


DATABASE_URL = "https://railway-monitoring-system-default-rtdb.asia-southeast1.firebasedatabase.app/"
LIVE_NODE = "/track_monitor/live"
PROCESSED_ALERTS_NODE = "/processed_alerts"
INCIDENTS_NODE = "/track_monitor/incidents"
MAX_POINTS = 50


def init_firebase():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    key_path = os.path.join(current_dir, "serviceAccountKey.json")
    if not firebase_admin._apps:
        cred = credentials.Certificate(key_path)
        firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})


def init_state():
    st.session_state.setdefault("view", "home")
    st.session_state.setdefault("selected_incident_id", None)
    st.session_state.setdefault("vibration_buffer", [])
    st.session_state.setdefault("distance_buffer", [])
    st.session_state.setdefault("temperature_buffer", [])
    st.session_state.setdefault("time_buffer", [])


def parse_timestamp(raw_value):
    if raw_value is None:
        return datetime.now()
    try:
        value = float(raw_value)
        if value < 1000000000:
            return datetime.now()
        return datetime.fromtimestamp(value)
    except (TypeError, ValueError, OSError):
        try:
            return datetime.fromisoformat(str(raw_value).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return datetime.now()


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def live_snapshot():
    return db.reference(LIVE_NODE).get() or {}


def clean_status_label(status_text):
    text = str(status_text or "").strip()
    upper = text.upper()
    for prefix in ("ALERT:", "CRITICAL:", "WARNING:"):
        if upper.startswith(prefix):
            return text[len(prefix):].strip()
    return text or "Sensor Alert"


def derive_fault_source(record):
    explicit = str(record.get("fault_source", "")).strip()
    if explicit and explicit.lower() != "unknown":
        return explicit

    active_problems = record.get("active_problems", [])
    if isinstance(active_problems, list) and active_problems:
        primary = str(active_problems[0]).strip()
        if primary:
            return primary

    status = str(record.get("status", "")).upper()
    if "DISTANCE" in status or "DEVIATION" in status or "WIDENED" in status:
        return "Distance"
    if "THERMAL" in status or "TEMP" in status:
        return "Temp"
    if "VIBRATION" in status or "IMPACT" in status or "MECHANICAL" in status:
        return "Vibration"
    if "ALERT" in status:
        return "General Alert"
    return "Sensor Alert"


def recent_incidents():
    payload = db.reference(PROCESSED_ALERTS_NODE).get() or {}
    incidents_payload = db.reference(INCIDENTS_NODE).get() or {}
    incident_keys = set(incidents_payload.keys())
    rows = []

    for key, value in payload.items():
        if not isinstance(value, dict):
            continue
        incident_id = value.get("incident_id", key)
        if incident_keys and incident_id not in incident_keys:
            continue
        timestamp = parse_timestamp(value.get("timestamp"))
        rows.append(
            {
                "incident_id": incident_id,
                "timestamp": timestamp,
                "timestamp_label": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "fault_source": derive_fault_source(value),
                "total_probability": float(value.get("total_probability", value.get("risk_score", 0.0))),
                "status": str(value.get("status", "UNKNOWN")),
                "active_problems": value.get("active_problems", []),
            }
        )

    rows.sort(key=lambda item: item["timestamp"], reverse=True)
    if rows:
        return rows[:24]

    for incident_id, value in incidents_payload.items():
        if not isinstance(value, dict):
            continue
        timestamp = parse_timestamp(value.get("timestamp"))
        rows.append(
            {
                "incident_id": incident_id,
                "timestamp": timestamp,
                "timestamp_label": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "fault_source": derive_fault_source(value),
                "total_probability": float(value.get("total_probability", value.get("risk_score", 0.0))),
                "status": str(value.get("status", "UNKNOWN")),
                "active_problems": value.get("active_problems", []),
            }
        )

    rows.sort(key=lambda item: item["timestamp"], reverse=True)
    return rows[:24]


def format_problem_list(incident):
    active_problems = incident.get("active_problems", [])
    if isinstance(active_problems, list):
        cleaned = [str(problem).strip() for problem in active_problems if str(problem).strip()]
        if cleaned:
            return " | ".join(cleaned)
    return clean_status_label(incident.get("status", "Sensor Alert"))


def incident_snapshot(incident_id):
    if not incident_id:
        return None
    return db.reference(f"{INCIDENTS_NODE}/{incident_id}").get()


def append_buffers(snapshot):
    timestamp = parse_timestamp(snapshot.get("ts"))
    vibration = float(snapshot.get("vibration", 0.0))
    distance = float(snapshot.get("distance", snapshot.get("track_distance", 0.0)))
    temperature = float(snapshot.get("temp", 0.0))

    st.session_state.time_buffer.append(timestamp)
    st.session_state.vibration_buffer.append(vibration)
    st.session_state.distance_buffer.append(distance)
    st.session_state.temperature_buffer.append(temperature)

    st.session_state.time_buffer = st.session_state.time_buffer[-MAX_POINTS:]
    st.session_state.vibration_buffer = st.session_state.vibration_buffer[-MAX_POINTS:]
    st.session_state.distance_buffer = st.session_state.distance_buffer[-MAX_POINTS:]
    st.session_state.temperature_buffer = st.session_state.temperature_buffer[-MAX_POINTS:]


def inject_styles():
    st.markdown(
        """
        <style>
        #MainMenu, header, footer {
            visibility: hidden;
        }
        .stApp {
            background-color: #05070a;
            background-image:
                linear-gradient(rgba(31, 41, 55, 0.16) 1px, transparent 1px),
                linear-gradient(90deg, rgba(31, 41, 55, 0.16) 1px, transparent 1px);
            background-size: 36px 36px;
            color: #eef4ff;
        }
        html, body, [class*="css"] {
            font-family: "Consolas", "Courier New", monospace;
        }
        .block-container {
            max-width: 1420px;
            padding-top: 1rem;
            padding-bottom: 1rem;
        }
        .top-card {
            background: linear-gradient(180deg, rgba(15, 20, 28, 0.96), rgba(8, 11, 16, 0.96));
            border: 1px solid #1f2937;
            border-radius: 20px;
            padding: 1.1rem 1.25rem;
            box-shadow: 0 0 18px rgba(59, 130, 246, 0.10), 0 18px 36px rgba(0, 0, 0, 0.28);
            margin-bottom: 1rem;
        }
        .kicker {
            color: #60a5fa;
            text-transform: uppercase;
            letter-spacing: 0.16em;
            font-size: 0.76rem;
        }
        .main-title {
            color: #f8fbff;
            font-size: 2rem;
            font-weight: 700;
            margin-top: 0.35rem;
        }
        .sub-copy {
            color: #97a6bb;
            margin-top: 0.35rem;
            font-size: 0.94rem;
        }
        .metric-shell {
            background: linear-gradient(180deg, rgba(16, 22, 30, 0.98), rgba(9, 12, 18, 0.98));
            border: 1px solid #1f2937;
            border-radius: 18px;
            padding: 1rem 1.1rem;
            box-shadow: 0 0 16px rgba(59, 130, 246, 0.10);
            margin-bottom: 1rem;
        }
        .metric-title {
            color: #8fa3bb;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            font-size: 0.74rem;
        }
        .metric-value {
            color: #f8fbff;
            font-size: 2rem;
            margin-top: 0.7rem;
        }
        .section-card {
            background: linear-gradient(180deg, rgba(15, 20, 28, 0.96), rgba(8, 11, 16, 0.96));
            border: 1px solid #1f2937;
            border-radius: 20px;
            padding: 1rem 1.1rem;
            box-shadow: 0 0 18px rgba(59, 130, 246, 0.08), 0 18px 34px rgba(0, 0, 0, 0.24);
        }
        .section-label {
            color: #5eead4;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            font-size: 0.78rem;
            margin-bottom: 0.75rem;
        }
        .incident-row {
            border-radius: 14px;
            margin-bottom: 0.24rem;
            padding: 0 0.5rem;
            background: rgba(15, 20, 28, 0.66);
        }
        .incident-row:nth-child(even) {
            background: rgba(20, 27, 37, 0.66);
        }
        .cell {
            padding: 0.8rem 0;
            border-bottom: 1px solid rgba(31, 41, 55, 0.8);
            color: #eef4ff;
            font-size: 1rem;
        }
        .fault-pill {
            display: inline-block;
            padding: 0.28rem 0.65rem;
            border-radius: 999px;
            background: rgba(255, 75, 75, 0.16);
            color: #ff9b9b;
            font-weight: 700;
            letter-spacing: 0.05em;
        }
        .inspect-wrap .stButton > button {
            width: 100%;
            min-height: 2.7rem;
            border-radius: 12px;
            border: 1px solid #1f2937;
            background: linear-gradient(180deg, #19212d 0%, #111722 100%);
            color: transparent;
            font-size: 0;
            position: relative;
            box-shadow: 0 0 12px rgba(59, 130, 246, 0.08);
        }
        .inspect-wrap .stButton > button::before {
            content: "";
            width: 1.45rem;
            height: 1.45rem;
            display: inline-block;
            background-repeat: no-repeat;
            background-position: center;
            background-size: contain;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'%3E%3Cpath d='M1.5 12S5.25 5.25 12 5.25 22.5 12 22.5 12 18.75 18.75 12 18.75 1.5 12 1.5 12Z' stroke='%23eef4ff' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/%3E%3Ccircle cx='12' cy='12' r='3.25' fill='%23eef4ff'/%3E%3C/svg%3E");
        }
        .nav-wrap .stButton > button {
            width: auto;
            border-radius: 12px;
            border: 1px solid rgba(59, 130, 246, 0.24);
            background: linear-gradient(180deg, #19212d 0%, #111722 100%);
            color: #d9e9ff;
            padding: 0.7rem 1rem;
        }
        .forensic-title {
            color: #ff4b4b;
            font-size: 2.05rem;
            font-weight: 700;
            margin-top: 0.65rem;
        }
        .forensic-sub {
            color: #9fb1c7;
            font-size: 1rem;
            margin-top: 0.35rem;
        }
        .sensor-flag {
            background: linear-gradient(180deg, rgba(24, 16, 18, 0.98), rgba(16, 10, 12, 0.98));
            border: 1px solid rgba(255, 75, 75, 0.28);
            border-radius: 16px;
            padding: 0.95rem 1rem;
            margin-top: 1rem;
            box-shadow: 0 0 18px rgba(255, 75, 75, 0.10);
        }
        .sensor-flag-label {
            color: #ff8a8a;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            font-size: 0.76rem;
        }
        .sensor-flag-value {
            color: #fff2f2;
            font-size: 1.1rem;
            margin-top: 0.65rem;
            line-height: 1.35;
        }
        .diagnosis-box {
            background: linear-gradient(180deg, rgba(24, 16, 18, 0.98), rgba(16, 10, 12, 0.98));
            border: 1px solid rgba(255, 75, 75, 0.28);
            border-radius: 18px;
            padding: 1rem 1.1rem;
            box-shadow: 0 0 18px rgba(255, 75, 75, 0.14);
            margin-top: 1rem;
        }
        .diagnosis-label {
            color: #ff8a8a;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            font-size: 0.76rem;
        }
        .diagnosis-value {
            color: #fff2f2;
            font-size: 1.65rem;
            margin-top: 0.7rem;
            line-height: 1.25;
        }
        .rfid-card {
            background: linear-gradient(180deg, rgba(15, 20, 28, 0.96), rgba(8, 11, 16, 0.96));
            border-radius: 18px;
            padding: 1rem 1.1rem;
            min-height: 150px;
            box-shadow: 0 18px 34px rgba(0, 0, 0, 0.24);
        }
        .rfid-card.green {
            border: 2px solid #00ff88;
            box-shadow: 0 18px 34px rgba(0, 0, 0, 0.24), 0 0 18px rgba(0, 255, 136, 0.16);
        }
        .rfid-card.red {
            border: 2px solid #ff4b4b;
            box-shadow: 0 18px 34px rgba(0, 0, 0, 0.24), 0 0 18px rgba(255, 75, 75, 0.16);
        }
        .rfid-label {
            color: #95a8bd;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            font-size: 0.76rem;
        }
        .rfid-value {
            color: #f8fbff;
            font-size: 1.9rem;
            margin-top: 0.75rem;
            line-height: 1.2;
        }
        .stPlotlyChart > div {
            border-radius: 18px;
            overflow: hidden;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_top_frame():
    st.markdown(
        """
        <div class="top-card">
            <div class="kicker">Professional Railway Monitoring Interface</div>
            <div class="main-title">Railway Checking Console</div>
            <div class="sub-copy">Live oscilloscope-style monitoring for vibration, track distance, and temperature with forensic incident drill-down.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_live_metrics(snapshot):
    vibration = float(snapshot.get("vibration", 0.0))
    distance = float(snapshot.get("distance", snapshot.get("track_distance", 0.0)))
    temperature = float(snapshot.get("temp", 0.0))
    cols = st.columns(3)
    cards = [
        ("Live Vibration", f"{vibration:.2f}"),
        ("Track Distance", f"{distance:.2f} cm"),
        ("Temperature", f"{temperature:.1f} C"),
    ]
    for col, (label, value) in zip(cols, cards):
        with col:
            st.markdown(
                f"""
                <div class="metric-shell">
                    <div class="metric-title">{label}</div>
                    <div class="metric-value">{value}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def build_live_graph(times, values, color, title, y_title):
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=times,
            y=values,
            mode="lines",
            line=dict(color=color, width=3),
            hovertemplate="%{x|%H:%M:%S}<br>%{y:.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=220,
        margin=dict(l=16, r=16, t=26, b=18),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#000000",
        font=dict(color="#ffffff", family="Consolas, monospace"),
        title=dict(text=title, font=dict(color="#dbe9ff", size=15)),
        xaxis_title="Time",
        yaxis_title=y_title,
        showlegend=False,
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=False, zeroline=False)
    return fig


def build_forensic_graph(snapshot):
    raw_samples = snapshot.get("raw_samples", [])
    xs = []
    ys = []
    for idx, sample in enumerate(raw_samples):
        if not isinstance(sample, dict):
            continue
        try:
            x_val = float(sample.get("x", 0.0))
            y_val = float(sample.get("y", 0.0))
            z_val = float(sample.get("z", 0.0))
        except (TypeError, ValueError):
            continue
        magnitude = math.sqrt(x_val ** 2 + y_val ** 2 + z_val ** 2)
        ts = sample.get("timestamp", sample.get("ts"))
        xs.append(parse_timestamp(ts) if ts is not None else idx)
        ys.append(magnitude)

    if not ys:
        return None

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=xs,
            y=ys,
            mode="lines+markers",
            line=dict(color="#ffffff", width=2.6),
            marker=dict(size=5, color="#ff4b4b"),
            hovertemplate="%{x}<br>Magnitude: %{y:.3f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=460,
        margin=dict(l=18, r=18, t=18, b=18),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#000000",
        font=dict(color="#ffffff", family="Consolas, monospace"),
        xaxis_title="Captured Window",
        yaxis_title="Vibration Magnitude",
        showlegend=False,
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=False, zeroline=False)
    return fig


def build_incident_timeline_graph(snapshot, field, color, title, y_title):
    history_window = snapshot.get("history_window", [])
    xs = []
    ys = []

    for idx, point in enumerate(history_window):
        if not isinstance(point, dict):
            continue
        xs.append(parse_timestamp(point.get("ts")) if point.get("ts") is not None else idx)
        ys.append(safe_float(point.get(field), 0.0))

    if not ys:
        current_value = snapshot.get(field)
        if current_value is None:
            return None
        xs = [parse_timestamp(snapshot.get("timestamp"))]
        ys = [safe_float(current_value, 0.0)]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=xs,
            y=ys,
            mode="lines+markers",
            line=dict(color=color, width=3),
            marker=dict(size=6, color=color),
            hovertemplate="%{x|%H:%M:%S}<br>%{y:.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=250,
        margin=dict(l=16, r=16, t=24, b=18),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#000000",
        font=dict(color="#ffffff", family="Consolas, monospace"),
        title=dict(text=title, font=dict(color="#dbe9ff", size=14)),
        xaxis_title="Incident Window",
        yaxis_title=y_title,
        showlegend=False,
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=False, zeroline=False)
    return fig


def render_home_view(snapshot):
    append_buffers(snapshot)
    render_top_frame()
    render_live_metrics(snapshot)

    times = st.session_state.time_buffer
    vib = st.session_state.vibration_buffer
    dist = st.session_state.distance_buffer
    temp = st.session_state.temperature_buffer

    st.plotly_chart(build_live_graph(times, vib, "#00f5ff", "Continuous Vibration", "Pulse"), use_container_width=True)
    st.plotly_chart(build_live_graph(times, dist, "#ffb020", "Continuous Distance", "Track Gauge"), use_container_width=True)
    st.plotly_chart(build_live_graph(times, temp, "#ff4b4b", "Continuous Temperature", "C"), use_container_width=True)

    incidents = recent_incidents()
    st.markdown('<div class="section-card" style="margin-top:1rem;"><div class="section-label">Recent Incidents</div>', unsafe_allow_html=True)
    headers = st.columns([1.55, 1.5, 0.95, 0.55])
    for col, label in zip(headers, ["Timestamp", "Risk Due To", "Total Prob", "Inspect"]):
        col.markdown(f"`{label}`")

    if not incidents:
        st.info("No captured incidents yet.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    for idx, incident in enumerate(incidents):
        st.markdown('<div class="incident-row">', unsafe_allow_html=True)
        row = st.columns([1.55, 1.5, 0.95, 0.55])
        row[0].markdown(f'<div class="cell">{incident["timestamp_label"]}</div>', unsafe_allow_html=True)
        row[1].markdown(f'<div class="cell"><span class="fault-pill">{format_problem_list(incident)}</span></div>', unsafe_allow_html=True)
        row[2].markdown(f'<div class="cell">{incident["total_probability"]:.1f}%</div>', unsafe_allow_html=True)
        with row[3]:
            st.markdown('<div class="inspect-wrap">', unsafe_allow_html=True)
            if st.button("inspect", key=f"inspect_{idx}_{incident['incident_id']}"):
                st.session_state["selected_incident_id"] = incident["incident_id"]
                st.session_state["view"] = "inspect"
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


def render_rfid_cards(snapshot):
    before_tag = snapshot.get("rfid_before", "UNKNOWN")
    after_tag = snapshot.get("rfid_after", "UNKNOWN")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            f"""
            <div class="rfid-card green">
                <div class="rfid-label">Last Confirmed Sector</div>
                <div class="rfid-value">{before_tag}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            f"""
            <div class="rfid-card red">
                <div class="rfid-label">Incident Detected Before</div>
                <div class="rfid-value">{after_tag}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_inspection_view():
    snapshot = incident_snapshot(st.session_state["selected_incident_id"])
    if not snapshot:
        st.session_state["view"] = "home"
        st.session_state["selected_incident_id"] = None
        st.warning("Selected incident snapshot was not found.")
        return

    st.markdown('<div class="nav-wrap">', unsafe_allow_html=True)
    if st.button("< Return to Live Monitor", key="return_live_monitor"):
        st.session_state["view"] = "home"
        st.session_state["selected_incident_id"] = None
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    fault_type = str(snapshot.get("status", "UNKNOWN"))
    timestamp = parse_timestamp(snapshot.get("timestamp")).strftime("%Y-%m-%d %H:%M:%S")
    st.markdown(
        f"""
        <div class="top-card">
            <div class="kicker">Incident Inspection</div>
            <div class="forensic-title">Forensic Analysis</div>
            <div class="forensic-sub">{fault_type} | {timestamp}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="diagnosis-box">
            <div class="diagnosis-label">AI Diagnosis</div>
            <div class="diagnosis-value">{fault_type}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    active_problems = snapshot.get("active_problems", [])
    if active_problems:
        st.markdown(
            f"""
            <div class="sensor-flag">
                <div class="sensor-flag-label">Sensor Trigger Highlight</div>
                <div class="sensor-flag-value">{derive_fault_source(snapshot)} | {' | '.join(active_problems)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown('<div style="margin-top:1rem;"></div>', unsafe_allow_html=True)
    render_rfid_cards(snapshot)

    st.markdown('<div class="section-card" style="margin-top:1rem;"><div class="section-label">Incident Window</div>', unsafe_allow_html=True)
    vibration_fig = build_incident_timeline_graph(snapshot, "vibration", "#00f5ff", "Vibration At Incident Time", "Pulse")
    distance_fig = build_incident_timeline_graph(snapshot, "distance", "#ffb020", "Distance At Incident Time", "Track Gauge")
    temperature_fig = build_incident_timeline_graph(snapshot, "temp", "#ff4b4b", "Temperature At Incident Time", "C")

    if vibration_fig is not None:
        st.plotly_chart(vibration_fig, use_container_width=True)
    if distance_fig is not None:
        st.plotly_chart(distance_fig, use_container_width=True)
    if temperature_fig is not None:
        st.plotly_chart(temperature_fig, use_container_width=True)

    if vibration_fig is None and distance_fig is None and temperature_fig is None:
        st.info("No incident window samples were stored for this incident.")
    st.markdown("</div>", unsafe_allow_html=True)


def main():
    st.set_page_config(page_title="Railway Monitoring", layout="wide")
    init_firebase()
    init_state()
    inject_styles()

    snapshot = live_snapshot()
    if st.session_state["view"] == "inspect":
        render_inspection_view()
    else:
        render_home_view(snapshot)

    time.sleep(1)
    st.rerun()


if __name__ == "__main__":
    main()

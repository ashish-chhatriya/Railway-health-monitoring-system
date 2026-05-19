import os
import time
from collections import deque
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials, db


DATABASE_URL = "https://railway-monitoring-system-default-rtdb.asia-southeast1.firebasedatabase.app/"
RAW_NODE = "/track_monitor/live"
OUTPUT_NODE = "/processed_alerts"
INCIDENTS_NODE = "/track_monitor/incidents"
POLL_INTERVAL_SECONDS = 1.0
SAMPLE_INTERVAL_SECONDS = 1.5
FORENSIC_WINDOW_SECONDS = 10
WINDOW_SAMPLE_COUNT = max(1, int(round(FORENSIC_WINDOW_SECONDS / SAMPLE_INTERVAL_SECONDS)))

DISTANCE_BASELINE_MM = 143.5
DISTANCE_THRESHOLD_MM = 2.5
TEMP_THRESHOLD_C = 42.0
VIBRATION_THRESHOLD = 650.0

MAX_VIB = 1023.0
MAX_DIST_DEVIATION = 10.0
MAX_TEMP_SURGE = 60.0


def init_firebase():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    key_path = os.path.join(current_dir, "serviceAccountKey.json")
    if not firebase_admin._apps:
        cred = credentials.Certificate(key_path)
        firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_marker(raw_data):
    if raw_data.get("ts") is not None:
        return f"ts:{raw_data.get('ts')}"
    return (
        f"{safe_float(raw_data.get('distance'), DISTANCE_BASELINE_MM):.3f}|"
        f"{safe_float(raw_data.get('temp'), 0.0):.3f}|"
        f"{safe_float(raw_data.get('vibration'), 0.0):.3f}"
    )


def calculate_ai_probability(data, previous_values):
    distance = safe_float(data.get("distance"), DISTANCE_BASELINE_MM)
    vibration = safe_float(data.get("vibration"), 0.0)
    temp = safe_float(data.get("temp"), 0.0)

    # Kept for compatibility with the caller; current logic is packet-based.
    _ = previous_values

    if distance <= 0 or distance > 1000:
        return {
            "status": "ALERT: Sensor Malfunction",
            "fault_source": "Distance",
            "source_risk": 100.0,
            "total_probability": 100.0,
            "active_problems": ["Sensor Malfunction"],
            "delta_change": 1.0,
        }

    tolerance = {
        "vibration": VIBRATION_THRESHOLD,
        "distance": DISTANCE_THRESHOLD_MM,
        "temp": TEMP_THRESHOLD_C,
    }

    active_problems = []
    severity_scores = []
    scored_faults = []

    dist_error = abs(distance - DISTANCE_BASELINE_MM)
    if dist_error > tolerance["distance"]:
        score = min(60.0 + (dist_error * 10.0), 100.0)
        active_problems.append("Track Geometry (Gap)")
        severity_scores.append(score)
        scored_faults.append(
            {
                "fault_source": "Distance",
                "label": "Track Geometry (Gap)",
                "score": score,
                "intensity": min(dist_error / MAX_DIST_DEVIATION, 1.0),
            }
        )

    if vibration > tolerance["vibration"]:
        score = 60.0 + ((vibration - tolerance["vibration"]) / (MAX_VIB - tolerance["vibration"]) * 40.0)
        score = min(score, 100.0)
        active_problems.append("Mechanical Vibration")
        severity_scores.append(score)
        scored_faults.append(
            {
                "fault_source": "Vibration",
                "label": "Mechanical Vibration",
                "score": score,
                "intensity": min(vibration / MAX_VIB, 1.0),
            }
        )

    if temp > tolerance["temp"]:
        score = 60.0 + ((temp - tolerance["temp"]) * 5.0)
        score = min(score, 100.0)
        active_problems.append("Thermal Stress")
        severity_scores.append(score)
        scored_faults.append(
            {
                "fault_source": "Temp",
                "label": "Thermal Stress",
                "score": score,
                "intensity": min(temp / MAX_TEMP_SURGE, 1.0),
            }
        )

    if not active_problems:
        return {
            "status": "NORMAL OPERATION",
            "fault_source": "None",
            "source_risk": 0.0,
            "total_probability": 0.0,
            "active_problems": [],
            "delta_change": 0.0,
        }

    primary = max(scored_faults, key=lambda item: item["score"])
    dynamic_risk = max(severity_scores)
    if len(scored_faults) > 1:
        dynamic_risk = min(dynamic_risk + 8.0, 100.0)

    main_fault = primary["fault_source"] if len(scored_faults) == 1 else "Multiple Failures"

    return {
        "status": "ALERT: " + " | ".join(active_problems),
        "fault_source": main_fault,
        "source_risk": round(primary["score"], 2),
        "total_probability": round(dynamic_risk, 2),
        "active_problems": active_problems,
        "delta_change": round(primary["intensity"], 2),
    }


def main():
    init_firebase()
    print("AI Node Active: Monitoring Track Metrics at /track_monitor/live...")

    last_processed_ts = None
    last_processed_marker = None
    previous_values = None
    history_window = deque(maxlen=(WINDOW_SAMPLE_COUNT * 2) + 2)
    pending_incidents = []

    while True:
        try:
            raw_data = db.reference(RAW_NODE).get()
            if not raw_data:
                print("AI Node: Waiting for data at /track_monitor/live...")
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            current_hw_ts = raw_data.get("ts")
            current_marker = safe_marker(raw_data)

            # Primary dedupe is the hardware timestamp. Marker is fallback only.
            is_new_packet = False
            if current_hw_ts is not None:
                is_new_packet = current_hw_ts != last_processed_ts
            else:
                is_new_packet = current_marker != last_processed_marker

            if is_new_packet:
                sample_ts = raw_data.get("ts", time.time())
                current_sample = {
                    "ts": sample_ts,
                    "distance": safe_float(raw_data.get("distance"), DISTANCE_BASELINE_MM),
                    "temp": safe_float(raw_data.get("temp"), 0.0),
                    "vibration": safe_float(raw_data.get("vibration"), 0.0),
                }
                history_window.append(current_sample)

                if pending_incidents:
                    completed_incidents = []
                    for pending in pending_incidents:
                        pending["history_window"].append(current_sample.copy())
                        pending["remaining_after_samples"] -= 1
                        db.reference(OUTPUT_NODE).child(pending["incident_id"]).update(
                            {"history_window": pending["history_window"]}
                        )
                        db.reference(INCIDENTS_NODE).child(pending["incident_id"]).update(
                            {"history_window": pending["history_window"]}
                        )
                        if pending["remaining_after_samples"] <= 0:
                            completed_incidents.append(pending)
                    pending_incidents = [item for item in pending_incidents if item not in completed_incidents]

                analysis = calculate_ai_probability(raw_data, previous_values)

                if analysis["total_probability"] > 0:
                    incident_ref = db.reference(INCIDENTS_NODE).push()
                    incident_id = incident_ref.key
                    forensic_window = [dict(point) for point in list(history_window)[-WINDOW_SAMPLE_COUNT:]]
                    result = {
                        "incident_id": incident_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": analysis["status"],
                        "fault_source": analysis["fault_source"],
                        "source_risk": analysis["source_risk"],
                        "total_probability": analysis["total_probability"],
                        "risk_score": analysis["total_probability"],
                        "delta_change": analysis["delta_change"],
                        "active_problems": analysis["active_problems"],
                        "distance": raw_data.get("distance"),
                        "vibration": raw_data.get("vibration"),
                        "temp": raw_data.get("temp"),
                        "history_window": forensic_window,
                        "rfid_before": raw_data.get("rfid_before", "Unknown"),
                        "rfid_after": raw_data.get("rfid_after", "Unknown"),
                    }
                    db.reference(OUTPUT_NODE).child(incident_id).set(result)
                    incident_ref.set(result)
                    pending_incidents.append(
                        {
                            "incident_id": incident_id,
                            "history_window": forensic_window,
                            "remaining_after_samples": WINDOW_SAMPLE_COUNT,
                        }
                    )

                    print(
                        f"AI ANALYSIS: {result['status']} | "
                        f"Fault Source: {result['fault_source']} | "
                        f"Total Prob: {result['total_probability']}% | "
                        f"RFID: {result['rfid_before']}"
                    )

                last_processed_ts = current_hw_ts
                last_processed_marker = current_marker
                previous_values = {
                    "distance": safe_float(raw_data.get("distance"), DISTANCE_BASELINE_MM),
                    "temp": safe_float(raw_data.get("temp"), 0.0),
                    "vibration": safe_float(raw_data.get("vibration"), 0.0),
                }

            time.sleep(POLL_INTERVAL_SECONDS)
        except Exception as exc:
            print(f"AI Error: {exc}")
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

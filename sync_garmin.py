import os
import sys
import json
import traceback
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from garminconnect import Garmin

# =====================================================================
# 1. DEFAULT EXERCISE NAME MAPPING (GARMIN CATEGORY -> IRONPULSE LIFT)
# =====================================================================
GARMIN_TO_IRONPULSE_MAP = {
    "INCLINE_BENCH_PRESS": "DB Incline BenchPress",
    "BARBELL_INCLINE_BENCH_PRESS": "BB Incline BenchPress",
    "DUMBBELL_INCLINE_BENCH_PRESS": "DB Incline BenchPress",
    "DECLINE_BENCH_PRESS": "DB Decline BenchPress",
    "DUMBBELL_DECLINE_BENCH_PRESS": "DB Decline BenchPress",
    "BENCH_PRESS": "DB Incline BenchPress",
    "SHOULDER_PRESS": "DB Shoulder Press",
    "DUMBBELL_SHOULDER_PRESS": "DB Shoulder Press",
    "OVERHEAD_PRESS": "DB Shoulder Press",
    "PULL_UP": "Pull-up",
    "WEIGHTED_PULL_UP": "Pull-up",
    "LAT_PULLDOWN": "Pull-up",
    "ROW": "Row",
    "BENT_OVER_ROW": "Row",
    "CABLE_ROW": "Row",
    "DUMBBELL_ROW": "Row",
    "CURL": "Bicep Curl",
    "BICEPS_CURL": "Bicep Curl",
    "DUMBBELL_BICEPS_CURL": "Bicep Curl",
    "TRICEPS_EXTENSION": "Tricep Extension",
    "CABLE_TRICEPS_EXTENSION": "Tricep Extension",
    "TRICEPS_PRESSDOWN": "Tricep Extension",
    "LATERAL_RAISE": "Lateral Raise",
    "DUMBBELL_LATERAL_RAISE": "Lateral Raise",
    "LEG_PRESS": "Leg Press",
    "LEG_EXTENSION": "Leg Extension",
    "LEG_CURL": "Hamstring Curl",
    "HAMSTRING_CURL": "Hamstring Curl",
    "ROMANIAN_DEADLIFT": "Romainian Deadlift",
    "DEADLIFT": "Romainian Deadlift",
    "STIFF_LEG_DEADLIFT": "Romainian Deadlift"
}

IRONPULSE_CATEGORIES = {
    "BB Incline BenchPress": "Upper / Push",
    "DB Incline BenchPress": "Upper / Push",
    "DB Decline BenchPress": "Upper / Push",
    "DB Shoulder Press": "Upper / Push",
    "Lateral Raise": "Upper / Push",
    "Pull-up": "Upper / Pull & Arms",
    "Row": "Upper / Pull & Arms",
    "Bicep Curl": "Upper / Pull & Arms",
    "Tricep Extension": "Upper / Pull & Arms",
    "Leg Press": "Lower Body",
    "Leg Extension": "Lower Body",
    "Hamstring Curl": "Lower Body",
    "Romainian Deadlift": "Lower Body"
}


def calculate_epley_1rm(weight: float, reps: int) -> float:
    if weight <= 0 or reps <= 0:
        return 0.0
    if reps == 1:
        return round(weight, 1)
    return round(weight * (1.0 + reps / 30.0), 1)


def resolve_garmin_exercise_name(ex_set: dict) -> tuple[str, str]:
    exercises_list = ex_set.get("exercises") or []
    raw_name = "UNKNOWN_EXERCISE"

    if len(exercises_list) > 0:
        top_guess = exercises_list[0]
        subtype = top_guess.get("name")
        category = top_guess.get("category")
        raw_name = subtype if subtype else (category if category else "UNKNOWN_EXERCISE")

    mapped_name = GARMIN_TO_IRONPULSE_MAP.get(raw_name, raw_name)
    return mapped_name, raw_name


def clean_gist_id(raw_gist: str) -> str:
    cleaned = raw_gist.strip().rstrip("/")
    if "/" in cleaned:
        cleaned = cleaned.split("/")[-1]
    return cleaned


def fetch_existing_gist(gist_id: str, github_token: str, filename: str) -> dict:
    url = f"https://api.github.com/gists/{gist_id}"
    req = Request(url, headers={
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "IronPulse-Garmin-Sync"
    })
    try:
        with urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            files = data.get("files", {})
            if filename in files and files[filename].get("content"):
                return json.loads(files[filename]["content"])
    except Exception as e:
        print(f"Note: Could not read existing gist content ({e}). Starting fresh payload.")
    return {"workoutLogs": [], "bodyweightLogs": [], "recoveryLogs": []}


def extract_sleep_and_hrv_for_date(client: Garmin, date_str: str) -> dict:
    sleep_hours = None
    resting_hr = None
    hrv_ms = None

    # 1. Fetch Sleep Duration & Resting HR from Garmin Sleep Data
    try:
        sleep_data = client.get_sleep_data(date_str) or {}
        daily_dto = sleep_data.get("dailySleepDTO") or {}
        sleep_sec = daily_dto.get("sleepTimeSeconds")
        if sleep_sec and float(sleep_sec) > 0:
            sleep_hours = round(float(sleep_sec) / 3600.0, 2)

        rhr_val = sleep_data.get("restingHeartRate") or daily_dto.get("restingHeartRate") or daily_dto.get("avgHeartRate")
        if rhr_val and float(rhr_val) > 0:
            resting_hr = round(float(rhr_val), 1)

        # Garmin often includes average overnight HRV directly on sleep_data
        overnight_hrv = sleep_data.get("avgOvernightHrv") or daily_dto.get("avgOvernightHrv")
        if overnight_hrv and float(overnight_hrv) > 0:
            hrv_ms = round(float(overnight_hrv), 1)
    except Exception:
        pass

    # 2. Fetch Dedicated HRV Summary if not already populated
    if hrv_ms is None:
        try:
            hrv_data = client.get_hrv_data(date_str) or {}
            hrv_summary = hrv_data.get("hrvSummary") or {}
            val = hrv_summary.get("lastNightAvg") or hrv_summary.get("weeklyAvg")
            if val and float(val) > 0:
                hrv_ms = round(float(val), 1)
        except Exception:
            pass

    # 3. Fallback for Resting Heart Rate via get_rhr_day or get_heart_rates
    if resting_hr is None:
        try:
            hr_data = client.get_heart_rates(date_str) or {}
            rhr_val = hr_data.get("restingHeartRate")
            if rhr_val and float(rhr_val) > 0:
                resting_hr = round(float(rhr_val), 1)
        except Exception:
            pass

    return {
        "sleepHours": sleep_hours,
        "restingHR": resting_hr,
        "hrv": hrv_ms
    }


def update_gist(gist_id: str, github_token: str, filename: str, payload: dict):
    url = f"https://api.github.com/gists/{gist_id}"
    body = json.dumps({
        "description": f"IronPulse Garmin Cloud Sync (Last updated: {payload['lastSyncedAt']})",
        "files": {
            filename: {
                "content": json.dumps(payload, indent=2)
            }
        }
    }).encode("utf-8")

    req = Request(url, data=body, method="PATCH", headers={
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "IronPulse-Garmin-Sync"
    })
    try:
        with urlopen(req) as resp:
            if resp.status not in (200, 201):
                raise RuntimeError(f"GitHub Gist update failed with HTTP {resp.status}")
        print("Successfully updated secret GitHub Gist with Garmin data.")
    except HTTPError as err:
        err_body = err.read().decode("utf-8", errors="ignore")
        print(f"ERROR updating GitHub Gist (HTTP {err.code}): {err_body}")
        sys.exit(1)


def main():
    email = (os.environ.get("GARMIN_EMAIL") or "").strip()
    password = (os.environ.get("GARMIN_PASSWORD") or "").strip()
    raw_gist_id = (os.environ.get("GIST_ID") or "").strip()
    github_token = (os.environ.get("GH_GIST_TOKEN") or "").strip()
    days_back = int(os.environ.get("SYNC_DAYS_BACK") or "30")
    custom_start_str = (os.environ.get("SYNC_START_DATE") or "").strip()
    custom_end_str = (os.environ.get("SYNC_END_DATE") or "").strip()
    gist_filename = "ironpulse_garmin_sync.json"

    if not email or not password or not raw_gist_id or not github_token:
        print("ERROR: Missing required GitHub Actions Secrets (GARMIN_EMAIL, GARMIN_PASSWORD, GIST_ID, GH_GIST_TOKEN).")
        sys.exit(1)

    gist_id = clean_gist_id(raw_gist_id)

    print("Authenticating with Garmin Connect...")
    try:
        client = Garmin(email, password)
        client.login()
    except Exception as e:
        print(f"ERROR authenticating with Garmin Connect: {e}")
        traceback.print_exc()
        sys.exit(1)

    today = datetime.now(timezone.utc).date()
    try:
        end_date = datetime.fromisoformat(custom_end_str).date() if custom_end_str else today
    except ValueError:
        end_date = today

    try:
        start_date = datetime.fromisoformat(custom_start_str).date() if custom_start_str else (end_date - timedelta(days=days_back))
    except ValueError:
        start_date = end_date - timedelta(days=days_back)

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    existing_data = fetch_existing_gist(gist_id, github_token, gist_filename)
    sets_by_id = {s["id"]: s for s in existing_data.get("workoutLogs", []) if "id" in s}
    bw_by_date = {b["date"]: b for b in existing_data.get("bodyweightLogs", []) if "date" in b}
    recovery_by_date = {r["date"]: r for r in existing_data.get("recoveryLogs", []) if "date" in r}

    print(f"Fetching Garmin activities from {start_date.isoformat()} to {end_date.isoformat()}...")
    all_activities = client.get_activities_by_date(start_date.isoformat(), end_date.isoformat())

    activities = [
        act for act in (all_activities or [])
        if (act.get("activityType") or {}).get("typeKey") in ("strength_training", "fitness_equipment")
    ]
    print(f"Found {len(activities)} strength activities out of {len(all_activities or [])} total activities.")

    new_sets_count = 0
    for act in activities:
        activity_id = act.get("activityId")
        if not activity_id:
            continue

        start_local = act.get("startTimeLocal") or act.get("startTimeGMT") or ""
        date_str = start_local[:10] if len(start_local) >= 10 else today.isoformat()
        try:
            base_ts = int(datetime.fromisoformat(start_local.replace(" ", "T")).timestamp() * 1000)
        except Exception:
            base_ts = int(datetime.now(timezone.utc).timestamp() * 1000)

        try:
            details = client.get_activity_exercise_sets(activity_id)
        except Exception as e:
            print(f"Skipping activity {activity_id}: could not fetch exercise sets ({e})")
            continue

        exercise_sets = (details or {}).get("exerciseSets") or []
        active_index = 0

        for ex_set in exercise_sets:
            if ex_set.get("setType") != "ACTIVE":
                continue

            reps = int(ex_set.get("repetitionCount") or 0)
            raw_weight_grams = float(ex_set.get("weight") or 0.0)
            weight_kg = round(raw_weight_grams / 1000.0, 2) if raw_weight_grams > 0 else 0.0

            if reps <= 0 and weight_kg <= 0:
                continue

            active_index += 1
            mapped_name, raw_garmin_name = resolve_garmin_exercise_name(ex_set)
            category = IRONPULSE_CATEGORIES.get(mapped_name, "Custom")
            volume = round(weight_kg * reps, 1)
            e1rm = calculate_epley_1rm(weight_kg, reps)

            set_id = f"garmin_{activity_id}_{active_index}"
            sets_by_id[set_id] = {
                "id": set_id,
                "garminActivityId": activity_id,
                "timestamp": base_ts + (active_index * 60000),
                "date": date_str,
                "exercise": mapped_name,
                "rawGarminExercise": raw_garmin_name,
                "category": category,
                "weight": weight_kg,
                "reps": reps,
                "notes": f"Garmin Sync ({raw_garmin_name})",
                "volume": volume,
                "e1rm": e1rm
            }
            new_sets_count += 1

    print(f"Fetching Garmin body composition from {start_date.isoformat()} to {end_date.isoformat()}...")
    try:
        body_comp = client.get_body_composition(start_date.isoformat(), end_date.isoformat())
        weight_list = (body_comp or {}).get("dateWeightList") or []
        for entry in weight_list:
            cal_date = entry.get("calendarDate")
            weight_grams = entry.get("weight")
            if cal_date and weight_grams:
                bw_kg = round(float(weight_grams) / 1000.0, 1)
                bw_by_date[cal_date] = {
                    "id": f"garmin_bw_{cal_date}",
                    "timestamp": int(entry.get("timestampGMT") or datetime.now().timestamp() * 1000),
                    "date": cal_date,
                    "weight": bw_kg
                }
    except Exception as e:
        print(f"Warning: Could not fetch body composition ({e})")

    print(f"Fetching Garmin sleep, resting HR & HRV from {start_date.isoformat()} to {end_date.isoformat()}...")
    cur_date = start_date
    recovery_days_count = 0
    while cur_date <= end_date:
        d_str = cur_date.isoformat()
        metrics = extract_sleep_and_hrv_for_date(client, d_str)
        if metrics["sleepHours"] is not None or metrics["restingHR"] is not None or metrics["hrv"] is not None:
            existing_rec = recovery_by_date.get(d_str, {})
            recovery_by_date[d_str] = {
                "id": f"garmin_rec_{d_str}",
                "timestamp": int(datetime.fromisoformat(d_str).replace(tzinfo=timezone.utc).timestamp() * 1000),
                "date": d_str,
                "sleepHours": metrics["sleepHours"] if metrics["sleepHours"] is not None else existing_rec.get("sleepHours"),
                "restingHR": metrics["restingHR"] if metrics["restingHR"] is not None else existing_rec.get("restingHR"),
                "hrv": metrics["hrv"] if metrics["hrv"] is not None else existing_rec.get("hrv")
            }
            recovery_days_count += 1
        cur_date += timedelta(days=1)

    merged_sets = sorted(sets_by_id.values(), key=lambda x: (x["date"], x["timestamp"]))
    merged_bw = sorted(bw_by_date.values(), key=lambda x: x["date"])
    merged_recovery = sorted(recovery_by_date.values(), key=lambda x: x["date"])

    payload = {
        "lastSyncedAt": datetime.now(timezone.utc).isoformat(),
        "syncedDaysWindow": days_back,
        "workoutLogs": merged_sets,
        "bodyweightLogs": merged_bw,
        "recoveryLogs": merged_recovery
    }

    update_gist(gist_id, github_token, gist_filename, payload)
    print(f"Sync complete! Total sets: {len(merged_sets)} (+{new_sets_count} recent), Weigh-ins: {len(merged_bw)}, Recovery days: {len(merged_recovery)} (+{recovery_days_count} checked)")


if __name__ == "__main__":
    main()

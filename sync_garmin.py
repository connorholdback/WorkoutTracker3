import os
import sys
import json
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from garminconnect import Garmin

# =====================================================================
# 1. DEFAULT EXERCISE NAME MAPPING (GARMIN CATEGORY -> IRONPULSE LIFT)
# =====================================================================
# Garmin stores exercises as (category, subtype). You can customize these
# or map any unmapped Garmin exercise directly inside the IronPulse UI!
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
    """
    Extracts the raw Garmin exercise identifier from the `exercises` array inside a set message
    and returns (mapped_ironpulse_name, raw_garmin_name).
    """
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
    """
    Accepts either a 32-char Gist ID or a full https://gist.github.com/user/id URL
    and returns the clean Gist ID.
    """
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
    return {"workoutLogs": [], "bodyweightLogs": []}


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
        print("✅ Successfully updated secret GitHub Gist with Garmin data.")
    except HTTPError as err:
        err_body = err.read().decode("utf-8", errors="ignore")
        print(f"❌ ERROR updating GitHub Gist (HTTP {err.code}): {err_body}")
        print("-> Check that GIST_ID is valid and GH_GIST_TOKEN has 'gist' (Read/Write) scope.")
        sys.exit(1)


def main():
    email = (os.environ.get("GARMIN_EMAIL") or "").strip()
    password = (os.environ.get("GARMIN_PASSWORD") or "").strip()
    raw_gist_id = (os.environ.get("GIST_ID") or "").strip()
    github_token = (os.environ.get("GH_GIST_TOKEN") or "").strip()
    days_back = int(os.environ.get("SYNC_DAYS_BACK") or "30")
    gist_filename = "ironpulse_garmin_sync.json"

    # 1. Explicitly check which GitHub Repository Secrets are present
    missing_secrets = []
    if not email:
        missing_secrets.append("GARMIN_EMAIL")
    if not password:
        missing_secrets.append("GARMIN_PASSWORD")
    if not raw_gist_id:
        missing_secrets.append("GIST_ID")
    if not github_token:
        missing_secrets.append("GH_GIST_TOKEN")

    if missing_secrets:
        print("❌ ERROR: The following required GitHub Actions Secrets are empty or missing:")
        for s in missing_secrets:
            print(f"   - {s}")
        print("\nFix: Go to your GitHub Repo -> Settings -> Secrets and variables -> Actions,")
        print("and ensure all 4 secrets are added under 'Repository secrets' (not Environment secrets).")
        sys.exit(1)

    gist_id = clean_gist_id(raw_gist_id)
    print(f"Configuration check passed (Gist ID length: {len(gist_id)} chars, Window: {days_back} days).")

    # 2. Authenticate with Garmin Connect
    print("Authenticating with Garmin Connect...")
    try:
        client = Garmin(email, password)
        client.login()
        print("✅ Authenticated with Garmin Connect successfully.")
    except Exception as e:
        print(f"❌ ERROR authenticating with Garmin Connect: {e}")
        print("-> Verify GARMIN_EMAIL and GARMIN_PASSWORD in GitHub Secrets, and ensure 2FA/MFA is disabled on your Garmin account.")
        sys.exit(1)

    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days_back)

    # Load existing Gist payload so we merge cleanly without losing older synced records
    existing_data = fetch_existing_gist(gist_id, github_token, gist_filename)
    sets_by_id = {s["id"]: s for s in existing_data.get("workoutLogs", []) if "id" in s}
    bw_by_date = {b["date"]: b for b in existing_data.get("bodyweightLogs", []) if "date" in b}

    # -----------------------------------------------------------------
    # A. FETCH STRENGTH TRAINING ACTIVITIES & EXERCISE SETS
    # -----------------------------------------------------------------
    print(f"Fetching Garmin activities since {start_date.isoformat()}...")
    activities = client.get_activities_by_date(
        start_date.isoformat(),
        today.isoformat(),
        activitytype="strength_training"
    )
    print(f"Found {len(activities)} strength_training activities.")

    new_sets_count = 0
    for act in activities:
        activity_id = act.get("activityId")
        start_local = act.get("startTimeLocal") or act.get("startTimeGMT") or ""
        date_str = start_local[:10] if len(start_local) >= 10 else today.isoformat()

        try:
            ex_sets_resp = client.get_activity_exercise_sets(activity_id)
        except Exception as e:
            print(f"Skipping activity {activity_id} (could not fetch exercise sets: {e})")
            continue

        if not ex_sets_resp:
            continue

        exercise_sets = ex_sets_resp.get("exerciseSets") or []
        active_set_idx = 0

        for s in exercise_sets:
            # Skip rest intervals; only process ACTIVE lifting sets
            if s.get("setType") != "ACTIVE":
                continue

            reps = int(s.get("repetitionCount") or 0)
            # Garmin stores weight in grams inside .FIT set messages (e.g. 60000 = 60.0 kg)
            raw_weight_grams = float(s.get("weight") or 0.0)
            weight_kg = round(raw_weight_grams / 1000.0, 2) if raw_weight_grams > 0 else 0.0

            if reps <= 0:
                continue

            active_set_idx += 1
            mapped_name, raw_garmin_name = resolve_garmin_exercise_name(s)
            category = IRONPULSE_CATEGORIES.get(mapped_name, "Custom")
            volume = round(weight_kg * reps, 1)
            e1rm = calculate_epley_1rm(weight_kg, reps)

            set_id = f"garmin_{activity_id}_{active_set_idx}"
            try:
                base_ts = int(datetime.fromisoformat(start_local).timestamp() * 1000)
            except Exception:
                base_ts = int(datetime.now().timestamp() * 1000)

            sets_by_id[set_id] = {
                "id": set_id,
                "garminActivityId": activity_id,
                "timestamp": base_ts + (active_set_idx * 60000),
                "date": date_str,
                "exercise": mapped_name,
                "rawGarminExercise": raw_garmin_name,
                "category": category,
                "weight": weight_kg,
                "reps": reps,
                "notes": f"Garmin Sync ({raw_garmin_name})" if mapped_name != raw_garmin_name else "Garmin Sync",
                "volume": volume,
                "e1rm": e1rm
            }
            new_sets_count += 1

    # -----------------------------------------------------------------
    # B. FETCH GARMIN BODYWEIGHT / INDEX SCALE WEIGH-INS
    # -----------------------------------------------------------------
    print(f"Fetching Garmin body composition from {start_date.isoformat()} to {today.isoformat()}...")
    try:
        body_comp = client.get_body_composition(start_date.isoformat(), today.isoformat())
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

    # Sort logs chronologically
    merged_sets = sorted(sets_by_id.values(), key=lambda x: (x["date"], x["timestamp"]))
    merged_bw = sorted(bw_by_date.values(), key=lambda x: x["date"])

    payload = {
        "lastSyncedAt": datetime.now(timezone.utc).isoformat(),
        "syncedDaysWindow": days_back,
        "workoutLogs": merged_sets,
        "bodyweightLogs": merged_bw
    }

    update_gist(gist_id, github_token, gist_filename, payload)
    print(f"🎉 Sync complete! Total sets in Gist: {len(merged_sets)} (processed {new_sets_count} recent sets), Total weigh-ins: {len(merged_bw)}")


if __name__ == "__main__":
    main()

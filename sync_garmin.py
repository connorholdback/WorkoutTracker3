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
        print("Successfully updated secret GitHub Gist with Garmin data.")
    except HTTPError as err:
        err_body = err.read().decode("utf-8", errors="ignore")
        print(f"ERROR updating GitHub Gist (HTTP {err.code}): {err_body}")
        print("-> Check that GIST_ID is valid and GH_GIST_TOKEN has 'gist' (Read/Write) scope.")
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
        print("ERROR: The following required GitHub Actions Secrets are empty or missing:")
        for s in missing_secrets:
            print(f"   - {s}")
        print("\nFix: Go to your GitHub Repo -> Settings -> Secrets and variables -> Actions,")
        print("and ensure all 4 secrets are added under 'Repository secrets' (not Environment secrets).")
        sys.exit(1)

    gist_id = clean_gist_id(raw_gist_id)
    print(f"Configuration check passed (Gist ID length: {len(gist_id)} chars, Window: {days_back} days).")

    print("Authenticating with Garmin Connect...")
    try:
        client = Garmin(email, password)
        client.login()
        print("Authenticated with Garmin Connect successfully.")
    except Exception as e:
        print(f"ERROR authenticating with Garmin Connect: {e}")
        traceback.print_exc()
        print("-> Verify GARMIN_EMAIL and GARMIN_PASSWORD in GitHub Secrets, and ensure 2FA/MFA is disabled on your Garmin account.")
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

    print(f"Fetching Garmin activities from {start_date.isoformat()} to {end_date.isoformat()}...")
    # Note: Garmin Connect API rejects subtypes like 'strength_training' in the activitytype query param (HTTP 400)
    # because 'strength_training' is a subtype under 'fitness_equipment'.
    # Fetching all activities in the date range and filtering in Python avoids the 400 error reliably.
    all_activities = client.get_activities_by_date(
        start_date.isoformat(),
        end_date.isoformat()
    )

    activities = [
# ... existing code ... -->
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

    merged_sets = sorted(sets_by_id.values(), key=lambda x: (x["date"], x["timestamp"]))
    merged_bw = sorted(bw_by_date.values(), key=lambda x: x["date"])

    payload = {
        "lastSyncedAt": datetime.now(timezone.utc).isoformat(),
        "syncedDaysWindow": days_back,
        "workoutLogs": merged_sets,
        "bodyweightLogs": merged_bw
    }

    update_gist(gist_id, github_token, gist_filename, payload)
    print(f"Sync complete! Total sets in Gist: {len(merged_sets)} (processed {new_sets_count} recent sets), Total weigh-ins: {len(merged_bw)}")


if __name__ == "__main__":
    main()

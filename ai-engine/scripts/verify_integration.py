import base64
import json
import time
import httpx
from pathlib import Path

API_URL = "http://127.0.0.1:8000/ai/analyze"

def get_base64_image(color=(100, 100, 100)):
    from PIL import Image
    import io
    import base64
    img = Image.new("RGB", (64, 64), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")

def get_another_base64_image():
    return get_base64_image(color=(0, 0, 255))

def print_result(title, res):
    print(f"\n==================================================")
    print(f"SCENARIO: {title}")
    print(f"==================================================")
    print(f"Status Code: {res.status_code}")
    try:
        data = res.json()
        print(json.dumps(data, indent=2))
    except Exception:
        print(res.text)

def main():
    client = httpx.Client(timeout=200.0)
    
    # 1. Genuine Critical Report
    payload_critical = {
        "report_id": "RPT-CRIT-001",
        "description": "Severe flooding. Water level rising fast, people trapped in ground floor with injuries.",
        "image": get_base64_image(),
        "latitude": 12.9716,
        "longitude": 77.5946,
        "client_timestamp": "2026-08-09T10:00:00Z",
        "reporter_pseudonym": "USER-CRIT-1"
    }
    res = client.post(API_URL, json=payload_critical)
    print_result("1. Genuine Critical Report", res)
    
    # 2. Duplicate Image
    # Reuse the same image, same pseud, far coordinates to trigger both duplicate & impossible movement -> FLAGGED
    payload_dup = {
        "report_id": "RPT-DUP-002",
        "description": "Water rising fast here too.",
        "image": get_base64_image(),
        "latitude": 18.5204,
        "longitude": 73.8567,
        "client_timestamp": "2026-08-09T10:05:00Z",
        "reporter_pseudonym": "USER-CRIT-1"
    }
    res = client.post(API_URL, json=payload_dup)
    print_result("2. Duplicate Image (Should decrease authenticity and be FLAGGED)", res)

    # 3. Invalid GPS
    payload_invalid_gps = {
        "report_id": "RPT-GPS-003",
        "description": "Storm damage here.",
        "image": None,
        "latitude": 500.0,  # Invalid GPS (should be -90 to 90)
        "longitude": 77.5946,
        "client_timestamp": "2026-08-09T10:10:00Z",
        "reporter_pseudonym": "USER-GPS-3"
    }
    res = client.post(API_URL, json=payload_invalid_gps)
    print_result("3. Invalid GPS", res)

    # 4. Corroborated Reports (Triplet)
    # We send report 1, then report 2, then report 3 close by
    payload_corr1 = {
        "report_id": "RPT-CORR-01",
        "description": "Flooding near the river path.",
        "image": get_another_base64_image(), # different image
        "latitude": 12.9720,
        "longitude": 77.5960,
        "client_timestamp": "2026-08-09T10:15:00Z",
        "reporter_pseudonym": "USER-CORR-1"
    }
    client.post(API_URL, json=payload_corr1)
    
    payload_corr2 = {
        "report_id": "RPT-CORR-02",
        "description": "Heavy flooding on the road.",
        "image": None,
        "latitude": 12.9722,
        "longitude": 77.5962,
        "client_timestamp": "2026-08-09T10:17:00Z",
        "reporter_pseudonym": "USER-CORR-2"
    }
    client.post(API_URL, json=payload_corr2)
    
    # This third one should trigger corroboration
    payload_corr3 = {
        "report_id": "RPT-CORR-03",
        "description": "Flooding entering our shops.",
        "image": None,
        "latitude": 12.9724,
        "longitude": 77.5964,
        "client_timestamp": "2026-08-09T10:20:00Z",
        "reporter_pseudonym": "USER-CORR-3"
    }
    res = client.post(API_URL, json=payload_corr3)
    print_result("4. Corroborated Reports (Third report in triplet)", res)

    # 5. Text-Only Report
    payload_text_only = {
        "report_id": "RPT-TEXT-005",
        "description": "Tree fallen on power line.",
        "image": None,
        "latitude": 12.9730,
        "longitude": 77.5970,
        "client_timestamp": "2026-08-09T10:25:00Z",
        "reporter_pseudonym": "USER-TEXT-5"
    }
    res = client.post(API_URL, json=payload_text_only)
    print_result("5. Text-Only Report (No image)", res)

if __name__ == "__main__":
    main()

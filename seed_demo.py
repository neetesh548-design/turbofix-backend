"""Seed script — creates the DEMO company with owner + 11 supervisors + 80 machines.

Uses the live API endpoints:
  - POST /admin/login          (admin token)
  - POST /admin/companies      (onboard company + owner)
  - POST /admin/companies/{code} (approve + set quota)
  - POST /auth/login           (owner token)
  - POST /auth/supervisors     (owner adds supervisors)
  - POST /vault/machines       (owner onboards machines)
"""

import sys
import time
import requests

BASE = "https://turbofix-backend-ehxb.onrender.com"
ADMIN_PWD = "dev-admin-change-me"

COMPANY_CODE = "DEMO"
COMPANY_NAME = "Shree Krishna Auto Components Pvt. Ltd."
ADMIN_PHONE = "+919876500001"
OWNER_NAME = "Rajesh Mehta"
OWNER_EMAIL = "owner@demo.turbofix.in"
OWNER_PWD = "Demo@1234"

# 4 key login credentials:
#   1. Owner:      owner@demo.turbofix.in  /  Demo@1234
#   2. Supervisor: vijay@demo.turbofix.in  /  Demo@1234
#   3. Supervisor: priya@demo.turbofix.in  /  Demo@1234
#   4. Supervisor: +919876500010           /  Demo@1234

SUPERVISORS = [
    ("Vijay Sharma",       "+919876500002", "vijay@demo.turbofix.in"),
    ("Priya Patel",        "+919876500003", "priya@demo.turbofix.in"),
    ("Amit Kulkarni",      "+919876500010", "amit@demo.turbofix.in"),
    ("Suresh Yadav",       "+919876500004", "suresh@demo.turbofix.in"),
    ("Deepak Joshi",       "+919876500005", "deepak@demo.turbofix.in"),
    ("Kavita Singh",       "+919876500006", "kavita@demo.turbofix.in"),
    ("Ramesh Gupta",       "+919876500007", "ramesh@demo.turbofix.in"),
    ("Anita Deshmukh",     "+919876500008", "anita@demo.turbofix.in"),
    ("Manoj Tiwari",       "+919876500009", "manoj@demo.turbofix.in"),
    ("Pooja Verma",        "+919876500011", "pooja@demo.turbofix.in"),
    ("Sanjay Mishra",      "+919876500012", "sanjay@demo.turbofix.in"),
]

MACHINES = [
    ("CNC Turning Center — Fanuc α", "Production Line A"),
    ("CNC Turning Center — Mazak QT", "Production Line A"),
    ("CNC VMC — Haas VF-2", "Production Line A"),
    ("CNC VMC — BFW Agni", "Production Line A"),
    ("CNC HMC — Makino A61nx", "Production Line A"),
    ("Traub CNC Lathe — SL-42", "Production Line A"),
    ("Traub CNC Lathe — SL-55", "Production Line A"),
    ("Conventional Lathe — HMT NH-22", "Production Line A"),
    ("Conventional Lathe — HMT NH-26", "Production Line A"),
    ("Conventional Lathe — Kirloskar", "Production Line A"),
    ("Auto Bar Feed — Iemca Boss", "Production Line A"),
    ("Bar Feed — LNS Quick Load", "Production Line A"),
    ("Turret Punch — Amada", "Production Line A"),
    ("CNC Drilling Center — BFW", "Production Line A"),
    ("Tapping Machine — Tapmatic", "Production Line A"),
    ("Thread Rolling Machine — Saroj", "Production Line A"),
    ("Broaching Machine — Laxmi", "Production Line A"),
    ("Honing Machine — Sunnen", "Production Line A"),
    ("Deburring Machine — Rösler", "Production Line A"),
    ("Chamfering Machine — Daito", "Production Line A"),
    ("Universal Milling — HMT FN-3", "Production Line B"),
    ("Vertical Mill — BFW VKM-30", "Production Line B"),
    ("Surface Grinder — Praga 450", "Production Line B"),
    ("Cylindrical Grinder — HMT K-130", "Production Line B"),
    ("Centerless Grinder — Micromatic ACE", "Production Line B"),
    ("Internal Grinder — Voumard", "Production Line B"),
    ("Tool & Cutter Grinder — Deckel", "Production Line B"),
    ("Gear Hobbing — Gleason-Pfauter", "Production Line B"),
    ("Gear Shaping — Fellows", "Production Line B"),
    ("Spline Rolling — Formtek", "Production Line B"),
    ("Jig Boring — SIP", "Production Line B"),
    ("EDM Wire Cut — Sodick VL400Q", "Production Line B"),
    ("EDM Sinker — Charmilles", "Production Line B"),
    ("Lapping Machine — Lapmaster", "Production Line B"),
    ("Polishing Machine — Autopulit", "Production Line B"),
    ("Hydraulic Press — 100T Kawa", "Assembly Section"),
    ("Hydraulic Press — 50T Rajkot", "Assembly Section"),
    ("Pneumatic Press — 10T Festo", "Assembly Section"),
    ("Bearing Press — Denford", "Assembly Section"),
    ("Assembly Conveyor — Belt Line 1", "Assembly Section"),
    ("Assembly Conveyor — Belt Line 2", "Assembly Section"),
    ("Torque Station — Atlas Copco ST", "Assembly Section"),
    ("Riveting Machine — Baltec", "Assembly Section"),
    ("Crimping Machine — TE Connectivity", "Assembly Section"),
    ("Leak Tester — ATEQ F600", "Assembly Section"),
    ("Ultrasonic Welder — Branson 2000", "Assembly Section"),
    ("Vision Inspection — Keyence CV-X", "Assembly Section"),
    ("Stretch Wrapper — Robopac", "Packaging Unit"),
    ("Shrink Tunnel — Cyklop", "Packaging Unit"),
    ("Carton Sealer — 3M-Matic", "Packaging Unit"),
    ("Strapping Machine — Signode", "Packaging Unit"),
    ("Labelling Machine — Domino A520i", "Packaging Unit"),
    ("Inkjet Printer — Videojet 1580", "Packaging Unit"),
    ("Weighing Scale — Mettler Toledo", "Packaging Unit"),
    ("Pallet Truck — Toyota BT Levio", "Packaging Unit"),
    ("Vacuum Packer — Henkelman", "Packaging Unit"),
    ("Dunnage Packer — Ranpak", "Packaging Unit"),
    ("Screw Compressor — Atlas Copco GA-30", "Utility Room"),
    ("Screw Compressor — Elgi EG-22", "Utility Room"),
    ("Air Dryer — Atlas Copco FD-150", "Utility Room"),
    ("Cooling Tower — Paharpur 3P", "Utility Room"),
    ("Chiller Unit — Blue Star 30TR", "Utility Room"),
    ("Diesel Generator — Cummins 250 kVA", "Utility Room"),
    ("Transformer — 1000 kVA ABB", "Utility Room"),
    ("Fire Pump — Kirloskar 75 HP", "Utility Room"),
    ("CMM — Zeiss Contura", "Quality Lab"),
    ("Profile Projector — Mitutoyo PJ-A3000", "Quality Lab"),
    ("Roundness Tester — Taylor Hobson", "Quality Lab"),
    ("Hardness Tester — Rockwell Wilson", "Quality Lab"),
    ("Surface Roughness — Mitutoyo SJ-410", "Quality Lab"),
    ("Shot Blasting — Wheelabrator", "Paint Shop"),
    ("Phosphating Tank — Thermax", "Paint Shop"),
    ("Powder Coating Booth — Nordson", "Paint Shop"),
    ("Curing Oven — 250°C Therelek", "Paint Shop"),
    ("Dip Painting — Spray Systems", "Paint Shop"),
    ("MIG Welder — Fronius TPS 400i", "Welding Bay"),
    ("TIG Welder — Miller Dynasty 280", "Welding Bay"),
    ("Spot Welder — TECNA 4664", "Welding Bay"),
    ("Robotic Welder — Fanuc ARC Mate", "Welding Bay"),
    ("Plasma Cutter — Hypertherm HPR260", "Welding Bay"),
]

TECH_PHONES = [
    "+919876500020", "+919876500021", "+919876500022", "+919876500023",
    "+919876500024", "+919876500025", "+919876500026", "+919876500027",
    "+919876500028", "+919876500029",
]


def main():
    s = requests.Session()
    s.headers["Content-Type"] = "application/json"

    # 1. Admin login
    print("1. Admin login...")
    r = s.post(f"{BASE}/admin/login", json={"password": ADMIN_PWD})
    if r.status_code != 200:
        print(f"   FAIL: {r.status_code} {r.text}")
        sys.exit(1)
    admin_token = r.json()["access_token"]
    admin_auth = {"Authorization": f"Bearer {admin_token}"}
    print("   OK")

    # 2. Onboard company (creates owner account)
    print(f"2. Onboarding company {COMPANY_CODE}...")
    r = s.post(f"{BASE}/admin/companies", json={
        "company_code": COMPANY_CODE,
        "company_name": COMPANY_NAME,
        "admin_contact_phone": ADMIN_PHONE,
        "owner_name": OWNER_NAME,
        "owner_email": OWNER_EMAIL,
        "owner_password": OWNER_PWD,
        "machine_quota": 100,
    }, headers=admin_auth)
    if r.status_code == 409:
        print("   SKIP — company already exists")
    elif r.status_code == 201:
        print(f"   OK — {r.json()}")
    else:
        print(f"   FAIL: {r.status_code} {r.text}")
        sys.exit(1)

    # 3. Approve company + set quota
    print("3. Approving company + setting quota=100...")
    r = s.post(f"{BASE}/admin/companies/{COMPANY_CODE}", json={
        "machine_quota": 100, "approved": True,
    }, headers=admin_auth)
    print(f"   {r.status_code}: {r.text}")

    # 4. Owner login
    print("4. Owner login...")
    r = s.post(f"{BASE}/auth/login", json={
        "identifier": OWNER_EMAIL, "password": OWNER_PWD,
    })
    if r.status_code != 200:
        print(f"   FAIL: {r.status_code} {r.text}")
        sys.exit(1)
    owner_token = r.json()["access_token"]
    owner_auth = {"Authorization": f"Bearer {owner_token}"}
    print("   OK")

    # 5. Create supervisors (owner-only endpoint)
    print("5. Creating supervisors via POST /auth/supervisors...")
    for name, phone, email in SUPERVISORS:
        r = s.post(f"{BASE}/auth/supervisors", json={
            "name": name,
            "phone": phone,
            "email": email,
            "password": OWNER_PWD,
        }, headers=owner_auth)
        if r.status_code == 201:
            uid = r.json().get("user_id", "?")
            print(f"   + {name} ({email}) -> {uid}")
        elif r.status_code == 409:
            print(f"   ~ {name} (already exists)")
        else:
            print(f"   FAIL {name}: {r.status_code} {r.text}")

    # 6. Onboard 80 machines
    print(f"6. Onboarding {len(MACHINES)} machines...")
    created = 0
    for i, (machine_name, location) in enumerate(MACHINES):
        tech = TECH_PHONES[i % len(TECH_PHONES)]
        r = s.post(f"{BASE}/vault/machines", json={
            "machine_name": machine_name,
            "location": location,
            "assigned_technician_phone": tech,
            "informed_phone_1": TECH_PHONES[(i + 1) % len(TECH_PHONES)],
            "informed_phone_2": TECH_PHONES[(i + 3) % len(TECH_PHONES)],
            "informed_phone_3": ADMIN_PHONE,
        }, headers=owner_auth)
        if r.status_code == 201:
            created += 1
            if created % 10 == 0 or created == len(MACHINES):
                mid = r.json().get("machine_id", "?")
                print(f"   [{created}/{len(MACHINES)}] {mid} — {machine_name}")
        else:
            print(f"   FAIL {machine_name}: {r.status_code} {r.text}")
            if r.status_code == 402:
                print("   Quota exhausted — stopping.")
                break
        if i % 20 == 19:
            time.sleep(1)

    print(f"\n{'='*60}")
    print("DONE! Demo credentials:")
    print(f"{'='*60}")
    print(f"  1. Owner:      {OWNER_EMAIL}  /  {OWNER_PWD}")
    print(f"  2. Supervisor: vijay@demo.turbofix.in  /  {OWNER_PWD}")
    print(f"  3. Supervisor: priya@demo.turbofix.in  /  {OWNER_PWD}")
    print(f"  4. Supervisor: +919876500010  /  {OWNER_PWD}")
    print(f"\nCompany: {COMPANY_NAME} ({COMPANY_CODE})")
    print(f"Machines onboarded: {created}")
    print(f"Supervisors: {len(SUPERVISORS)}")


if __name__ == "__main__":
    main()

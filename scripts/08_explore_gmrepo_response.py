"""
08_explore_gmrepo_response.py

Diagnostic only. Testing whether the GMrepo API is alive at all via
simpler parameter-free endpoints, and trying the bulk health-phenotype
endpoint as a potentially better-architected alternative to querying
111 organisms one at a time.

Usage:
    pip install requests
    python scripts/08_explore_gmrepo_response.py
"""

import json

import requests

BASE = "https://gmrepo.humangut.info/api"

print("=== Test 1: get_all_gut_microbes (no params -- checks if API is alive at all) ===")
try:
    resp = requests.post(f"{BASE}/get_all_gut_microbes", data={}, timeout=30)
    print(f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print(f"keys: {list(data.keys())}")
        species = data.get("all_species")
        if species:
            print(f"all_species: {len(species)} entries, first entry keys: {list(species[0].keys())}")
            print(f"first entry: {species[0]}")
except Exception as exc:
    print(f"failed: {type(exc).__name__}: {exc}")

print("\n=== Test 2: getAssociatedSpeciesByMeshID for Health (D006262) -- bulk, one call ===")
try:
    resp = requests.post(
        f"{BASE}/getAssociatedSpeciesByMeshID",
        data=json.dumps({"mesh_id": "D006262"}),
        timeout=30,
    )
    print(f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        if isinstance(data, list):
            print(f"got a list: {len(data)} entries")
            if data:
                print(f"first entry keys: {list(data[0].keys())}")
                print(f"first entry: {data[0]}")
        else:
            print(f"keys: {list(data.keys())}")
except Exception as exc:
    print(f"failed: {type(exc).__name__}: {exc}")

"""
18_explore_bacspad.py

Diagnostic only -- not the real loader. BacSPaD (Zenodo record
13235447) looks like an excellent match for body-site + commensal/
pathogen data: its documented field list includes biotic_relationship
(controlled vocabulary: '', 'free living', 'parasite', 'commensal',
'symbiont') and body_sample_site on the same row, plus a broader
pathogenicity_label (HP/NHP). But that's the *documented* schema --
real files often have surprises (see: UHGG's GTDB-not-NCBI taxonomy
discovery earlier this project). This fetches the real file via
Zenodo's REST API and prints actual columns and a few real rows
before any loader gets built.

Usage:
    pip install requests
    python scripts/18_explore_bacspad.py
"""

import requests

ZENODO_RECORD_API = "https://zenodo.org/api/records/13235447"

resp = requests.get(ZENODO_RECORD_API, timeout=30)
resp.raise_for_status()
record = resp.json()

files = record.get("files", [])
print(f"Record title: {record.get('metadata', {}).get('title')}")
print(f"Found {len(files)} file(s) attached:\n")
for f in files:
    print(f"  {f.get('key')} -- {f.get('size')} bytes -- {f.get('links', {}).get('self')}")

if not files:
    raise SystemExit("No files found via the API -- check the record manually at https://zenodo.org/records/13235447")

target = files[0]
download_url = target["links"]["self"]
print(f"\nDownloading {target['key']} ...")
data_resp = requests.get(download_url, timeout=120)
data_resp.raise_for_status()

filename = target["key"]
with open(f"/tmp/{filename}", "wb") as fh:
    fh.write(data_resp.content)
print(f"Saved to /tmp/{filename} ({len(data_resp.content) / 1024:.0f} KB)")

text = data_resp.content[:5000].decode("utf-8", errors="replace")
print("\nFirst ~5000 characters of the file:\n")
print(text)

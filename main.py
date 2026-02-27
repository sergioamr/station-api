import asyncio
import os
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

# TfL StopPoint Arrivals endpoints (anonymous access, no key needed)
TFL_BASE = "https://api.tfl.gov.uk/StopPoint"
STATIONS = {
    "910GWOLWXR": "Elizabeth",   # Woolwich (Elizabeth Line)
    "940GZZDLWLA": "DLR",       # Woolwich Arsenal (DLR)
}

# National Rail Darwin SOAP API (Woolwich Arsenal, CRS: WWA)
DARWIN_TOKEN = os.environ.get("DARWIN_TOKEN", "")
DARWIN_URL = "https://lite.realtime.nationalrail.co.uk/OpenLDBWS/ldb11.asmx"
DARWIN_CRS = "WWA"
DARWIN_SOAP = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:typ="http://thalesgroup.com/RTTI/2013-11-28/Token/types"
               xmlns:ldb="http://thalesgroup.com/RTTI/2017-10-01/ldb/">
  <soap:Header>
    <typ:AccessToken>
      <typ:TokenValue>{token}</typ:TokenValue>
    </typ:AccessToken>
  </soap:Header>
  <soap:Body>
    <ldb:GetDepartureBoardRequest>
      <ldb:numRows>10</ldb:numRows>
      <ldb:crs>{crs}</ldb:crs>
    </ldb:GetDepartureBoardRequest>
  </soap:Body>
</soap:Envelope>"""

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
MAX_ARRIVALS = 10
EXCLUDED_DESTINATIONS = {"Abbey Wood", "Rainham (Kent)"}

# In-memory cache
_cache = {"updated": None, "arrivals": []}


def _parse_darwin_response(xml_text):
    """Parse Darwin SOAP response into arrival dicts."""
    arrivals = []
    root = ET.fromstring(xml_text)

    ns = {
        "lt4": "http://thalesgroup.com/RTTI/2015-11-27/ldb/types",
        "lt5": "http://thalesgroup.com/RTTI/2016-02-16/ldb/types",
        "lt7": "http://thalesgroup.com/RTTI/2017-10-01/ldb/types",
    }

    for service in root.iter("{http://thalesgroup.com/RTTI/2017-10-01/ldb/types}service"):
        dest_el = service.find("lt5:destination/lt4:location/lt4:locationName", ns)
        destination = dest_el.text if dest_el is not None else "Unknown"

        operator_el = service.find("lt4:operator", ns)
        operator = operator_el.text if operator_el is not None else ""

        std_el = service.find("lt4:std", ns)
        scheduled = std_el.text if std_el is not None else ""

        etd_el = service.find("lt4:etd", ns)
        estimated = etd_el.text if etd_el is not None else scheduled

        platform_el = service.find("lt4:platform", ns)
        platform = platform_el.text if platform_el is not None else ""

        # Build an ISO timestamp from the scheduled time (HH:MM today)
        if not scheduled:
            continue
        now = datetime.now(timezone.utc)
        try:
            hour, minute = map(int, scheduled.split(":"))
            expected_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            expected_iso = expected_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError):
            continue

        if destination in EXCLUDED_DESTINATIONS:
            continue

        arrivals.append({
            "destination": destination,
            "line": operator or "National Rail",
            "platform": platform,
            "expected": expected_iso,
            "status": estimated,
        })

    return arrivals


async def fetch_tfl_arrivals(client):
    """Fetch arrivals from TfL for Elizabeth Line and DLR."""
    arrivals = []
    coros = []
    labels = []
    for naptan_id, line_label in STATIONS.items():
        url = f"{TFL_BASE}/{naptan_id}/Arrivals"
        coros.append(client.get(url))
        labels.append(line_label)

    responses = await asyncio.gather(*coros, return_exceptions=True)

    for line_label, resp in zip(labels, responses):
        if isinstance(resp, Exception):
            print(f"[warn] Failed to fetch {line_label}: {resp}")
            continue
        if resp.status_code != 200:
            print(f"[warn] TfL returned {resp.status_code} for {line_label}")
            continue

        for p in resp.json():
            destination = p.get("destinationName", "Unknown")
            if destination in EXCLUDED_DESTINATIONS:
                continue
            arrivals.append({
                "destination": destination,
                "line": line_label,
                "platform": p.get("platformName", ""),
                "expected": p.get("expectedArrival", ""),
            })

    return arrivals


async def fetch_darwin_arrivals(client):
    """Fetch departures from Darwin SOAP API for National Rail."""
    if not DARWIN_TOKEN:
        return []

    body = DARWIN_SOAP.format(token=DARWIN_TOKEN, crs=DARWIN_CRS)
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "http://thalesgroup.com/RTTI/2012-01-13/ldb/GetDepartureBoard",
    }

    try:
        resp = await client.post(DARWIN_URL, content=body, headers=headers)
        if resp.status_code != 200:
            print(f"[warn] Darwin returned {resp.status_code}")
            return []
        return _parse_darwin_response(resp.text)
    except Exception as e:
        print(f"[warn] Darwin fetch failed: {e}")
        return []


async def fetch_arrivals():
    """Fetch arrivals from all sources concurrently."""
    async with httpx.AsyncClient(timeout=15) as client:
        tfl_task = fetch_tfl_arrivals(client)
        darwin_task = fetch_darwin_arrivals(client)
        tfl_arrivals, darwin_arrivals = await asyncio.gather(
            tfl_task, darwin_task, return_exceptions=True
        )

    arrivals = []
    if not isinstance(tfl_arrivals, Exception):
        arrivals.extend(tfl_arrivals)
    else:
        print(f"[warn] TfL fetch failed: {tfl_arrivals}")

    if not isinstance(darwin_arrivals, Exception):
        arrivals.extend(darwin_arrivals)
    else:
        print(f"[warn] Darwin fetch failed: {darwin_arrivals}")

    # Sort by expected arrival time
    arrivals.sort(key=lambda a: a["expected"])

    _cache["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _cache["arrivals"] = arrivals


@asynccontextmanager
async def lifespan(application):
    # Fetch immediately on startup
    await fetch_arrivals()

    # Schedule recurring fetch
    scheduler = AsyncIOScheduler()
    scheduler.add_job(fetch_arrivals, "interval", seconds=POLL_INTERVAL)
    scheduler.start()

    yield

    scheduler.shutdown()


app = FastAPI(title="Woolwich Station API", lifespan=lifespan)


@app.get("/arrivals")
async def get_arrivals():
    """Return cached arrivals with interpolated TTL."""
    now = datetime.now(timezone.utc)
    results = []

    for a in _cache["arrivals"]:
        try:
            expected_dt = datetime.fromisoformat(a["expected"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        ttl = int((expected_dt - now).total_seconds())
        if ttl <= 0:
            continue

        entry = {
            "destination": a["destination"],
            "line": a["line"],
            "platform": a["platform"],
            "expected": a["expected"],
            "ttl": ttl,
        }
        if "status" in a:
            entry["status"] = a["status"]

        results.append(entry)

        if len(results) >= MAX_ARRIVALS:
            break

    return {
        "station": "Woolwich",
        "updated": _cache["updated"],
        "arrivals": results,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8177"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)

import os
import httpx
import asyncio
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="FreightPay Investigator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── CONFIG ──────────────────────────────────────────────
OTM_BASE      = os.getenv("OTM_BASE_URL", "").rstrip("/")
OTM_USER      = os.getenv("OTM_USERNAME", "")
OTM_PASS      = os.getenv("OTM_PASSWORD", "")
OTM_DOMAIN    = "KRAFT"
OTM_SUBDOMAIN = "KFNA"

SEARCH_FIELDS = (
    "shipmentXid,shipmentName,transportModeGid,"
    "servprov.servprovXid,"
    "sourceLocation.locationXid,"
    "destLocation.locationXid,"
    "startTime,endTime,"
    "totalWeight,totalVolume,totalActualCost,"
    "attribute10,"
    "statuses"
)

DETAIL_FIELDS = (
    "shipmentXid,shipmentName,transportModeGid,"
    "servprov.servprovXid,"
    "sourceLocation.locationXid,"
    "destLocation.locationXid,"
    "startTime,endTime,"
    "totalWeight,totalVolume,totalActualCost,"
    "shipmentAsWork,"
    "attribute1,attribute2,attribute5,attribute10,"
    "insertDate,updateDate,"
    "statuses"
)

QUERIES = {
    "order": [
        f"{OTM_SUBDOMAIN}.FP_ORD_DIRECT",
        f"{OTM_SUBDOMAIN}.FP_ORD_INDIRECT",
    ],
    "shipment": [
        f"{OTM_SUBDOMAIN}.FP_SHP_NAME_DIRECT",
        f"{OTM_SUBDOMAIN}.FP_SHP_NAME_INDIRECT",
    ],
}

# The 4 FP status types we care about
FP_STATUS_TYPES = {
    "BTF_SHIP_IND",
    "BTF_RATE_IND",
    "SEND_SHIPMENT_USB",
    "SENT_TO_USB",
}

# ── HELPERS ─────────────────────────────────────────────

def build_search_url(query_name: str, param_value: str) -> str:
    return (
        f"{OTM_BASE}/logisticsRestApi/resources-int/v2"
        f"/custom-actions/savedQueries/shipments"
        f"/{OTM_DOMAIN}/{query_name}"
        f"?fields={SEARCH_FIELDS}&expand=statuses&parameterValue={param_value}"
    )

def build_detail_url(shipment_xid: str) -> str:
    return (
        f"{OTM_BASE}/logisticsRestApi/resources-int/v2"
        f"/shipments/{OTM_DOMAIN}/{OTM_SUBDOMAIN}.{shipment_xid}"
        f"?fields={DETAIL_FIELDS}&expand=statuses"
    )

def extract_xid_from_link(links: list, rel: str = "canonical") -> Optional[str]:
    for link in links:
        if link.get("rel") == rel:
            href = link.get("href", "")
            if "/" in href:
                last = href.rsplit("/", 1)[-1]
                if "." in last:
                    return last.split(".", 1)[1]
                return last
    return None

def extract_status_value(status_type_gid: str, status_value_gid: str) -> str:
    val = status_value_gid
    if "." in val:
        val = val.split(".", 1)[1]
    if " - " in val:
        return val.split(" - ", 1)[1].strip()
    type_name = status_type_gid
    if "." in type_name:
        type_name = type_name.split(".", 1)[1]
    if val.upper().startswith(type_name.upper() + "_"):
        return val[len(type_name) + 1:].strip()
    if "_" in val:
        return val.rsplit("_", 1)[-1].strip()
    return val.strip()

def parse_inline_statuses(raw_statuses: dict) -> dict:
    result = {}
    for item in (raw_statuses or {}).get("items", []):
        type_gid  = item.get("statusTypeGid", "")
        value_gid = item.get("statusValueGid", "")
        type_key  = type_gid.split(".", 1)[1] if "." in type_gid else type_gid
        if type_key in FP_STATUS_TYPES:
            update = item.get("updateDate") or item.get("insertDate") or {}
            result[type_key] = {
                "value":      extract_status_value(type_gid, value_gid),
                "updateDate": update.get("value"),
            }
    return result

def parse_shipment(raw: dict) -> dict:
    src_links = raw.get("sourceLocation", {}).get("links", [])
    dst_links = raw.get("destLocation",   {}).get("links", [])
    svc_links = raw.get("servprov",       {}).get("links", [])
    weight = raw.get("totalWeight",     {}) or {}
    volume = raw.get("totalVolume",     {}) or {}
    cost   = raw.get("totalActualCost", {}) or {}
    start  = raw.get("startTime",       {}) or {}
    end    = raw.get("endTime",         {}) or {}
    ins    = raw.get("insertDate",      {}) or {}
    upd    = raw.get("updateDate",      {}) or {}

    statuses = parse_inline_statuses(raw.get("statuses", {}))

    # FP = has SEND_SHIPMENT_USB status or shipmentAsWork flag
    is_fp = raw.get("shipmentAsWork", False) or "SEND_SHIPMENT_USB" in statuses

    return {
        "shipmentXid":     raw.get("shipmentXid"),
        "shipmentName":    raw.get("shipmentName"),
        "transportMode":   raw.get("transportModeGid"),
        "carrier":         extract_xid_from_link(svc_links),
        "sourceLocation":  extract_xid_from_link(src_links),
        "destLocation":    extract_xid_from_link(dst_links),
        "startTime":       start.get("value"),
        "endTime":         end.get("value"),
        "insertDate":      ins.get("value"),
        "updateDate":      upd.get("value"),
        "totalWeight":     weight.get("value"),
        "weightUnit":      weight.get("unit"),
        "totalVolume":     volume.get("value"),
        "volumeUnit":      volume.get("unit"),
        "totalActualCost": cost.get("value"),
        "currency":        cost.get("currency"),
        "shipmentAsWork":  is_fp,
        "perspective":     raw.get("perspective"),
        "attribute10":     raw.get("attribute10"),
        "statuses":        statuses,
    }

async def fetch_query(client: httpx.AsyncClient, url: str, query_name: str) -> dict:
    try:
        resp = await client.get(url, auth=(OTM_USER, OTM_PASS), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items = [parse_shipment(i) for i in data.get("items", [])]
        return {
            "query":   query_name,
            "count":   data.get("count", len(items)),
            "hasMore": data.get("hasMore", False),
            "items":   items,
            "error":   None,
        }
    except httpx.HTTPStatusError as e:
        return {
            "query": query_name, "count": 0, "hasMore": False, "items": [],
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"
        }
    except Exception as e:
        return {
            "query": query_name, "count": 0, "hasMore": False, "items": [],
            "error": str(e)
        }

# ── ROUTES ──────────────────────────────────────────────

@app.get("/api/search")
async def search(
    q:    str = Query(..., description="Search value"),
    type: str = Query("shipment", description="'order' or 'shipment'")
):
    if type not in QUERIES:
        raise HTTPException(400, f"type must be 'order' or 'shipment', got '{type}'")

    query_names = QUERIES[type]
    urls = [build_search_url(qn, q) for qn in query_names]

    async with httpx.AsyncClient(verify=False) as client:
        results = await asyncio.gather(
            *[fetch_query(client, url, qn) for url, qn in zip(urls, query_names)]
        )

    seen = set()
    merged = []
    for result in results:
        for item in result["items"]:
            xid = item["shipmentXid"]
            if xid not in seen:
                seen.add(xid)
                merged.append(item)

    return {
        "searchType":  type,
        "searchValue": q,
        "totalCount":  len(merged),
        "queries":     [{"name": r["query"], "count": r["count"], "error": r["error"]} for r in results],
        "errors":      [r["error"] for r in results if r["error"]],
        "items":       merged,
    }


@app.get("/api/health")
async def health():
    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(
                f"{OTM_BASE}/logisticsRestApi/resources-int/v2/shipments?limit=1",
                auth=(OTM_USER, OTM_PASS),
                timeout=10
            )
            return {"status": "ok", "otm_http": resp.status_code}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ── SERVE FRONTEND ───────────────────────────────────────
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    if os.path.exists("static/index.html"):
        return FileResponse("static/index.html")
    return {"message": "FreightPay Investigator API is running. See /docs"}

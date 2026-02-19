import os
import httpx
import asyncio
from fastapi import FastAPI, Query, HTTPException, Body
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
    "attribute10,attributeNumber1,"
    "statuses,refnums"
)

DETAIL_FIELDS = (
    "shipmentXid,shipmentName,transportModeGid,"
    "servprov.servprovXid,"
    "sourceLocation.locationXid,"
    "destLocation.locationXid,"
    "startTime,endTime,"
    "totalWeight,totalVolume,totalActualCost,"
    "shipmentAsWork,"
    "attribute10,attributeNumber1,"
    "insertDate,updateDate,"
    "statuses,refnums"
)

# All queries run for every search
ALL_QUERIES = [
    f"{OTM_SUBDOMAIN}.FP_SHP_NAME_DIRECT",
    f"{OTM_SUBDOMAIN}.FP_SHP_NAME_INDIRECT",
    f"{OTM_SUBDOMAIN}.FP_ORD_DIRECT",
    f"{OTM_SUBDOMAIN}.FP_ORD_INDIRECT",
    f"{OTM_SUBDOMAIN}.FP_ORD_PL_SHP_DIRECT",
    f"{OTM_SUBDOMAIN}.FP_SHP_NAME_SAP",
]

# The 4 FP status types we care about
FP_STATUS_TYPES = {
    "BTF_SHIP_IND",
    "BTF_RATE_IND",
    "SEND_SHIPMENT_USB",
    "SENT_TO_USB",
}

DATA_SOURCE_QUALIFIER = "DATA_SOURCE"

# OTM WMServlet endpoint
OTM_WMSERVLET = "https://otmgtm-a629995.otmgtm.us-phoenix-1.ocs.oraclecloud.com/GC3/glog.integration.servlet.WMServlet"

# ── HELPERS ─────────────────────────────────────────────

def build_search_url(query_name: str, param_value: str) -> str:
    return (
        f"{OTM_BASE}/logisticsRestApi/resources-int/v2"
        f"/custom-actions/savedQueries/shipments"
        f"/{OTM_DOMAIN}/{query_name}"
        f"?fields={SEARCH_FIELDS}&expand=statuses,refnums&parameterValue={param_value}"
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

def parse_refnums(raw_refnums: dict) -> Optional[str]:
    for item in (raw_refnums or {}).get("items", []):
        qualifier = item.get("shipmentRefnumQualGid", "")
        if "." in qualifier:
            qualifier = qualifier.split(".", 1)[1]
        if qualifier.upper() == DATA_SOURCE_QUALIFIER:
            return item.get("shipmentRefnumValue")
    return None

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

    statuses    = parse_inline_statuses(raw.get("statuses", {}))
    data_source = parse_refnums(raw.get("refnums", {}))
    is_fp       = raw.get("shipmentAsWork", False) or "SEND_SHIPMENT_USB" in statuses

    return {
        "shipmentXid":      raw.get("shipmentXid"),
        "shipmentName":     raw.get("shipmentName"),
        "transportMode":    raw.get("transportModeGid"),
        "carrier":          extract_xid_from_link(svc_links),
        "sourceLocation":   extract_xid_from_link(src_links),
        "destLocation":     extract_xid_from_link(dst_links),
        "startTime":        start.get("value"),
        "endTime":          end.get("value"),
        "insertDate":       ins.get("value"),
        "updateDate":       upd.get("value"),
        "totalWeight":      weight.get("value"),
        "weightUnit":       weight.get("unit"),
        "totalVolume":      volume.get("value"),
        "volumeUnit":       volume.get("unit"),
        "totalActualCost":  cost.get("value"),
        "currency":         cost.get("currency"),
        "shipmentAsWork":   is_fp,
        "perspective":      raw.get("perspective"),
        "attribute10":      raw.get("attribute10"),
        "attributeNumber1": raw.get("attributeNumber1"),
        "dataSource":       data_source,
        "statuses":         statuses,
    }

def build_btf_payload(shipment_xid: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<otm:Transmission xmlns:otm="http://xmlns.oracle.com/apps/otm/transmission/v6.4" xmlns:gtm="http://xmlns.oracle.com/apps/gtm/transmission/v6.4">
    <otm:TransmissionHeader>
        <otm:Refnum>
            <otm:RefnumQualifierGid>
                <otm:Gid>
                    <otm:DomainName>KRAFT/KFNA</otm:DomainName>
                    <otm:Xid>TYPE</otm:Xid>
                </otm:Gid>
            </otm:RefnumQualifierGid>
            <otm:RefnumValue>BTFTOOTM</otm:RefnumValue>
        </otm:Refnum>
        <otm:DataQueueGid>
            <otm:Gid>
                <otm:DomainName>KRAFT</otm:DomainName>
                <otm:Xid>BTFRECON</otm:Xid>
            </otm:Gid>
        </otm:DataQueueGid>
    </otm:TransmissionHeader>
    <otm:TransmissionBody>
        <otm:GLogXMLElement>
            <otm:GenericStatusUpdate>
                <otm:GenericStatusObjectType>SHIPMENT</otm:GenericStatusObjectType>
                <otm:Gid>
                    <otm:DomainName>KRAFT/KFNA</otm:DomainName>
                    <otm:Xid>{shipment_xid}</otm:Xid>
                </otm:Gid>
                <otm:TransactionCode>IU</otm:TransactionCode>
                <otm:Status>
                    <otm:StatusTypeGid>
                        <otm:Gid>
                            <otm:DomainName>KRAFT/KFNA</otm:DomainName>
                            <otm:Xid>BTF_RATE_IND</otm:Xid>
                        </otm:Gid>
                    </otm:StatusTypeGid>
                    <otm:StatusValueGid>
                        <otm:Gid>
                            <otm:DomainName>KRAFT/KFNA</otm:DomainName>
                            <otm:Xid>BTF_RATE - REPROCESS</otm:Xid>
                        </otm:Gid>
                    </otm:StatusValueGid>
                </otm:Status>
            </otm:GenericStatusUpdate>
        </otm:GLogXMLElement>
    </otm:TransmissionBody>
</otm:Transmission>"""

def build_usb_payload(shipment_xid: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<otm:Transmission xmlns:otm="http://xmlns.oracle.com/apps/otm/transmission/v6.4" xmlns:gtm="http://xmlns.oracle.com/apps/gtm/transmission/v6.4">
    <otm:TransmissionHeader/>
    <otm:TransmissionBody>
        <otm:GLogXMLElement>
            <otm:GenericStatusUpdate>
                <otm:GenericStatusObjectType>SHIPMENT</otm:GenericStatusObjectType>
                <otm:Gid>
                    <otm:DomainName>KRAFT/KFNA</otm:DomainName>
                    <otm:Xid>{shipment_xid}</otm:Xid>
                </otm:Gid>
                <otm:TransactionCode>IU</otm:TransactionCode>
                <otm:Status>
                    <otm:StatusTypeGid>
                        <otm:Gid>
                            <otm:DomainName>KRAFT/KFNA</otm:DomainName>
                            <otm:Xid>SEND_SHIPMENT_USB</otm:Xid>
                        </otm:Gid>
                    </otm:StatusTypeGid>
                    <otm:StatusValueGid>
                        <otm:Gid>
                            <otm:DomainName>KRAFT/KFNA</otm:DomainName>
                            <otm:Xid>SEND_SHIPMENT_USB - R</otm:Xid>
                        </otm:Gid>
                    </otm:StatusValueGid>
                </otm:Status>
            </otm:GenericStatusUpdate>
        </otm:GLogXMLElement>
    </otm:TransmissionBody>
</otm:Transmission>"""

def build_send_to_po_payload(shipment_xid: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<otm:Transmission xmlns:otm="http://xmlns.oracle.com/apps/otm/transmission/v6.4" xmlns:gtm="http://xmlns.oracle.com/apps/gtm/transmission/v6.4">
    <otm:TransmissionHeader>
    </otm:TransmissionHeader>
    <otm:TransmissionBody>
        <otm:GLogXMLElement>
            <otm:GenericStatusUpdate>
                <otm:GenericStatusObjectType>SHIPMENT</otm:GenericStatusObjectType>
                <otm:Gid>
                    <otm:DomainName>KRAFT/KFNA</otm:DomainName>
                    <otm:Xid>{shipment_xid}</otm:Xid>
                </otm:Gid>
                <otm:TransactionCode>IU</otm:TransactionCode>
                <otm:Status>
                    <otm:StatusTypeGid>
                        <otm:Gid>
                            <otm:DomainName>KRAFT/KFNA</otm:DomainName>
                            <otm:Xid>SEND_SHIPMENT_PO</otm:Xid>
                        </otm:Gid>
                    </otm:StatusTypeGid>
                    <otm:StatusValueGid>
                        <otm:Gid>
                            <otm:DomainName>KRAFT/KFNA</otm:DomainName>
                            <otm:Xid>SEND_SHIPMENT_PO - R</otm:Xid>
                        </otm:Gid>
                    </otm:StatusValueGid>
                </otm:Status>
            </otm:GenericStatusUpdate>
        </otm:GLogXMLElement>
    </otm:TransmissionBody>
</otm:Transmission>"""

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

async def search_single(client: httpx.AsyncClient, q: str) -> dict:
    """Run all OTM queries for a single search value and return grouped result."""
    q = q.strip()
    urls = [build_search_url(qn, q) for qn in ALL_QUERIES]
    results = await asyncio.gather(
        *[fetch_query(client, url, qn) for url, qn in zip(urls, ALL_QUERIES)]
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
        "searchValue": q,
        "totalCount":  len(merged),
        "queries":     [{"name": r["query"], "count": r["count"], "error": r["error"]} for r in results],
        "errors":      [r["error"] for r in results if r["error"]],
        "items":       merged,
    }


@app.get("/api/search")
async def search(q: str = Query(..., description="Search value")):
    async with httpx.AsyncClient(verify=False) as client:
        return await search_single(client, q)


@app.post("/api/bulk-search")
async def bulk_search(
    values: str = Body(..., media_type="text/plain", description="Comma-separated search values"),
):
    """
    Accept a comma-separated list of search values.
    Runs all OTM queries for each value in parallel.
    Returns results grouped by each input value.
    """
    raw_values = [v.strip() for v in values.split(",") if v.strip()]
    if not raw_values:
        raise HTTPException(status_code=400, detail="No search values provided.")
    if len(raw_values) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 values per bulk request.")

    async with httpx.AsyncClient(verify=False) as client:
        group_results = await asyncio.gather(
            *[search_single(client, q) for q in raw_values]
        )

    total_shipments = sum(r["totalCount"] for r in group_results)
    return {
        "totalValues":    len(raw_values),
        "totalShipments": total_shipments,
        "results":        list(group_results),
    }


@app.post("/api/trigger-btf/{shipment_xid}")
async def trigger_btf(shipment_xid: str):
    """POST XML to OTM WMServlet to trigger BTF pricing reprocess."""
    payload = build_btf_payload(shipment_xid)
    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                OTM_WMSERVLET,
                content=payload.encode("utf-8"),
                headers={"Content-Type": "application/xml"},
                auth=(OTM_USER, OTM_PASS),
                timeout=30,
            )
        return {
            "status":      "ok" if resp.status_code < 400 else "error",
            "httpStatus":  resp.status_code,
            "shipmentXid": shipment_xid,
            "response":    resp.text[:500],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trigger-usb/{shipment_xid}")
async def trigger_usb(shipment_xid: str):
    """POST XML to OTM WMServlet to trigger USB transmission."""
    payload = build_usb_payload(shipment_xid)
    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                OTM_WMSERVLET,
                content=payload.encode("utf-8"),
                headers={"Content-Type": "application/xml"},
                auth=(OTM_USER, OTM_PASS),
                timeout=30,
            )
        return {
            "status":      "ok" if resp.status_code < 400 else "error",
            "httpStatus":  resp.status_code,
            "shipmentXid": shipment_xid,
            "response":    resp.text[:500],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/send-to-po/{shipment_xid}")
async def send_to_po(shipment_xid: str):
    """POST XML to OTM WMServlet to trigger Send to PO workflow for TOC shipment."""
    payload = build_send_to_po_payload(shipment_xid)
    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                OTM_WMSERVLET,
                content=payload.encode("utf-8"),
                headers={"Content-Type": "application/xml"},
                auth=(OTM_USER, OTM_PASS),
                timeout=30,
            )
        return {
            "status":      "ok" if resp.status_code < 400 else "error",
            "httpStatus":  resp.status_code,
            "shipmentXid": shipment_xid,
            "response":    resp.text[:500],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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

# FreightPay Investigator

Search OTM in real time for FP shipments by shipment name or order number.

## Requirements
- Python 3.9+

## Setup & Run (3 steps)

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure credentials
The `.env` file is already pre-filled with your OTM credentials.
Edit if needed:
```
OTM_BASE_URL=https://otmgtm-a629995.otmgtm.us-phoenix-1.ocs.oraclecloud.com
OTM_USERNAME=KRAFT/KFNA.BULK_PLAN
OTM_PASSWORD=Changeme123$
```

### 3. Start the server
```bash
uvicorn main:app --reload --port 8000
```

Then open your browser at:
```
http://localhost:8000
```

---

## How it works

| Search Type | OTM Queries Run |
|---|---|
| Shipment Name / XID | `FP_SHP_NAME_DIRECT` + `FP_SHP_NAME_DIRECT_INDIRECT` |
| Order / Reference # | `FP_ORD_DIRECT` + `FP_ORD_INDIRECT` |

Both queries run **in parallel** (asyncio). Results are merged and deduplicated by `shipmentXid`.

---

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Serves the frontend |
| `GET /api/search?q=VALUE&type=shipment` | Search by shipment name |
| `GET /api/search?q=VALUE&type=order` | Search by order number |
| `GET /api/health` | OTM connectivity check |
| `GET /docs` | Auto-generated API docs (Swagger UI) |

---

## File Structure
```
fp-investigator/
├── main.py              # FastAPI backend
├── requirements.txt     # Python dependencies
├── .env                 # Credentials (do not commit)
├── README.md
└── static/
    └── index.html       # Frontend (served by FastAPI)
```

---

## Troubleshooting

**SSL errors?**
The backend uses `verify=False` for OTM's self-signed cert. No action needed.

**401 Unauthorized?**
Check `OTM_USERNAME` and `OTM_PASSWORD` in `.env`.

**No results returned?**
Try a known shipment XID like `2100416780` with type `shipment`.
Check `/docs` to test the API directly.

**Port already in use?**
```bash
uvicorn main:app --reload --port 8001
```

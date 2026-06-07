#!/usr/bin/env python3
# =============================================================================
# fetch_indicators.py  (v2 - fast & bounded)
# Leading-economic-indicator fetcher for the KONE FP&A dashboard.
#
# Backends (both keyless):
#   1) World Bank  -> ALL countries fetched in ONE call per indicator (fast)
#   2) DBnomics    -> hard-capped: max pages + 90s budget + 1 series/country
#                     so it can never hang the job.
#
# Output (matches your existing Power BI schema):
#   data/leading_annual.csv   /   data/leading_periodic.csv   /   data/fetch_log.txt
#
# To add/remove an indicator: edit the INDICATORS list. One row each.
# =============================================================================

import csv, os, time
from datetime import datetime
from urllib.parse import quote
import requests

OUT_DIR = "data"; os.makedirs(OUT_DIR, exist_ok=True)
LOG = []
def log(m): print(m); LOG.append(m)

COUNTRIES_ISO3 = [
    "DEU","CYP","BGR","ROU","SRB","MKD","MNE","HUN","ISR","CZE","POL","LVA",
    "LTU","EST","FIN","SWE","NOR","DNK","ISL","BEL","FRA","GBR","IRL","NLD",
    "ITA","USA","CAN","MEX","AUS","MYS","SGP","THA","VNM","IDN","PHL","IND",
    "TUR","ARE","SAU","OMN","QAT","KWT","BHR","KAZ","KEN","UGA","MAR","EGY",
    "ZAF","TUN","CHN","HKG","TWN","MAC","RUS","NZL",
]
WB_REGIONS = ["WLD","CEB","EMU","EAS","ECA","SSF","MNA","LCN","SAS"]

ISO3_TO_ISO2 = {
    "DEU":"DE","CYP":"CY","BGR":"BG","ROU":"RO","SRB":"RS","MKD":"MK","MNE":"ME",
    "HUN":"HU","ISR":"IL","CZE":"CZ","POL":"PL","LVA":"LV","LTU":"LT","EST":"EE",
    "FIN":"FI","SWE":"SE","NOR":"NO","DNK":"DK","ISL":"IS","BEL":"BE","FRA":"FR",
    "GBR":"UK","IRL":"IE","NLD":"NL","ITA":"IT","USA":"US","CAN":"CA","MEX":"MX",
    "AUS":"AU","MYS":"MY","SGP":"SG","THA":"TH","VNM":"VN","IDN":"ID","PHL":"PH",
    "IND":"IN","TUR":"TR","ARE":"AE","SAU":"SA","OMN":"OM","QAT":"QA","KWT":"KW",
    "BHR":"BH","KAZ":"KZ","KEN":"KE","UGA":"UG","MAR":"MA","EGY":"EG","ZAF":"ZA",
    "TUN":"TN","CHN":"CN","HKG":"HK","TWN":"TW","MAC":"MO","RUS":"RU","NZL":"NZ",
}
TARGET_CODES = set(COUNTRIES_ISO3) | set(ISO3_TO_ISO2.values())

# ============================ INDICATOR CATALOG ==============================
INDICATORS = [
    # --- World Bank (annual, keyless, rock solid) ---
    {"backend":"worldbank","freq":"annual","name":"GDP growth (annual %)","wb_code":"NY.GDP.MKTP.KD.ZG"},
    {"backend":"worldbank","freq":"annual","name":"Inflation, consumer prices (annual %)","wb_code":"FP.CPI.TOTL.ZG"},
    {"backend":"worldbank","freq":"annual","name":"Real interest rate (%)","wb_code":"FR.INR.RINR"},
    {"backend":"worldbank","freq":"annual","name":"Lending interest rate (%)","wb_code":"FR.INR.LEND"},
    {"backend":"worldbank","freq":"annual","name":"Inflation, GDP deflator (annual %)","wb_code":"NY.GDP.DEFL.KD.ZG"},
    {"backend":"worldbank","freq":"annual","name":"Exports of goods and services (% of GDP)","wb_code":"NE.EXP.GNFS.ZS"},
    {"backend":"worldbank","freq":"annual","name":"Urban population (% of total)","wb_code":"SP.URB.TOTL.IN.ZS"},
    {"backend":"worldbank","freq":"annual","name":"Manufacturing, value added (annual % growth)","wb_code":"NV.IND.MANF.KD.ZG"},
    {"backend":"worldbank","freq":"annual","name":"Manufacturing, value added (% of GDP)","wb_code":"NV.IND.MANF.ZS"},
    {"backend":"worldbank","freq":"annual","name":"Gross capital formation (% of GDP)","wb_code":"NE.GDI.TOTL.ZS"},
    {"backend":"worldbank","freq":"annual","name":"Domestic credit to private sector (% of GDP)","wb_code":"FS.AST.PRVT.GD.ZS"},
    {"backend":"worldbank","freq":"annual","name":"Logistics Performance Index, overall","wb_code":"LP.LPI.OVRL.XQ"},

    # --- DBnomics (the leading indicators not in World Bank) ---
    {"backend":"dbnomics","freq":"monthly","name":"Building permits (m2 floor area, SA)",
     "provider":"Eurostat","dataset":"sts_cobp_m","mask":"","explorer":"https://db.nomics.world/Eurostat/sts_cobp_m"},
    {"backend":"dbnomics","freq":"monthly","name":"Construction production index",
     "provider":"Eurostat","dataset":"sts_copr_m","mask":"","explorer":"https://db.nomics.world/Eurostat/sts_copr_m"},
    {"backend":"dbnomics","freq":"monthly","name":"Construction confidence indicator",
     "provider":"Eurostat","dataset":"ei_bsco_m","mask":"","explorer":"https://db.nomics.world/Eurostat/ei_bsco_m"},
    {"backend":"dbnomics","freq":"quarterly","name":"Residential property prices",
     "provider":"BIS","dataset":"PP","mask":"","explorer":"https://db.nomics.world/BIS/PP"},
    {"backend":"dbnomics","freq":"monthly","name":"OECD Composite Leading Indicator (CLI)",
     "provider":"OECD","dataset":"DSD_STES@DF_CLI","mask":"","explorer":"https://db.nomics.world/OECD"},
    {"backend":"dbnomics","freq":"monthly","name":"Economic Sentiment Indicator (ESI)",
     "provider":"Eurostat","dataset":"ei_bssi_m_r2","mask":"","explorer":"https://db.nomics.world/Eurostat/ei_bssi_m_r2"},
    {"backend":"dbnomics","freq":"monthly","name":"ISM Manufacturing PMI (US)",
     "provider":"ISM","dataset":"pmi","mask":"","explorer":"https://db.nomics.world/ISM/pmi"},
    {"backend":"dbnomics","freq":"monthly","name":"ECB main refinancing rate",
     "provider":"ECB","dataset":"FM","mask":"","explorer":"https://db.nomics.world/ECB/FM"},
]
# =============================================================================

ANNUAL_FIELDS   = ["Country Code","Country Name","Indicator Code","Indicator Name","Year","Value"]
PERIODIC_FIELDS = ["Country","Country Name","Indicator Code","Indicator Name",
                   "Date","Year","Month","Calendar Date","Month-Year","Value"]
annual_rows, periodic_rows = [], []
MONTH_ABBR = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
START_YEAR = 2015

def add_periodic(cc, cn, icode, iname, y, m, v):
    periodic_rows.append({"Country":cc,"Country Name":cn,"Indicator Code":icode,"Indicator Name":iname,
        "Date":f"{y}M{m:02d}","Year":str(y),"Month":MONTH_ABBR[m],
        "Calendar Date":f"01.{m:02d}.{y}","Month-Year":f"{MONTH_ABBR[m]}'{str(y)[-2:]}","Value":v})

session = requests.Session()
session.headers.update({"User-Agent":"KONE-FPA-indicator-fetch/2.0"})

# ---------------------------- World Bank (batched) ---------------------------
def fetch_worldbank(ind):
    code, name = ind["wb_code"], ind["name"]
    codes = ";".join(COUNTRIES_ISO3 + WB_REGIONS)   # all in ONE request
    got, page, pages = 0, 1, 1
    while page <= pages:
        url = (f"https://api.worldbank.org/v2/country/{codes}/indicator/{code}"
               f"?date={START_YEAR}:2026&format=json&per_page=20000&page={page}")
        try:
            r = session.get(url, timeout=30)
            if r.status_code != 200: break
            j = r.json()
            if not (len(j) > 1 and isinstance(j[1], list)): break
            pages = j[0].get("pages", 1)
            for e in j[1]:
                if e.get("value") is not None:
                    annual_rows.append({"Country Code":e["country"]["id"],"Country Name":e["country"]["value"],
                        "Indicator Code":code,"Indicator Name":name,"Year":e["date"],"Value":e["value"]})
                    got += 1
        except requests.exceptions.RequestException as ex:
            log(f"  WB error {code}: {ex}"); break
        page += 1
    log(f"[WorldBank] {name}: {got} rows")

# ---------------------------- DBnomics (capped) ------------------------------
def _country_from_doc(doc):
    dims = doc.get("dimensions", {}) or {}
    dvl  = doc.get("dimensions_values_labels", {}) or {}
    for k in ("geo","REF_AREA","LOCATION","Country","COUNTRY","country","area","AREA"):
        if k in dims:
            v = dims[k]
            lab = dvl.get(k, {}).get(v) if isinstance(dvl.get(k), dict) else None
            return v, (lab or v)
    return None, None

def _parse_period(per, freq):
    try:
        per = str(per)
        if "Q" in per:  return int(per[:4]), {1:3,2:6,3:9,4:12}.get(int(per.split("Q")[-1]),12)
        if "M" in per:  return int(per[:4]), int(per.split("M")[-1])
        if "-" in per:
            p = per.split("-"); return int(p[0]), (int(p[1]) if len(p)>1 else 12)
        return int(per[:4]), 12
    except Exception:
        return None, None

def fetch_dbnomics(ind):
    prov, ds, mask = ind["provider"], ind["dataset"], ind.get("mask","")
    name, freq = ind["name"], ind["freq"]
    base = f"https://api.db.nomics.world/v22/series/{prov}/{ds}"
    if mask: base += "/" + quote(mask, safe="+.")
    captured, got = set(), 0
    MAX_PAGES, limit = 12, 100
    deadline = time.time() + 90      # hard 90s budget per indicator
    page, offset, total = 0, 0, None
    while page < MAX_PAGES and time.time() < deadline:
        try:
            r = session.get(f"{base}?observations=1&limit={limit}&offset={offset}", timeout=25)
        except requests.exceptions.RequestException as ex:
            log(f"[DBnomics] {name} ({prov}/{ds}) ERROR: {ex} -> {ind.get('explorer','')}"); return
        if r.status_code != 200:
            log(f"[DBnomics] {name} ({prov}/{ds}) HTTP {r.status_code} -> {ind.get('explorer','')}"); return
        payload = r.json(); docs = payload.get("series", {}).get("docs", [])
        if not docs: break
        if total is None: total = payload.get("series", {}).get("num_found", 0)
        for doc in docs:
            cc, cn = _country_from_doc(doc)
            if not cc or cc.upper() not in TARGET_CODES: continue
            key = cc.upper()
            if key in captured: continue        # keep ONE series per country
            captured.add(key)
            icode = doc.get("series_code", f"{prov}.{ds}")
            for per, val in zip(doc.get("period", []), doc.get("value", [])):
                if val is None or per is None: continue
                y, m = _parse_period(per, freq)
                if y is None or y < START_YEAR: continue
                got += 1
                if freq == "annual":
                    annual_rows.append({"Country Code":cc,"Country Name":cn,"Indicator Code":icode,
                        "Indicator Name":name,"Year":str(y),"Value":val})
                else:
                    add_periodic(cc, cn, icode, name, y, m, val)
        offset += limit; page += 1
        if total is not None and offset >= total: break
    flag = "" if got else f"  <-- 0 rows, CHECK CODES: {ind.get('explorer','')}"
    log(f"[DBnomics] {name} ({prov}/{ds}): {got} rows, {len(captured)} countries{flag}")

# --------------------------------- run ---------------------------------------
log(f"Run started {datetime.utcnow().isoformat()}Z")
for ind in INDICATORS:
    (fetch_worldbank if ind["backend"]=="worldbank" else fetch_dbnomics)(ind)

with open(os.path.join(OUT_DIR,"leading_annual.csv"),"w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=ANNUAL_FIELDS); w.writeheader(); w.writerows(annual_rows)
with open(os.path.join(OUT_DIR,"leading_periodic.csv"),"w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=PERIODIC_FIELDS); w.writeheader(); w.writerows(periodic_rows)
log(f"DONE  annual={len(annual_rows)} rows  periodic={len(periodic_rows)} rows")
with open(os.path.join(OUT_DIR,"fetch_log.txt"),"w",encoding="utf-8") as f:
    f.write("\n".join(LOG))

#!/usr/bin/env python3
# =============================================================================
# fetch_indicators.py
# Unified leading-economic-indicator fetcher for the KONE FP&A dashboard.
#
# TWO keyless backends:
#   1) World Bank   (api.worldbank.org)   -> rock solid, your existing pattern
#   2) DBnomics     (api.db.nomics.world) -> one hub over OECD, Eurostat, ISM,
#                                            IMF, ECB, BIS, national agencies
#
# OUTPUT (two files, matching your existing Power BI schema exactly):
#   data/leading_annual.csv     <- annual series
#   data/leading_periodic.csv   <- monthly + quarterly series
#                                  (quarterly mapped to quarter-end month)
#
# TO ADD/REMOVE AN INDICATOR: edit the INDICATORS list below. One row each.
# The first GitHub Actions run is the VALIDATION run: anything that fails is
# written to data/fetch_log.txt with the exact reason, so codes are easy to fix.
# =============================================================================

import csv
import json
import os
import time
from datetime import datetime
from urllib.parse import quote

import requests

OUT_DIR = "data"
os.makedirs(OUT_DIR, exist_ok=True)
LOG = []

def log(msg):
    print(msg)
    LOG.append(msg)

# -----------------------------------------------------------------------------
# Countries (ISO3 used by World Bank; DBnomics docs are filtered client-side by
# matching ISO3 / ISO2 / name). Regions kept for World Bank only.
# -----------------------------------------------------------------------------
COUNTRIES_ISO3 = [
    "DEU","CYP","BGR","ROU","SRB","MKD","MNE","HUN","ISR","CZE","POL","LVA",
    "LTU","EST","FIN","SWE","NOR","DNK","ISL","BEL","FRA","GBR","IRL","NLD",
    "ITA","USA","CAN","MEX","AUS","MYS","SGP","THA","VNM","IDN","PHL","IND",
    "TUR","ARE","SAU","OMN","QAT","KWT","BHR","KAZ","KEN","UGA","MAR","EGY",
    "ZAF","TUN","CHN","HKG","TWN","MAC","RUS","NZL",
]
WB_REGIONS = ["WLD","CEB","EMU","EAS","ECA","SSF","MNA","LCN","SAS"]

# ISO3 -> ISO2 (for matching DBnomics providers that key on ISO2, e.g. Eurostat)
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
ISO2_SET = set(ISO3_TO_ISO2.values())
ISO3_SET = set(COUNTRIES_ISO3)

# =============================================================================
# INDICATOR CATALOG  --  edit here to add/remove indicators
# -----------------------------------------------------------------------------
# backend = "worldbank":
#     wb_code, freq ("annual"), name
# backend = "dbnomics":
#     provider, dataset, mask (SDMX-style; "" = whole dataset, "+" = OR),
#     freq ("monthly"|"quarterly"|"annual"), name
#     confidence: "verified" or "check" (check = confirm code on first run)
#     explorer:   link to confirm the dataset/series codes
# =============================================================================
INDICATORS = [
    # ---- World Bank (annual, keyless, confirmed pattern) --------------------
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
    # NEW World Bank addition (Theme D - logistics, structural benchmark)
    {"backend":"worldbank","freq":"annual","name":"Logistics Performance Index, overall","wb_code":"LP.LPI.OVRL.XQ"},

    # ---- DBnomics (the leading indicators that aren't in World Bank) --------
    # NOTE: these provider/dataset codes are my best mapping; the first run
    # validates them. Confirm/adjust any flagged "check" using the explorer link.

    # Theme A - construction & building cycle
    {"backend":"dbnomics","freq":"monthly","name":"Building permits (m2 floor area, SA)",
     "provider":"Eurostat","dataset":"sts_cobp_m","mask":"",
     "confidence":"check","explorer":"https://db.nomics.world/Eurostat/sts_cobp_m"},
    {"backend":"dbnomics","freq":"monthly","name":"Construction production index",
     "provider":"Eurostat","dataset":"sts_copr_m","mask":"",
     "confidence":"check","explorer":"https://db.nomics.world/Eurostat/sts_copr_m"},
    {"backend":"dbnomics","freq":"monthly","name":"Construction confidence indicator",
     "provider":"Eurostat","dataset":"ei_bsco_m","mask":"",
     "confidence":"check","explorer":"https://db.nomics.world/Eurostat/ei_bsco_m"},
    {"backend":"dbnomics","freq":"quarterly","name":"Residential property prices",
     "provider":"BIS","dataset":"PP","mask":"",
     "confidence":"check","explorer":"https://db.nomics.world/BIS/PP"},

    # Theme B - composite leading / sentiment
    {"backend":"dbnomics","freq":"monthly","name":"OECD Composite Leading Indicator (CLI)",
     "provider":"OECD","dataset":"DSD_STES@DF_CLI","mask":"",
     "confidence":"check","explorer":"https://db.nomics.world/OECD?q=composite+leading+indicator"},
    {"backend":"dbnomics","freq":"monthly","name":"Economic Sentiment Indicator (ESI)",
     "provider":"Eurostat","dataset":"ei_bssi_m_r2","mask":"",
     "confidence":"check","explorer":"https://db.nomics.world/Eurostat/ei_bssi_m_r2"},

    # Theme C - PMI (free substitutes)
    {"backend":"dbnomics","freq":"monthly","name":"ISM Manufacturing PMI (US)",
     "provider":"ISM","dataset":"pmi","mask":"",
     "confidence":"check","explorer":"https://db.nomics.world/ISM/pmi"},

    # Theme E - monetary / cost of capital
    {"backend":"dbnomics","freq":"monthly","name":"ECB main refinancing rate",
     "provider":"ECB","dataset":"FM","mask":"",
     "confidence":"check","explorer":"https://db.nomics.world/ECB/FM"},
]

# =============================================================================
# OUTPUT SCHEMAS  (identical to your existing files so Power BI append works)
# =============================================================================
ANNUAL_FIELDS   = ["Country Code","Country Name","Indicator Code","Indicator Name","Year","Value"]
PERIODIC_FIELDS = ["Country","Country Name","Indicator Code","Indicator Name",
                   "Date","Year","Month","Calendar Date","Month-Year","Value"]

annual_rows   = []
periodic_rows = []

MONTH_ABBR = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
              7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}

def add_periodic(country_code, country_name, ind_code, ind_name, year, month, value):
    cal = f"01.{month:02d}.{year}"
    my  = f"{MONTH_ABBR[month]}'{str(year)[-2:]}"
    periodic_rows.append({
        "Country": country_code, "Country Name": country_name,
        "Indicator Code": ind_code, "Indicator Name": ind_name,
        "Date": f"{year}M{month:02d}", "Year": str(year),
        "Month": MONTH_ABBR[month], "Calendar Date": cal,
        "Month-Year": my, "Value": value,
    })

session = requests.Session()
session.headers.update({"User-Agent": "KONE-FPA-indicator-fetch/1.0"})
START_YEAR = 2015

# -----------------------------------------------------------------------------
# WORLD BANK backend
# -----------------------------------------------------------------------------
def fetch_worldbank(ind):
    code = ind["wb_code"]
    name = ind["name"]
    targets = COUNTRIES_ISO3 + WB_REGIONS
    got = 0
    for cc in targets:
        url = (f"https://api.worldbank.org/v2/country/{cc}/indicator/{code}"
               f"?date={START_YEAR}:2026&format=json&per_page=1000")
        try:
            r = session.get(url, timeout=15)
            if r.status_code != 200:
                continue
            j = r.json()
            if len(j) > 1 and isinstance(j[1], list):
                for e in j[1]:
                    if e.get("value") is not None:
                        annual_rows.append({
                            "Country Code": e["country"]["id"],
                            "Country Name": e["country"]["value"],
                            "Indicator Code": code,
                            "Indicator Name": name,
                            "Year": e["date"],
                            "Value": e["value"],
                        })
                        got += 1
        except requests.exceptions.RequestException as ex:
            log(f"  WB error {cc} {code}: {ex}")
        time.sleep(0.1)
    log(f"[WorldBank] {name}: {got} rows")

# -----------------------------------------------------------------------------
# DBNOMICS backend
# -----------------------------------------------------------------------------
def _country_from_doc(doc):
    """Best-effort: pull a country code + name from a DBnomics series doc."""
    dims = doc.get("dimensions", {}) or {}
    # candidate dimension keys that usually carry geography
    for key in ("geo","REF_AREA","LOCATION","Country","COUNTRY","country","area","AREA"):
        if key in dims:
            val = dims[key]
            # try to find a human label for it
            label = None
            dvl = doc.get("dimensions_values_labels", {}) or {}
            if key in dvl and isinstance(dvl[key], dict):
                label = dvl[key].get(val)
            return val, (label or val)
    return None, None

def _match_country(code):
    if code is None:
        return False
    c = code.upper()
    return c in ISO3_SET or c in ISO2_SET

def fetch_dbnomics(ind):
    provider = ind["provider"]; dataset = ind["dataset"]
    mask = ind.get("mask",""); name = ind["name"]; freq = ind["freq"]
    base = f"https://api.db.nomics.world/v22/series/{provider}/{dataset}"
    if mask:
        base += "/" + quote(mask, safe="+.")
    got = 0; matched = 0; offset = 0; limit = 100
    while True:
        url = f"{base}?observations=1&limit={limit}&offset={offset}"
        try:
            r = session.get(url, timeout=30)
        except requests.exceptions.RequestException as ex:
            log(f"[DBnomics] {provider}/{dataset} ERROR: {ex}  -> verify: {ind.get('explorer','')}")
            return
        if r.status_code != 200:
            log(f"[DBnomics] {provider}/{dataset} HTTP {r.status_code}  -> verify: {ind.get('explorer','')}")
            return
        payload = r.json()
        docs = payload.get("series", {}).get("docs", [])
        if not docs:
            break
        for doc in docs:
            ccode, cname = _country_from_doc(doc)
            if not _match_country(ccode):
                continue
            periods = doc.get("period", [])
            values  = doc.get("value", [])
            ind_code = doc.get("series_code", f"{provider}.{dataset}")
            for per, val in zip(periods, values):
                if val is None or per is None:
                    continue
                y, m = _parse_period(per, freq)
                if y is None or y < START_YEAR:
                    continue
                got += 1
                if freq == "annual":
                    annual_rows.append({
                        "Country Code": ccode, "Country Name": cname,
                        "Indicator Code": ind_code, "Indicator Name": name,
                        "Year": str(y), "Value": val})
                else:
                    add_periodic(ccode, cname, ind_code, name, y, m, val)
            matched += 1
        total = payload.get("series", {}).get("num_found", 0)
        offset += limit
        if offset >= total:
            break
        time.sleep(0.2)
    flag = "" if got else "  <-- 0 rows, CHECK CODES: " + ind.get("explorer","")
    log(f"[DBnomics] {name} ({provider}/{dataset}): {got} rows from {matched} series{flag}")

def _parse_period(per, freq):
    """Return (year, month). Quarterly -> quarter-end month. Annual -> (y, 12)."""
    try:
        per = str(per)
        if "Q" in per:                      # 2025-Q1 / 2025Q1
            year = int(per[:4]); q = int(per.split("Q")[-1])
            return year, {1:3,2:6,3:9,4:12}.get(q,12)
        if "M" in per:                       # 2025M03
            year = int(per[:4]); month = int(per.split("M")[-1])
            return year, month
        if "-" in per:                       # 2025-03 / 2025-03-01
            parts = per.split("-")
            year = int(parts[0]); month = int(parts[1]) if len(parts) > 1 else 12
            return year, month
        return int(per[:4]), 12              # bare year
    except Exception:
        return None, None

# =============================================================================
# RUN
# =============================================================================
log(f"Run started {datetime.utcnow().isoformat()}Z")
for ind in INDICATORS:
    if ind["backend"] == "worldbank":
        fetch_worldbank(ind)
    elif ind["backend"] == "dbnomics":
        fetch_dbnomics(ind)

with open(os.path.join(OUT_DIR,"leading_annual.csv"),"w",newline="",encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=ANNUAL_FIELDS); w.writeheader(); w.writerows(annual_rows)
with open(os.path.join(OUT_DIR,"leading_periodic.csv"),"w",newline="",encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=PERIODIC_FIELDS); w.writeheader(); w.writerows(periodic_rows)

log(f"DONE  annual={len(annual_rows)} rows  periodic={len(periodic_rows)} rows")
with open(os.path.join(OUT_DIR,"fetch_log.txt"),"w",encoding="utf-8") as f:
    f.write("\n".join(LOG))

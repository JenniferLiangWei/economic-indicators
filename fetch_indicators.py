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
WB_GEM_REGIONS = ["WLD","EMU","EAP","ECA","LAC","MNA","SSA"]

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

# Canonical full name for every ISO3 (the single name used across ALL sources)
CANON_NAME = {
    "DEU":"Germany","CYP":"Cyprus","BGR":"Bulgaria","ROU":"Romania","SRB":"Serbia",
    "MKD":"North Macedonia","MNE":"Montenegro","HUN":"Hungary","ISR":"Israel",
    "CZE":"Czechia","POL":"Poland","LVA":"Latvia","LTU":"Lithuania","EST":"Estonia",
    "FIN":"Finland","SWE":"Sweden","NOR":"Norway","DNK":"Denmark","ISL":"Iceland",
    "BEL":"Belgium","FRA":"France","GBR":"United Kingdom","IRL":"Ireland",
    "NLD":"Netherlands","ITA":"Italy","USA":"United States","CAN":"Canada",
    "MEX":"Mexico","AUS":"Australia","MYS":"Malaysia","SGP":"Singapore",
    "THA":"Thailand","VNM":"Vietnam","IDN":"Indonesia","PHL":"Philippines",
    "IND":"India","TUR":"Türkiye","ARE":"United Arab Emirates","SAU":"Saudi Arabia",
    "OMN":"Oman","QAT":"Qatar","KWT":"Kuwait","BHR":"Bahrain","KAZ":"Kazakhstan",
    "KEN":"Kenya","UGA":"Uganda","MAR":"Morocco","EGY":"Egypt","ZAF":"South Africa",
    "TUN":"Tunisia","CHN":"China","HKG":"Hong Kong","TWN":"Taiwan","MAC":"Macao",
    "RUS":"Russia","NZL":"New Zealand",
}
ISO2_TO_ISO3 = {v: k for k, v in ISO3_TO_ISO2.items()}

def canon_country(code):
    """Map any ISO2 or ISO3 code to (canonical ISO3, canonical name).
    Returns None for non-target codes (e.g. regions like WLD, EMU, U2)."""
    if not code:
        return None
    c = str(code).upper()
    iso3 = c if c in CANON_NAME else ISO2_TO_ISO3.get(c)
    if iso3 and iso3 in CANON_NAME:
        return iso3, CANON_NAME[iso3]
    return None


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
    {"backend":"dbnomics","freq":"quarterly","name":"House price index",
     "provider":"Eurostat","dataset":"prc_hpi_q","mask":"","explorer":"https://db.nomics.world/Eurostat/prc_hpi_q"},
    {"backend":"dbnomics","freq":"monthly","name":"OECD Composite Leading Indicator (CLI)",
     "provider":"OECD","dataset":"DSD_STES@DF_CLI","mask":"","explorer":"https://db.nomics.world/OECD"},
    {"backend":"dbnomics","freq":"monthly","name":"Economic Sentiment Indicator (ESI)",
     "provider":"Eurostat","dataset":"ei_bssi_m_r2","mask":"","explorer":"https://db.nomics.world/Eurostat/ei_bssi_m_r2"},
    {"backend":"dbnomics","freq":"monthly","name":"ISM Manufacturing PMI (US)",
     "provider":"ISM","dataset":"pmi","mask":"pm","force_country":("USA","United States"),
     "explorer":"https://db.nomics.world/ISM/pmi"},
    {"backend":"dbnomics","freq":"monthly","name":"ECB main refinancing rate",
     "provider":"ECB","dataset":"FM","mask":"B.U2.EUR.4F.KR.MRR_FR.LEV","force_country":("U2","Euro area"),
     "forward_fill":True,"explorer":"https://db.nomics.world/ECB/FM"},

    # --- IMF World Economic Outlook (ANNUAL, includes 2025 & 2026 forecasts) ---
    {"backend":"dbnomics","freq":"annual","name":"Real GDP growth (IMF WEO)",
     "provider":"IMF","dataset":"WEO:latest","mask":".NGDP_RPCH","explorer":"https://db.nomics.world/IMF/WEO:latest"},
    {"backend":"dbnomics","freq":"annual","name":"Inflation, avg consumer prices (IMF WEO)",
     "provider":"IMF","dataset":"WEO:latest","mask":".PCPIPCH","explorer":"https://db.nomics.world/IMF/WEO:latest"},
    {"backend":"dbnomics","freq":"annual","name":"Government gross debt (% of GDP, IMF WEO)",
     "provider":"IMF","dataset":"WEO:latest","mask":".GGXWDG_NGDP","explorer":"https://db.nomics.world/IMF/WEO:latest"},
    {"backend":"dbnomics","freq":"annual","name":"Current account balance (% of GDP, IMF WEO)",
     "provider":"IMF","dataset":"WEO:latest","mask":".BCA_NGDPD","explorer":"https://db.nomics.world/IMF/WEO:latest"},
    {"backend":"dbnomics","freq":"annual","name":"Unemployment rate (IMF WEO)",
     "provider":"IMF","dataset":"WEO:latest","mask":".LUR","explorer":"https://db.nomics.world/IMF/WEO:latest"},

    # --- World Bank GEM (MONTHLY, your original set, source=15) ---
    {"backend":"worldbank_gem","freq":"monthly","name":"CPI inflation, % YoY","wb_code":"CPTOTSAXNZGY"},
    {"backend":"worldbank_gem","freq":"monthly","name":"Industrial Production Index","wb_code":"IPTOTNSKD"},
    {"backend":"worldbank_gem","freq":"monthly","name":"Merchandise Exports (USD, NSA)","wb_code":"DXGSRMRCHNSCD"},
    {"backend":"worldbank_gem","freq":"monthly","name":"Merchandise Imports (USD, NSA)","wb_code":"DMGSRMRCHNSCD"},
    {"backend":"worldbank_gem","freq":"monthly","name":"Retail Sales Volume Index (SA)","wb_code":"RETSALESSA"},
    {"backend":"worldbank_gem","freq":"monthly","name":"Real Effective Exchange Rate Index","wb_code":"REER"},
    {"backend":"worldbank_gem","freq":"monthly","name":"Terms of Trade Index","wb_code":"TOT"},
    {"backend":"worldbank_gem","freq":"monthly","name":"Total International Reserves (USD millions)","wb_code":"TOTRESV"},
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
        r = None
        for attempt in range(3):                 # retry transient timeouts
            try:
                r = session.get(url, timeout=30); break
            except requests.exceptions.RequestException as ex:
                log(f"  WB retry {attempt+1} {code}: {ex}"); time.sleep(3)
        if r is None or r.status_code != 200: break
        j = r.json()
        if not (len(j) > 1 and isinstance(j[1], list)): break
        pages = j[0].get("pages", 1)
        for e in j[1]:
            if e.get("value") is not None:
                canon = canon_country(e["country"]["id"])
                if canon:
                    cc, cn = canon                       # normalized country
                else:
                    cc, cn = e["country"]["id"], e["country"]["value"]   # region (World, Euro area...)
                annual_rows.append({"Country Code":cc,"Country Name":cn,
                    "Indicator Code":code,"Indicator Name":name,"Year":e["date"],"Value":e["value"]})
                got += 1
        page += 1
    log(f"[WorldBank] {name}: {got} rows")

# ------------------------- World Bank GEM (monthly) --------------------------
def fetch_worldbank_gem(ind):
    code, name = ind["wb_code"], ind["name"]
    codes = ";".join(COUNTRIES_ISO3 + WB_GEM_REGIONS)
    got, page, pages = 0, 1, 1
    while page <= pages:
        url = (f"https://api.worldbank.org/v2/country/{codes}/indicator/{code}"
               f"?date={START_YEAR}M01:2026M12&source=15&format=json&per_page=20000&page={page}")
        r = None
        for attempt in range(3):
            try:
                r = session.get(url, timeout=30); break
            except requests.exceptions.RequestException as ex:
                log(f"  GEM retry {attempt+1} {code}: {ex}"); time.sleep(3)
        if r is None or r.status_code != 200: break
        j = r.json()
        if not (len(j) > 1 and isinstance(j[1], list)): break
        pages = j[0].get("pages", 1)
        for e in j[1]:
            if e.get("value") is None: continue
            raw = e["date"]
            if "M" not in raw: continue
            y = int(raw[:4]); m = int(raw.split("M")[1])
            if y < START_YEAR: continue
            canon = canon_country(e["country"]["id"])
            if canon:
                cc, cn = canon
            else:
                cc, cn = e["country"]["id"], e["country"]["value"]   # region passthrough
            add_periodic(cc, cn, code, name, y, m, e["value"])
            got += 1
        page += 1
    log(f"[WorldBank GEM] {name}: {got} rows")

# ---------------------------- DBnomics (capped) ------------------------------
def _country_from_doc(doc):
    dims = doc.get("dimensions", {}) or {}
    dvl  = doc.get("dimensions_values_labels", {}) or {}
    # 1) known geography dimension keys (existing behaviour)
    for k in ("geo","REF_AREA","LOCATION","Country","COUNTRY","country","area","AREA"):
        if k in dims and dims[k]:
            v = dims[k]
            lab = dvl.get(k, {}).get(v) if isinstance(dvl.get(k), dict) else None
            return v, (lab or v)
    # 2) any dimension whose value is a known country code (covers IMF WEO etc.)
    for k, v in dims.items():
        if isinstance(v, str) and v.upper() in TARGET_CODES:
            lab = dvl.get(k, {}).get(v) if isinstance(dvl.get(k), dict) else None
            return v, (lab or v)
    # 3) fallback: country code as the prefix of the series code (WEO: "DEU.NGDP_RPCH")
    sc = doc.get("series_code", "")
    if sc:
        p = sc.split(".")[0]
        if p.upper() in TARGET_CODES:
            return p, p
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

def _pairs_from_observations(raw):
    pairs = []
    if isinstance(raw, dict):
        for per, val in raw.items():
            pairs.append((per, val))
        return pairs
    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, (list, tuple)) and len(it) >= 2:
                pairs.append((it[0], it[1]))
                continue
            if isinstance(it, dict):
                per = (it.get("period") or it.get("date") or it.get("time_period")
                       or it.get("time") or it.get("period_code"))
                val = it.get("value")
                if val is None:
                    for vk in ("obs_value", "obs", "v"):
                        if vk in it and it.get(vk) is not None:
                            val = it.get(vk); break
                if per is not None:
                    pairs.append((per, val))
    return pairs

def _doc_observations(doc, series_payload):
    # Old schema: parallel arrays on each doc
    period = doc.get("period", [])
    value = doc.get("value", [])
    if isinstance(period, list) and isinstance(value, list) and period and value:
        return list(zip(period, value))
    # New schema (doc-level): observations can be dict/list of points
    pairs = _pairs_from_observations(doc.get("observations"))
    if pairs:
        return pairs
    # Fallback schema (series-level): observations may be keyed by series_code
    obs_map = series_payload.get("observations") if isinstance(series_payload, dict) else None
    if isinstance(obs_map, dict):
        sc = doc.get("series_code")
        if sc in obs_map:
            pairs = _pairs_from_observations(obs_map.get(sc))
            if pairs:
                return pairs
    return []

def fetch_dbnomics(ind):
    prov, ds, mask = ind["provider"], ind["dataset"], ind.get("mask","")
    name, freq = ind["name"], ind["freq"]
    base = f"https://api.db.nomics.world/v22/series/{prov}/{ds}"
    if mask: base += "/" + quote(mask, safe="+.")
    fc = ind.get("force_country")
    best = {}          # country -> {"last":"YYYYMM","cn":,"icode":,"obs":[(y,m,val)]}
    MAX_PAGES, limit = 25, 100
    deadline = time.time() + 90      # hard 90s budget per indicator
    page, offset, total = 0, 0, None
    while page < MAX_PAGES and time.time() < deadline:
        used_limit, r, req_err = limit, None, None
        for lim, timeout_sec in ((limit, 25), (25, 45)):   # fallback: smaller page + longer timeout
            try:
                r = session.get(f"{base}?observations=1&limit={lim}&offset={offset}", timeout=timeout_sec)
                used_limit = lim
                req_err = None
                break
            except requests.exceptions.RequestException as ex:
                req_err = ex
        if r is None:
            log(f"[DBnomics] {name} ({prov}/{ds}) ERROR: {req_err} -> {ind.get('explorer','')}"); return
        if r.status_code != 200:
            log(f"[DBnomics] {name} ({prov}/{ds}) HTTP {r.status_code} -> {ind.get('explorer','')}"); return
        payload = r.json()
        series = payload.get("series", {}) if isinstance(payload, dict) else {}
        docs = series.get("docs", [])
        if not docs: break
        if total is None: total = series.get("num_found", 0)
        for doc in docs:
            if fc:
                cc, cn = fc
            else:
                raw_cc, _ = _country_from_doc(doc)
                canon = canon_country(raw_cc)
                if not canon: continue
                cc, cn = canon
            obs, last = [], ""
            for per, val in _doc_observations(doc, series):
                if val is None or per is None: continue
                y, m = _parse_period(per, freq)
                if y is None or y < START_YEAR: continue
                obs.append((y, m, val))
                pk = f"{y:04d}{m:02d}"
                if pk > last: last = pk
            if not obs: continue
            obs.sort(key=lambda x: (x[0], x[1]))   # keep monthly collapse behavior deterministic
            key = (cc or "").upper()
            cur = best.get(key)
            # keep the series that runs FURTHEST (tie: the one with more points)
            if cur is None or last > cur["last"] or (last == cur["last"] and len(obs) > len(cur["obs"])):
                best[key] = {"last":last, "cc":cc, "cn":cn,
                             "icode":doc.get("series_code", f"{prov}.{ds}"), "obs":obs}
        limit = used_limit
        offset += used_limit; page += 1
        if total is not None and offset >= total: break
    # emit the chosen series
    collapse = ind.get("monthly_collapse") or ind.get("forward_fill")
    ffill = ind.get("forward_fill")
    got, latest = 0, ""
    for b in best.values():
        obs = b["obs"]
        if collapse and freq != "annual":
            md = {}
            for (y, m, val) in obs:      # obs are chronological -> last day wins
                md[(y, m)] = val
            if ffill and md:
                now = datetime.utcnow()
                yy, mm = sorted(md)[0]
                cur, out = None, {}
                while (yy, mm) <= (now.year, now.month):
                    if (yy, mm) in md: cur = md[(yy, mm)]
                    if cur is not None: out[(yy, mm)] = cur
                    mm += 1
                    if mm > 12: mm = 1; yy += 1
                md = out
            obs = [(y, m, v) for (y, m), v in md.items()]
        for (y, m, val) in obs:
            got += 1
            pk = f"{y:04d}{m:02d}"
            if pk > latest: latest = pk
            if freq == "annual":
                annual_rows.append({"Country Code":b["cc"],"Country Name":b["cn"],
                    "Indicator Code":b["icode"],"Indicator Name":name,"Year":str(y),"Value":val})
            else:
                add_periodic(b["cc"], b["cn"], b["icode"], name, y, m, val)
    last_lbl = f"{latest[:4]}M{latest[4:]}" if latest else "-"
    flag = "" if got else f"  <-- 0 rows, CHECK CODES: {ind.get('explorer','')}"
    log(f"[DBnomics] {name} ({prov}/{ds}): {got} rows, {len(best)} countries, latest {last_lbl}{flag}")

# --------------------------------- run ---------------------------------------
log(f"Run started {datetime.utcnow().isoformat()}Z")
for ind in INDICATORS:
    b = ind["backend"]
    if b == "worldbank":       fetch_worldbank(ind)
    elif b == "worldbank_gem": fetch_worldbank_gem(ind)
    else:                      fetch_dbnomics(ind)

with open(os.path.join(OUT_DIR,"leading_annual.csv"),"w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=ANNUAL_FIELDS); w.writeheader(); w.writerows(annual_rows)
with open(os.path.join(OUT_DIR,"leading_periodic.csv"),"w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=PERIODIC_FIELDS); w.writeheader(); w.writerows(periodic_rows)
log(f"DONE  annual={len(annual_rows)} rows  periodic={len(periodic_rows)} rows")
with open(os.path.join(OUT_DIR,"fetch_log.txt"),"w",encoding="utf-8") as f:
    f.write("\n".join(LOG))

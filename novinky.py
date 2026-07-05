# -*- coding: utf-8 -*-
"""Novinky od dodavatele: položky z webu StoneX, které e-shop nemá,
připravené jako nové produkty pro import (vč. Product number, Mint,
dostupnosti, hmotnosti a CDN obrázku)."""
import re, time, html
import httpx
from core import norm, implied_spot, czk_fmt

LIST_URL = "https://stonexbullion.com/en/{path}/?page={page}"
PATHS = {"gold": "gold", "silver": "silver",
         "platinum": "platinum-palladium", "palladium": "platinum-palladium"}
# Přístup k dodavateli je výhradně veřejný – ceny jsou stejné pro všechny,
# přihlašování se nepoužívá (potvrzeno). Žádné přihlašovací údaje ani cookie.
HDRS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) cenotvorba-mz/1.0"}

def _get(url):
    r = httpx.get(url, timeout=40, follow_redirects=True, headers=HDRS)
    r.raise_for_status()
    return r.text

def list_products(metal="gold", max_pages=15, sleep=0.4):
    """[(name, url)] z výpisu kategorie; deduplikováno, bez SOLD OUT filtrace
    (ta probíhá až na detailu, výpis ji nenese spolehlivě)."""
    seen, out = set(), []
    for p in range(1, max_pages + 1):
        try:
            h = _get(LIST_URL.format(path=PATHS[metal], page=p))
        except Exception:
            break
        # produktové odkazy: /en/gold-bars/.../slug/ apod. s textem názvu
        links = re.findall(
            r'href="(https://stonexbullion\.com/en/(?:gold|silver|platinum|palladium)'
            r'[a-z\-]*(?:/[a-z0-9\-]+){1,3}/)"[^>]*>([^<]{6,120})</a>', h)
        found = 0
        for url, name in links:
            name = html.unescape(name).strip()
            if "/mints/" in url or url.count("/") < 6:      # kategorie, ne produkt
                continue
            if url in seen or "Add to" in name:
                continue
            seen.add(url); out.append((name, url)); found += 1
        if found == 0:
            break
        time.sleep(sleep)
    return out

SPEC = {
 "product_number": r"Product\s+number\s*\|?\s*(?:</[^>]+>\s*<[^>]+>)?\s*(\d{3,9})",
 "mint":           r"(?<!Royal )(?<!Perth )(?<!States )\bMint\s*\|\s*([^|<\n]{2,60})",
 "mint_txt":       r">Mint<\s*/[^>]+>\s*<[^>]+>\s*([^<]{2,60})<",
 "weight":         r"Weight1?\s*\|?\s*(?:</[^>]+>\s*<[^>]+>)?\s*([\d.,]+)\s*g",
 "avail_num":      r"Availability:\s*(\d[\d,\.]*)",
 "dispatch":       r"(?:est\.\s*dispatch|ships)\s*in[^\n]*?days",
 "soldout":        r"Currently\s+out\s+of\s+stock|SOLD\s*OUT",
 "image":          r"(https://cdn\.stonexbullion\.com/cache/img/[^\s\"')]+)",
 "premium":        r"Premium:\s*€\s*([\d.,]+)",
 "metal":          r"Metal\s*Type\W{0,4}(Gold|Silver|Platinum|Palladium)",
 "metal_txt":      r"\bMetal\s*\|\s*(Gold|Silver|Platinum|Palladium)",
}
def spot_from_page(h, metal_word):
    m=re.search(metal_word+r"[^€\d]{0,40}€\s*([\d.,]+)", h)
    return _f(m.group(1))/31.1035 if m else None   # €/oz -> €/g

def parse_detail(h):
    d = {}
    for k in ("product_number", "weight", "avail_num", "image", "premium"):
        m = re.search(SPEC[k], h, re.I | re.S)
        d[k] = m.group(1) if m else None
    m = re.search(SPEC["mint"], h, re.I | re.S) or re.search(SPEC["mint_txt"], h, re.I)
    d["mint"] = html.unescape(m.group(1)).strip() if m else None
    d["soldout"] = bool(re.search(SPEC["soldout"], h, re.I))
    d["dispatch"] = bool(re.search(SPEC["dispatch"], h, re.I))
    m = re.search(SPEC["metal"], h, re.I|re.S) or re.search(SPEC["metal_txt"], h, re.I)
    d["metal"] = m.group(1).capitalize() if m else None
    return d

def _f(s):  # "7,775875" / "1,112.36" -> float (autodetekce formátu)
    if s is None: return None
    s = s.strip()
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".") if len(s.split(",")[-1]) != 3 else s.replace(",", "")
    return float(s)

def collect_new(mapping, metal, spot_g, fx, margin_pct, rounding=1,
                limit=None, sleep=0.6, log=None):
    """Nové produkty = výpis − (názvy již spárované v mapě). Sold out se vynechá."""
    have = {tuple(sorted(norm(v["stonex"]))) for v in mapping.values()}
    rows, errs = [], []
    items = list_products(metal)
    news = [(n, u) for n, u in items if tuple(sorted(norm(n))) not in have]
    if limit: news = news[:limit]
    for name, url in news:
        try:
            h = _get(url)
            d = parse_detail(h)
            if d["soldout"]:
                continue
            if not (d["product_number"] and d["weight"] and d["premium"]):
                errs.append((name, url, "neúplný detail")); continue
            wg = _f(d["weight"]); prem = _f(d["premium"])
            sg = spot_from_page(h, d["metal"] or metal.capitalize()) or \
                 (spot_g if metal == "gold" else None)
            if sg is None:
                errs.append((name, url, "spot kovu nenalezen na stránce")); continue
            cost = (sg * wg + prem) * fx
            price = round(cost * (1 + margin_pct / 100) / rounding) * rounding
            avail = (f"U dodavatele: {d['avail_num']} ks" if d["avail_num"] else "Na dotaz") \
                    + (" · delší expedice" if d["dispatch"] else "")
            rows.append({
                "code": d["product_number"], "pairCode": "", "name": name, "guid": "",
                "price": price, "purchasePrice": round(cost, 2),
                "Category": "", "variant:Váha": czk_fmt(wg),
                "manufacturer": d["mint"] or "", "availability": avail,
                "image": d["image"] or "", "url": url,
            })
        except Exception as e:
            errs.append((name, url, str(e)))
        time.sleep(sleep)
        if log: log(name)
    return rows, errs

def export_new_csv(rows):
    import io, csv
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=",", lineterminator="\r\n")
    w.writerow(["code","pairCode","name","guid","price","purchasePrice",
                "Category","variant:Váha","manufacturer","availability","image"])
    for r in rows:
        w.writerow([r["code"], r["pairCode"], r["name"], r["guid"],
                    czk_fmt(r["price"]), czk_fmt(r["purchasePrice"]),
                    r["Category"], r["variant:Váha"], r["manufacturer"],
                    r["availability"], r["image"]])
    return "\ufeff" + buf.getvalue()

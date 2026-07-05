# -*- coding: utf-8 -*-
"""Cenotvorba moje-zlato.cz: StoneX katalog -> Shoptet import (CSV/XML)."""
import re, io, csv, json, statistics, datetime
import httpx, pdfplumber

CNB_TXT = ("https://www.cnb.cz/cs/financni-trhy/devizovy-trh/"
           "kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt")
STONEX_PDF = ("https://stonexbullion.com/api/client/catalog/pdf/"
              "?t={ts}&url={path}&update_filters=true&metal_ids%5B%5D={mid}&term=&page=1")
# metal_ids: zlato=1 ověřeno; stříbro/platina/palladium NUTNO ověřit prvním stažením (viz README)
METALS = {"gold": (1, "%2Fen%2Fgold%2F"), "silver": (2, "%2Fen%2Fsilver%2F"),
          "platinum": (3, "%2Fen%2Fplatinum%2F"), "palladium": (4, "%2Fen%2Fpalladium%2F")}

NOISE = ("WE BUY", "WE SELL", "stonexbullion", "StoneX Bullion", "Precious Metals",
         "Page ", "support@", "VAT", "Item Weight", "generated", "GmbH")

def eur(s): return float(s.replace(".", "").replace(",", "."))

def cnb_eur_czk():
    """Kurz EUR/CZK z denního TXT ČNB. Vrací (kurz, datum)."""
    r = httpx.get(CNB_TXT, timeout=20); r.raise_for_status()
    lines = r.text.splitlines()
    date = lines[0].split(" ")[0]
    for ln in lines:
        p = ln.split("|")
        if len(p) == 5 and p[3] == "EUR":
            return float(p[4].replace(",", ".")), date
    raise RuntimeError("EUR v kurzovním lístku ČNB nenalezen")

def fetch_stonex_pdf(metal="gold"):
    mid, path = METALS[metal]
    ts = int(datetime.datetime.now().timestamp() * 1000)
    url = STONEX_PDF.format(ts=ts, mid=mid, path=path)
    r = httpx.get(url, timeout=60, follow_redirects=True)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise RuntimeError(f"Odpověď není PDF (metal={metal}) – ověřte metal_ids/cestu")
    return r.content

def parse_catalog(pdf_bytes):
    """PDF -> [{name, wg, sell:[(qty,eur,pct)], buy:(eur,pct)|None}]
    Tolerantní k rozpadu řádků: položka končí tokenem hmotnosti 'NN.NNg'."""
    text = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pg in pdf.pages:
            text.append(pg.extract_text() or "")
    lines = [l for l in "\n".join(text).splitlines()
             if l.strip() and not any(n in l for n in NOISE)]
    items, chunk = [], []
    W = re.compile(r"(\d+(?:\.\d+)?)g\s*$")
    for ln in lines:
        chunk.append(ln.strip())
        m = W.search(ln.strip())
        if m:
            items.append(_parse_chunk(" ".join(chunk), float(m.group(1))))
            chunk = []
    return [i for i in items if i]

def _parse_chunk(s, wg):
    body = re.sub(r"(\d+(?:\.\d+)?)g\s*$", "", s).strip()
    toks = list(re.finditer(r"(\d+)\+|(-?[\d\.]+,\d{2}) €|(-?\d+\.\d{2})%", body))
    if not toks:
        return {"name": body, "wg": wg, "sell": [], "buy": None}
    name = body[:toks[0].start()].strip(" |~-")
    qtys, eurs, pcts = [], [], []
    pend_q = None
    for t in toks:
        if t.group(1): pend_q = int(t.group(1))
        elif t.group(2): eurs.append((pend_q, eur(t.group(2)))); pend_q = None
        elif t.group(3): pcts.append(float(t.group(3)))
    pairs = list(zip(eurs, pcts + [None] * (len(eurs) - len(pcts))))
    buy, sell = None, []
    for (q, e), p in pairs:
        if e < 0 or (p is not None and p < 0): buy = (e, p)
        else: sell.append((q or 1, e, p))
    return {"name": name, "wg": wg, "sell": sorted(sell), "buy": buy}

def implied_spot(items):
    """Medián spotu €/g implikovaného z dvojic € <-> % v katalogu."""
    v = []
    for it in items:
        pairs = list(it["sell"]) + ([( None, it["buy"][0], it["buy"][1])] if it["buy"] else [])
        for _, e, p in pairs:
            if p: v.append(e / (p / 100) / it["wg"])
    if not v: raise RuntimeError("Nelze odvodit spot – katalog bez % hodnot")
    return round(statistics.median(v), 2), len(v)

def sell_premium(item, qty=1):
    """Prémie € pro dané odběrové množství (nejvyšší pásmo <= qty)."""
    if not item["sell"]: return None
    ok = [t for t in item["sell"] if t[0] <= qty]
    return (max(ok)[1] if ok else min(item["sell"])[1])

def compute_rows(cenik_rows, mapping, catalog, spot_g, fx, margin_pct,
                 bands=None, rounding=1, qty=1, categories=None):
    """cenik_rows: dict-rows exportu Shoptetu; mapping: {code:{stonex,shoda}}
       bands: [{'max_g':10,'pct':5.0},...] má přednost před margin_pct."""
    by_name = {c["name"]: c for c in catalog}
    out, skipped = [], []
    for r in cenik_rows:
        code = r.get("code", "").strip()
        m = mapping.get(code)
        it = by_name.get(m["stonex"]) if m else None
        if not it:
            skipped.append({**r, "_duvod": "nespárováno"}); continue
        prem = sell_premium(it, qty)
        if prem is None:
            skipped.append({**r, "_duvod": "StoneX bez prodejní ceny"}); continue
        cost_eur = spot_g * it["wg"] + prem
        cost_czk = cost_eur * fx
        pct = margin_pct
        for b in (bands or []):
            if it["wg"] <= b["max_g"]: pct = b["pct"]; break
        price = round(cost_czk * (1 + pct / 100) / rounding) * rounding
        out.append({
            "code": code, "pairCode": r.get("pairCode", ""),
            "name": r.get("name", ""), "guid": r.get("guid", ""),
            "price": price, "purchasePrice": round(cost_czk, 2),
            "Category": (categories or {}).get(r.get("guid", ""), ""),
            "variant:Váha": r.get("variant:Váha", ""),
            "_stonex": it["name"], "_wg": it["wg"], "_prem_eur": prem,
            "_shoda": m.get("shoda", ""), "_stara_cena": r.get("price", ""),
        })
    return out, skipped

def czk_fmt(v): return f"{v:.2f}".replace(".", ",")

def export_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=",", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(["code", "pairCode", "name", "guid", "price",
                "purchasePrice", "Category", "variant:Váha"])
    for r in rows:
        w.writerow([r["code"], r["pairCode"], r["name"], r["guid"],
                    czk_fmt(r["price"]), czk_fmt(r["purchasePrice"]),
                    r["Category"], r["variant:Váha"]])
    return "\ufeff" + buf.getvalue()

def export_xml(rows):
    from xml.sax.saxutils import escape as esc
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<SHOP>"]
    for r in rows:
        parts.append(
            f'<SHOPITEM><CODE>{esc(str(r["code"]))}</CODE>'
            f'<NAME>{esc(r["name"])}</NAME><GUID>{esc(r["guid"])}</GUID>'
            f'<PRICE>{czk_fmt(r["price"])}</PRICE>'
            f'<PURCHASE_PRICE>{czk_fmt(r["purchasePrice"])}</PURCHASE_PRICE>'
            "</SHOPITEM>")
    parts.append("</SHOP>")
    return "\n".join(parts)

def load_categories_from_xml(xml_bytes):
    """productsComplete XML -> {guid: kategorie} (DEFAULT_CATEGORY)."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_bytes)
    out = {}
    for it in root.findall("SHOPITEM"):
        g = it.findtext("GUID") or ""
        c = it.find(".//DEFAULT_CATEGORY")
        if g and c is not None: out[g] = c.text or ""
    return out

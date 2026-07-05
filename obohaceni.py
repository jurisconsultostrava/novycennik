# -*- coding: utf-8 -*-
"""Obohacení XLSX/CSV nespárovaných produktů daty z veřejného webu StoneX.
Doplňuje: guid(*), price, purchasePrice, manufacturer(Mint), availability,
image(CDN), variant:Váha(fine weight), Category(z categories.csv dle názvu).
(*) guid na StoneX neexistuje – je to identifikátor Shoptetu; nechává se prázdný
    k doplnění při importu (nový produkt si GUID přidělí Shoptet sám).
Vstupní kódy = StoneX Product number. URL detailu se získá indexem z výpisů
kategorií (deterministicky, bez hádání URL)."""
import re, time, html
import httpx
from core import norm, czk_fmt, cnb_eur_czk
from novinky import list_products, parse_detail, spot_from_page, _get, _f

# ---------- kategorie e-shopu (z categories CSV) ----------
def load_categories_csv(text):
    """[(cesta_titulků, url_segment)] seřazeno od nejhlubších; pro klasifikaci názvu."""
    import csv, io
    rows=list(csv.reader(io.StringIO(text), delimiter=';'))
    hdr=rows[0]; idx={h:i for i,h in enumerate(hdr)}
    by_id={}
    for r in rows[1:]:
        if len(r)<len(hdr): continue
        cid=r[idx['id']].strip('"'); pid=r[idx['parentId']].strip('"')
        by_id[cid]=dict(pid=pid, title=r[idx['title']].strip('"'),
                        url=r[idx['url']].strip('"'))
    def path(cid):
        out=[]; seen=set()
        while cid in by_id and cid not in seen:
            seen.add(cid); out.append(by_id[cid]['title']); cid=by_id[cid]['pid']
        return " > ".join(reversed(out))
    cats=[(cid, path(cid)) for cid in by_id]
    return by_id, cats

WORDNUM = {'zlat':'Investiční zlato','gold':'Investiční zlato',
           'stribr':'Investiční stříbro','silver':'Investiční stříbro',
           'platin':'Investiční platina a palladium','platinum':'Investiční platina a palladium',
           'pallad':'Investiční platina a palladium','palladium':'Investiční platina a palladium'}

def _fold(s):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD',s.lower())
                   if unicodedata.category(c)!='Mn')

def classify(name, catindex):
    """Zvolí nejvhodnější existující kategorii e-shopu podle názvu produktu.
    Vrací (cesta, jistota). Vrací '' když nelze bezpečně určit."""
    n=_fold(name)
    metal=None
    for k,v in WORDNUM.items():
        if k in n: metal=v; break
    if not metal:
        return "", "nelze určit kov"
    # hmotnostní/typová podkategorie: zkusit najít kategorii, jejíž titul je v názvu
    _, cats = catindex
    metal_paths=[(cid,p) for cid,p in cats if p.startswith(metal)]
    # zlato: podkategorie dle gramáže (1 g, 5 g, ... 1 kg, 1 Oz) nebo "mince/slitky"
    best=("",0)
    for cid,p in metal_paths:
        leaf=p.split(">")[-1].strip().lower()
        score=0
        if leaf==metal.lower(): score=1
        # gramáž v názvu odpovídá listu ("100 g","5 g","1 oz"...)
        m=re.search(r'(\d+(?:[.,]\d+)?)\s*(kg|g|oz)\b', name, re.I)
        if m:
            num=m.group(1); unit=m.group(2).lower()
            if unit=='kg': num=str(int(float(num.replace(',','.'))*1000)); unit='g'
            wtxt=(num+" "+unit).replace('.0','')
            lf=_fold(leaf).replace(" ","")
            if wtxt.replace(" ","") in lf: score=3
            # "1 oz" v listu bývá "1 Oz"
            if unit=='oz' and ('1oz' in lf or (num+'oz') in lf): score=3
        if ('mince' in leaf and ('coin' in n or 'mince' in n)): score=max(score,2)
        if ('slitk' in leaf and ('bar' in n or 'slitek' in n)): score=max(score,2)
        if score>best[1]: best=(p,score)
    return (best[0] or metal), ("přesná" if best[1]>=3 else "kategorie kovu" if best[0] else "kov")

# ---------- index Product number -> URL z výpisů ----------
def build_pn_index(metals=("gold","silver","platinum"), sleep=0.5, log=None):
    idx={}
    for met in metals:
        for name,url in list_products(met):
            try:
                h=_get(url); d=parse_detail(h)
                if d.get("product_number"):
                    idx[str(d["product_number"])]={"url":url,"html":h,"name":name}
                if log: log(f"{met}: {d.get('product_number')} {name[:40]}")
            except Exception:
                pass
            time.sleep(sleep)
    return idx

# ---------- hlavní obohacení ----------
def enrich(rows, categories_text, metals=("gold","silver","platinum"),
           margin_pct=1.25, rounding=1, qty=1, fx=None, log=None):
    """rows: [{'code','name',...}]; vrací (obohacené, chyby)."""
    catindex=load_categories_csv(categories_text)
    if fx is None: fx,_=cnb_eur_czk()
    pn=build_pn_index(metals, log=log)
    out, errs=[], []
    for r in rows:
        code=str(r.get("code","")).strip()
        name=r.get("name","")
        cat,catconf=classify(name, catindex)
        rec=dict(code=code, pairCode="", name=name, guid="", price="",
                 purchasePrice="", Category=cat, **{"variant:Váha":""},
                 manufacturer="", availability="", image="",
                 _catconf=catconf, _zdroj="")
        hit=pn.get(code)
        if not hit:
            rec["_zdroj"]="Product number nenalezen ve výpisech StoneX"
            errs.append(rec); out.append(rec); continue
        h=hit["html"]; d=parse_detail(h)
        wg=_f(d.get("weight")) if d.get("weight") else None
        prem=_f(d.get("premium")) if d.get("premium") else None
        img=None
        m=re.search(r'(https://cdn\.stonexbullion\.com/cache/img/[^\s"\')]+\.webp)', h)
        if m: img=m.group(1)
        elif code: img=f"https://stonexbullion.com/product/image/{code}/"  # fallback og:image
        sg=spot_from_page(h, d.get("metal") or "")
        rec["manufacturer"]=d.get("mint") or ""
        rec["variant:Váha"]=(d.get("weight") or "").replace(".",",") if d.get("weight") else ""
        rec["image"]=img or ""
        if d.get("soldout"):
            rec["availability"]="Vyprodáno u dodavatele"
        elif d.get("avail_num"):
            rec["availability"]=f"Skladem u dodavatele: {d['avail_num']} ks"
        else:
            rec["availability"]="Na dotaz"
        if wg and prem is not None and sg:
            czk=(sg*wg+prem)*fx
            rec["purchasePrice"]=czk_fmt(round(czk,2))
            rec["price"]=czk_fmt(round(czk*(1+margin_pct/100)/rounding)*rounding)
            rec["_zdroj"]=hit["url"]
        else:
            rec["_zdroj"]=f"neúplný detail (w={wg},prem={prem},spot={sg}) {hit['url']}"
            errs.append(rec)
        out.append(rec)
        if log: log(f"OK {code} {name[:40]}")
    return out, errs

def export_enriched_csv(rows):
    import csv, io
    buf=io.StringIO()
    cols=["code","pairCode","name","guid","price","purchasePrice",
          "Category","variant:Váha","manufacturer","availability","image"]
    w=csv.writer(buf, delimiter=",", lineterminator="\r\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c,"") for c in cols])
    return "\ufeff"+buf.getvalue()

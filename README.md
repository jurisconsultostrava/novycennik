# Cenotvorba moje-zlato.cz — StoneX → Shoptet

Interní aplikace: stáhne ceníkový PDF katalog dodavatele (StoneX Bullion), kurz ČNB,
spáruje položky s exportem ceníku Shoptetu a vygeneruje importní **CSV/XML**
se strukturou `code,pairCode,name,guid,price,purchasePrice,Category,variant:Váha`.

## Logika výpočtu
- **nákupní cena (purchasePrice)** = (spot €/g × hmotnost + WE SELL prémie € z katalogu) × kurz CZK/EUR
- **prodejní cena (price)** = nákupní × (1 + marže %), zaokrouhleno dle volby (1/10/100 Kč)
- spot: implikovaný mediánem z PDF (doporučeno; robustní vůči zastaralé cache grafu), nebo ručně
- kurz: automaticky denní TXT lístek ČNB, nebo ručně
- prémie: pásmo dle zvoleného odběrového množství (1+/10+/…)
- marže: globální %, volitelně pásma dle hmotnosti `[{"max_g":10,"pct":5},{"max_g":100,"pct":2}]`

## Deploy na Railway přes GitHub
1. Vytvořte **privátní** GitHub repo a nahrajte obsah této složky (git init → add → commit → push).
2. Railway → New Project → **Deploy from GitHub repo** → vyberte repo.
   Nixpacks detekuje Python; start command je v `railway.json`.
3. **Variables:** nastavte `APP_TOKEN` (silný řetězec). Bez něj je aplikace veřejně
   přístupná — obsahuje vaše nákupní ceny, token je nutnost.
4. Settings → Networking → Generate Domain. Hotovo; UI běží na kořenové URL.

Lokální běh: `pip install -r requirements.txt && uvicorn main:app --reload`

## Použití
1. V Shoptetu vyexportujte ceník (CSV se sloupci code, pairCode, name, guid, price,
   purchasePrice, variant:Váha…). Volitelně přiložte `productsComplete` XML — doplní se
   sloupec Category (join přes GUID).
2. Zvolte kovy, marži, zaokrouhlení → **Náhled** (tabulka s Δ% a spolehlivostí shody) →
   **Stáhnout CSV/XML**.
3. Nouzový režim: PDF katalog lze nahrát ručně (pole „StoneX PDF ručně") — má přednost
   před on-line stažením.

## Párování (mapping.json)
Repo obsahuje výchozí mapu **168 spárovaných kódů** (vytvořeno poloautomaticky 5. 7. 2026;
položky se `"shoda":"ke kontrole"` doporučuji jednorázově projít). Úpravy: přímo v souboru
(commit = trvalé), nebo za běhu přes `POST /api/mapping` — pozor, souborový zápis je na
Railway **efemérní** (zaniká redeployem), trvalé změny patří do repa.

## Co je nutné ověřit prvním ostrým spuštěním (v sandboxu nešlo otestovat)
1. **On-line stažení PDF** ze StoneX API (zde ověřen pouze parser na reálném textovém
   obsahu katalogu, oběma layouty — inline i rozpadané řádky). Selže-li fetch
   (ochrana proti botům apod.), použijte ruční nahrání PDF v UI.
2. **metal_ids pro stříbro/platinu/palladium** — ověřeno je jen zlato (=1). Odhad 2/3/4
   je v `core.METALS`; správné hodnoty zjistíte z URL PDF exportu na webu StoneX
   u příslušného kovu a opravíte na jednom řádku.
3. Extrakce textu přes **pdfplumber** může řádkovat jinak než referenční extraktor —
   parser je psán tolerantně (položku ukotvuje token hmotnosti, € a % páruje sekvenčně),
   ale první běh zkontrolujte proti Náhledu.

## Bezpečnostní poznámky
- `APP_TOKEN` nastavit vždy; repo držet privátní (mapping.json prozrazuje dodavatele).
- Aplikace nikam neukládá nahrané soubory; vše se zpracovává v paměti požadavku.

## Novinky od dodavatele (modul novinky.py)
Sekce „Novinky" projde výpis kategorií StoneX, vybere položky, které e-shop nemá
(porovnání normalizovaných názvů proti mapping.json), stáhne jejich detaily a vygeneruje
CSV nových produktů: `code`(=Product number), `name`, `price`/`purchasePrice`
(spot×váha+prémie ×kurz ×marže), `variant:Váha`(fine weight g), `manufacturer`(=Mint),
`availability`(počet ks u dodavatele / delší expedice), `image`(CDN URL). Sold out se
vynechává; sloupec Category se nechává prázdný k redakčnímu doplnění. Parser detailu je
zkalibrován proti reálné stránce (ověřeno vč. cenové rovnice na cent); scraping je
šetrný (pauzy mezi požadavky), pro první test použijte Limit=5.

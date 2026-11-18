#!/usr/bin/env python3
"""
Zwijnenburg Mode – Google XML (RSS, via Channable) productfeed -> OpenAI Ads productfeed (CSV / CSV.GZ)

Bron:   https://files.channable.com/A04bq2JBmsGjyN7Di8zJPA==.xml
Output: dist/zwijnenburg_openai.csv      (voor "Connect your feed via URL")
        dist/zwijnenburg_openai.csv.gz   (voor SFTP-upload)
        dist/report.txt                  (controlerapport)

Kledingfeed met varianten: elke maat is een eigen rij (item_id), varianten van
hetzelfde product delen group_id (= g:item_group_id) en krijgen
listing_has_variations=true + variant_dict met de maat.

Alleen standaardbibliotheek, geen extra packages nodig.
"""

import csv
import gzip
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

FEED_URL = os.environ.get("FEED_URL", "https://files.channable.com/A04bq2JBmsGjyN7Di8zJPA==.xml")
OUT_DIR = os.environ.get("OUT_DIR", "dist")
OUT_NAME = os.environ.get("OUT_NAME", "zwijnenburg_openai")

SELLER_NAME = os.environ.get("SELLER_NAME", "Zwijnenburg Mode")
SELLER_URL = os.environ.get("SELLER_URL", "https://www.zwijnenburgmode.nl")
# Alleen gebruikt als een product geen g:brand heeft.
BRAND_FALLBACK = os.environ.get("BRAND_FALLBACK", "Zwijnenburg Mode")
# true  = uitverkochte producten wel in de feed, maar is_ads_eligible=false
# false = uitverkochte producten helemaal weglaten
KEEP_OUT_OF_STOCK = os.environ.get("KEEP_OUT_OF_STOCK", "true").lower() == "true"
MIN_PRODUCTS = int(os.environ.get("MIN_PRODUCTS", "500"))
# De Channable-links bevatten Google-UTM's. Die worden verwijderd en vervangen door deze.
# Leeg laten = geen UTM's toevoegen.
UTM_PARAMS = os.environ.get("UTM_PARAMS", "utm_source=chatgpt&utm_medium=cpc&utm_campaign=openai_ads")
# Optioneel, alleen invullen als Ads Manager dit per rij vraagt (bijv. "NL").
TARGET_COUNTRIES = os.environ.get("TARGET_COUNTRIES", "").strip()
STORE_COUNTRY = os.environ.get("STORE_COUNTRY", "").strip()
SHIPPING_COUNTRY = os.environ.get("SHIPPING_COUNTRY", "NL")

NS_G = "http://base.google.com/ns/1.0"
NS_ATOM = "http://www.w3.org/2005/Atom"
G = "{%s}" % NS_G

COLUMNS = [
    "item_id", "group_id", "listing_has_variations", "variant_dict",
    "title", "description", "url", "brand", "seller_name",
    "image_url", "additional_image_urls", "availability", "price", "sale_price",
    "shipping_price", "gtin", "mpn", "condition", "product_category",
    "color", "size", "gender", "age_group", "seller_url", "is_ads_eligible",
]
if TARGET_COUNTRIES:
    COLUMNS.append("target_countries")
if STORE_COUNTRY:
    COLUMNS.append("store_country")

VALID_AVAILABILITY = {"in_stock", "out_of_stock", "pre_order", "backorder", "unknown"}
AVAILABILITY_MAP = {
    "preorder": "pre_order", "in stock": "in_stock", "out of stock": "out_of_stock",
    "op voorraad": "in_stock", "niet op voorraad": "out_of_stock",
}
VALID_CONDITION = {"new", "refurbished", "used"}
GENDER_MAP = {"male": "male", "female": "female", "unisex": "unisex",
              "heren": "male", "man": "male", "dames": "female", "vrouw": "female"}
VALID_AGE_GROUP = {"newborn", "infant", "toddler", "kids", "adult"}
PLACEHOLDERS = {"", "-", "null", "none", "n/a", "na", "unknown", "0"}
PRICE_RE = re.compile(r"^\s*(\d+(?:[.,]\d{1,2})?)\s*([A-Za-z]{3})\s*$")
WS_RE = re.compile(r"\s+")
TAG_RE = re.compile(r"<[^>]+>")


def clean_text(value: str) -> str:
    if not value:
        return ""
    value = TAG_RE.sub(" ", value)          # eventuele HTML eruit
    value = WS_RE.sub(" ", value).strip()   # dubbele spaties / regeleinden
    return value


def is_placeholder(value: str) -> bool:
    return value.strip().lower() in PLACEHOLDERS


def valid_gtin(code: str) -> bool:
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in code]
    check = digits.pop()
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - total % 10) % 10 == check


def parse_price(raw: str):
    """Geeft (bedrag, valuta) terug, of None."""
    m = PRICE_RE.match(raw or "")
    if not m:
        return None
    return float(m.group(1).replace(",", ".")), m.group(2).upper()


def fmt_price(amount: float, currency: str) -> str:
    return f"{amount:.2f} {currency}"


def field(entry, name: str) -> str:
    """Zoekt eerst g:<name>, daarna <name> zonder namespace (RSS title/link/description)."""
    for tag in (G + name, name, f"{{{NS_ATOM}}}{name}"):
        el = entry.find(tag)
        if el is not None and el.text and el.text.strip():
            return el.text.strip()
    return ""


def fix_url(url: str) -> str:
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    if not url.startswith("https://"):
        return url
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_")]
    if UTM_PARAMS:
        query += parse_qsl(UTM_PARAMS)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def shipping_price(entry, currency: str) -> str:
    for ship in entry.findall(G + "shipping"):
        country = (ship.findtext(G + "country") or "").strip().upper()
        if country and country != SHIPPING_COUNTRY.upper():
            continue
        parsed = parse_price(ship.findtext(G + "price") or "")
        if parsed and parsed[1] == currency:
            return fmt_price(*parsed)
    return ""


def download(url: str) -> str:
    path = os.path.join(OUT_DIR, "source.xml")
    req = urllib.request.Request(url, headers={"User-Agent": "zwijnenburg-openai-feed/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp, open(path, "wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    return path


def convert(source_path: str):
    stats = Counter()
    issues = Counter()
    examples = {}
    seen_ids = set()
    rows = []

    def note(kind, item_id):
        issues[kind] += 1
        lst = examples.setdefault(kind, [])
        if len(lst) < 5:
            lst.append(item_id)

    for _, el in ET.iterparse(source_path, events=("end",)):
        if el.tag not in (f"{{{NS_ATOM}}}entry", "entry", "item"):
            continue
        stats["entries"] += 1

        item_id = field(el, "id")
        group_id = field(el, "item_group_id")
        title = clean_text(field(el, "title"))
        description = clean_text(field(el, "description"))
        url = field(el, "link")
        image_url = field(el, "image_link")
        extra_images = [e.text.strip() for e in el.findall(G + "additional_image_link")
                        if e.text and e.text.strip().startswith("http")]
        availability = field(el, "availability").lower()
        availability = AVAILABILITY_MAP.get(availability, availability)
        price = parse_price(field(el, "price"))
        sale = parse_price(field(el, "sale_price"))
        gtin = field(el, "gtin")
        mpn = clean_text(field(el, "mpn"))
        condition = field(el, "condition").lower()
        brand = clean_text(field(el, "brand"))
        product_type = clean_text(field(el, "product_type"))
        google_cat = clean_text(field(el, "google_product_category"))
        color = clean_text(field(el, "color"))
        size = clean_text(field(el, "size"))
        gender = GENDER_MAP.get(field(el, "gender").lower(), "")
        age_group = field(el, "age_group").lower()
        ship = shipping_price(el, price[1]) if price else ""
        el.clear()

        # --- verplichte velden; rij overslaan als iets ontbreekt
        if not item_id:
            note("geen id (overgeslagen)", "?")
            continue
        if item_id in seen_ids:
            note("dubbel id (overgeslagen)", item_id)
            continue
        if not title:
            note("geen titel (overgeslagen)", item_id)
            continue
        if not url.startswith("http"):
            note("geen/ongeldige url (overgeslagen)", item_id)
            continue
        if not image_url.startswith("http"):
            note("geen afbeelding (overgeslagen)", item_id)
            continue
        if price is None or price[0] <= 0:
            note("ongeldige prijs (overgeslagen)", item_id)
            continue
        if availability not in VALID_AVAILABILITY:
            note("onbekende availability -> unknown", item_id)
            availability = "unknown"

        # --- prijzen
        price_str = fmt_price(*price)
        sale_str = ""
        if sale:
            if 0 < sale[0] < price[0] and sale[1] == price[1]:
                sale_str = fmt_price(*sale)
                stats["met sale_price"] += 1
            else:
                note("sale_price ongeldig (weggelaten)", item_id)

        # --- opschonen / aanvullen
        if not description:
            description = title
        if description == title:
            issues["description gelijk aan titel"] += 1
        if not brand or is_placeholder(brand):
            brand = BRAND_FALLBACK
            note("brand = fallback", item_id)
        if gtin and not valid_gtin(gtin):
            note("ongeldige gtin (weggelaten)", item_id)
            gtin = ""
        if condition not in VALID_CONDITION:
            condition = "new"
        if age_group not in VALID_AGE_GROUP:
            age_group = ""
        # Leesbaar pad ("Heren > Overhemden") heeft voorkeur boven een numerieke Google-ID.
        category = product_type
        if is_placeholder(category):
            category = "" if (google_cat.isdigit() or is_placeholder(google_cat)) else google_cat
        url = fix_url(url)
        if image_url.startswith("http://"):
            image_url = "https://" + image_url[len("http://"):]

        # --- varianten (maat)
        has_variant = bool(group_id) and group_id != item_id and bool(size)
        if has_variant:
            variant_dict = json.dumps({"size": size}, ensure_ascii=False, separators=(",", ":"))
        else:
            variant_dict = ""
            if group_id and group_id != item_id and not size:
                note("variant zonder maat (niet gegroepeerd)", item_id)
            group_id = ""

        eligible = availability in ("in_stock", "pre_order", "backorder")
        if not eligible and not KEEP_OUT_OF_STOCK:
            stats["uitverkocht weggelaten"] += 1
            continue

        seen_ids.add(item_id)
        stats["ads_eligible" if eligible else "niet_eligible"] += 1
        row = {
            "item_id": item_id,
            "group_id": group_id,
            "listing_has_variations": "true" if has_variant else "",
            "variant_dict": variant_dict,
            "title": title[:150],
            "description": description[:5000],
            "url": url,
            "brand": brand[:70],
            "seller_name": SELLER_NAME,
            "image_url": image_url,
            "additional_image_urls": ",".join(u.replace(",", "%2C") for u in extra_images[:10]),
            "availability": availability,
            "price": price_str,
            "sale_price": sale_str,
            "shipping_price": ship,
            "gtin": gtin,
            "mpn": mpn[:70],
            "condition": condition,
            "product_category": category,
            "color": color[:40],
            "size": size[:20],
            "gender": gender,
            "age_group": age_group,
            "seller_url": SELLER_URL,
            "is_ads_eligible": "true" if eligible else "false",
        }
        if TARGET_COUNTRIES:
            row["target_countries"] = TARGET_COUNTRIES
        if STORE_COUNTRY:
            row["store_country"] = STORE_COUNTRY
        rows.append(row)

    # Groepen met maar één rij hebben geen echte varianten.
    group_sizes = Counter(r["group_id"] for r in rows if r["group_id"])
    for r in rows:
        if r["group_id"] and group_sizes[r["group_id"]] == 1:
            r["group_id"] = r["listing_has_variations"] = r["variant_dict"] = ""
    stats["productgroepen"] = sum(1 for n in group_sizes.values() if n > 1)

    return rows, stats, issues, examples


def write_outputs(rows):
    csv_path = os.path.join(OUT_DIR, f"{OUT_NAME}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(rows)
    with open(csv_path, "rb") as src, gzip.open(csv_path + ".gz", "wb") as dst:
        dst.write(src.read())
    return csv_path


def write_report(rows, stats, issues, examples):
    lines = [
        "Zwijnenburg Mode -> OpenAI Ads feed",
        f"Bron: {FEED_URL}",
        "",
        f"Items in XML:            {stats['entries']}",
        f"Rijen in output:         {len(rows)}",
        f"  is_ads_eligible=true:  {stats['ads_eligible']}",
        f"  is_ads_eligible=false: {stats['niet_eligible']}",
        f"Productgroepen (maten):  {stats['productgroepen']}",
        f"Met sale_price:          {stats['met sale_price']}",
        f"Uitverkocht weggelaten:  {stats['uitverkocht weggelaten']}",
        "",
        "Meldingen:",
    ]
    for kind, n in issues.most_common():
        ex = ", ".join(examples.get(kind, []))
        lines.append(f"  {kind}: {n}" + (f"  (bijv. {ex})" if ex else ""))
    if not issues:
        lines.append("  geen")
    text = "\n".join(lines) + "\n"
    with open(os.path.join(OUT_DIR, "report.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    source = sys.argv[1] if len(sys.argv) > 1 else download(FEED_URL)
    rows, stats, issues, examples = convert(source)
    write_report(rows, stats, issues, examples)
    if stats["ads_eligible"] < MIN_PRODUCTS:
        # Niet publiceren: voorkomt dat een kapotte bronfeed de live feed leegmaakt.
        sys.exit(f"FOUT: slechts {stats['ads_eligible']} eligible producten (minimum {MIN_PRODUCTS}). Output niet gepubliceerd.")
    write_outputs(rows)
    if len(sys.argv) <= 1:
        os.remove(source)


if __name__ == "__main__":
    main()

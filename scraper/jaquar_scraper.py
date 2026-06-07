"""
Jaquar Faucets Scraper
======================
Scrapes one series at a time. Designed for GitHub Actions —
each run processes exactly one series and outputs a CSV chunk.

Usage:
    python jaquar_scraper.py --series "Fusion Prime"
    python jaquar_scraper.py --series-index 0
    python jaquar_scraper.py --list-series

Environment variables (optional overrides):
    SERIES_NAME   = "Fusion Prime"
    SERIES_INDEX  = "0"
"""

import requests
from bs4 import BeautifulSoup
import csv
import time
import re
import json
import logging
import os
import sys
import argparse
from dataclasses import dataclass, fields
from typing import Optional
import random

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL     = "https://www.jaquar.com"
API_ENDPOINT = "https://www.jaquar.com/en/shoppingcart/productdetails_attributechange"

# Delays (seconds)
DELAY_PAGES        = 3.0
DELAY_PRODUCTS     = 2.0
DELAY_VARIANTS     = 1.0

# Retry config
MAX_RETRIES        = 4
BACKOFF_BASE       = 8    # seconds — doubles each retry: 8, 16, 32, 64
TIMEOUT_SECS       = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         BASE_URL,
}

# ── Master Faucets Catalogue ──────────────────────────────────────────────────
# Format: (series_name, series_url)
FAUCET_SERIES = [
    ("Fusion Prime",         "https://www.jaquar.com/en/fusion-prime-faucets"),
    ("Florentine Prime",     "https://www.jaquar.com/en/florentine-prime"),
    ("Laguna",               "https://www.jaquar.com/en/bathroom-mixer-faucets"),
    ("Continental Prime",    "https://www.jaquar.com/en/continental-prime-faucets"),
    ("Blush Sensor Faucets", "https://www.jaquar.com/en/blush-sensor-faucets"),
    ("Queen's Prime",        "https://www.jaquar.com/en/queens-prime"),
    ("Arc",                  "https://www.jaquar.com/en/arc-faucets"),
    ("Kubix Prime",          "https://www.jaquar.com/en/jaquar-kubix-prime-faucet-range"),
    ("Opal Prime",           "https://www.jaquar.com/en/jaquar-opal-prime-faucet-range"),
    ("Ornamix Prime",        "https://www.jaquar.com/en/jaquar-ornamix-prime-faucet-range"),
    ("Alive",                "https://www.jaquar.com/en/jaquar-alive-faucet-range"),
    ("Queen's",              "https://www.jaquar.com/en/jaquar-queen-faucet-range"),
    ("Lyric",                "https://www.jaquar.com/en/jaquar-lyric-faucet-range"),
    ("Aria",                 "https://www.jaquar.com/en/jaquar-aria-faucet-range"),
    ("Vignette Prime",       "https://www.jaquar.com/en/jaquar-vignette-p-faucet-range"),
    ("Fusion",               "https://www.jaquar.com/en/jaquar-fusion-faucet-range"),
    ("Solo",                 "https://www.jaquar.com/en/jaquar-solo-faucet-range"),
    ("Clarion",              "https://www.jaquar.com/en/jaquar-clarion-faucet-range"),
    ("Floor Standing Mixer", "https://www.jaquar.com/en/floor-standing-bath-tub-mixer"),
    ("Sensor Faucets",       "https://www.jaquar.com/en/jaquar-sensor-faucets"),
    ("New Age Pressmatic",   "https://www.jaquar.com/en/new-age-elbow-foot-pressmatic-faucets"),
    ("Pressmatic Taps",      "https://www.jaquar.com/en/jaquar-pressmatic-auto-faucet-range"),
    ("Medi Series",          "https://www.jaquar.com/en/jaquar-medical-faucet-range"),
    ("Spout Operating Tap",  "https://www.jaquar.com/en/jaquar-spout-operating-faucet-tap-range"),
    ("Bathtub Spouts",       "https://www.jaquar.com/en/jaquar-bathtub-spouts"),
    ("Allied",               "https://www.jaquar.com/en/allied-bathfittings-mechanisms"),
]

# ── Data Model ────────────────────────────────────────────────────────────────
@dataclass
class ProductRow:
    category:           str
    series:             str
    parent_product_id:  str
    parent_name:        str
    is_parent:          str        # "Parent" or "Variant"
    variant_id:         str
    product_name:       str
    product_code:       str
    color:              str
    image_url:          str
    mrp:                str
    description:        str
    product_url:        str
    bullet_points:      str

CSV_COLUMNS = [f.name for f in fields(ProductRow)]

# ── Retry-aware HTTP helpers ───────────────────────────────────────────────────

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(BASE_URL + "/en/", timeout=15)
        time.sleep(1)
    except Exception:
        pass
    return s


def get_with_retry(session: requests.Session, url: str) -> requests.Response:
    """GET with exponential backoff. Raises on final failure."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=TIMEOUT_SECS)
            if resp.status_code == 429:
                wait = BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 3)
                log.warning("    Rate limited (429). Waiting %.0fs before retry %d/%d",
                            wait, attempt, MAX_RETRIES)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.Timeout:
            wait = BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 3)
            log.warning("    Timeout on attempt %d/%d. Waiting %.0fs...",
                        attempt, MAX_RETRIES, wait)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(wait)
        except requests.HTTPError as e:
            if attempt == MAX_RETRIES:
                raise
            wait = BACKOFF_BASE * attempt
            log.warning("    HTTP error %s attempt %d/%d. Waiting %.0fs...",
                        e, attempt, MAX_RETRIES, wait)
            time.sleep(wait)
    raise RuntimeError(f"All {MAX_RETRIES} retries failed for {url}")


def post_with_retry(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """POST with exponential backoff."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(url, timeout=TIMEOUT_SECS, **kwargs)
            if resp.status_code == 429:
                wait = BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 3)
                log.warning("    Rate limited (429). Waiting %.0fs before retry %d/%d",
                            wait, attempt, MAX_RETRIES)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.Timeout:
            wait = BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 3)
            log.warning("    Timeout on attempt %d/%d. Waiting %.0fs...",
                        attempt, MAX_RETRIES, wait)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(wait)
        except requests.HTTPError as e:
            if attempt == MAX_RETRIES:
                raise
            wait = BACKOFF_BASE * attempt
            log.warning("    HTTP error %s attempt %d/%d. Waiting %.0fs...",
                        e, attempt, MAX_RETRIES, wait)
            time.sleep(wait)
    raise RuntimeError(f"All {MAX_RETRIES} POST retries failed for {url}")


def get_soup(session: requests.Session, url: str) -> BeautifulSoup:
    resp = get_with_retry(session, url)
    return BeautifulSoup(resp.text, "lxml")

# ── Utility ───────────────────────────────────────────────────────────────────

def clean_price(raw: str) -> str:
    if not raw:
        return ""
    return re.sub(r"[^\d.]", "", raw.replace(",", ""))


def strip_color_suffix(name: str) -> str:
    parts = name.rsplit(" - ", 1)
    return parts[0].strip() if len(parts) > 1 else name.strip()


def build_bullet_points(series: str, sku: str, color: str) -> str:
    return ", ".join(p for p in [series, sku, color] if p)


def safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name).strip("_")

# ── Pagination ────────────────────────────────────────────────────────────────

def get_all_listing_page_urls(session: requests.Session, base_url: str):
    """Returns (list_of_page_urls, first_page_soup)."""
    soup = get_soup(session, base_url)
    pages = [base_url]

    total_tag = soup.select_one("li.total-summary")
    if total_tag:
        text = total_tag.get_text(strip=True)
        log.info("    Pagination info: %s", text)
        m = re.search(r"Page\s+\d+\s+of\s+(\d+)", text)
        if m:
            total_pages = int(m.group(1))
            for p in range(2, total_pages + 1):
                sep = "&" if "?" in base_url else "?"
                pages.append(f"{base_url}{sep}pageNumber={p}")

    # Fallback: anchor tags with pageNumber
    if len(pages) == 1:
        for a in soup.select("a[href*='pageNumber']"):
            href = a["href"]
            full = href if href.startswith("http") else BASE_URL + href
            if full not in pages:
                pages.append(full)

    log.info("    Listing pages: %d", len(pages))
    return pages, soup

# ── Listing parsing ───────────────────────────────────────────────────────────

def extract_products_from_soup(soup: BeautifulSoup) -> list[dict]:
    products = []
    for item in soup.select("div.product-item[data-productid]"):
        pid  = item["data-productid"].strip()
        link = item.select_one("div.details h3.product-title a")
        if not link:
            continue
        href = link["href"]
        full_url = href if href.startswith("http") else BASE_URL + href
        products.append({
            "parent_id": pid,
            "name":      link.get_text(strip=True),
            "url":       full_url,
        })
    return products


def get_products_for_series(session: requests.Session, series_url: str) -> list[dict]:
    pages_urls, first_soup = get_all_listing_page_urls(session, series_url)
    all_products = []
    seen_ids = set()

    for i, page_url in enumerate(pages_urls):
        if i == 0:
            soup = first_soup
        else:
            time.sleep(DELAY_PAGES)
            log.info("    Fetching listing page %d: %s", i + 1, page_url)
            soup = get_soup(session, page_url)

        items = extract_products_from_soup(soup)
        for p in items:
            if p["parent_id"] not in seen_ids:
                seen_ids.add(p["parent_id"])
                all_products.append(p)

        log.info("    Page %d: %d products (total: %d)", i + 1, len(items), len(all_products))

    return all_products

# ── Product scraping ──────────────────────────────────────────────────────────

def scrape_product(
    session:  requests.Session,
    product:  dict,
    series:   str,
) -> list[ProductRow]:
    url  = product["url"]
    soup = get_soup(session, url)

    # CSRF token
    token_input = soup.select_one('input[name="__RequestVerificationToken"]')
    csrf_token  = token_input["value"] if token_input else ""

    # Page product ID — try multiple patterns
    page_product_id = None
    sku_div = soup.select_one("div.descrpt-value[id^='sku-']")
    if sku_div:
        page_product_id = sku_div["id"].split("-")[-1]
    if not page_product_id:
        qty_input = soup.select_one("input[name*='EnteredQuantity']")
        if qty_input:
            m = re.search(r"addtocart_(\d+)\.", qty_input.get("name", ""))
            if m:
                page_product_id = m.group(1)
    if not page_product_id:
        form = soup.select_one("form#product-details-form, form[action*='addtocart']")
        if form:
            m = re.search(r"/(\d+)", form.get("action", ""))
            if m:
                page_product_id = m.group(1)
    if not page_product_id:
        page_product_id = product["parent_id"]

    # Description (stable across variants)
    desc_div = soup.select_one("div.descrpt-value[id^='short-description-']")
    page_description = desc_div.get_text(strip=True) if desc_div else ""

    # Color swatches
    swatch_ul    = soup.select_one("ul.attribute-squares.image-squares")
    attribute_id = None
    swatches     = []

    if swatch_ul:
        attribute_id = swatch_ul.get("id", "").replace("image-squares-", "")
        for li in swatch_ul.select("li[data-attr-value]"):
            radio       = li.select_one("input[type='radio']")
            is_selected = "selected-value" in li.get("class", [])
            swatches.append({
                "attr_value_id": li["data-attr-value"],
                "title":         radio["title"] if radio else "",
                "is_selected":   is_selected,
            })

    # ── No swatches: standalone product ───────────────────────────────────────
    if not swatches:
        name_h1  = soup.select_one("h1[id^='product-name-']")
        sku_el   = soup.select_one("div.descrpt-value[id^='sku-']")
        price_el = soup.select_one("span.price.actual-price, span.actual-price")
        img_el   = soup.select_one("img.cloudzoom, img#main-product-img, div.picture img")

        p_name  = name_h1.get_text(strip=True) if name_h1 else product["name"]
        p_sku   = sku_el.get_text(strip=True)  if sku_el  else ""
        p_price = clean_price(price_el.get_text(strip=True)) if price_el else ""
        p_image = ""
        if img_el:
            p_image = img_el.get("src") or img_el.get("data-src", "")
            if p_image and not p_image.startswith("http"):
                p_image = BASE_URL + p_image

        color_span = soup.select_one("span.value")
        p_color = color_span.get_text(strip=True) if color_span else ""

        return [ProductRow(
            category          = "Faucets",
            series            = series,
            parent_product_id = product["parent_id"],
            parent_name       = strip_color_suffix(p_name),
            is_parent         = "Parent",
            variant_id        = page_product_id,
            product_name      = p_name,
            product_code      = p_sku,
            color             = p_color,
            image_url         = p_image,
            mrp               = p_price,
            description       = page_description or p_name,
            product_url       = url,
            bullet_points     = build_bullet_points(series, p_sku, p_color),
        )]

    # ── With swatches: API call for every swatch ───────────────────────────────
    rows        = []
    parent_name = strip_color_suffix(product["name"])
    first_row   = True

    for swatch in swatches:
        time.sleep(DELAY_VARIANTS)
        data = fetch_variant_api(
            session       = session,
            product_id    = page_product_id,
            attribute_id  = attribute_id,
            attr_value_id = swatch["attr_value_id"],
            csrf_token    = csrf_token,
        )
        if data is None:
            log.warning("      API returned None for swatch %s (%s)",
                        swatch["attr_value_id"], swatch["title"])
            continue

        v_name  = data.get("productName", "")
        v_sku   = data.get("sku", "")
        v_price = clean_price(data.get("price", ""))
        v_image = data.get("pictureDefaultSizeUrl") or data.get("pictureFullSizeUrl", "")
        v_desc  = data.get("productShortDescription", "") or page_description
        v_color = swatch["title"]

        if first_row:
            parent_name = strip_color_suffix(v_name) if v_name else parent_name

        rows.append(ProductRow(
            category          = "Faucets",
            series            = series,
            parent_product_id = product["parent_id"],
            parent_name       = parent_name,
            is_parent         = "Parent" if first_row else "Variant",
            variant_id        = str(data.get("productId", page_product_id)),
            product_name      = v_name,
            product_code      = v_sku,
            color             = v_color,
            image_url         = v_image,
            mrp               = v_price,
            description       = v_desc,
            product_url       = url,
            bullet_points     = build_bullet_points(series, v_sku, v_color),
        ))
        log.info("      [%s] %s | %s | %s",
                 "Parent " if first_row else "Variant",
                 v_name, v_sku, v_color)
        first_row = False

    return rows

# ── API call ──────────────────────────────────────────────────────────────────

def fetch_variant_api(
    session:       requests.Session,
    product_id:    str,
    attribute_id:  str,
    attr_value_id: str,
    csrf_token:    str,
) -> Optional[dict]:
    params = {
        "productId":                   product_id,
        "validateAttributeConditions": "False",
        "loadPicture":                 "True",
    }
    form_data = {
        f"product_attribute_{attribute_id}": attr_value_id,
        f"addtocart_{product_id}.EnteredQuantity": "1",
        "__RequestVerificationToken": csrf_token,
    }
    try:
        resp = post_with_retry(session, API_ENDPOINT, params=params, data=form_data)
        return resp.json()
    except json.JSONDecodeError:
        log.error("      JSON decode error")
    except Exception as e:
        log.error("      API error: %s", e)
    return None

# ── CSV output ────────────────────────────────────────────────────────────────

def write_series_csv(rows: list[ProductRow], series_name: str, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.join(output_dir, f"{safe_filename(series_name)}.csv")
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: getattr(row, col) for col in CSV_COLUMNS})
    log.info("CSV written: %s (%d rows)", filename, len(rows))
    return filename

# ── Summary JSON (for GitHub Actions job summary) ─────────────────────────────

def write_summary(series_name: str, rows: int, products: int, failed: int, output_file: str):
    summary = {
        "series":      series_name,
        "rows":        rows,
        "products":    products,
        "failed":      failed,
        "output_file": output_file,
    }
    with open("scrape_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    log.info("Summary: %s", json.dumps(summary))

# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Jaquar faucets series scraper")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--series",       type=str, help="Series name e.g. 'Fusion Prime'")
    group.add_argument("--series-index", type=int, help="0-based index into FAUCET_SERIES")
    group.add_argument("--list-series",  action="store_true", help="List all series and exit")
    parser.add_argument("--output-dir",  type=str, default="data", help="Output directory for CSVs")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.list_series:
        for i, (name, url) in enumerate(FAUCET_SERIES):
            print(f"{i:02d}  {name:<25}  {url}")
        return

    # Resolve series from args or environment
    series_name = args.series or os.environ.get("SERIES_NAME")
    series_index_str = os.environ.get("SERIES_INDEX")

    if series_index_str is not None and not series_name:
        args.series_index = int(series_index_str)

    if args.series_index is not None:
        if args.series_index >= len(FAUCET_SERIES):
            log.error("series-index %d out of range (max %d)", args.series_index, len(FAUCET_SERIES) - 1)
            sys.exit(1)
        series_name, series_url = FAUCET_SERIES[args.series_index]
    elif series_name:
        match = [(n, u) for n, u in FAUCET_SERIES if n.lower() == series_name.lower()]
        if not match:
            log.error("Series '%s' not found. Run --list-series to see options.", series_name)
            sys.exit(1)
        series_name, series_url = match[0]
    else:
        log.error("Provide --series, --series-index, or set SERIES_NAME env var.")
        parser.print_help()
        sys.exit(1)

    log.info("=" * 60)
    log.info("Series: %s", series_name)
    log.info("URL:    %s", series_url)
    log.info("=" * 60)

    session  = make_session()
    all_rows = []
    failed   = 0

    # Get all products
    products = get_products_for_series(session, series_url)
    log.info("Products found: %d", len(products))

    for idx, product in enumerate(products, 1):
        log.info("[%d/%d] %s (id=%s)", idx, len(products), product["name"], product["parent_id"])
        try:
            time.sleep(DELAY_PRODUCTS)
            rows = scrape_product(session, product, series_name)
            all_rows.extend(rows)
            log.info("  → %d variant(s)", len(rows))
        except Exception as e:
            log.error("  ✗ Failed after all retries: %s", e)
            failed += 1
            # Extra cool-down after a failure — server may be throttling
            log.info("  Cooling down 30s after failure...")
            time.sleep(30)

    # Write output
    out_file = write_series_csv(all_rows, series_name, args.output_dir)
    write_summary(series_name, len(all_rows), len(products), failed, out_file)

    log.info("=" * 60)
    log.info("DONE. %d rows | %d products | %d failed", len(all_rows), len(products), failed)

    # Exit code 1 if >20% failed — signals GitHub Actions to mark job failed
    if len(products) > 0 and (failed / len(products)) > 0.20:
        log.error("Too many failures (%.0f%%). Marking run as failed.", 100 * failed / len(products))
        sys.exit(1)


if __name__ == "__main__":
    main()

# Jaquar Faucets Scraper

Scrapes all products across all 26 Jaquar Faucet series, capturing every color variant via Jaquar's internal attribute-change API.

## Output

Each series produces a CSV in `/data/` with these columns:

| Column | Description |
|---|---|
| `category` | Always "Faucets" |
| `series` | e.g. "Fusion Prime" |
| `parent_product_id` | Product ID from listing page |
| `parent_name` | Base product name without color suffix |
| `is_parent` | "Parent" (first variant) or "Variant" |
| `variant_id` | Product ID for this specific color variant |
| `product_name` | Full product name including color |
| `product_code` | SKU code e.g. FUP-GBP-29011BPM |
| `color` | Color name e.g. "Gold Bright PVD" or "Lever: Gold Bright PVD \| Body: Gold Matt PVD" |
| `image_url` | Direct image URL (960px version) |
| `mrp` | Price in INR (numeric, no symbols) |
| `description` | Product short description |
| `product_url` | Full Jaquar product page URL |
| `bullet_points` | "Series, SKU, Color" — ready for listing |

## Running on GitHub Actions

### Option 1: Run all 26 series automatically (recommended)

1. Go to **Actions** tab
2. Select **"Scrape All Faucets (Orchestrator)"**
3. Click **"Run workflow"**
4. Leave `start_index` as `0` for a full run

Each series runs sequentially. If one fails, subsequent series are skipped — re-run the orchestrator with `start_index` set to the failed series index to resume.

### Option 2: Scrape a single series manually

1. Go to **Actions** → **"Scrape One Series"**
2. Set `series_index` (see table below) or `series_name`

### Series Index Reference

| Index | Series Name |
|---|---|
| 0 | Fusion Prime |
| 1 | Florentine Prime |
| 2 | Laguna |
| 3 | Continental Prime |
| 4 | Blush Sensor Faucets |
| 5 | Queen's Prime |
| 6 | Arc |
| 7 | Kubix Prime |
| 8 | Opal Prime |
| 9 | Ornamix Prime |
| 10 | Alive |
| 11 | Queen's |
| 12 | Lyric |
| 13 | Aria |
| 14 | Vignette Prime |
| 15 | Fusion |
| 16 | Solo |
| 17 | Clarion |
| 18 | Floor Standing Mixer |
| 19 | Sensor Faucets |
| 20 | New Age Pressmatic |
| 21 | Pressmatic Taps |
| 22 | Medi Series |
| 23 | Spout Operating Tap |
| 24 | Bathtub Spouts |
| 25 | Allied |

## Running locally

```bash
pip install -r scraper/requirements.txt

# List all series
python scraper/jaquar_scraper.py --list-series

# Run one series by name
python scraper/jaquar_scraper.py --series "Fusion Prime"

# Run one series by index
python scraper/jaquar_scraper.py --series-index 2

# Output goes to ./data/<series_name>.csv
```

## How it works

1. Fetches all listing pages for a series (handles pagination via `?pageNumber=N`)
2. For each product page, extracts the CSRF token and all color swatches
3. POSTs to Jaquar's internal API (`/productdetails_attributechange`) for **every** swatch — including the default — to get accurate SKU, image, price, and name
4. Products with no swatches are captured directly from the page as standalone products
5. Includes exponential backoff (8s → 16s → 32s → 64s) on timeouts and rate limits
6. Each series CSV is committed to the repo immediately after completion

## Notes

- Delays: 2s between products, 1s between variants, 3s between pages
- Timeout: 30s per request, 4 retries with exponential backoff
- A 30s cool-down is applied after any failed product
- Exit code 1 (marks GitHub job failed) if >20% of products fail

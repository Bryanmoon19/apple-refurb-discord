# 🍎 Apple Refurb Monitor → Discord v2.0

Fork of the Reddit /r/macmini idea, rebuilt for Discord with deduplication, rich embeds, and deal grading (inspired by [FBM Sniper](https://github.com/ethanashi/fbm-sniper-community)).

## What it does
- Runs every **10 minutes** via GitHub Actions (free)
- Scrapes any Apple refurb page you configure
- **Grades deals A–F** based on % off retail price
- **Junk-filters** accessories, cables, cases, etc.
- Sends **Discord embeds** only when a *new* item matches your filters
- **Dual webhook routing**: all deals → one channel, A/B hot deals → another
- Remembers what it already told you (10 K item cap, no unbounded growth)

## Deal Grades

| Grade | Price vs Retail | Urgency |
|---|---|---|
| **A** | ≤ 80% retail (≥ 20% off) | 🟢 **HOT** — sent to both webhooks |
| **B** | 81–90% retail (10–20% off) | 🔵 Good — sent to both webhooks |
| **C** | 91–100% retail (0–10% off) | 🟡 Fair — primary webhook only |
| **D** | 101–130% retail (overpriced) | 🟠 Skip — primary webhook only |
| **F** | > 130% retail or junk | 🔴 Auto-skipped |

Discord embeds are **colour-coded** by grade and show savings amount + discount %.

## Filters (pick one or both)

| Variable | Example | Effect |
|----------|---------|--------|
| `PRICE_CAP` | `600` | Only alert if price ≤ $600 |
| `RAM_SIZE` | `24gb` | Exact RAM match |
| `RAM_SIZE` | `16gb,24gb` | Multiple exact matches |
| `RAM_SIZE` | `>24gb` | More than 24GB |
| `RAM_SIZE` | `>=24gb` | 24GB or more |
| `RAM_SIZE` | `<32gb` | Less than 32GB |
| `RAM_SIZE` | `16gb-32gb` | Between 16GB and 32GB (inclusive) |

Set both `PRICE_CAP` and `RAM_SIZE` and alerts only fire when **both** match.

## Setup

1. **Fork this repo** to your GitHub account
2. **Create Discord webhooks** in your server:
   - Server Settings → Integrations → Webhooks → New Webhook → Copy URL
   - Create **two** webhooks: one for `#apple-deals` (all), one for `#apple-hot-deals` (A/B only)
3. **Add secrets** in your fork:
   - Settings → Secrets and variables → Actions → **New repository secret**
   - Name: `DISCORD_WEBHOOK` → value: your "all deals" webhook URL
   - Name: `DISCORD_WEBHOOK_HOT` → value: your "hot deals" webhook URL *(optional)*
4. *(Optional)* **Set filters** as repo variables:
   - Settings → Secrets and variables → Actions → **Variables** tab
   - `PRICE_CAP` → e.g. `600`
   - `RAM_SIZE` → e.g. `24gb` or `>24gb`
   - `APPLE_URL` → any refurb page (default is Mac mini)
   - `RETAIL_PRICES` → override retail table (see below)
5. **Done.** The cron starts on its own.

## Retail Price Override

The bot has a built-in retail price table for common configs. If Apple changes prices or you want to add a rare config, set the `RETAIL_PRICES` variable:

```
mac mini m4 16gb 256gb=599,mac mini m4 pro 24gb 512gb=1399
```

Format: `key=price,key=price` (all lowercase, spaces between words, `gb` suffix for RAM/storage).

## Want other Apple products?

Change `APPLE_URL`:
- Mac Studio: `https://www.apple.com/shop/refurbished/mac/mac-studio`
- MacBook Air: `https://www.apple.com/shop/refurbished/mac/macbook-air`
- MacBook Pro: `https://www.apple.com/shop/refurbished/mac/macbook-pro`

## Junk Filter

These keywords auto-reject a listing (prevents accessory spam):
- `cable`, `charger`, `adapter`, `case`, `cover`, `stand`
- `keyboard`, `mouse`, `trackpad`, `display`, `monitor`
- `dock`, `hub`, `accessory`, `screen guard`, `tempered glass`
- `for parts`, `as-is`, `no funciona`, `reparar`

## Running Locally (with browser auto-open)

```bash
# Install Python 3.12+
export DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."
export DISCORD_WEBHOOK_HOT="https://discord.com/api/webhooks/..."  # optional
export RAM_SIZE=">24gb"
export OPEN_BROWSER=true  # auto-opens Apple page on A/B deals
python monitor.py
```

## How it works (FBM Sniper lessons applied)

| Feature | FBM Sniper Pattern | How we use it |
|---|---|---|
| Price-band scoring | `A ≤80%`, `B ≤90%`, `C ≤100%`, `D ≤130%` | Same grades, colour-coded Discord embeds |
| Junk regex kill-list | Reject cases, cables, broken items | Same list, adapted for Apple accessories |
| Dual webhook routing | `All` / `Buy Now` / `Maybe` channels | `All deals` + `Hot deals (A/B)` |
| Seen-ID cap (10 K) | Prevents unbounded `seen_ids.json` growth | Same cap, keeps `seen.json` lean |
| Auto-open browser | `spawn("open", [url])` on hot deals | Only when `OPEN_BROWSER=true` locally |

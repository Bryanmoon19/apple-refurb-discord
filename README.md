# 🍎 Apple Refurb Monitor → Discord

Fork of the Reddit /r/macmini idea, rebuilt for Discord with deduplication and rich embeds.

## What it does
- Runs every **10 minutes** via GitHub Actions (free)
- Scrapes any Apple refurb page you configure
- Sends a **Discord embed** only when a *new* item matches your filters
- Remembers what it already told you so it **doesn't spam**

## Filters (pick one or both)
| Variable | Example | Effect |
|----------|---------|--------|
| `PRICE_CAP` | `600` | Only alert if price ≤ $600 |
| `RAM_SIZE` | `24gb` | Only alert if RAM matches |
| `RAM_SIZE` | `16gb,24gb` | Comma-list for multiple RAM sizes |

Set both and alerts only fire when **both** match.

## Setup

1. **Fork this repo** to your GitHub account
2. **Create a Discord webhook** in your server:
   - Server Settings → Integrations → Webhooks → New Webhook → Copy URL
3. **Add the secret** in your fork:
   - Settings → Secrets and variables → Actions → **New repository secret**
   - Name: `DISCORD_WEBHOOK`
   - Value: the webhook URL from step 2
4. *(Optional)* **Set filters** as repo variables:
   - Settings → Secrets and variables → Actions → **Variables** tab
   - `PRICE_CAP` → e.g. `600`
   - `RAM_SIZE` → e.g. `24gb` (or `16gb,24gb`)
   - `APPLE_URL` → any refurb page (default is Mac mini)
5. **Done.** The cron starts on its own.

## Want other Apple products?
Change `APPLE_URL`:
- Mac Studio: `https://www.apple.com/shop/refurbished/mac/mac-studio`
- MacBook Air: `https://www.apple.com/shop/refurbished/mac/macbook-air`
- iPad: `https://www.apple.com/shop/refurbished/ipad`

## File map
| File | Purpose |
|------|---------|
| `monitor.py` | Scraper + Discord sender |
| `.github/workflows/monitor.yml` | GitHub Actions cron |
| `seen.json` | Persistent dedup cache (managed by Actions cache) |

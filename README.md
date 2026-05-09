# 🍎 Apple Refurb Monitor → Discord

Fork of the Reddit /r/macmini idea, rebuilt for Discord with deduplication and rich embeds.

## What it does
- Runs every **10 minutes** via GitHub Actions (free)
- Scrapes any Apple refurb page you configure
- Sends a **Discord embed** only when a *new* item drops at or below your price cap
- Remembers what it already told you so it **doesn't spam**

## Setup

1. **Fork this repo** to your GitHub account
2. **Create a Discord webhook** in your server:
   - Server Settings → Integrations → Webhooks → New Webhook → Copy URL
3. **Add the secret** in your fork:
   - Settings → Secrets and variables → Actions → **New repository secret**
   - Name: `DISCORD_WEBHOOK`
   - Value: the webhook URL from step 2
4. *(Optional)* **Set variables** for customization:
   - `PRICE_CAP` — max price you want alerts for (default `$600`)
   - `APPLE_URL` — any Apple refurb page (default `…/mac-mini`)
5. **Done.** The cron starts automatically.

## Want other Apple products?
Just change `APPLE_URL`:
- Mac Studio: `https://www.apple.com/shop/refurbished/mac/mac-studio`
- MacBook Air: `https://www.apple.com/shop/refurbished/mac/macbook-air`
- iPad: `https://www.apple.com/shop/refurbished/ipad`

## File map
| File | Purpose |
|------|---------|
| `monitor.py` | Scraper + Discord sender |
| `.github/workflows/monitor.yml` | GitHub Actions cron |
| `seen.json` | Persistent dedup cache (managed by Actions cache) |

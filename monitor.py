"""
Apple Refurb Price Monitor → Discord
Checks Apple's refurb page and sends Discord embeds when new deals appear.
Features: deduplication, embed formatting, configurable category/price.
"""
import os
import re
import hashlib
import json
import urllib.request
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────────
APPLE_URL = os.getenv("APPLE_URL", "https://www.apple.com/shop/refurbished/mac/mac-mini")
PRICE_CAP = float(os.getenv("PRICE_CAP", "600"))
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
STATE_FILE = os.getenv("STATE_FILE", "seen.json")

# ── Helpers ─────────────────────────────────────────────────────────
def fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def item_id(title: str, price: float, part: str = "") -> str:
    return hashlib.md5(f"{title}:{price}:{part}".encode()).hexdigest()


# ── Parsing ─────────────────────────────────────────────────────────
def parse_listings(html: str) -> list[dict]:
    """
    Apple refurb pages embed product tiles as JSON inside <script> tags.
    Each tile has filters.dimensions, currentPrice.raw_amount, and a title
    in the adjacent sibling object.
    """
    listings = []

    # Strategy 1: Find the big page data JSON that contains "tiles"
    # Apple dumps a JSON array/object in a script tag near the bottom
    for script in re.findall(r"<script[^>]*>(.*?)\s*</script>", html, re.S):
        if len(script) < 5000:
            continue
        if "tiles" not in script and "refurbProduct" not in script:
            continue
        try:
            # The script might be pure JSON or JS assignment
            json_text = script
            if json_text.startswith("window.") or "=" in json_text[:200]:
                # Extract JSON after first = or ({
                m = re.search(r"(\{.*\})", json_text, re.S)
                if m:
                    json_text = m.group(1)
            data = json.loads(json_text)

            # Drill into data to find tiles
            tiles = _extract_tiles(data)
            for tile in tiles:
                item = _tile_to_listing(tile)
                if item:
                    listings.append(item)
            if listings:
                return listings
        except Exception:
            continue

    # Strategy 2: Regex scrape on the raw HTML near refurbProduct markers
    refurb_spots = [m.start() for m in re.finditer(r'"refurbProduct"', html)]
    seen_ids = set()
    for idx in refurb_spots:
        window = html[max(0, idx - 3000):idx + 1500]
        title_match = re.search(r'"(?:title|displayName|name)":"([^"]+)"', window)
        price_match = re.search(r'"raw_amount":"([\d.]+)"', window)
        part_match = re.search(r'"partNumber":"([^"]+)"', window)
        if title_match and price_match:
            title = title_match.group(1).strip()
            price = float(price_match.group(1))
            part = part_match.group(1) if part_match else ""
            iid = item_id(title, price, part)
            if iid not in seen_ids:
                seen_ids.add(iid)
                listings.append({
                    "title": title,
                    "price": price,
                    "part": part,
                })
    return listings


def _extract_tiles(data):
    """Recursively find arrays named 'tiles' inside the page JSON."""
    if isinstance(data, list):
        for item in data:
            yield from _extract_tiles(item)
    elif isinstance(data, dict):
        if "tiles" in data and isinstance(data["tiles"], list):
            yield from data["tiles"]
            return  # found it, stop recursing this branch
        for v in data.values():
            yield from _extract_tiles(v)
    return


def _tile_to_listing(tile: dict) -> dict | None:
    """Convert a single Apple tile object into our listing dict."""
    try:
        dims = tile.get("filters", {}).get("dimensions", {})
        # Title is often built from dimensionColor + dimensionCapacity + product name
        title = tile.get("title", "")
        if not title:
            parts = []
            model = dims.get("refurbClearModel", "")
            color = dims.get("dimensionColor", "")
            capacity = dims.get("dimensionCapacity", "")
            year = dims.get("dimensionRelYear", "")
            screen = dims.get("dimensionScreensize", "")
            if model:
                parts.append(model.replace("macbookair", "MacBook Air").replace("macmini", "Mac mini").replace("macstudio", "Mac Studio").replace("macbookpro", "MacBook Pro"))
            if screen:
                parts.append(screen)
            if year:
                parts.append(year)
            if color:
                parts.append(color)
            if capacity:
                parts.append(capacity)
            title = " ".join(parts)
            if not title:
                title = "Apple Refurbished Product"

        # Price
        price_info = tile.get("price", {})
        raw = price_info.get("currentPrice", {}).get("raw_amount")
        if raw is None:
            raw = price_info.get("previousPrice", {}).get("raw_amount")
        if raw is None:
            return None
        price = float(raw)

        part = tile.get("partNumber", "")
        return {"title": title, "price": price, "part": part}
    except Exception:
        return None


# ── Discord ─────────────────────────────────────────────────────────
def send_discord(items: list[dict], url: str, cap: float) -> None:
    if not DISCORD_WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK not set")

    embeds = []
    for it in items:
        embed = {
            "title": it["title"][:256],  # Discord limit
            "url": url,
            "color": 0x5865F2,
            "fields": [
                {"name": "Price", "value": f"${it['price']:.2f}", "inline": True},
                {"name": "Your Cap", "value": f"${cap:.0f}", "inline": True},
                {"name": "Status", "value": "✅ Under cap!", "inline": True},
            ],
            "footer": {
                "text": "Apple Refurb Monitor • "
                + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            },
        }
        if it.get("part"):
            embed["fields"].insert(0, {"name": "Part #", "value": it["part"], "inline": True})
        embeds.append(embed)

    payload = {"embeds": embeds[:10]}  # Discord limit 10 embeds per message

    req = urllib.request.Request(
        DISCORD_WEBHOOK,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "apple-refurb-bot/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"Discord returned {resp.status}")


def send_discord_no_deals(url: str, cap: float) -> None:
    """Optional: send a heartbeat when no deals found (disabled by default)."""
    pass  # Uncomment below if you want hourly status pings
    # embed = {
    #     "title": "No new deals right now",
    #     "url": url,
    #     "color": 0x95a5a6,
    #     "description": f"Checked Apple's refurb page. Nothing at or below ${cap:.0f} yet.",
    #     "footer": {"text": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")},
    # }
    # req = urllib.request.Request(
    #     DISCORD_WEBHOOK,
    #     data=json.dumps({"embeds": [embed]}).encode(),
    #     headers={"Content-Type": "application/json"},
    # )
    # urllib.request.urlopen(req, timeout=30)


# ── State ───────────────────────────────────────────────────────────
def load_seen() -> set[str]:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set[str]) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(list(seen), f)


# ── Main ────────────────────────────────────────────────────────────
def main():
    if not DISCORD_WEBHOOK:
        print("❌ DISCORD_WEBHOOK env var missing")
        return 1

    print(f"Fetching {APPLE_URL} …")
    html = fetch_html(APPLE_URL)

    all_items = parse_listings(html)
    print(f"Found {len(all_items)} items on page")

    # Filter by target URL content (e.g., only Mac mini even if page has others)
    keyword = os.path.basename(APPLE_URL).replace("-", " ").lower()
    filtered = [it for it in all_items if keyword in it["title"].lower()]
    if not filtered:
        filtered = all_items  # if keyword filter is too aggressive, show all

    deals = [it for it in filtered if it["price"] <= PRICE_CAP]
    print(f"{len(deals)} items under ${PRICE_CAP:.0f}")

    seen = load_seen()
    new_deals = []
    for it in deals:
        iid = item_id(it["title"], it["price"], it.get("part", ""))
        if iid not in seen:
            new_deals.append(it)
            seen.add(iid)

    if new_deals:
        print(f"🚨 {len(new_deals)} new deal(s) — pinging Discord")
        send_discord(new_deals, APPLE_URL, PRICE_CAP)
        save_seen(seen)
    else:
        print("No new deals. Discord stays quiet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

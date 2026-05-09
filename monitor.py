"""
Apple Refurb Monitor → Discord
Checks Apple's refurb page and sends Discord embeds when new deals appear.
Features: deduplication, embed formatting, configurable RAM/price filters.
"""
import os
import re
import hashlib
import json
import urllib.request
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────────
APPLE_URL = os.getenv("APPLE_URL", "https://www.apple.com/shop/refurbished/mac/mac-mini")
PRICE_CAP = os.getenv("PRICE_CAP", "")
RAM_SIZE = os.getenv("RAM_SIZE", "")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
STATE_FILE = os.getenv("STATE_FILE", "seen.json")


# ── RAM parsing ─────────────────────────────────────────────────────
def _ram_to_gb(raw: str) -> int | None:
    """Convert '24gb' or '16 GB' → integer GB."""
    m = re.search(r"(\d+)", raw.lower().replace(" ", ""))
    return int(m.group(1)) if m else None


def _parse_ram_config(raw: str) -> dict:
    """
    Parse RAM filter expression.
    Returns dict with keys:
        mode: 'exact' | 'min' | 'max' | 'range'
        values: list[str]          (for exact mode)
        min_gb: int | None         (for min / range)
        max_gb: int | None         (for max / range)
    Syntax:
        '24gb'          → exact 24gb
        '16gb,24gb'     → exact 16gb OR 24gb
        '>24gb'         → more than 24gb
        '>=24gb'        → 24gb or more
        '<32gb'         → less than 32gb
        '16gb-32gb'     → between 16 and 32gb (inclusive)
    """
    result = {"mode": "exact", "values": [], "min_gb": None, "max_gb": None}
    if not raw:
        return result

    raw = raw.strip().lower().replace(" ", "")

    # Range syntax: 16gb-32gb
    range_match = re.match(r"^(\d+)gb?\s*-\s*(\d+)gb?", raw)
    if range_match:
        result["mode"] = "range"
        result["min_gb"] = int(range_match.group(1))
        result["max_gb"] = int(range_match.group(2))
        return result

    # Comparison syntax: >24gb, >=24gb, <32gb, <=32gb
    comp_match = re.match(r"^(>=?|<=?)(\d+)gb?", raw)
    if comp_match:
        op, val = comp_match.group(1), int(comp_match.group(2))
        if op == ">":
            result["mode"] = "min"
            result["min_gb"] = val + 1  # strictly greater
        elif op == ">=":
            result["mode"] = "min"
            result["min_gb"] = val
        elif op == "<":
            result["mode"] = "max"
            result["max_gb"] = val - 1  # strictly less
        elif op == "<=":
            result["mode"] = "max"
            result["max_gb"] = val
        return result

    # Exact match syntax: 24gb or 16gb,24gb
    result["mode"] = "exact"
    result["values"] = [x.strip() for x in raw.split(",") if x.strip()]
    return result


RAM_CONFIG = _parse_ram_config(RAM_SIZE)


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
    Each tile has filters.dimensions, currentPrice.raw_amount, RAM, storage.
    """
    listings = []

    # Strategy 1: Find the big page data JSON that contains "tiles"
    for script in re.findall(r"<script[^>]*>(.*?)\s*</script>", html, re.S):
        if len(script) < 5000:
            continue
        if "tiles" not in script and "refurbProduct" not in script:
            continue
        try:
            json_text = script
            if json_text.startswith("window.") or "=" in json_text[:200]:
                m = re.search(r"(\{.*\})", json_text, re.S)
                if m:
                    json_text = m.group(1)
            data = json.loads(json_text)
            tiles = _extract_tiles(data)
            for tile in tiles:
                item = _tile_to_listing(tile)
                if item:
                    listings.append(item)
            if listings:
                return listings
        except Exception:
            continue

    # Strategy 2: Regex scrape on raw HTML near refurbProduct markers
    refurb_spots = [m.start() for m in re.finditer(r'"refurbProduct"', html)]
    seen_ids = set()
    for idx in refurb_spots:
        window = html[max(0, idx - 3000):idx + 1500]
        title_match = re.search(r'"(?:title|displayName|name)":"([^"]+)"', window)
        price_match = re.search(r'"raw_amount":"([\d.]+)"', window)
        part_match = re.search(r'"partNumber":"([^"]+)"', window)
        ram_match = re.search(r'"tsMemorySize":"([^"]+)"', window)
        storage_match = re.search(r'"dimensionCapacity":"([^"]+)"', window)
        if title_match and price_match:
            title = title_match.group(1).strip()
            price = float(price_match.group(1))
            part = part_match.group(1) if part_match else ""
            ram = ram_match.group(1) if ram_match else ""
            storage = storage_match.group(1) if storage_match else ""
            iid = item_id(title, price, part)
            if iid not in seen_ids:
                seen_ids.add(iid)
                listings.append({
                    "title": title,
                    "price": price,
                    "part": part,
                    "ram": ram,
                    "storage": storage,
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
            return
        for v in data.values():
            yield from _extract_tiles(v)
    return


def _tile_to_listing(tile: dict) -> dict | None:
    """Convert a single Apple tile object into our listing dict."""
    try:
        dims = tile.get("filters", {}).get("dimensions", {})
        ram = dims.get("tsMemorySize", "")
        storage = dims.get("dimensionCapacity", "")

        title = tile.get("title", "")
        if not title:
            parts = []
            model = dims.get("refurbClearModel", "")
            color = dims.get("dimensionColor", "")
            capacity = dims.get("dimensionCapacity", "")
            year = dims.get("dimensionRelYear", "")
            screen = dims.get("dimensionScreensize", "")
            if model:
                parts.append(
                    model.replace("macbookair", "MacBook Air")
                    .replace("macmini", "Mac mini")
                    .replace("macstudio", "Mac Studio")
                    .replace("macbookpro", "MacBook Pro")
                )
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

        price_info = tile.get("price", {})
        raw = price_info.get("currentPrice", {}).get("raw_amount")
        if raw is None:
            raw = price_info.get("previousPrice", {}).get("raw_amount")
        if raw is None:
            return None
        price = float(raw)

        part = tile.get("partNumber", "")
        return {
            "title": title,
            "price": price,
            "part": part,
            "ram": ram,
            "storage": storage,
        }
    except Exception:
        return None


# ── Filters ─────────────────────────────────────────────────────────
def matches_criteria(item: dict) -> bool:
    """Check if item passes both RAM and price filters (if set)."""
    # RAM filter
    if RAM_CONFIG.get("mode") != "exact" or RAM_CONFIG.get("values"):
        item_ram = item.get("ram", "").strip().lower()
        item_ram_gb = _ram_to_gb(item_ram)

        if RAM_CONFIG["mode"] == "exact":
            if item_ram not in [v.lower() for v in RAM_CONFIG["values"]]:
                return False
        elif RAM_CONFIG["mode"] == "min":
            if item_ram_gb is None or item_ram_gb < RAM_CONFIG["min_gb"]:
                return False
        elif RAM_CONFIG["mode"] == "max":
            if item_ram_gb is None or item_ram_gb > RAM_CONFIG["max_gb"]:
                return False
        elif RAM_CONFIG["mode"] == "range":
            if item_ram_gb is None:
                return False
            if item_ram_gb < RAM_CONFIG["min_gb"] or item_ram_gb > RAM_CONFIG["max_gb"]:
                return False

    # Price filter
    if PRICE_CAP:
        try:
            cap = float(PRICE_CAP)
            if item["price"] > cap:
                return False
        except ValueError:
            pass

    return True


def _ram_filter_label() -> str:
    """Human-readable RAM filter for Discord embeds."""
    if RAM_CONFIG["mode"] == "exact" and RAM_CONFIG["values"]:
        return ", ".join(RAM_CONFIG["values"]).upper()
    elif RAM_CONFIG["mode"] == "min":
        return f"≥ {RAM_CONFIG['min_gb']}GB"
    elif RAM_CONFIG["mode"] == "max":
        return f"≤ {RAM_CONFIG['max_gb']}GB"
    elif RAM_CONFIG["mode"] == "range":
        return f"{RAM_CONFIG['min_gb']}-{RAM_CONFIG['max_gb']}GB"
    return "Any"


# ── Discord ─────────────────────────────────────────────────────────
def send_discord(items: list[dict], url: str) -> None:
    if not DISCORD_WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK not set")

    embeds = []
    for it in items:
        fields = [
            {"name": "💰 Price", "value": f"${it['price']:.2f}", "inline": True},
        ]
        if it.get("ram"):
            fields.append({"name": "🧠 RAM", "value": it["ram"].upper(), "inline": True})
        if it.get("storage"):
            fields.append({"name": "💾 Storage", "value": it["storage"].upper(), "inline": True})

        # Show active filters
        filter_notes = []
        if RAM_CONFIG.get("values") or RAM_CONFIG["mode"] != "exact":
            filter_notes.append(f"RAM: {_ram_filter_label()}")
        if PRICE_CAP:
            filter_notes.append(f"Max price: ${float(PRICE_CAP):.0f}")
        if filter_notes:
            fields.append({"name": "🔍 Your Filter", "value": " | ".join(filter_notes), "inline": False})

        if it.get("part"):
            fields.insert(0, {"name": "Part #", "value": it["part"], "inline": True})

        embed = {
            "title": it["title"][:256],
            "url": url,
            "color": 0x5865F2,
            "fields": fields,
            "footer": {
                "text": "Apple Refurb Monitor • "
                + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            },
        }
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

    # Optional: keyword filter from URL tail
    keyword = os.path.basename(APPLE_URL).replace("-", " ").lower()
    filtered = [it for it in all_items if keyword in it["title"].lower()]
    if not filtered:
        filtered = all_items

    # Apply RAM/price filters
    deals = [it for it in filtered if matches_criteria(it)]
    filter_desc = []
    if RAM_CONFIG["mode"] != "exact" or RAM_CONFIG["values"]:
        filter_desc.append(f"RAM: {_ram_filter_label()}")
    if PRICE_CAP:
        filter_desc.append(f"price ≤ ${PRICE_CAP}")
    print(f"{len(deals)} items match ({', '.join(filter_desc) if filter_desc else 'no filters'})")

    seen = load_seen()
    new_deals = []
    for it in deals:
        iid = item_id(it["title"], it["price"], it.get("part", ""))
        if iid not in seen:
            new_deals.append(it)
            seen.add(iid)

    if new_deals:
        print(f"🚨 {len(new_deals)} new deal(s) — pinging Discord")
        send_discord(new_deals, APPLE_URL)
        save_seen(seen)
    else:
        print("No new deals. Discord stays quiet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Apple Refurb Monitor → Discord v2.0
Features:
  • Grade-based deal scoring (A–F, stolen from FBM Sniper)
  • Junk-filter regex kill list
  • Dual-webhook routing (all deals + hot-deals A/B only)
  • 10 K seen-ID cap (prevents unbounded growth)
  • Auto-open browser on A-grade deals (local runs only)
  • Discord embeds with colour-coded urgency + "Open Listing" button
"""
import os
import re
import hashlib
import json
import urllib.request
import webbrowser
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────────
APPLE_URL = os.getenv("APPLE_URL", "https://www.apple.com/shop/refurbished/mac/mac-mini")
PRICE_CAP = os.getenv("PRICE_CAP", "")
RAM_SIZE = os.getenv("RAM_SIZE", "")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
DISCORD_WEBHOOK_HOT = os.getenv("DISCORD_WEBHOOK_HOT", "")
STATE_FILE = os.getenv("STATE_FILE", "seen.json")
OPEN_BROWSER = os.getenv("OPEN_BROWSER", "false").lower() in ("1", "true", "yes")
RETAIL_PRICES = os.getenv("RETAIL_PRICES", "")  # override table: "mac mini m4 16gb=599,mac mini m4 pro 24gb=1399"

# ── Retail price table (US MSRP) ───────────────────────────────────
# Hardcoded for common configs; override via RETAIL_PRICES env var.
_DEFAULT_RETAIL = {
    # Mac mini M4
    "mac mini m4 16gb 256gb": 599,
    "mac mini m4 16gb 512gb": 799,
    "mac mini m4 16gb 1tb": 999,
    "mac mini m4 16gb 2tb": 1399,
    # Mac mini M4 Pro
    "mac mini m4 pro 24gb 512gb": 1399,
    "mac mini m4 pro 24gb 1tb": 1599,
    "mac mini m4 pro 24gb 2tb": 1999,
    "mac mini m4 pro 48gb 512gb": 1699,
    "mac mini m4 pro 48gb 1tb": 1899,
    "mac mini m4 pro 48gb 2tb": 2299,
    # Mac mini M2 (legacy, still pops up)
    "mac mini m2 8gb 256gb": 599,
    "mac mini m2 8gb 512gb": 799,
    "mac mini m2 16gb 256gb": 799,
    "mac mini m2 16gb 512gb": 999,
    "mac mini m2 pro 16gb 512gb": 1299,
    "mac mini m2 pro 32gb 512gb": 1699,
    # MacBook Air M3
    "macbook air m3 8gb 256gb": 1099,
    "macbook air m3 8gb 512gb": 1299,
    "macbook air m3 16gb 256gb": 1299,
    "macbook air m3 16gb 512gb": 1499,
    # MacBook Air M2
    "macbook air m2 8gb 256gb": 999,
    "macbook air m2 8gb 512gb": 1199,
    "macbook air m2 16gb 256gb": 1199,
    "macbook air m2 16gb 512gb": 1399,
    # MacBook Pro M4
    "macbook pro m4 16gb 512gb": 1599,
    "macbook pro m4 16gb 1tb": 1799,
    "macbook pro m4 24gb 512gb": 1799,
    "macbook pro m4 24gb 1tb": 1999,
    # MacBook Pro M4 Pro
    "macbook pro m4 pro 24gb 512gb": 1999,
    "macbook pro m4 pro 24gb 1tb": 2199,
    "macbook pro m4 pro 48gb 512gb": 2299,
    "macbook pro m4 pro 48gb 1tb": 2499,
    # MacBook Pro M4 Max
    "macbook pro m4 max 36gb 1tb": 3199,
    "macbook pro m4 max 48gb 1tb": 3499,
    "macbook pro m4 max 64gb 1tb": 3899,
    # Mac Studio M2 Ultra
    "mac studio m2 ultra 64gb 1tb": 3999,
    "mac studio m2 ultra 128gb 1tb": 4999,
    "mac studio m2 ultra 64gb 2tb": 4399,
    "mac studio m2 ultra 128gb 2tb": 5399,
    # Mac Studio M2 Max
    "mac studio m2 max 32gb 512gb": 1999,
    "mac studio m2 max 32gb 1tb": 2199,
    "mac studio m2 max 64gb 1tb": 2599,
    "mac studio m2 max 64gb 2tb": 2999,
}


def _load_retail_table() -> dict[str, int]:
    """Merge default retail table with user overrides from RETAIL_PRICES env."""
    table = dict(_DEFAULT_RETAIL)
    if RETAIL_PRICES:
        for pair in RETAIL_PRICES.split(","):
            if "=" not in pair:
                continue
            key, val = pair.split("=", 1)
            key = key.strip().lower().replace("-", " ")
            try:
                table[key] = int(val.strip())
            except ValueError:
                pass
    return table


RETAIL_TABLE = _load_retail_table()


# ── Junk filter (FBM Sniper pattern) ────────────────────────────────
_JUNK_RE = [
    re.compile(r"\bcable\b", re.I),
    re.compile(r"\bcharger\b", re.I),
    re.compile(r"\badapter\b", re.I),
    re.compile(r"\bcase\b", re.I),
    re.compile(r"\bcover\b", re.I),
    re.compile(r"\bstand\b", re.I),
    re.compile(r"\bkeyboard\b", re.I),
    re.compile(r"\bmouse\b", re.I),
    re.compile(r"\btrackpad\b", re.I),
    re.compile(r"\bdisplay\b", re.I),
    re.compile(r"\bmonitor\b", re.I),
    re.compile(r"\bdock\b", re.I),
    re.compile(r"\bhub\b", re.I),
    re.compile(r"\baccessory\b", re.I),
    re.compile(r"\baccessories\b", re.I),
    re.compile(r"\bfunda\b", re.I),
    re.compile(r"\bcarcasa\b", re.I),
    re.compile(r"\bscreen\s*guard\b", re.I),
    re.compile(r"\btempered\s*glass\b", re.I),
    re.compile(r"\bfor\s+parts\b", re.I),
    re.compile(r"\bas[-\s]?is\b", re.I),
    re.compile(r"\bno\s+funciona\b", re.I),
    re.compile(r"\breparar\b", re.I),
]


# ── RAM parsing ─────────────────────────────────────────────────────
def _ram_to_gb(raw: str) -> int | None:
    m = re.search(r"(\d+)", raw.lower().replace(" ", ""))
    return int(m.group(1)) if m else None


def _parse_ram_config(raw: str) -> dict:
    result = {"mode": "exact", "values": [], "min_gb": None, "max_gb": None}
    if not raw:
        return result
    raw = raw.strip().lower().replace(" ", "")
    range_match = re.match(r"^(\d+)gb?\s*-\s*(\d+)gb?", raw)
    if range_match:
        result["mode"] = "range"
        result["min_gb"] = int(range_match.group(1))
        result["max_gb"] = int(range_match.group(2))
        return result
    comp_match = re.match(r"^(>=?|<=?)(\d+)gb?", raw)
    if comp_match:
        op, val = comp_match.group(1), int(comp_match.group(2))
        if op == ">":
            result["mode"] = "min"
            result["min_gb"] = val + 1
        elif op == ">=":
            result["mode"] = "min"
            result["min_gb"] = val
        elif op == "<":
            result["mode"] = "max"
            result["max_gb"] = val - 1
        elif op == "<=":
            result["mode"] = "max"
            result["max_gb"] = val
        return result
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


# ── Grading engine (FBM Sniper price-band rules) ────────────────────
def _normalize(text: str) -> str:
    return text.lower().replace("-", " ").replace(",", " ").replace("  ", " ").strip()


def _lookup_retail(title: str, ram: str, storage: str) -> int | None:
    """Try to match title + specs against the retail price table."""
    ram_gb = _ram_to_gb(ram) or 0
    storage_gb = _ram_to_gb(storage) or 0
    storage_label = f"{storage_gb}gb" if storage_gb else ""

    # Build candidate keys
    base = _normalize(title)
    candidates = [
        f"{base} {ram_gb}gb {storage_label}".strip(),
        f"{base} {ram_gb}gb".strip(),
        base,
    ]
    for c in candidates:
        if c in RETAIL_TABLE:
            return RETAIL_TABLE[c]
    return None


def grade_deal(price: float, retail: int | None) -> dict:
    """
    Returns {go, grade, score, reasons, savings, ratio}
    A ≤80%  B 81-90%  C 91-100%  D 101-130%  F >130% or unknown
    """
    reasons = []
    if retail is None:
        return {
            "go": True,
            "grade": "?",
            "score": 50,
            "reasons": ["no retail price — review manually"],
            "savings": None,
            "ratio": None,
        }

    ratio = price / retail
    savings = retail - price

    if ratio > 1.30:
        return {"go": False, "grade": "F", "score": 0, "reasons": [f"${price:.0f} > 1.3× retail ${retail}"], "savings": savings, "ratio": ratio}

    if ratio <= 0.80:
        grade, score = "A", 95
    elif ratio <= 0.90:
        grade, score = "B", 80
    elif ratio <= 1.00:
        grade, score = "C", 65
    else:
        grade, score = "D", 40

    if savings > 0:
        reasons.append(f"saves ${savings:.0f} vs retail ${retail}")
    else:
        reasons.append(f"list ${price:.0f} vs retail ${retail}")

    return {"go": True, "grade": grade, "score": score, "reasons": reasons, "savings": savings, "ratio": ratio}


def is_junk(title: str) -> tuple[bool, str | None]:
    for pattern in _JUNK_RE:
        if pattern.search(title):
            return True, pattern.pattern
    return False, None


# ── Parsing ─────────────────────────────────────────────────────────
def parse_listings(html: str) -> list[dict]:
    listings = []
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
                listings.append({"title": title, "price": price, "part": part, "ram": ram, "storage": storage})
    return listings


def _extract_tiles(data):
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
        return {"title": title, "price": price, "part": part, "ram": ram, "storage": storage}
    except Exception:
        return None


# ── Filters ─────────────────────────────────────────────────────────
def matches_criteria(item: dict) -> bool:
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
    if PRICE_CAP:
        try:
            cap = float(PRICE_CAP)
            if item["price"] > cap:
                return False
        except ValueError:
            pass
    return True


def _ram_filter_label() -> str:
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
_GRADE_COLOUR = {
    "A": 0x3fb950,  # green
    "B": 0x58a6ff,  # blue
    "C": 0xd4a72c,  # yellow
    "D": 0xf78166,  # orange
    "?": 0x8b949e,  # grey
}


def _build_embed(it: dict, grade_info: dict, item_url: str) -> dict:
    grade = grade_info["grade"]
    score = grade_info["score"]
    reasons = grade_info["reasons"]
    savings = grade_info.get("savings")
    ratio = grade_info.get("ratio")
    retail = grade_info.get("retail")

    fields = [
        {"name": "💰 Price", "value": f"${it['price']:.2f}", "inline": True},
        {"name": "📊 Grade", "value": f"**{grade}** ({score})", "inline": True},
    ]

    if retail:
        fields.append({"name": "🏷️ Retail", "value": f"${retail}", "inline": True})
    if savings is not None and savings > 0:
        fields.append({"name": "💵 Savings", "value": f"${savings:.0f}", "inline": True})
    if ratio is not None:
        pct = round((1 - ratio) * 100, 1)
        fields.append({"name": "📉 Discount", "value": f"{pct}%", "inline": True})
    if it.get("ram"):
        fields.append({"name": "🧠 RAM", "value": it["ram"].upper(), "inline": True})
    if it.get("storage"):
        fields.append({"name": "💾 Storage", "value": it["storage"].upper(), "inline": True})
    if it.get("part"):
        fields.append({"name": "Part #", "value": it["part"], "inline": True})

    filter_notes = []
    if RAM_CONFIG.get("values") or RAM_CONFIG["mode"] != "exact":
        filter_notes.append(f"RAM: {_ram_filter_label()}")
    if PRICE_CAP:
        filter_notes.append(f"Max price: ${float(PRICE_CAP):.0f}")
    if filter_notes:
        fields.append({"name": "🔍 Your Filter", "value": " | ".join(filter_notes), "inline": False})

    embed = {
        "title": it["title"][:256],
        "url": item_url,
        "color": _GRADE_COLOUR.get(grade, 0x5865F2),
        "description": "\n".join(reasons[:4]) if reasons else None,
        "fields": fields,
        "footer": {
            "text": f"Apple Refurb Monitor v2 • Grade {grade} • "
            + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        },
    }
    return embed


def _send_payload(webhook: str, payload: dict) -> None:
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "apple-refurb-bot/2.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"Discord returned {resp.status}")


def send_discord(items: list[dict], item_url: str) -> dict:
    """
    Route items to webhooks based on grade.
    Returns {all_sent, hot_sent, hot_count} for logging.
    """
    if not DISCORD_WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK not set")

    all_embeds = []
    hot_embeds = []  # A or B grades

    for it in items:
        grade_info = it.get("_grade_info", {})
        grade = grade_info.get("grade", "?")
        embed = _build_embed(it, grade_info, item_url)
        all_embeds.append(embed)
        if grade in ("A", "B"):
            hot_embeds.append(embed)

    # Add "Open Listing" button component to every message
    components = [{
        "type": 1,
        "components": [{"type": 2, "style": 5, "label": "🛒 Open Listing", "url": item_url}],
    }]

    result = {"all_sent": False, "hot_sent": False, "hot_count": len(hot_embeds)}

    # Send all deals to primary webhook
    if all_embeds:
        _send_payload(DISCORD_WEBHOOK, {"embeds": all_embeds[:10], "components": components})
        result["all_sent"] = True

    # Send hot deals (A/B) to secondary webhook
    if hot_embeds and DISCORD_WEBHOOK_HOT:
        _send_payload(DISCORD_WEBHOOK_HOT, {"embeds": hot_embeds[:10], "components": components})
        result["hot_sent"] = True

    return result


# ── State ───────────────────────────────────────────────────────────
def load_seen() -> set[str]:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set[str]) -> None:
    """Cap at 10 K IDs to prevent unbounded growth (FBM Sniper pattern)."""
    arr = list(seen)
    if len(arr) > 10000:
        arr = arr[-10000:]
    with open(STATE_FILE, "w") as f:
        json.dump(arr, f)


# ── Auto-open browser (local runs only) ─────────────────────────────
def maybe_open_browser(url: str, grade: str) -> None:
    if not OPEN_BROWSER or grade not in ("A", "B"):
        return
    try:
        webbrowser.open(url)
        print(f"🖥️  Opened browser: {url}")
    except Exception as e:
        print(f"⚠️  Browser open failed: {e}")


# ── Main ────────────────────────────────────────────────────────────
def main():
    if not DISCORD_WEBHOOK:
        print("❌ DISCORD_WEBHOOK env var missing")
        return 1

    print(f"Fetching {APPLE_URL} …")
    html = fetch_html(APPLE_URL)

    all_items = parse_listings(html)
    print(f"Found {len(all_items)} items on page")

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

    # Grade + junk-filter each deal
    graded_deals = []
    for it in deals:
        # Junk check
        junk, pattern = is_junk(it["title"])
        if junk:
            print(f"  🗑️  Junk-filtered: {it['title'][:60]}… (matched {pattern})")
            continue

        # Grade
        retail = _lookup_retail(it["title"], it.get("ram", ""), it.get("storage", ""))
        grade_info = grade_deal(it["price"], retail)
        it["_grade_info"] = grade_info
        it["_grade_info"]["retail"] = retail
        graded_deals.append(it)
        print(f"  [{grade_info['grade']}] {it['title'][:60]}… ${it['price']:.0f} (retail ${retail or '?'}) — {grade_info['reasons'][0]}")

    seen = load_seen()
    new_deals = []
    for it in graded_deals:
        iid = item_id(it["title"], it["price"], it.get("part", ""))
        if iid not in seen:
            new_deals.append(it)
            seen.add(iid)

    if new_deals:
        print(f"🚨 {len(new_deals)} new deal(s) — pinging Discord")
        result = send_discord(new_deals, APPLE_URL)
        if result["hot_sent"]:
            print(f"🔥 {result['hot_count']} hot deal(s) also sent to hot-deals webhook")
        save_seen(seen)

        # Auto-open browser for A/B grades (local runs)
        for it in new_deals:
            grade = it["_grade_info"].get("grade", "")
            if grade in ("A", "B"):
                maybe_open_browser(APPLE_URL, grade)
    else:
        print("No new deals. Discord stays quiet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

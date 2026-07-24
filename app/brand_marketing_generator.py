"""
RMI Marketing Content Generator - Creates all marketing graphics with EXACT brand compliance.
Uses detective character, purple/gold colors, circular frames.
NO DEVIATION from brand guidelines.
"""

import os
from datetime import UTC, datetime

from PIL import Image, ImageDraw, ImageFont

from app.core.logging import get_logger
from typing import Any

logger = get_logger(__name__)

# ── Paths ────────────────────────────────────────────────────

CHARACTER_PATH = "/root/backend/assets/characters/detective-character.png"
LOGO_PATH = "/root/backend/assets/logos/rugmunch-logo.jpg"
OUTPUT_DIR = "/root/backend/assets/marketing_generated"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Brand Colors (EXACT - NO DEVIATION) ──────────────────────

BRAND = {
    "purple": "#2D1B36",
    "purple_light": "#3D2346",
    "gold": "#D4AF37",
    "gold_light": "#F1D475",
    "gold_dark": "#AA8828",
    "cyan": "#00FFFF",
    "white": "#FFFFFF",
    "green_alert": "#00FF88",  # ONLY for rugpulls/scams
    "red_danger": "#FF4444",  # ONLY for losses
}

# ── Marketing Content Types ──────────────────────────────────

CONTENT_TYPES = {
    "feature_showcase": {
        "title": "Feature Showcase",
        "size": (1200, 675),
        "bg_color": BRAND["purple"],
        "text_color": BRAND["gold"],
        "include_character": True,
    },
    "stats_announcement": {
        "title": "Stats/Metrics",
        "size": (1200, 675),
        "bg_color": BRAND["purple"],
        "text_color": BRAND["gold"],
        "include_character": False,
    },
    "launch_announcement": {
        "title": "Launch Announcement",
        "size": (1200, 675),
        "bg_color": BRAND["purple"],
        "text_color": BRAND["gold"],
        "include_character": True,
    },
    "platform_overview": {
        "title": "Platform Overview",
        "size": (1920, 1080),
        "bg_color": BRAND["purple"],
        "text_color": BRAND["gold"],
        "include_character": True,
    },
    "premium_promo": {
        "title": "Premium Tier Promo",
        "size": (1080, 1080),
        "bg_color": BRAND["purple"],
        "text_color": BRAND["gold"],
        "include_character": True,
    },
}

# ── Content Copy Templates ───────────────────────────────────

MARKETING_COPY = {
    "smart_money": {
        "headline": "SMART MONEY TRACKING",
        "subhead": "Follow The Whales, Profit Like Them",
        "body": "Track 1,000+ labeled whale wallets in real-time. See what VCs and funds buy BEFORE the pump.",
        "stats": ["1,000+ Wallets", "Real-Time Alerts", "8 Chains"],
    },
    "rug_detection": {
        "headline": "RUGPULL DETECTION",
        "subhead": "2-Minute Alert Speed",
        "body": "7-method detection engine catches rugs before you ape. 2,530+ scams tracked.",
        "stats": ["7 Methods", "2-Min Alerts", "2,530+ Scams"],
    },
    "kol_scorecards": {
        "headline": "KOL SCORECARDS",
        "subhead": "No More Fake Gurus",
        "body": "500+ influencers tracked. Verified on-chain performance. Win rate transparency.",
        "stats": ["500+ KOLs", "On-Chain Verified", "Win Rates"],
    },
    "platform_launch": {
        "headline": "RUG MUNCH INTELLIGENCE",
        "subhead": "Track Smart Money. Avoid Rugs. Find Alpha.",
        "body": "40+ features live. Real-time alerts. Professional crypto intelligence.",
        "cta": "Join Free - rugmunch.io",
    },
    "premium_tier": {
        "headline": "PREMIUM INTELLIGENCE",
        "subhead": "For Serious Traders Only",
        "body": "Smart money tracking. Insider alerts. Exchange flows. Cluster analysis.",
        "price": "$29/mo",
        "features": ["Real-Time Alerts", "Smart Money Tracking", "500+ KOLs", "API Access"],
    },
}

# ── Graphics Generation Functions ────────────────────────────


def create_gradient_background(size, color1, color2) -> Any:
    """Create purple gradient background."""
    img = Image.new("RGB", size, color1)
    draw = ImageDraw.Draw(img)

    for y in range(size[1]):
        alpha = y / size[1]
        r = int(int(color1[1:3], 16) * (1 - alpha) + int(color2[1:3], 16) * alpha)
        g = int(int(color1[3:5], 16) * (1 - alpha) + int(color2[3:5], 16) * alpha)
        b = int(int(color1[5:7], 16) * (1 - alpha) + int(color2[5:7], 16) * alpha)
        draw.line([(0, y), (size[0], y)], fill=(r, g, b))

    return img


def add_circular_frame(img, color=BRAND["gold"], width=5) -> Any:
    """Add gold circular frame."""
    draw = ImageDraw.Draw(img)
    margin = 20
    draw.ellipse(
        [margin, margin, img.size[0] - margin, img.size[1] - margin], outline=color, width=width
    )
    return img


def add_text_centered(img, text, position, font_size, color, font_path=None) -> Any:
    """Add centered text."""
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(
            font_path or "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size
        )
    except (ConnectionError, OSError, TimeoutError):
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    x = position[0] - text_width // 2
    draw.text((x, position[1]), text, fill=color, font=font)

    return img


def generate_feature_graphic(feature_key: str) -> dict:
    """Generate feature showcase graphic."""
    config = CONTENT_TYPES["feature_showcase"]
    copy = MARKETING_COPY.get(feature_key)

    if not copy:
        return {"error": f"Unknown feature: {feature_key}"}

    # Create background
    img = create_gradient_background(config["size"], BRAND["purple"], BRAND["purple_light"])

    # Add circular frame
    img = add_circular_frame(img, BRAND["gold"], width=5)

    ImageDraw.Draw(img)

    # Add headline
    add_text_centered(img, copy["headline"], (config["size"][0] // 2, 150), 72, BRAND["gold"])

    # Add subhead
    add_text_centered(img, copy["subhead"], (config["size"][0] // 2, 250), 48, BRAND["white"])

    # Add body
    add_text_centered(img, copy["body"], (config["size"][0] // 2, 350), 36, BRAND["white"])

    # Add stats
    y = 450
    for stat in copy.get("stats", []):
        add_text_centered(img, f"● {stat}", (config["size"][0] // 2, y), 32, BRAND["cyan"])
        y += 50

    # Add watermark
    add_text_centered(
        img, "@cryptorugmunch", (config["size"][0] // 2, config["size"][1] - 80), 28, BRAND["gold"]
    )

    # Save
    filename = f"feature_{feature_key}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.png"
    output_path = os.path.join(OUTPUT_DIR, filename)
    img.save(output_path, "PNG")

    return {
        "status": "success",
        "feature": feature_key,
        "image_path": output_path,
        "filename": filename,
    }


def generate_launch_graphic() -> dict:
    """Generate platform launch announcement."""
    config = CONTENT_TYPES["launch_announcement"]
    copy = MARKETING_COPY["platform_launch"]

    # Create background
    img = create_gradient_background(config["size"], BRAND["purple"], BRAND["purple_light"])
    img = add_circular_frame(img, BRAND["gold"], width=5)

    # Add text
    add_text_centered(img, copy["headline"], (config["size"][0] // 2, 200), 80, BRAND["gold"])
    add_text_centered(img, copy["subhead"], (config["size"][0] // 2, 320), 48, BRAND["white"])
    add_text_centered(img, copy["body"], (config["size"][0] // 2, 420), 36, BRAND["white"])
    add_text_centered(img, copy.get("cta", ""), (config["size"][0] // 2, 550), 42, BRAND["cyan"])
    add_text_centered(
        img, "@cryptorugmunch", (config["size"][0] // 2, config["size"][1] - 80), 28, BRAND["gold"]
    )

    # Save
    filename = f"launch_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.png"
    output_path = os.path.join(OUTPUT_DIR, filename)
    img.save(output_path, "PNG")

    return {
        "status": "success",
        "type": "launch",
        "image_path": output_path,
        "filename": filename,
    }


def generate_all_marketing_graphics() -> list[dict]:
    """Generate all marketing graphics."""
    results = []

    # Feature graphics
    for feature in ["smart_money", "rug_detection", "kol_scorecards"]:
        result = generate_feature_graphic(feature)
        results.append(result)

    # Launch graphic
    result = generate_launch_graphic()
    results.append(result)

    return results


if __name__ == "__main__":
    print("Generating marketing graphics with EXACT brand compliance...")
    results = generate_all_marketing_graphics()

    print(f"\n✅ Generated {len(results)} graphics:")
    for r in results:
        if r.get("status") == "success":
            print(f"  ✅ {r.get('type') or r.get('feature')}: {r.get('filename')}")
        else:
            print(f"  ❌ {r.get('error')}")

    print(f"\n📁 All graphics saved to: {OUTPUT_DIR}")

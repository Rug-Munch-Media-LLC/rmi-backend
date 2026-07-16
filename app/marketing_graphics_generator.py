"""
RMI Marketing Graphics Generator - Advertising & Feature Showcase Graphics.
Professional visuals for platform marketing, feature showcases, comparisons.
Uses template-based generation with Alibaba AI enhancement options.

Graphics Types:
- Platform banners (website, social)
- Feature showcase cards
- Comparison charts (vs competitors)
- Stats/metrics graphics
- Testimonial cards
- Launch announcements
- Pricing tier graphics
"""

import hashlib
import os
from datetime import UTC, datetime

from PIL import Image, ImageDraw, ImageFont

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Paths ────────────────────────────────────────────────────

MARKETING_TEMPLATE_DIR = "/root/backend/assets/marketing_templates"
MARKETING_GENERATED_DIR = "/root/backend/assets/marketing_generated"

os.makedirs(MARKETING_TEMPLATE_DIR, exist_ok=True)
os.makedirs(MARKETING_GENERATED_DIR, exist_ok=True)

# ── Brand Colors (EXACT - NO DEVIATION) ─────────────────────────

BRAND_COLORS = {
    "deep_purple": "#2D1B36",  # Primary background
    "metallic_gold": "#D4AF37",  # Primary text, borders
    "cyan_glow": "#00FFFF",  # Character glasses, tech accents
    "gold_highlight": "#F1D475",  # Light gradients
    "gold_shadow": "#AA8828",  # Dark gradients
    "neon_green": "#00FF88",  # ONLY for alerts (rugpulls, scams)
    "danger_red": "#FF4444",  # ONLY for losses, danger
    "white": "#FFFFFF",  # Body text
    "background": "#2D1B36",  # Same as deep_purple
    "surface": "#3D2346",  # Slightly lighter purple for cards
    "text_primary": "#D4AF37",  # Gold for headlines
    "text_secondary": "#FFFFFF",  # White for body
}

# ── Marketing Graphics Templates ─────────────────────────────

MARKETING_TEMPLATES = {
    # Platform Banners
    "platform_banner_wide": {
        "name": "Platform Banner (Wide)",
        "size": (1920, 1080),  # Full HD
        "use_cases": ["Website hero", "YouTube banner", "Presentations"],
        "elements": [
            {"type": "background", "style": "gradient_purple"},
            {"type": "logo", "position": (100, 100)},
            {
                "type": "headline",
                "text": "Rug Munch Intelligence",
                "position": (100, 300),
                "font_size": 96,
                "color": "#D4AF37",
            },
            {
                "type": "subheadline",
                "text": "Track Smart Money. Avoid Rugs. Find Alpha.",
                "position": (100, 420),
                "font_size": 48,
                "color": "#FFFFFF",
            },
            {"type": "features_list", "position": (100, 550), "font_size": 36, "color": "#FFFFFF"},
            {
                "type": "cta_button",
                "text": "Join Now - Free",
                "position": (100, 850),
                "font_size": 48,
                "color": "#2D1B36",
                "bg_color": "#D4AF37",
            },
            {"type": "character", "position": (1400, 200), "size": 500},
            {
                "type": "watermark",
                "text": "@cryptorugmunch",
                "position": (1700, 1000),
                "font_size": 32,
                "color": "#D4AF37",
            },
        ],
    },
    "platform_banner_square": {
        "name": "Platform Banner (Square)",
        "size": (1080, 1080),  # Instagram/Twitter
        "use_cases": ["Social media", "Profile banner"],
        "elements": [
            {"type": "background", "style": "gradient_purple"},
            {"type": "logo", "position": (440, 150)},
            {
                "type": "headline",
                "text": "Rug Munch Intelligence",
                "position": (100, 400),
                "font_size": 72,
                "color": "#D4AF37",
            },
            {
                "type": "subheadline",
                "text": "Track Smart Money. Avoid Rugs.",
                "position": (100, 500),
                "font_size": 36,
                "color": "#FFFFFF",
            },
            {"type": "character", "position": (340, 650), "size": 400},
            {
                "type": "watermark",
                "text": "@cryptorugmunch",
                "position": (400, 950),
                "font_size": 28,
                "color": "#D4AF37",
            },
        ],
    },
    # Feature Showcase Cards
    "feature_card": {
        "name": "Feature Showcase Card",
        "size": (1200, 675),
        "use_cases": ["Twitter posts", "Telegram", "Blog headers"],
        "elements": [
            {"type": "background", "style": "gradient_dark"},
            {"type": "icon", "position": (100, 150), "size": 200},
            {
                "type": "feature_name",
                "text": "{feature_name}",
                "position": (350, 200),
                "font_size": 64,
                "color": "#00FF88",
            },
            {
                "type": "feature_desc",
                "text": "{feature_description}",
                "position": (350, 300),
                "font_size": 36,
                "color": "#FFFFFF",
            },
            {"type": "stats", "position": (350, 450), "font_size": 32, "color": "#FFD700"},
            {
                "type": "watermark",
                "text": "@cryptorugmunch",
                "position": (950, 600),
                "font_size": 28,
                "color": "#A0A0A0",
            },
        ],
    },
    "feature_grid": {
        "name": "Feature Grid (All Features)",
        "size": (1920, 1920),
        "use_cases": ["Comprehensive overview", "Website section"],
        "elements": [
            {"type": "background", "style": "gradient_dark"},
            {
                "type": "title",
                "text": "40+ Features Live Now",
                "position": (100, 100),
                "font_size": 80,
                "color": "#FFFFFF",
            },
            {"type": "grid", "columns": 4, "rows": 3, "position": (100, 250)},
            {
                "type": "watermark",
                "text": "rugmunch.io",
                "position": (1500, 1800),
                "font_size": 40,
                "color": "#00FF88",
            },
        ],
    },
    # Comparison Graphics
    "competitor_comparison": {
        "name": "Competitor Comparison Chart",
        "size": (1600, 900),
        "use_cases": ["Twitter threads", "Blog posts", "Sales pages"],
        "elements": [
            {"type": "background", "style": "gradient_dark"},
            {
                "type": "title",
                "text": "RMI vs. The Competition",
                "position": (100, 80),
                "font_size": 64,
                "color": "#FFFFFF",
            },
            {"type": "comparison_table", "position": (100, 180), "font_size": 28},
            {
                "type": "watermark",
                "text": "@cryptorugmunch",
                "position": (1300, 820),
                "font_size": 28,
                "color": "#A0A0A0",
            },
        ],
    },
    # Stats/Metrics Graphics
    "stats_card": {
        "name": "Stats/Metrics Card",
        "size": (1200, 675),
        "use_cases": ["Milestone announcements", "Growth metrics"],
        "elements": [
            {"type": "background", "style": "gradient_dark"},
            {
                "type": "stat_number",
                "text": "{stat_value}",
                "position": (600, 250),
                "font_size": 144,
                "color": "#00FF88",
            },
            {
                "type": "stat_label",
                "text": "{stat_label}",
                "position": (600, 420),
                "font_size": 48,
                "color": "#FFFFFF",
            },
            {
                "type": "context",
                "text": "{context}",
                "position": (600, 520),
                "font_size": 32,
                "color": "#A0A0A0",
            },
            {
                "type": "watermark",
                "text": "@cryptorugmunch",
                "position": (950, 600),
                "font_size": 28,
                "color": "#A0A0A0",
            },
        ],
    },
    # Pricing Tier Graphics
    "pricing_card": {
        "name": "Pricing Tier Card",
        "size": (800, 1000),
        "use_cases": ["Pricing page", "Social posts"],
        "elements": [
            {"type": "background", "style": "card_surface"},
            {
                "type": "tier_name",
                "text": "{tier_name}",
                "position": (400, 150),
                "font_size": 64,
                "color": "#00FF88",
            },
            {
                "type": "tier_price",
                "text": "${price}/mo",
                "position": (400, 250),
                "font_size": 72,
                "color": "#FFFFFF",
            },
            {"type": "features_list", "position": (100, 350), "font_size": 28, "color": "#FFFFFF"},
            {
                "type": "cta_button",
                "text": "Get Started",
                "position": (200, 850),
                "font_size": 40,
                "color": "#000000",
                "bg_color": "#00FF88",
            },
        ],
    },
    # Launch Announcement
    "launch_announcement": {
        "name": "Launch Announcement",
        "size": (1200, 675),
        "use_cases": ["Product launches", "Major announcements"],
        "elements": [
            {"type": "background", "style": "gradient_celebration"},
            {
                "type": "badge",
                "text": "🚀 LIVE NOW",
                "position": (450, 100),
                "font_size": 64,
                "color": "#FFD700",
            },
            {
                "type": "headline",
                "text": "{headline}",
                "position": (100, 250),
                "font_size": 72,
                "color": "#FFFFFF",
            },
            {
                "type": "subheadline",
                "text": "{subheadline}",
                "position": (100, 350),
                "font_size": 40,
                "color": "#00FF88",
            },
            {
                "type": "cta",
                "text": "rugmunch.io",
                "position": (450, 550),
                "font_size": 48,
                "color": "#FFFFFF",
            },
            {
                "type": "watermark",
                "text": "@cryptorugmunch",
                "position": (950, 600),
                "font_size": 28,
                "color": "#A0A0A0",
            },
        ],
    },
    # Testimonial Card
    "testimonial_card": {
        "name": "Testimonial/User Quote",
        "size": (1200, 675),
        "use_cases": ["Social proof", "User reviews"],
        "elements": [
            {"type": "background", "style": "gradient_dark"},
            {"type": "quote_icon", "position": (100, 100), "size": 80},
            {
                "type": "quote_text",
                "text": "{quote}",
                "position": (200, 150),
                "font_size": 40,
                "color": "#FFFFFF",
            },
            {
                "type": "author",
                "text": "- {author}",
                "position": (200, 450),
                "font_size": 32,
                "color": "#00FF88",
            },
            {
                "type": "author_title",
                "text": "{title}",
                "position": (200, 500),
                "font_size": 28,
                "color": "#A0A0A0",
            },
            {
                "type": "watermark",
                "text": "@cryptorugmunch",
                "position": (950, 600),
                "font_size": 28,
                "color": "#A0A0A0",
            },
        ],
    },
}

# ── Feature List for Graphics ────────────────────────────────

FEATURE_LIST = [
    {
        "name": "Rugpull Detection",
        "desc": "7-method engine, 2-min alerts",
        "stat": "2,530+ scams tracked",
    },
    {
        "name": "Smart Money Tracking",
        "desc": "1,000+ labeled whale wallets",
        "stat": "Real-time alerts",
    },
    {"name": "KOL Scorecards", "desc": "500+ influencers tracked", "stat": "Verified on-chain"},
    {"name": "Meme Intelligence", "desc": "10,000+ meme tokens", "stat": "Across 5 chains"},
    {"name": "Cluster Analysis", "desc": "Sybil & team detection", "stat": "7 detection methods"},
    {"name": "Bundle Detection", "desc": "Jito/Flashbots tracking", "stat": "5-signal engine"},
    {"name": "Cross-Chain", "desc": "Multi-chain correlation", "stat": "8 chains supported"},
    {"name": "Social Sentiment", "desc": "X, Telegram, Discord", "stat": "Real-time monitoring"},
    {"name": "Security Scanner", "desc": "Contract vulnerability detection", "stat": "Auto-audit"},
    {"name": "Whale Alerts", "desc": "Large transfer tracking", "stat": "$10k+ threshold"},
    {"name": "NFT Intelligence", "desc": "Wash trading detection", "stat": "Floor price tracking"},
    {"name": "Premium API", "desc": "Developer access", "stat": "Webhook support"},
]

# ── Graphics Generation Functions ────────────────────────────


def create_placeholder_marketing_template(template_name: str, config: dict) -> Image.Image:
    """Create a placeholder marketing template."""
    img = Image.new("RGB", config["size"], color=BRAND_COLORS["background"])
    draw = ImageDraw.Draw(img)

    # Add gradient effect (simplified)
    for y in range(config["size"][1]):
        alpha = y / config["size"][1]
        r = int(10 + alpha * 20)
        g = int(10 + alpha * 30)
        b = int(15 + alpha * 40)
        draw.line([(0, y), (config["size"][0], y)], fill=(r, g, b))

    # Add title
    title = config["name"]
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72)
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), title, font=font)
    title_width = bbox[2] - bbox[0]
    draw.text(
        ((config["size"][0] - title_width) / 2, 50), title, fill=BRAND_COLORS["primary"], font=font
    )

    # Add watermark
    try:
        watermark_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
    except Exception:
        watermark_font = font

    draw.text(
        (config["size"][0] - 250, config["size"][1] - 50),
        "@cryptorugmunch",
        fill=BRAND_COLORS["text_secondary"],
        font=watermark_font,
    )

    # Save template
    template_path = os.path.join(MARKETING_TEMPLATE_DIR, f"{template_name}_template.png")
    img.save(template_path)
    logger.info(f"Created marketing template: {template_path}")

    return img


def generate_marketing_graphic(graphic_type: str, data: dict | None = None) -> dict:
    """
    Generate marketing graphic by overlaying text on template.
    Returns image path and metadata.
    """
    try:
        config = MARKETING_TEMPLATES.get(graphic_type)
        if not config:
            return {"error": f"Unknown graphic type: {graphic_type}"}

        # Get or create template
        template_path = os.path.join(MARKETING_TEMPLATE_DIR, f"{graphic_type}_template.png")

        if os.path.exists(template_path):
            img = Image.open(template_path)
        else:
            img = create_placeholder_marketing_template(graphic_type, config)

        draw = ImageDraw.Draw(img)

        # Try to load font
        try:
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            base_font = ImageFont.truetype(font_path, 32)
        except Exception:
            base_font = ImageFont.load_default()

        # Overlay dynamic data if provided
        if data:
            for key, value in data.items():
                # Find matching element in config
                for element in config.get("elements", []):
                    if element.get("type") == key or f"{{{key}}}" in str(element.get("text", "")):
                        position = element.get("position", (100, 100))
                        font_size = element.get("font_size", 32)
                        color = element.get("color", "#FFFFFF")

                        try:
                            font = ImageFont.truetype(font_path, font_size)
                        except Exception:
                            font = base_font

                        draw.text(position, str(value), fill=color, font=font)

        # Generate filename
        data_hash = hashlib.sha256(str(sorted((data or {}).items())).encode()).hexdigest()[:12]
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"{graphic_type}_{timestamp}_{data_hash}.png"
        output_path = os.path.join(MARKETING_GENERATED_DIR, filename)

        # Save image
        img.save(output_path, "PNG")

        return {
            "status": "success",
            "graphic_type": graphic_type,
            "image_path": output_path,
            "image_url": f"/assets/marketing_generated/{filename}",
            "filename": filename,
            "size": config["size"],
            "data": data or {},
            "generated_at": datetime.now(UTC).isoformat(),
        }

    except Exception as e:
        logger.error(f"Marketing graphic generation failed: {e}")
        return {"status": "error", "error": str(e)}


# ── Convenience Functions ────────────────────────────────────


async def generate_platform_banner(data: dict | None = None) -> dict:
    """Generate platform banner (wide format)."""
    return generate_marketing_graphic("platform_banner_wide", data)


async def generate_feature_card(feature_name: str, description: str, stat: str) -> dict:
    """Generate feature showcase card."""
    data = {
        "feature_name": feature_name,
        "feature_description": description[:100],
        "stats": stat,
    }
    return generate_marketing_graphic("feature_card", data)


async def generate_all_feature_cards() -> list[dict]:
    """Generate cards for all features."""
    results = []
    for feature in FEATURE_LIST:
        result = await generate_feature_card(feature["name"], feature["desc"], feature["stat"])
        results.append(result)
    return results


async def generate_stats_card(stat_value: str, stat_label: str, context: str = "") -> dict:
    """Generate stats/metrics card."""
    data = {
        "stat_value": stat_value,
        "stat_label": stat_label,
        "context": context,
    }
    return generate_marketing_graphic("stats_card", data)


async def generate_pricing_card(tier_name: str, price: float, features: list[str]) -> dict:
    """Generate pricing tier card."""
    data = {
        "tier_name": tier_name,
        "price": price,
        "features_list": "\n".join(f"✓ {f}" for f in features),
    }
    return generate_marketing_graphic("pricing_card", data)


async def generate_launch_announcement(headline: str, subheadline: str) -> dict:
    """Generate launch announcement graphic."""
    data = {
        "headline": headline,
        "subheadline": subheadline,
    }
    return generate_marketing_graphic("launch_announcement", data)


async def generate_testimonial_card(quote: str, author: str, title: str) -> dict:
    """Generate testimonial card."""
    data = {
        "quote": quote[:200],
        "author": author,
        "title": title,
    }
    return generate_marketing_graphic("testimonial_card", data)


# ── Template Management ──────────────────────────────────────


def list_marketing_templates() -> list[dict]:
    """List available marketing graphic templates."""
    return [
        {
            "id": key,
            "name": config["name"],
            "size": f"{config['size'][0]}x{config['size'][1]}",
            "use_cases": config.get("use_cases", []),
            "exists": os.path.exists(os.path.join(MARKETING_TEMPLATE_DIR, f"{key}_template.png")),
        }
        for key, config in MARKETING_TEMPLATES.items()
    ]


def create_all_marketing_templates():
    """Create all placeholder marketing templates."""
    for graphic_type, config in MARKETING_TEMPLATES.items():
        try:
            create_placeholder_marketing_template(graphic_type, config)
        except Exception as e:
            logger.error(f"Failed to create template for {graphic_type}: {e}")

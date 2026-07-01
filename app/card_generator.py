"""
RMI Card Generator - Template-based card generation with text overlay.
Pre-designed graphics with dynamic text overlay.
Fast, consistent, professional trading platform aesthetic.
"""

import hashlib
import logging
import os
from datetime import UTC, datetime

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────

TEMPLATE_DIR = "/root/backend/assets/card_templates"
GENERATED_DIR = "/root/backend/assets/generated_cards"

# Ensure directories exist
os.makedirs(TEMPLATE_DIR, exist_ok=True)
os.makedirs(GENERATED_DIR, exist_ok=True)

# ── Card Templates Config ────────────────────────────────────

CARD_TEMPLATES = {
    "big_win": {
        "name": "Big Win Alert",
        "template_file": "big_win_template.png",
        "size": (1200, 675),  # Twitter optimized
        "text_fields": {
            "wallet_address": {
                "position": (80, 150),
                "font_size": 32,
                "color": "#FFFFFF",
                "format": "0x{first8}...{last6}",
            },
            "token_symbol": {
                "position": (600, 280),
                "font_size": 72,
                "color": "#00FF88",
                "format": "{symbol}",
            },
            "pnl_usd": {
                "position": (600, 400),
                "font_size": 96,
                "color": "#00FF88",
                "format": "+${amount:,.0f}",
            },
            "pnl_pct": {
                "position": (600, 500),
                "font_size": 64,
                "color": "#00FF88",
                "format": "({pct:.1f}%)",
            },
        },
        "colors": {
            "primary": "#00FF88",  # Neon green
            "accent": "#FFD700",
        },
    },
    "big_loss": {
        "name": "Loss Porn Alert",
        "template_file": "big_loss_template.png",
        "size": (1200, 675),
        "text_fields": {
            "wallet_address": {
                "position": (80, 150),
                "font_size": 32,
                "color": "#FFFFFF",
                "format": "0x{first8}...{last6}",
            },
            "token_symbol": {
                "position": (600, 280),
                "font_size": 72,
                "color": "#FF4444",
                "format": "{symbol}",
            },
            "pnl_usd": {
                "position": (600, 400),
                "font_size": 96,
                "color": "#FF4444",
                "format": "-${amount:,.0f}",
            },
            "pnl_pct": {
                "position": (600, 500),
                "font_size": 64,
                "color": "#FF4444",
                "format": "({pct:.1f}%)",
            },
        },
        "colors": {
            "primary": "#FF4444",  # Neon red
            "accent": "#FF6B6B",
        },
    },
    "kol_scorecard": {
        "name": "KOL Scorecard",
        "template_file": "kol_scorecard_template.png",
        "size": (1200, 1200),  # Square
        "text_fields": {
            "kol_handle": {
                "position": (600, 200),
                "font_size": 56,
                "color": "#FFFFFF",
                "format": "@{handle}",
            },
            "tier": {
                "position": (1000, 150),
                "font_size": 48,
                "color": "#A855F7",
                "format": "{tier} TIER",
            },
            "win_rate": {
                "position": (300, 400),
                "font_size": 64,
                "color": "#00FF88",
                "format": "{rate:.1f}%",
            },
            "total_calls": {
                "position": (300, 500),
                "font_size": 48,
                "color": "#FFFFFF",
                "format": "{count}",
            },
            "avg_pnl": {
                "position": (300, 600),
                "font_size": 48,
                "color": "#FFD700",
                "format": "{pnl:.1f}%",
            },
            "followers": {
                "position": (300, 700),
                "font_size": 48,
                "color": "#FFFFFF",
                "format": "{count:,}",
            },
        },
        "colors": {
            "primary": "#A855F7",  # Purple
            "accent": "#F472B6",
        },
    },
    "rug_call": {
        "name": "Rugpull Alert",
        "template_file": "rugpull_template.png",
        "size": (1200, 675),
        "text_fields": {
            "token_symbol": {
                "position": (600, 200),
                "font_size": 72,
                "color": "#FF8C00",
                "format": "{symbol}",
            },
            "amount_stolen": {
                "position": (600, 350),
                "font_size": 96,
                "color": "#FF4500",
                "format": "${amount:,.0f}",
            },
            "victims": {
                "position": (400, 500),
                "font_size": 48,
                "color": "#FFFFFF",
                "format": "{count} victims",
            },
            "rug_type": {
                "position": (800, 500),
                "font_size": 48,
                "color": "#FFFFFF",
                "format": "{type}",
            },
        },
        "colors": {
            "primary": "#FF8C00",  # Orange
            "accent": "#FF4500",
        },
    },
    "whale_move": {
        "name": "Whale Movement",
        "template_file": "whale_template.png",
        "size": (1200, 675),
        "text_fields": {
            "wallet_label": {
                "position": (80, 150),
                "font_size": 48,
                "color": "#3B82F6",
                "format": "{label}",
            },
            "action": {
                "position": (600, 300),
                "font_size": 72,
                "color": "#3B82F6",
                "format": "{action}",
            },
            "amount": {
                "position": (600, 400),
                "font_size": 96,
                "color": "#60A5FA",
                "format": "{amount:,.0f} {symbol}",
            },
            "usd_value": {
                "position": (600, 500),
                "font_size": 64,
                "color": "#FFFFFF",
                "format": "${value:,.0f}",
            },
        },
        "colors": {
            "primary": "#3B82F6",  # Blue
            "accent": "#60A5FA",
        },
    },
    "smart_money": {
        "name": "Smart Money Pick",
        "template_file": "smart_money_template.png",
        "size": (1200, 675),
        "text_fields": {
            "wallet_label": {
                "position": (80, 150),
                "font_size": 48,
                "color": "#FFD700",
                "format": "{label}",
            },
            "token_symbol": {
                "position": (600, 280),
                "font_size": 72,
                "color": "#FFD700",
                "format": "{symbol}",
            },
            "position_size": {
                "position": (600, 400),
                "font_size": 96,
                "color": "#FFEC8B",
                "format": "${amount:,.0f}",
            },
            "pnl": {
                "position": (600, 500),
                "font_size": 64,
                "color": "#00FF88",
                "format": "+{pnl:.1f}%",
            },
        },
        "colors": {
            "primary": "#FFD700",  # Gold
            "accent": "#FFEC8B",
        },
    },
}

# ── Social Media Accounts ────────────────────────────────────

SOCIAL_ACCOUNTS = {
    "twitter": {
        "handle": "@cryptorugmunch",
        "name": "Rug Munch Intelligence",
        "purpose": "Main posting account - alerts, wins, losses, KOL scorecards",
    },
    "telegram_bot": {
        "handle": "@rugmunchbot",
        "name": "Rug Munch Bot",
        "purpose": "Interactive bot - respond to questions, commands, alerts",
        "commands": [
            "/start - Welcome + features",
            "/trending - Top meme tokens",
            "/whales - Recent whale moves",
            "/kol [handle] - KOL scorecard",
            "/wallet [address] - Wallet analysis",
            "/alerts - Manage notifications",
            "/premium - Upgrade info",
            "/help - Command list",
        ],
    },
}

# ── Card Generation Functions ────────────────────────────────


def format_wallet_short(address: str) -> str:
    """Format wallet address for display."""
    if len(address) >= 14:
        return f"{address[:8]}...{address[-6:]}"
    return address


def get_or_create_template(card_type: str) -> Image.Image:
    """Get template image or create placeholder if doesn't exist."""
    template_config = CARD_TEMPLATES.get(card_type)
    if not template_config:
        raise ValueError(f"Unknown card type: {card_type}")

    template_path = os.path.join(TEMPLATE_DIR, template_config["template_file"])

    if os.path.exists(template_path):
        return Image.open(template_path)
    else:
        # Create placeholder template
        logger.warning(f"Template not found: {template_path}, creating placeholder")
        return create_placeholder_template(card_type, template_config)


def create_placeholder_template(card_type: str, config: dict) -> Image.Image:
    """Create a placeholder template if none exists."""
    img = Image.new("RGB", config["size"], color=config["colors"].get("background", "#0A0A0F"))
    draw = ImageDraw.Draw(img)

    # Add title
    title = config["name"]
    bbox = draw.textbbox((0, 0), title, font_size=72)
    title_width = bbox[2] - bbox[0]
    draw.text(
        ((config["size"][0] - title_width) / 2, 50),
        title,
        fill=config["colors"]["primary"],
        font_size=72,
    )

    # Add watermark
    draw.text(
        (config["size"][0] - 200, config["size"][1] - 50),
        "@cryptorugmunch",
        fill="#FFFFFF",
        font_size=32,
    )

    # Save as template
    template_path = os.path.join(TEMPLATE_DIR, config["template_file"])
    img.save(template_path)
    logger.info(f"Created placeholder template: {template_path}")

    return img


def generate_card(card_type: str, data: dict) -> dict:
    """
    Generate card by overlaying text on template.
    Returns image path and metadata.
    """
    try:
        config = CARD_TEMPLATES.get(card_type)
        if not config:
            return {"error": f"Unknown card type: {card_type}"}

        # Get or create template
        template = get_or_create_template(card_type)
        draw = ImageDraw.Draw(template)

        # Try to load font (use default if not available)
        try:
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            base_font = ImageFont.truetype(font_path, 32)
        except Exception:
            base_font = ImageFont.load_default()

        # Overlay text fields
        for field_name, field_config in config["text_fields"].items():
            if field_name in data:
                value = data[field_name]

                # Format value
                if "format" in field_config:
                    try:
                        # Handle wallet formatting
                        if field_name == "wallet_address":
                            value = format_wallet_short(str(value))
                        else:
                            value = field_config["format"].format(**{field_name: value, **data})
                    except Exception:
                        pass

                # Draw text
                position = field_config["position"]
                font_size = field_config.get("font_size", 32)
                color = field_config.get("color", "#FFFFFF")

                try:
                    font = ImageFont.truetype(font_path, font_size)
                except Exception:
                    font = base_font

                draw.text(position, str(value), fill=color, font=font)

        # Add watermark
        draw.text(
            (config["size"][0] - 250, config["size"][1] - 60),
            "@cryptorugmunch",
            fill="#FFFFFF",
            font_size=36,
        )

        # Generate unique filename
        data_hash = hashlib.md5(str(sorted(data.items())).encode()).hexdigest()[:12]
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"{card_type}_{timestamp}_{data_hash}.png"
        output_path = os.path.join(GENERATED_DIR, filename)

        # Save image
        template.save(output_path, "PNG")

        return {
            "status": "success",
            "card_type": card_type,
            "image_path": output_path,
            "image_url": f"/assets/generated_cards/{filename}",
            "filename": filename,
            "data": data,
            "generated_at": datetime.now(UTC).isoformat(),
            "share_urls": {
                "twitter": f"https://rugmunch.io/share/{card_type}/{data_hash}",
                "telegram": f"https://rugmunch.io/share/{card_type}/{data_hash}",
            },
        }

    except Exception as e:
        logger.error(f"Card generation failed: {e}")
        return {"status": "error", "error": str(e)}


# ── Convenience Functions ────────────────────────────────────


async def generate_win_card(
    wallet_address: str,
    token_symbol: str,
    pnl_usd: float,
    pnl_pct: float,
    entry_price: float,
    exit_price: float,
    hold_time: str,
    chain: str = "solana",
) -> dict:
    """Generate big win alert card."""
    data = {
        "wallet_address": wallet_address,
        "token_symbol": token_symbol.upper(),
        "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "hold_time": hold_time,
        "chain": chain,
    }
    return generate_card("big_win", data)


async def generate_loss_card(
    wallet_address: str,
    token_symbol: str,
    pnl_usd: float,
    pnl_pct: float,
    entry_price: float,
    exit_price: float,
    hold_time: str,
    chain: str = "solana",
) -> dict:
    """Generate loss porn card."""
    data = {
        "wallet_address": wallet_address,
        "token_symbol": token_symbol.upper(),
        "pnl_usd": abs(pnl_usd),
        "pnl_pct": abs(pnl_pct),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "hold_time": hold_time,
        "chain": chain,
    }
    return generate_card("big_loss", data)


async def generate_kol_scorecard(
    kol_handle: str,
    win_rate: float,
    total_calls: int,
    avg_pnl: float,
    followers: int,
    tier: str = "A",
) -> dict:
    """Generate KOL scorecard."""
    data = {
        "kol_handle": kol_handle,
        "win_rate": win_rate,
        "total_calls": total_calls,
        "avg_pnl": avg_pnl,
        "followers": followers,
        "tier": tier,
    }
    return generate_card("kol_scorecard", data)


async def generate_rug_call_card(token_symbol: str, amount_stolen: float, victims: int, rug_type: str) -> dict:
    """Generate rugpull alert card."""
    data = {
        "token_symbol": token_symbol.upper(),
        "amount_stolen": amount_stolen,
        "victims": victims,
        "rug_type": rug_type,
    }
    return generate_card("rug_call", data)


async def generate_whale_alert_card(
    wallet_label: str, action: str, amount: float, symbol: str, usd_value: float
) -> dict:
    """Generate whale movement card."""
    data = {
        "wallet_label": wallet_label,
        "action": action,
        "amount": amount,
        "symbol": symbol.upper(),
        "usd_value": usd_value,
    }
    return generate_card("whale_move", data)


async def generate_smart_money_card(wallet_label: str, token_symbol: str, position_size: float, pnl: float) -> dict:
    """Generate smart money pick card."""
    data = {
        "wallet_label": wallet_label,
        "token_symbol": token_symbol.upper(),
        "position_size": position_size,
        "pnl": pnl,
    }
    return generate_card("smart_money", data)


# ── Template Management ──────────────────────────────────────


def list_templates() -> list[dict]:
    """List available card templates."""
    return [
        {
            "id": key,
            "name": config["name"],
            "size": config["size"],
            "file": config["template_file"],
            "exists": os.path.exists(os.path.join(TEMPLATE_DIR, config["template_file"])),
        }
        for key, config in CARD_TEMPLATES.items()
    ]


def create_all_templates():
    """Create all placeholder templates."""
    for card_type, _config in CARD_TEMPLATES.items():
        try:
            get_or_create_template(card_type)
        except Exception as e:
            logger.error(f"Failed to create template for {card_type}: {e}")

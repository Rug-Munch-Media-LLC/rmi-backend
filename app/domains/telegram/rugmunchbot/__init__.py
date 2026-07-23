"""
RugMunchBot subsystem - modular architecture.

Re-exports key symbols for backward compatibility.
"""

# Re-export from new modules for backward compatibility
from app.domains.telegram.rugmunchbot.formatters import (
    EVM_RE,
    SOL_RE,
    TOKEN_RE,
    detect_chain,
    footer_links,
    format_scan_report,
    format_wallet_report,
    is_evm,
    is_owner,
    is_sol,
    paywall_text,
    sep,
    short_addr,
    social_proof_text,
    thin_sep,
    web_scan_button,
)
from app.domains.telegram.rugmunchbot.keyboards import (
    back_kb,
    buy_stars_kb,
    main_menu_kb,
    paywall_kb,
    pricing_kb,
    scan_result_kb,
    scamschool_kb,
    social_kb,
    topup_kb,
)
from app.domains.telegram.rugmunchbot.scanner import analyze_wallet, scan_token
from app.domains.telegram.rugmunchbot.states import check_spam, get_sol_price, is_owner

# Re-export main entry point
from app.domains.telegram.rugmunchbot.bot import main

__all__ = [
    "main",
    "check_spam",
    "get_sol_price",
    "is_owner",
    "is_evm",
    "is_sol",
    "detect_chain",
    "short_addr",
    "sep",
    "thin_sep",
    "footer_links",
    "web_scan_button",
    "social_proof_text",
    "paywall_text",
    "format_scan_report",
    "format_wallet_report",
    "scan_token",
    "analyze_wallet",
    "main_menu_kb",
    "scan_result_kb",
    "pricing_kb",
    "topup_kb",
    "buy_stars_kb",
    "scamschool_kb",
    "back_kb",
    "social_kb",
    "paywall_kb",
]

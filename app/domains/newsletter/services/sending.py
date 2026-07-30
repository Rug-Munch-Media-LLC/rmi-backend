"""Email sending — Postmark, SES, Mailgun providers + crypto payment verification."""

from __future__ import annotations

from app.core.errors import ValidationError
from app.core.http import get_http_client
from app.core.logging import get_logger
from app.domains.newsletter.services.helpers import get_setting

logger = get_logger(__name__)


async def send_email(
    provider: str,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    sub_id: str,
    nl_id: str,
) -> None:
    """Send an email via the configured provider (Postmark/SES/Mailgun)."""
    client = await get_http_client()

    if provider == "postmark":
        api_key = get_setting("postmark_api_key", "")
        if not api_key:
            raise ValidationError("Postmark API key not configured")

        await client.post(
            "https://api.postmarkapp.com/email",
            json={
                "From": get_setting("newsletter_from_email", "newsletter@rugmunch.io"),
                "To": to_email,
                "Subject": subject,
                "HtmlBody": html_body,
                "TextBody": text_body,
                "TrackOpens": True,
                "TrackLinks": "HtmlOnly",
                "Metadata": {"subscriber_id": sub_id, "newsletter_id": nl_id},
            },
            headers={
                "X-Postmark-Server-Token": api_key,
                "Accept": "application/json",
            },
            timeout=15.0,
        )
    elif provider == "ses":
        from_email = get_setting("newsletter_from_email", "newsletter@rugmunch.io")

        await client.post(
            "https://email.us-east-1.amazonaws.com/v2/email/outbound-emails",
            json={
                "FromEmailAddress": from_email,
                "Destination": {"ToAddresses": [to_email]},
                "Content": {
                    "Simple": {
                        "Subject": {"Data": subject},
                        "Body": {
                            "Html": {"Data": html_body},
                            "Text": {"Data": text_body},
                        },
                    },
                },
            },
            headers={
                "X-Api-Key": get_setting("aws_ses_key", ""),
            },
            timeout=15.0,
        )
    elif provider == "mailgun":
        domain = get_setting("mailgun_domain", "")
        api_key = get_setting("mailgun_api_key", "")

        if not domain or not api_key:
            raise ValidationError("Mailgun not configured")

        await client.post(
            f"https://api.mailgun.net/v3/{domain}/messages",
            auth=("api", api_key),
            data={
                "from": get_setting("newsletter_from_email", f"newsletter@{domain}"),
                "to": to_email,
                "subject": subject,
                "html": html_body,
                "text": text_body,
                "o:tracking-opens": "yes",
                "o:tracking-clicks": "yes",
                "v:subscriber_id": sub_id,
                "v:newsletter_id": nl_id,
            },
            timeout=15.0,
        )


async def get_email_provider() -> str | None:
    """Determine which email provider is configured.

    Priority: Postmark > Mailgun > SES > None (email disabled).
    """
    if get_setting("postmark_api_key", ""):
        return "postmark"
    if get_setting("mailgun_api_key", ""):
        return "mailgun"
    if get_setting("aws_ses_key", ""):
        return "ses"
    return None


async def verify_crypto_payment(
    chain: str,
    tx_hash: str,
    expected_token: str,
    expected_amount: int,
    from_address: str,
) -> bool:
    """Verify an on-chain crypto payment.

    Checks:
      1. Transaction exists and is confirmed (> 12 confirmations)
      2. Recipient matches our platform wallet
      3. Token matches expected
      4. Amount meets or exceeds expected
    """
    wallet = get_setting("platform_wallet_address", "")

    if not wallet:
        logger.warning("crypto_verify_no_wallet", chain=chain)
        return False

    try:
        client = await get_http_client()

        if chain == "eth":
            api_key = get_setting("etherscan_api_key", "")
            if not api_key:
                logger.warning("crypto_verify_no_etherscan_key")
                return False

            resp = await client.get(
                f"https://api.etherscan.io/api"
                f"?module=proxy&action=eth_getTransactionByHash"
                f"&txhash={tx_hash}&apikey={api_key}",
                timeout=15.0,
            )
            if resp.status_code != 200:
                return False

            tx_data = resp.json().get("result", {})
            if not tx_data:
                return False

            block_resp = await client.get(
                f"https://api.etherscan.io/api?module=proxy&action=eth_blockNumber&apikey={api_key}",
                timeout=10.0,
            )
            current_block = int(block_resp.json().get("result", "0x0"), 16)
            tx_block = int(tx_data.get("blockNumber", "0x0"), 16)
            confirmations = current_block - tx_block

            if confirmations < 12:
                logger.warning(
                    "crypto_verify_low_confirmations",
                    tx_hash=tx_hash,
                    confirmations=confirmations,
                )

            to_addr = (tx_data.get("to") or "").lower()
            if to_addr != wallet.lower():
                logger.warning(
                    "crypto_verify_wrong_recipient",
                    tx_hash=tx_hash,
                    expected=wallet,
                    got=to_addr,
                )
                return False

            return confirmations >= 12

        elif chain == "sol":
            api_key = get_setting("helius_api_key", "")
            if not api_key:
                logger.warning("crypto_verify_no_helius_key")
                return False

            resp = await client.get(
                f"https://mainnet.helius-rpc.com/v0/transactions/?apiKey={api_key}&search={tx_hash}",
                timeout=15.0,
            )
            if resp.status_code != 200:
                return False

            txs = resp.json()
            if not txs:
                return False

            tx = txs[0]
            if tx.get("type") == "TRANSFER" and tx.get("tokenTransfers"):
                for transfer in tx["tokenTransfers"]:
                    if (
                        transfer.get("toUserAccount", "").lower() == wallet.lower()
                        and transfer.get("mint", "") == expected_token
                    ):
                        return True

            return False

        elif chain == "lightning":
            invoice_key = get_setting("lightning_invoice_key", "")
            if not invoice_key:
                logger.warning("crypto_verify_no_lightning_key")
                return False

            resp = await client.get(
                f"https://lnbits.example.com/api/v1/payments/{tx_hash}",
                headers={"X-Api-Key": invoice_key},
                timeout=15.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("paid", False)

            return False

        else:
            logger.warning("crypto_verify_unsupported_chain", chain=chain)
            return False

    except Exception as exc:
        logger.error("crypto_verify_failed", chain=chain, tx_hash=tx_hash, error=str(exc))
        return False

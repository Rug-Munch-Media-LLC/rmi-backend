# Email Forwarding Setup Guide
# =============================
#
# This system forwards emails from your domains to a Gmail inbox,
# then polls that inbox and forwards emails to your Telegram admin bot.
#
# NO AUTO-REPLIES - emails appear in Telegram for admin review.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SETUP STEPS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. CREATE GMAIL RECEIVER ACCOUNT
   ------------------------------
   Create a new Gmail account (or use existing):

   Example: rugmunch.admin@gmail.com

   IMPORTANT: Enable 2FA and create an App Password:
   - Go to: https://myaccount.google.com/security
   - Enable 2-Step Verification (if not already)
   - Go to: https://myaccount.google.com/apppasswords
   - Create a new app password (select "Mail" and your device)
   - COPY THE 16-CHARACTER PASSWORD (no spaces)

   You'll need:
   - Email: rugmunch.admin@gmail.com (or your choice)
   - App Password: XXXX XXXX XXXX XXXX (16 chars)

2. CONFIGURE CLOUDFLARE EMAIL ROUTING
   -----------------------------------

   For rugmunch.io:
   - Log in to Cloudflare dashboard
   - Go to your rugmunch.io zone
   - Click "Email" → "Email Routing"
   - Click "Add Route"
   - Create these addresses (all forward to same Gmail):

     admin@rugmunch.io → rugmunch.admin@gmail.com
     support@rugmunch.io → rugmunch.admin@gmail.com
     contact@rugmunch.io → rugmunch.admin@gmail.com

   For cryptorugmunch.com:
   - Go to your cryptorugmunch.com zone
   - Click "Email" → "Email Routing"
   - Click "Add Route"
   - Create these addresses:

     admin@cryptorugmunch.com → rugmunch.admin@gmail.com
     team@cryptorugmunch.com → rugmunch.admin@gmail.com
     info@cryptorugmunch.com → rugmunch.admin@gmail.com

   IMPORTANT: Make sure Email Routing is ENABLED (toggle ON)

3. CONFIGURE BACKEND ENVIRONMENT VARIABLES
   ----------------------------------------

   Add these to your /root/.secrets/project_envs/rmi-backend.env:

   # Email Forwarding
   EMAIL_RECEIVER=rugmunch.admin@gmail.com
   EMAIL_PASSWORD=xxxx xxxx xxxx xxxx  (app password, no spaces)
   TELEGRAM_ADMIN_CHAT_ID=123456789    (your admin Telegram chat ID)
   EMAIL_POLL_INTERVAL=60              # check every 60 seconds

   # Get your Telegram Chat ID:
   - Message your bot: @userinfobot
   - Send any message
   - It will reply with your ID (e.g., 123456789)

4. START RESTART BACKEND
   ----------------------
   Restart your backend service:

   sudo systemctl restart rmi-backend

   Or if running manually:
   cd /srv/rmi/backend
   source venv/bin/activate
   python main.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

VERIFICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Check logs for email polling startup:
  sudo journalctl -u rmi-backend -f

You should see:
  [RMI] 📧 Email polling enabled - starting IMAP service

Send a test email to:
  admin@rugmunch.io

Wait 1-2 minutes. You should receive a Telegram message in your admin chat:

  📧 New Email

  From: sender@example.com
  Domain: rugmunch.io
  Subject: Test Email
  Time: 2026-05-01 12:34:56

  ━━━━━━━━━━━━━━━━━━━━

  This is the email body...

  Inbox: rugmunch.admin@gmail.com

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FAQ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Q: Can users reply to these emails?
A: Emails can be replied to (standard email), but your system
   won't auto-reply. Admins review in Telegram and respond manually
   via their email client.

Q: What happens if Gmail IMAP fails?
A: The poller logs errors and retries on next interval. Emails
   are marked as read, so they won't be re-sent to Telegram.

Q: Can I forward to a different email?
A: Yes, just change EMAIL_RECEIVER to any Gmail/IMAP-enabled
   address. You'll need an app password for Gmail.

Q: How many emails can I receive?
A: Gmail free accounts: 15GB storage (roughly 10,000+ emails)
   Cloudflare Email Routing: Unlimited forwards

Q: Do I need to configure MX records?
A: NO! Cloudflare Email Routing handles this automatically when
   you enable Email Routing for your zone.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TROUBLESHOOTING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"IMAP login failed":
  - Check EMAIL_PASSWORD is correct (use app password, not regular password)
  - Ensure IMAP is enabled in Gmail settings
  - Try logging into Gmail IMAP manually: telnet imap.gmail.com 993

"No emails appearing in Telegram":
  - Verify Cloudflare Email Routing is ENABLED
  - Check that emails are actually arriving in Gmail inbox
  - Look at backend logs for polling errors
  - Wait up to 2 minutes (poll interval)

"Duplicate emails in Telegram":
  - Emails are marked as read after first poll
  - Check Gmail isn't moving emails back to inbox
  - Reset seen_message_ids by restarting backend

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

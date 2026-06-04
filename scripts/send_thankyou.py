#!/usr/bin/env python3
"""
Sun & Soil Lawn Care - Automated Thank You Email Script

Reads Job Tracker Google Sheet and sends branded HTML emails via Gmail API.
Marks each row with today's date in column H "Email Sent" after sending.
"""

import argparse
import base64
import json
import logging
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ── Logging ──────────────────────────────────────────────────────────────────
log_dir = Path.home() / "OpenJarvis" / "logs"
log_dir.mkdir(exist_ok=True)

logger = logging.getLogger("send_thankyou")
logger.setLevel(logging.INFO)

file_handler = logging.FileHandler(log_dir / "thankyou_email.log")
file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(file_handler)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
logger.addHandler(console_handler)

# ── Configuration ─────────────────────────────────────────────────────────────
SPREADSHEET_ID = "12dDbfDGX1rjicSetU7Ex9r48sWAt4ko8L-O_tJz72Bo"
SHEET_NAME = "Sheet1"
SENDER_EMAIL = "sunandsoillawncare@gmail.com"
GMAIL_CREDENTIALS = Path.home() / ".openjarvis" / "connectors" / "lawncare.json"

# QR code embedded from Public_Profile_View_QR.png
QR_CODE_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAMgAAADIAQAAAACFI5MzAAABy0lEQVR4nO1XS46DMAy1KVKy"
    "CzdwL8KU3ou29CPNtWi5SHIDsiNSacahs2U3mFnUC1r0FnZe7OdHFucig7n4IB/k/yAOEXPA"
    "zB9QA5z5rZCujaelgaGkqx0ANjG+Fsozj4Tp0BwUskRJJV7BFBpd7vTX4nlmEadaE3bxsk4F"
    "KvZA/Ptt0wVQbMUr4O5vnN8V4IayGBEz6QqIZ+EJuutrUBc48VsvXAHwBJy3sfW4DXtux8Xy"
    "zCIpZQ14KGqCkhrAnbgiqRFzGpC51w/fkLpK3wKFTXz+cv8FtYNSnANdFjdQLWzs0PFcevFZ"
    "cOrenyJL84gsS7h9C6MoB3zoG5g7r6dQweauLuIV6BIDDnxyMiM2BOIcEG+DG+mIV+srwz2J8"
    "op0hJOFrj9voXMN+WqhPLMIHzoiApjGqZG4ExfKM48QPFgTQ+V5OYBnt/SQriDlVVaNBRC7N"
    "KYkyvvEl3kyByZPj2hNu1CeWeTtlYe0nfk+buD34rOQvDKp1ufOvLgj1EpeOclh9BWbhBW2c"
    "wqnSwqYPNK0ntZw60Pnrjb9oTQVa3jlaSMmMXIgz0HyyuwPzGEXjvxYQRMnr5w+GW2892xT5"
    "P3BB/kgf4r8AP9w2niCC7mYAAAAAElFTkSuQmCC"
)

EMAIL_TEMPLATE = """\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
  <div style="background-color: #2e7d32; padding: 20px; border-radius: 8px 8px 0 0;">
    <h2 style="color: white; margin: 0;">&#x2618; Sun &amp; Soil Lawn Care</h2>
  </div>
  <div style="border: 1px solid #ddd; padding: 30px; border-radius: 0 0 8px 8px;">
    <h3 style="color: #2e7d32;">Thank You for Choosing Sun &amp; Soil Lawn Care!</h3>
    <p>Hi {first_name},</p>
    <p>Thank you for choosing Sun &amp; Soil Lawn Care! We just completed your
       <strong>{job_type}</strong> service and hope everything looks great.</p>
    <p>If you have any questions or would like to schedule your next service,
       don't hesitate to reach out:</p>
    <p style="font-size: 18px;">&#128222; <strong>(239) 878-3897</strong></p>
    <p>We appreciate your business and look forward to keeping your property looking its best!</p>
    <hr style="border: 1px solid #eee; margin: 20px 0;">
    <p>Best regards,<br>
    <strong>Chris Bennett</strong><br>
    Sun &amp; Soil Lawn Care<br>
    North Fort Myers, FL</p>
    <br>
    <img src="data:image/png;base64,{qr_code}"
         alt="Scan to view our profile" width="150" height="150"/>
    <p style="font-size: 12px; color: #999;">Scan the QR code to view our profile</p>
  </div>
</body>
</html>"""


def load_credentials():
    with open(GMAIL_CREDENTIALS) as f:
        data = json.load(f)

    creds = Credentials(
        token=data.get("access_token") or data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        data["access_token"] = creds.token
        data["token"] = creds.token
        with open(GMAIL_CREDENTIALS, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("OAuth token refreshed and saved.")

    return creds


def ensure_headers(sheets):
    result = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A1:H1",
    ).execute()

    headers = (result.get("values") or [[]])[0]
    updates = []

    if len(headers) < 7 or headers[6].strip() != "Email":
        updates.append({"range": f"{SHEET_NAME}!G1", "values": [["Email"]]})
    if len(headers) < 8 or headers[7].strip() != "Email Sent":
        updates.append({"range": f"{SHEET_NAME}!H1", "values": [["Email Sent"]]})

    if updates:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()
        logger.info("Headers added: %s", [u["range"] for u in updates])


def get_rows(sheets):
    result = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A2:H",
    ).execute()
    return result.get("values") or []


def build_email(to_email, first_name, job_type):
    html = EMAIL_TEMPLATE.format(
        first_name=first_name,
        job_type=job_type,
        qr_code=QR_CODE_BASE64,
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Thank You for Choosing Sun & Soil Lawn Care, {first_name}!"
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html"))
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def mark_sent(sheets, row_index):
    today = datetime.today().strftime("%m/%d/%Y")
    cell = f"{SHEET_NAME}!H{row_index + 2}"  # +2: row 1 is header, list is 0-indexed
    sheets.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=cell,
        valueInputOption="RAW",
        body={"values": [[today]]},
    ).execute()


def main(dry_run=False):
    logger.info("=== Thank you email run started (dry_run=%s) ===", dry_run)

    creds = load_credentials()
    sheets = build("sheets", "v4", credentials=creds)
    gmail = build("gmail", "v1", credentials=creds)

    ensure_headers(sheets)
    rows = get_rows(sheets)

    sent = skipped = errors = 0

    for i, row in enumerate(rows):
        while len(row) < 8:
            row.append("")

        date_completed = row[0].strip()
        client_name    = row[1].strip()
        job_type       = row[2].strip() or "lawn care"
        email          = row[6].strip()
        email_sent     = row[7].strip()

        if not date_completed or not client_name:
            continue

        if email_sent:
            skipped += 1
            continue

        if not email:
            logger.warning("Row %d — no email for '%s', skipping.", i + 2, client_name)
            skipped += 1
            continue

        first_name = client_name.split()[0]

        try:
            raw = build_email(email, first_name, job_type)
            if dry_run:
                logger.info(
                    "[DRY RUN] Row %d — would send to %s (%s) for '%s' service",
                    i + 2, email, first_name, job_type,
                )
            else:
                gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
                mark_sent(sheets, i)
                logger.info(
                    "Row %d — sent to %s (%s) for '%s' service",
                    i + 2, email, first_name, job_type,
                )
            sent += 1
        except Exception as exc:
            logger.error("Row %d — failed for '%s': %s", i + 2, client_name, exc)
            errors += 1

    logger.info("=== Done: %d sent, %d skipped, %d errors ===", sent, skipped, errors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send Sun & Soil thank you emails")
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending")
    args = parser.parse_args()
    main(dry_run=args.dry_run)

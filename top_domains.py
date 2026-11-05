#!/usr/bin/env python3

#
# Purpose: Count who emails you most, grouped by sender comain, for mail from CUTOFF_YEAR onward
#
import argparse
import mailbox
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime

#
# Purpose: Count who emails you most, grouped by sender comain, for mail from CUTOFF_YEAR onward
#
INBOX = os.path.expandvars("$PROFILE/ImapMail/imap.gmail.com/INBOX")
CUTOFF_YEAR = 2026

parser = argparse.ArgumentParser(description="Count recent emails by sender domain")
parser.add_argument(
    "--days",
    type=int,
    default=14,
    help="how many days back to look (default: 14)",
)
args = parser.parse_args()
cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
counts = Counter()
skipped = 0

for msg in mailbox.mbox(INBOX, create=False):
    try:
        sent = parsedate_to_datetime(msg["Date"])
    except (TypeError, ValueError):
        skipped += 1
        continue
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=timezone.utc)
    if sent < cutoff:
        continue
    _, address = parseaddr(msg["From"] or "")
    if "@" not in address:
        skipped += 1
        continue
    domain = address.split("@", 1)[-1].lower()
    counts[domain] += 1

for domain, n in counts.most_common(20):
    print(f"{n:6}  {domain}")

total = sum(counts.values())
print(f"\n{total} emails in the last {args.days} days (since {cutoff: %b %d}),{skipped} skipped")
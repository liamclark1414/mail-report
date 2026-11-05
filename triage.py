#!/usr/bin/env python3
# ---------------------------------------------------------------
# PURPOSE: Ask a local AI model (through Ollama) to triage recent
#          personal email into OPPORTUNITY / NEEDS_REPLY / FYI / IGNORE.
# Privacy: Ollama runs on localhost, so no email leaves this machine.
# ---------------------------------------------------------------

import argparse
import email
import email.policy
import json
import mailbox
import os
import re
import urllib.request   # Sends HTTP requests; this is how we talk to Ollama
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# --- Settings ---
INBOX = os.path.expandvars("$PROFILE/ImapMail/imap.gmail.com/INBOX")
OLLAMA_URL = "http://localhost:11434/api/chat"   # Ollama's chat endpoint on your machine
MODEL = "qwen3:14b"
BODY_CHARS = 1500   # Only send the start of each email: faster, and usually enough

# --- The spec for the model ---
# This prompt IS the product requirement. Edit the definitions to match
# what "opportunity" means for YOU, then re-run and compare results.
INSTRUCTIONS = """You triage email for one person. Put the email in exactly one category:
- OPPORTUNITY: a job, project, collaboration, deal, or invitation the reader might actually want.
- NEEDS_REPLY: a real person is asking the reader something or waiting on them.
- FYI: worth knowing but needs no action (account notices, receipts, updates).
- IGNORE: marketing, promotions, or noise.
Write a one-sentence summary and a short reason for the category you chose."""

# --- The required answer shape (a JSON Schema) ---
# Ollama forces the model's reply to match this, so the script can rely on it.
# "enum" means category must be one of these exact four words.
SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["OPPORTUNITY", "NEEDS_REPLY", "FYI", "IGNORE"]},
        "summary": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["category", "summary", "reason"],
}


# --- Functions: named, reusable chunks of code ---
# "def" defines a function. Code inside it runs only when the function is called.
# The text in triple quotes right under "def" is a docstring: it describes the function.

def get_body_text(msg):
    """Return the first BODY_CHARS characters of an email's text."""
    # get_body picks the best part of the email: plain text if it exists, else HTML
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()   # Decodes the raw bytes into readable text
    except (LookupError, UnicodeDecodeError):
        return ""   # The email declared a broken or unknown character encoding

    if part.get_content_type() == "text/html":
        # Crude HTML cleanup using regular expressions (text patterns):
        # remove <style> and <script> blocks entirely, then strip every <tag>.
        text = re.sub(r"(?is)<(style|script).*?</\1>", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)

    text = re.sub(r"\s+", " ", text).strip()   # Squash runs of spaces and newlines into one space
    return text[:BODY_CHARS]   # [:N] means "the first N characters"


def ask_model(sender, subject, body):
    """Send one email to Ollama and return its verdict as a Python dictionary."""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": INSTRUCTIONS},   # The standing rules
            {"role": "user", "content": f"From: {sender}\nSubject: {subject}\n\n{body}"},
        ],
        "format": SCHEMA,       # Force the answer into our JSON shape
        "think": False,         # Skip Qwen's slow "thinking out loud" step
        "stream": False,        # Return the whole answer at once, not word by word
        "keep_alive": "10m",    # Keep the model loaded between emails
        "options": {"temperature": 0},   # 0 = least random: same input gives about the same output
    }
    # Build an HTTP POST request with the payload as JSON text
    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),   # dict -> JSON text -> bytes
        headers={"Content-Type": "application/json"},
    )
    # "with" opens the connection and closes it automatically afterwards.
    # timeout=300 gives up after 5 minutes instead of hanging forever.
    with urllib.request.urlopen(request, timeout=300) as response:
        reply = json.load(response)   # JSON text -> Python dict
    # The model's answer is itself JSON text inside reply["message"]["content"]
    return json.loads(reply["message"]["content"])


# --- Command-line options ---
parser = argparse.ArgumentParser(description="Triage recent personal email with a local AI model.")
parser.add_argument("--days", type=int, default=7, help="how many days back to look (default: 7)")
parser.add_argument("--limit", type=int, default=10, help="max emails to send to the model (default: 10)")
args = parser.parse_args()

cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

# --- Step 1: collect candidate emails (fast, no AI) ---
# factory=...: read each email with Python's modern email parser, which also
# decodes encoded headers like "=?UTF-8?B?...?=" into readable text.
# A lambda is a tiny unnamed function: "given f, parse f".
def parse_email(f):
    return email.message_from_binary_file(f, policy=email.policy.default)

candidates = []   # A list; we'll append (date, email) pairs to it
bulk = 0

for msg in mailbox.mbox(INBOX, factory=parse_email, create=False):
    try:
        sent = parsedate_to_datetime(msg["Date"])
    except (TypeError, ValueError):
        continue
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=timezone.utc)
    if sent < cutoff:
        continue
    # Pre-filter: bulk/marketing mail announces itself with this header.
    # Skipping it here saves the slow AI for mail that needs judgment.
    if msg["List-Unsubscribe"]:
        bulk += 1
        continue
    candidates.append((sent, msg))

# Newest first. key=... tells sort what to compare: item[0] is the date.
candidates.sort(key=lambda item: item[0], reverse=True)
to_send = candidates[: args.limit]

# flush=True prints immediately instead of waiting, so you see progress live
print(f"{len(candidates)} personal emails in the last {args.days} days "
      f"({bulk} bulk skipped). Sending {len(to_send)} to {MODEL}...\n", flush=True)

# --- Step 2: ask the model about each one ---
# A dictionary of lists: one bucket per category
results = {"OPPORTUNITY": [], "NEEDS_REPLY": [], "FYI": [], "IGNORE": []}

# enumerate(..., start=1) gives a counter alongside each item: 1, 2, 3...
for i, (sent, msg) in enumerate(to_send, start=1):
    sender = str(msg["From"] or "")
    subject = str(msg["Subject"] or "(no subject)")
    print(f"[{i}/{len(to_send)}] {subject[:60]}", flush=True)
    try:
        verdict = ask_model(sender, subject, get_body_text(msg))
    except (OSError, ValueError, KeyError) as err:
        # OSError:    Ollama not running, or the connection failed
        # ValueError: the reply wasn't valid JSON
        # KeyError:   the reply was missing an expected field
        print(f"    ! skipped, model call failed: {err}")
        continue
    results[verdict["category"]].append((sent, sender, verdict))

# --- Step 3: print the report, most important categories first ---
for category in ["OPPORTUNITY", "NEEDS_REPLY", "FYI"]:
    items = results[category]
    print(f"\n=== {category} ({len(items)}) ===")
    for sent, sender, verdict in items:
        print(f"- {sent:%b %d}  {sender}")
        print(f"    {verdict['summary']}")
        print(f"    why: {verdict['reason']}")

print(f"\n({len(results['IGNORE'])} more marked IGNORE)")
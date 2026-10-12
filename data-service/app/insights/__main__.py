"""python -m app.insights [--email] [--trigger schedule]  (used by the scheduled GitHub Action)"""
import argparse
import json
import sys

from app.insights.report import generate

ap = argparse.ArgumentParser()
ap.add_argument("--email", action="store_true", help="send the report by e-mail (needs SMTP_* variables)")
ap.add_argument("--trigger", default="cli")
args = ap.parse_args()
result = generate(trigger=args.trigger, send_email=args.email)
print(json.dumps(result, indent=2, default=str))
sys.exit(1 if args.email and result["email_status"] and result["email_status"].startswith("FAILED") else 0)

"""Email notifier for Agent 3 drafts.

Sends a review email with:
  - the report.md content (why each draft exists)
  - each draft file as attachment
  - exact next steps

Config (config.yaml):
  notify:
    to: "you@domain.com"
    from: "aurora10@gmail.com"
    gmail_user: "aurora10@gmail.com"
    gmail_app_password: "xxxx xxxx xxxx xxxx"   # app password, NOT your login password
    subject_prefix: "[SEO drafts]"
"""
import smtplib
from email.message import EmailMessage
from pathlib import Path


def send_review_email(cfg: dict, drafts_dir: str, report: str) -> None:
    n = cfg["notify"]
    files = sorted(Path(drafts_dir).glob("*.json"))

    msg = EmailMessage()
    msg["From"] = n["from"]
    msg["To"] = n["to"]
    msg["Subject"] = (f"{n.get('subject_prefix', '[SEO drafts]')} "
                      f"{len(files)} new draft(s) to review")

    body = f"""Hi,

Agent 3 has generated {len(files)} new content draft(s) based on the latest
market analysis. Everything is LOCAL only — nothing is live until you act.

WHY THESE DRAFTS EXIST
----------------------
{report}

WHAT TO DO (5-10 min)
---------------------
1. Review each attached .json file (they contain proposed copy;
   edit freely — it's your voice, LLM is just the drafter).
2. Merge approved fragments into src/messages/nl.json of the
   constructief repo (exact keys are inside each file).
3. git add, commit, push to google-sheets — Vercel deploys.
4. Next GSC sync will measure the impact.

Files attached: {', '.join(f.name for f in files)}

— SEO agent
"""
    msg.set_content(body)

    for f in files:
        msg.add_attachment(f.read_bytes(),
                           maintype="application", subtype="json",
                           filename=f.name)
    # also attach the report for convenience
    report_path = Path(drafts_dir) / "report.md"
    if report_path.exists():
        msg.add_attachment(report_path.read_text(),
                           subtype="plain", filename="report.md")

    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(n["gmail_user"], n["gmail_app_password"].replace(" ", ""))
        s.send_message(msg)
    print(f"Email sent to {n['to']} ({len(files)} drafts attached)")

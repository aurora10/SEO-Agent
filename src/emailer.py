"""Reusable plain-text email sender (Gmail) for job alerts / failures."""
import smtplib
from email.message import EmailMessage


def send(cfg: dict, subject: str, body: str) -> None:
    n = cfg.get("notify")
    if not n:
        return
    msg = EmailMessage()
    msg["From"] = n["from"]
    msg["To"] = n["to"]
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(n["gmail_user"], n["gmail_app_password"].replace(" ", ""))
        s.send_message(msg)
    print(f"  -> email sent to {n['to']}")

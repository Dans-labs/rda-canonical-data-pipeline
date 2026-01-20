import logging
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import akmi_utils as a_commons
import tomli
from dynaconf import Dynaconf

# Determine project root (BASE_DIR). Prefer an explicit env var, otherwise search upwards
base_dir = os.getenv("BASE_DIR")

def _find_project_root(start_path: str, markers=('pyproject.toml', 'conf', '.git')) -> str:
    p = os.path.abspath(start_path)
    while True:
        for m in markers:
            if os.path.exists(os.path.join(p, m)):
                return p
        parent = os.path.dirname(p)
        if parent == p:
            # fallback: go up four levels from this file (best-effort)
            return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        p = parent

if not base_dir:
    base_dir = _find_project_root(os.path.dirname(os.path.abspath(__file__)))
    os.environ["BASE_DIR"] = base_dir

app_settings = Dynaconf(root_path=os.path.join(os.environ["BASE_DIR"], 'conf'), settings_files=["*.toml"],
                    environments=True)

def get_project_details(base_dir: str, keys: list):
    with open(os.path.join(base_dir, 'pyproject.toml'), 'rb') as file:
        package_details = tomli.load(file)
    poetry = package_details['project']
    return {key: poetry[key] for key in keys}

def send_mail(subject: str, body: str, to: list | None = None, from_addr: str | None = None) -> bool:
    mail_host = app_settings.get("mail_host") or os.environ.get("MAIL_HOST") or "smtp.gmail.com"
    try:
        mail_port = int(app_settings.get("mail_port", os.environ.get("MAIL_PORT", 587)))
    except Exception:
        mail_port = 587

    mail_use_tls = app_settings.get("mail_use_tls", os.environ.get("MAIL_USE_TLS", True))
    mail_use_ssl = app_settings.get("mail_use_ssl", os.environ.get("MAIL_USE_SSL", False))
    mail_use_auth = app_settings.get("mail_use_auth", os.environ.get("MAIL_USE_AUTH", False))

    mail_usr = app_settings.get("mail_usr") or os.environ.get("MAIL_USR")
    mail_pass = app_settings.get("mail_pass") or os.environ.get("MAIL_PASS")

    mail_to = app_settings.get("mail_to", os.environ.get("MAIL_TO"))


    from_addr = from_addr or app_settings.get("mail_from") or os.environ.get("MAIL_FROM") or mail_usr or "no-reply@example.com"

    # retry configuration
    try:
        retries = int(os.environ.get("MAIL_SEND_RETRIES", app_settings.get("mail_send_retries") or 3))
    except Exception:
        retries = 3
    try:
        interval = int(os.environ.get("MAIL_SEND_INTERVAL", app_settings.get("mail_send_interval") or 2))
    except Exception:
        interval = 2

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = ", ".join(mail_to)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    attempt = 1
    while attempt <= retries:
        try:
            if mail_use_ssl:
                logging.debug("[mail attempt %d/%d] Connecting to SMTP (SSL) %s:%s", attempt, retries, mail_host, mail_port)
                with smtplib.SMTP_SSL(mail_host, mail_port, timeout=10) as server:
                    if mail_use_auth and mail_usr and mail_pass:
                        server.login(mail_usr, mail_pass)
                    server.sendmail(from_addr, mail_to, msg.as_string())
            else:
                logging.debug("[mail attempt %d/%d] Connecting to SMTP %s:%s (tls=%s)", attempt, retries, mail_host, mail_port, mail_use_tls)
                with smtplib.SMTP(mail_host, mail_port, timeout=10) as server:
                    server.ehlo()
                    if mail_use_tls:
                        # Only attempt STARTTLS if the server advertises it
                        if server.has_extn("starttls"):
                            server.starttls()
                            server.ehlo()
                        else:
                            logging.warning("STARTTLS extension not supported by server; continuing without TLS.")
                    # Only attempt login if auth is requested and server supports AUTH
                    if mail_use_auth and mail_usr and mail_pass:
                        if server.has_extn("auth"):
                            server.login(mail_usr, mail_pass)
                        else:
                            logging.warning("SMTP server does not advertise AUTH extension; skipping login.")
                    server.sendmail(from_addr, mail_to, msg.as_string())

            logging.info("Email sent successfully to %s", mail_to)
            return True
        except smtplib.SMTPAuthenticationError as e:
            logging.error("Authentication failed when sending email: %s", e)
            return False
        except Exception as e:
            logging.warning("Failed to send email on attempt %d/%d: %s", attempt, retries, e)
            if attempt < retries:
                logging.info("Retrying email send in %s seconds...", interval)
                try:
                    time.sleep(interval)
                except Exception:
                    pass
            attempt += 1

    logging.error("All attempts to send email failed (%d attempts)", retries)
    return False

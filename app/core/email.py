import os
import requests
import logging
from typing import Optional, Dict
from .config import settings

# Configure logging
logger = logging.getLogger(__name__)

class EmailSender:
    """
    Email sender utility class using Mailtrap SANDBOX API (HTTP).
    Fully dynamic configuration via Environment Variables.
    """
    
    def __init__(self):
        self.api_token = settings.MAILTRAP_TOKEN
        self.use_sandbox = settings.MAILTRAP_USE_SANDBOX
        self.inbox_id = settings.MAILTRAP_INBOX_ID

        # Sender details
        self.from_email = settings.FROM_EMAIL
        self.from_name = settings.FROM_NAME

        # Log config status at init for debugging
        mode = "SANDBOX" if self.use_sandbox else "LIVE"
        logger.info(f"EmailSender initialized in {mode} mode | token_set={bool(self.api_token)} | from={self.from_email}")
        if not self.api_token:
            logger.warning("MAILTRAP_TOKEN is not set - emails will not be sent!")
        if self.use_sandbox and not self.inbox_id:
            logger.warning("MAILTRAP_INBOX_ID is not set - sandbox emails will fail!")

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        """
        Sends an email using either Sandbox or Live API based on settings.
        """
        # 1. Validation
        if not self.api_token:
            logger.error("❌ Missing Env Var: MAILTRAP_TOKEN")
            return False
            
        # Sandbox specific validation
        if self.use_sandbox and not self.inbox_id:
            logger.error("❌ Missing Env Var: MAILTRAP_INBOX_ID (Required for Sandbox mode)")
            return False

        # 2. Configure Endpoint and Headers based on mode
        url: str
        headers: Dict[str, str]

        if self.use_sandbox:
            # --- SANDBOX MODE ---
            # URL includes the Inbox ID
            url = f"https://sandbox.api.mailtrap.io/api/send/{self.inbox_id}"
            
            # Sandbox uses 'Api-Token' header
            headers = {
                "Api-Token": self.api_token,
                "Content-Type": "application/json"
            }
            logger.info(f"📧 Preparing to send SANDBOX email to {to_email} (Inbox {self.inbox_id})...")
        else:
            # --- LIVE SENDING MODE ---
            # Standard Sending API URL
            url = "https://send.api.mailtrap.io/api/send"
            
            # Live API uses 'Authorization: Bearer' header
            headers = {
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json"
            }
            logger.info(f"📧 Preparing to send LIVE email to {to_email} via Mailtrap Sending API...")

        # 3. Payload Construction
        # Mailtrap Sending API and Sandbox share the same payload structure
        payload = {
            "from": {
                "email": self.from_email,
                "name": self.from_name
            },
            "to": [
                {"email": to_email}
            ],
            "subject": subject,
            "html": html_content,
            "text": text_content if text_content else "Please view this email in an HTML compatible client.",
            "category": "Transactional"
        }

        # 4. Execute Request
        try:
            response = requests.post(
                url, 
                headers=headers, 
                json=payload, 
                timeout=10
            )

            # 5. Handle Response
            if response.status_code in [200, 201]: # Mailtrap sometimes returns 201 for creation
                logger.info(f"✅ Email sent successfully to {to_email}")
                return True
            else:
                logger.error(f"❌ Failed to send email. Status: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False

        except requests.exceptions.Timeout:
            logger.error("❌ Email sending timed out.")
            return False
        except requests.exceptions.ConnectionError:
            logger.error("❌ Connection error while contacting Mailtrap.")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected exception in email sending: {str(e)}")
            return False

# Create email sender instance
email_sender = EmailSender()

# ---------------------------------------------------------
# Shared Email Shell
# ---------------------------------------------------------

# Brand colors matching the frontend design system
_NAVY = "#0B1120"
_NAVY_LIGHT = "#151e32"
_TEAL = "#0e7c9b"
_SLATE_50 = "#f8fafc"
_SLATE_200 = "#e2e8f0"
_SLATE_400 = "#94a3b8"
_SLATE_500 = "#64748b"
_SLATE_700 = "#334155"
_SLATE_900 = "#0f172a"


def _wrap_email(title: str, body_html: str) -> str:
    """Wrap email body in the branded Sitespace shell."""
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background-color:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f1f5f9;padding:40px 20px;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background-color:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.08);">

  <!-- Header -->
  <tr>
    <td style="background-color:{_NAVY};padding:32px 40px;text-align:center;">
      <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto;">
        <tr>
          <td style="background-color:#ffffff;width:36px;height:36px;border-radius:10px;text-align:center;vertical-align:middle;font-weight:bold;font-size:18px;color:{_NAVY};">S</td>
          <td style="padding-left:12px;font-size:22px;font-weight:700;color:#ffffff;letter-spacing:-0.3px;">Sitespace</td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- Body -->
  <tr>
    <td style="padding:40px;">
      {body_html}
    </td>
  </tr>

  <!-- Footer -->
  <tr>
    <td style="background-color:{_SLATE_50};border-top:1px solid {_SLATE_200};padding:24px 40px;text-align:center;">
      <p style="margin:0;font-size:13px;color:{_SLATE_400};">&copy; {settings.APP_NAME}. All rights reserved.</p>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _button(url: str, label: str, color: str = _NAVY) -> str:
    """Render a branded CTA button."""
    return f"""\
<table role="presentation" cellpadding="0" cellspacing="0" style="margin:28px 0;">
<tr>
  <td style="background-color:{color};border-radius:10px;text-align:center;">
    <a href="{url}" target="_blank" style="display:inline-block;padding:14px 32px;font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;letter-spacing:0.2px;">{label}</a>
  </td>
</tr>
</table>"""


def _link_fallback(url: str) -> str:
    """Render the 'or copy this link' fallback text."""
    return f"""\
<p style="margin:0 0 6px;font-size:13px;color:{_SLATE_500};">Or copy and paste this link into your browser:</p>
<p style="margin:0;font-size:13px;color:{_TEAL};word-break:break-all;">{url}</p>"""


def _notice(text: str) -> str:
    """Render a warning/info notice box."""
    return f"""\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:24px;">
<tr>
  <td style="background-color:#fef9ec;border:1px solid #fde68a;border-radius:10px;padding:14px 18px;">
    <p style="margin:0;font-size:13px;color:{_SLATE_700};line-height:1.5;">{text}</p>
  </td>
</tr>
</table>"""


# ---------------------------------------------------------
# Helper functions (Templates)
# ---------------------------------------------------------

def send_verification_email(
    to_email: str,
    user_name: str,
    verification_token: str
):
    """Send email verification link"""

    verification_url = f"{settings.FRONTEND_URL}/verify-email?token={verification_token}"

    body = f"""\
<h2 style="margin:0 0 8px;font-size:22px;font-weight:700;color:{_SLATE_900};">Verify your email</h2>
<p style="margin:0 0 24px;font-size:15px;color:{_SLATE_500};">Hi {user_name}, thanks for signing up.</p>
<p style="margin:0;font-size:15px;color:{_SLATE_700};line-height:1.6;">Please confirm your email address by clicking the button below so you can start managing your projects.</p>
{_button(verification_url, "Verify Email Address")}
{_link_fallback(verification_url)}
{_notice("This link will expire in <strong>24 hours</strong>. If you didn't create an account, you can safely ignore this email.")}"""

    html_content = _wrap_email("Verify your email", body)

    text_content = f"""\
Hi {user_name},

Thanks for signing up! Please verify your email address:

{verification_url}

This link will expire in 24 hours.

If you didn't create an account, please ignore this email.

- {settings.APP_NAME} Team"""

    return email_sender.send_email(
        to_email=to_email,
        subject=f"Verify your email for {settings.APP_NAME}",
        html_content=html_content,
        text_content=text_content
    )


def send_password_reset_email(
    to_email: str,
    user_name: str,
    reset_token: str
):
    """Send password reset email"""

    reset_url = f"{settings.FRONTEND_URL}/reset-password?token={reset_token}"

    body = f"""\
<h2 style="margin:0 0 8px;font-size:22px;font-weight:700;color:{_SLATE_900};">Reset your password</h2>
<p style="margin:0 0 24px;font-size:15px;color:{_SLATE_500};">Hi {user_name}, we received a password reset request.</p>
<p style="margin:0;font-size:15px;color:{_SLATE_700};line-height:1.6;">Click the button below to choose a new password. For security, this link is only valid for a limited time.</p>
{_button(reset_url, "Reset Password")}
{_link_fallback(reset_url)}
{_notice("<strong>Important:</strong> This link expires in <strong>1 hour</strong>. If you didn't request this, no action is needed — your password won't be changed.")}"""

    html_content = _wrap_email("Reset your password", body)

    text_content = f"""\
Hi {user_name},

We received a request to reset your password:

{reset_url}

This link will expire in 1 hour.

If you didn't request this, please ignore this email.

- {settings.APP_NAME} Team"""

    return email_sender.send_email(
        to_email=to_email,
        subject=f"Reset your password — {settings.APP_NAME}",
        html_content=html_content,
        text_content=text_content
    )


def send_welcome_email(
    to_email: str,
    user_name: str
):
    """Send welcome email after successful verification"""

    dashboard_url = f"{settings.FRONTEND_URL}/home"

    body = f"""\
<h2 style="margin:0 0 8px;font-size:22px;font-weight:700;color:{_SLATE_900};">You're all set!</h2>
<p style="margin:0 0 24px;font-size:15px;color:{_SLATE_500};">Welcome aboard, {user_name}.</p>
<p style="margin:0;font-size:15px;color:{_SLATE_700};line-height:1.6;">Your email has been verified. You now have full access to your dashboard where you can manage bookings, assets, and your team.</p>
{_button(dashboard_url, "Go to Dashboard", _TEAL)}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:28px;">
<tr><td style="border-top:1px solid {_SLATE_200};padding-top:20px;">
  <p style="margin:0 0 10px;font-size:14px;font-weight:600;color:{_SLATE_900};">Get started:</p>
  <table role="presentation" cellpadding="0" cellspacing="0">
    <tr><td style="padding:4px 0;font-size:14px;color:{_SLATE_700};">1. Switch to your project from the dashboard</td></tr>
    <tr><td style="padding:4px 0;font-size:14px;color:{_SLATE_700};">2. Create your first booking on the live calendar</td></tr>
    <tr><td style="padding:4px 0;font-size:14px;color:{_SLATE_700};">3. Invite subcontractors to collaborate</td></tr>
  </table>
</td></tr>
</table>"""

    html_content = _wrap_email(f"Welcome to {settings.APP_NAME}", body)

    text_content = f"""\
Welcome aboard, {user_name}!

Your email has been verified. You now have full access.

Get started:
1. Switch to your project from the dashboard
2. Create your first booking on the live calendar
3. Invite subcontractors to collaborate

Go to your dashboard: {dashboard_url}

- {settings.APP_NAME} Team"""

    return email_sender.send_email(
        to_email=to_email,
        subject=f"Welcome to {settings.APP_NAME}!",
        html_content=html_content,
        text_content=text_content
    )


def send_subcontractor_invite_email(
    to_email: str,
    user_name: str,
    reset_token: str
):
    """Send invitation email to new subcontractor to set password"""

    # /set-password on the frontend distinguishes new accounts from resets
    setup_url = f"{settings.FRONTEND_URL}/set-password?token={reset_token}"

    body = f"""\
<h2 style="margin:0 0 8px;font-size:22px;font-weight:700;color:{_SLATE_900};">You've been invited</h2>
<p style="margin:0 0 24px;font-size:15px;color:{_SLATE_500};">Hi {user_name}, a project manager has added you to a project on {settings.APP_NAME}.</p>
<p style="margin:0;font-size:15px;color:{_SLATE_700};line-height:1.6;">Set your password below to access the dashboard, view your bookings, and get started with your team.</p>
{_button(setup_url, "Set My Password", _TEAL)}
{_link_fallback(setup_url)}
{_notice("This link is valid for <strong>24 hours</strong>. If you weren't expecting this invitation, you can safely ignore this email.")}"""

    html_content = _wrap_email(f"You've been invited to {settings.APP_NAME}", body)

    text_content = f"""\
Hi {user_name},

A project manager has added you to a project on {settings.APP_NAME}.

Set your password to get started:
{setup_url}

This link is valid for 24 hours.

- {settings.APP_NAME} Team"""

    return email_sender.send_email(
        to_email=to_email,
        subject=f"You've been invited to {settings.APP_NAME}",
        html_content=html_content,
        text_content=text_content
    )
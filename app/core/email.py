import hashlib
import html as html_mod
import os
import requests
import logging
import threading
from typing import Optional, Dict, List
from uuid import UUID
from .config import settings

# Configure logging
logger = logging.getLogger(__name__)


def _redact_email(email: str) -> str:
    """Return a redacted email safe for logging (sha256 prefix)."""
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:12]
    return f"<{digest}>"

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

        mode = "SANDBOX" if self.use_sandbox else "LIVE"
        logger.debug(
            "Email sender configured",
            extra={
                "mail_mode": mode,
                "mailtrap_token_configured": bool(self.api_token),
                "from_email": self.from_email,
            },
        )
        if not self.api_token:
            logger.warning("Email delivery disabled because MAILTRAP_TOKEN is not configured")
        if self.use_sandbox and not self.inbox_id:
            logger.warning("Sandbox email delivery disabled because MAILTRAP_INBOX_ID is not configured")

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
            logger.info(f"📧 Preparing to send SANDBOX email to {_redact_email(to_email)} (Inbox {self.inbox_id})...")
        else:
            # --- LIVE SENDING MODE ---
            # Standard Sending API URL
            url = "https://send.api.mailtrap.io/api/send"
            
            # Live API uses 'Authorization: Bearer' header
            headers = {
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json"
            }
            logger.info(f"📧 Preparing to send LIVE email to {_redact_email(to_email)} via Mailtrap Sending API...")

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
                logger.info(f"✅ Email sent successfully to {_redact_email(to_email)}")
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
        except Exception:
            logger.exception("❌ Unexpected exception in email sending")
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


# ---------------------------------------------------------
# Booking Notification Emails
# ---------------------------------------------------------

_RED = "#dc2626"
_GREEN = "#16a34a"
_AMBER = "#d97706"
_BLUE = "#2563eb"

# Action → (heading, subtitle, accent color, status badge color)
_BOOKING_ACTION_META = {
    "created":     ("New Booking Created",   "A booking has been created.",          _TEAL,  _GREEN),
    "approved":    ("Booking Approved",      "A booking has been approved.",         _TEAL,  _GREEN),
    "denied":      ("Booking Denied",        "A booking has been denied.",           _RED,   _RED),
    "updated":     ("Booking Updated",       "A booking has been updated.",          _NAVY,  _BLUE),
    "rescheduled": ("Booking Rescheduled",   "A booking has been rescheduled.",      _NAVY,  _AMBER),
    "cancelled":   ("Booking Cancelled",     "A booking has been cancelled.",        _RED,   _RED),
}


def _status_badge(status: str, color: str) -> str:
    """Render an inline status badge. Escapes the status text."""
    safe_status = html_mod.escape(status)
    return (
        f'<span style="display:inline-block;padding:3px 10px;font-size:12px;'
        f'font-weight:700;color:#ffffff;background-color:{color};'
        f'border-radius:6px;text-transform:uppercase;letter-spacing:0.4px;">'
        f'{safe_status}</span>'
    )


def _booking_detail_row(label: str, value: str, raw_html: bool = False) -> str:
    """Render a single row in the booking preview card.

    ``label`` is always escaped.  ``value`` is escaped unless
    ``raw_html=True`` (used for pre-built badge HTML).
    """
    safe_label = html_mod.escape(label)
    safe_value = value if raw_html else html_mod.escape(value)
    return (
        f'<tr>'
        f'<td style="padding:6px 12px;font-size:13px;color:{_SLATE_500};'
        f'white-space:nowrap;vertical-align:top;">{safe_label}</td>'
        f'<td style="padding:6px 12px;font-size:13px;color:{_SLATE_900};'
        f'font-weight:500;">{safe_value}</td>'
        f'</tr>'
    )


def send_booking_notification_email(
    to_email: str,
    recipient_name: str,
    action: str,
    actor_name: str,
    booking_id: str,
    booking_details: Dict[str, str],
) -> bool:
    """
    Send a booking notification email.

    ``action`` should be one of the BookingAuditAction values:
    created, approved, denied, updated, rescheduled, cancelled.

    ``booking_details`` keys: date, start_time, end_time, asset, project,
    status, purpose (all strings, already formatted for display).
    """

    meta = _BOOKING_ACTION_META.get(action)
    if not meta:
        logger.warning(f"Unknown booking action '{action}' — skipping email")
        return False

    heading, subtitle, accent, badge_color = meta
    booking_url = f"{settings.FRONTEND_URL}/bookings?highlight={booking_id}"

    # Escape all user-controlled strings for HTML context
    safe_recipient = html_mod.escape(recipient_name)
    safe_actor = html_mod.escape(actor_name)

    # Build the preview card rows (_booking_detail_row handles escaping)
    rows = ""
    if booking_details.get("project"):
        rows += _booking_detail_row("Project", booking_details["project"])
    if booking_details.get("asset"):
        rows += _booking_detail_row("Asset", booking_details["asset"])
    if booking_details.get("date"):
        rows += _booking_detail_row("Date", booking_details["date"])
    if booking_details.get("start_time") and booking_details.get("end_time"):
        rows += _booking_detail_row(
            "Time", f'{booking_details["start_time"]} — {booking_details["end_time"]}'
        )
    if booking_details.get("status"):
        # _status_badge returns safe HTML (it escapes the status text internally)
        rows += _booking_detail_row(
            "Status", _status_badge(booking_details["status"], badge_color),
            raw_html=True,
        )
    if booking_details.get("purpose"):
        rows += _booking_detail_row("Purpose", booking_details["purpose"])

    body = f"""\
<h2 style="margin:0 0 8px;font-size:22px;font-weight:700;color:{_SLATE_900};">{heading}</h2>
<p style="margin:0 0 24px;font-size:15px;color:{_SLATE_500};">Hi {safe_recipient}, {subtitle.lower()}</p>
<p style="margin:0 0 20px;font-size:15px;color:{_SLATE_700};line-height:1.6;">
  <strong>{safe_actor}</strong> {_action_verb(action)} this booking.
</p>

<!-- Booking preview card -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="margin:0 0 8px;border:1px solid {_SLATE_200};border-radius:12px;overflow:hidden;">
  <tr>
    <td style="background-color:{accent};height:4px;font-size:1px;line-height:1px;">&nbsp;</td>
  </tr>
  <tr>
    <td style="padding:16px 4px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        {rows}
      </table>
    </td>
  </tr>
</table>

{_button(booking_url, "View Booking", accent)}
{_link_fallback(booking_url)}"""

    html_content = _wrap_email(heading, body)

    # Plain-text fallback (no escaping needed — plain text is not HTML-rendered)
    text_lines = [
        f"Hi {recipient_name},",
        "",
        f"{actor_name} {_action_verb(action)} a booking.",
        "",
    ]
    for label, key in [
        ("Project", "project"), ("Asset", "asset"), ("Date", "date"),
        ("Time", "start_time"), ("Status", "status"), ("Purpose", "purpose"),
    ]:
        val = booking_details.get(key, "")
        if key == "start_time" and val:
            val = f'{val} - {booking_details.get("end_time", "")}'
        if val:
            text_lines.append(f"  {label}: {val}")
    text_lines += ["", f"View booking: {booking_url}", "", f"- {settings.APP_NAME} Team"]
    text_content = "\n".join(text_lines)

    return email_sender.send_email(
        to_email=to_email,
        subject=f"{heading} — {settings.APP_NAME}",
        html_content=html_content,
        text_content=text_content,
    )


def _action_verb(action: str) -> str:
    """Return a past-tense verb for the action."""
    return {
        "created": "created",
        "approved": "approved",
        "denied": "denied",
        "updated": "updated",
        "rescheduled": "rescheduled",
        "cancelled": "cancelled",
    }.get(action, "modified")


# ---------------------------------------------------------
# Booking Notification Orchestrator
# ---------------------------------------------------------

def notify_booking_change(
    db,
    booking_id: UUID,
    action: str,
    actor_id: UUID,
) -> None:
    """
    Determine recipients and send booking notification emails
    in a background thread so the API response is not delayed.

    Call this from route handlers AFTER the CRUD operation has committed.
    """
    from app.models.slot_booking import SlotBooking
    from app.models.user import User
    from app.models.subcontractor import Subcontractor
    from sqlalchemy.orm import joinedload

    # Load booking with relationships (still on the request thread / DB session)
    booking = (
        db.query(SlotBooking)
        .options(
            joinedload(SlotBooking.project),
            joinedload(SlotBooking.manager),
            joinedload(SlotBooking.subcontractor),
            joinedload(SlotBooking.asset),
        )
        .filter(SlotBooking.id == booking_id)
        .first()
    )
    if not booking:
        logger.warning(f"notify_booking_change: booking {booking_id} not found")
        return

    # Format booking details for the email
    booking_details: Dict[str, str] = {
        "date": booking.booking_date.strftime("%B %d, %Y") if booking.booking_date else "",
        "start_time": booking.start_time.strftime("%I:%M %p") if booking.start_time else "",
        "end_time": booking.end_time.strftime("%I:%M %p") if booking.end_time else "",
        "status": booking.status.value if booking.status else "",
        "purpose": booking.purpose or "",
        "asset": "",
        "project": "",
    }
    if booking.asset:
        asset_label = booking.asset.name or ""
        if booking.asset.asset_code:
            asset_label = f"{asset_label} ({booking.asset.asset_code})"
        booking_details["asset"] = asset_label
    if booking.project:
        booking_details["project"] = booking.project.name or ""

    # Resolve actor name
    actor_name = "Someone"
    if booking.manager and str(booking.manager.id) == str(actor_id):
        actor_name = f"{booking.manager.first_name} {booking.manager.last_name}"
    elif booking.subcontractor and str(booking.subcontractor.id) == str(actor_id):
        actor_name = f"{booking.subcontractor.first_name} {booking.subcontractor.last_name}"
    else:
        # Actor might be an admin who is neither the manager nor subcontractor
        actor = db.query(User).filter(User.id == actor_id).first()
        if actor:
            actor_name = f"{actor.first_name} {actor.last_name}"
        else:
            sub_actor = db.query(Subcontractor).filter(Subcontractor.id == actor_id).first()
            if sub_actor:
                actor_name = f"{sub_actor.first_name} {sub_actor.last_name}"

    # Collect recipients: (email, display_name) — skip the actor
    recipients: List[tuple] = []

    if booking.manager and str(booking.manager.id) != str(actor_id):
        recipients.append((
            booking.manager.email,
            booking.manager.first_name,
        ))

    if booking.subcontractor and str(booking.subcontractor.id) != str(actor_id):
        recipients.append((
            booking.subcontractor.email,
            booking.subcontractor.first_name,
        ))

    if not recipients:
        return

    bid = str(booking_id)

    # Fire-and-forget in a background thread
    def _send():
        for email_addr, name in recipients:
            try:
                send_booking_notification_email(
                    to_email=email_addr,
                    recipient_name=name,
                    action=action,
                    actor_name=actor_name,
                    booking_id=bid,
                    booking_details=booking_details,
                )
            except Exception:
                logger.exception(f"Failed to send booking notification to {_redact_email(email_addr)}")

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()

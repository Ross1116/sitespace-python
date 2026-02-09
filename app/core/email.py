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
# Helper functions (Templates)
# ---------------------------------------------------------

def send_verification_email(
    to_email: str,
    user_name: str,
    verification_token: str
):
    """Send email verification link"""
    
    verification_url = f"{settings.FRONTEND_URL}/verify-email?token={verification_token}"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #4CAF50; color: white; padding: 20px; text-align: center; }}
            .content {{ background-color: #f9f9f9; padding: 20px; margin-top: 20px; }}
            .button {{ display: inline-block; padding: 10px 20px; background-color: #4CAF50; color: white; text-decoration: none; border-radius: 5px; margin-top: 20px; }}
            .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #666; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Email Verification</h1>
            </div>
            <div class="content">
                <h2>Hello {user_name},</h2>
                <p>Thank you for registering! Please verify your email address by clicking the button below:</p>
                <a href="{verification_url}" class="button">Verify Email</a>
                <p>Or copy and paste this link into your browser:</p>
                <p style="word-break: break-all;">{verification_url}</p>
                <p>This link will expire in 24 hours.</p>
                <p>If you didn't create an account, please ignore this email.</p>
            </div>
            <div class="footer">
                <p>&copy; {settings.APP_NAME}. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    text_content = f"""
    Hello {user_name},
    
    Thank you for registering! Please verify your email address by clicking the link below:
    
    {verification_url}
    
    This link will expire in 24 hours.
    
    If you didn't create an account, please ignore this email.
    
    Best regards,
    {settings.APP_NAME} Team
    """
    
    subject = f"Verify your email for {settings.APP_NAME}"
    
    return email_sender.send_email(
        to_email=to_email,
        subject=subject,
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
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #FF9800; color: white; padding: 20px; text-align: center; }}
            .content {{ background-color: #f9f9f9; padding: 20px; margin-top: 20px; }}
            .button {{ display: inline-block; padding: 10px 20px; background-color: #FF9800; color: white; text-decoration: none; border-radius: 5px; margin-top: 20px; }}
            .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #666; }}
            .warning {{ background-color: #fff3cd; border: 1px solid #ffc107; padding: 10px; margin-top: 20px; border-radius: 5px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Password Reset Request</h1>
            </div>
            <div class="content">
                <h2>Hello {user_name},</h2>
                <p>We received a request to reset your password. Click the button below to create a new password:</p>
                <a href="{reset_url}" class="button">Reset Password</a>
                <p>Or copy and paste this link into your browser:</p>
                <p style="word-break: break-all;">{reset_url}</p>
                <p>This link will expire in 1 hour for security reasons.</p>
                <div class="warning">
                    <strong>Important:</strong> If you didn't request a password reset, please ignore this email. Your password won't be changed.
                </div>
            </div>
            <div class="footer">
                <p>&copy; {settings.APP_NAME}. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    text_content = f"""
    Hello {user_name},
    
    We received a request to reset your password. Click the link below to create a new password:
    
    {reset_url}
    
    This link will expire in 1 hour for security reasons.
    
    Important: If you didn't request a password reset, please ignore this email. Your password won't be changed.
    
    Best regards,
    {settings.APP_NAME} Team
    """
    
    subject = f"Password Reset Request for {settings.APP_NAME}"
    
    return email_sender.send_email(
        to_email=to_email,
        subject=subject,
        html_content=html_content,
        text_content=text_content
    )

def send_welcome_email(
    to_email: str,
    user_name: str
):
    """Send welcome email after successful verification"""
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #4CAF50; color: white; padding: 20px; text-align: center; }}
            .content {{ background-color: #f9f9f9; padding: 20px; margin-top: 20px; }}
            .button {{ display: inline-block; padding: 10px 20px; background-color: #4CAF50; color: white; text-decoration: none; border-radius: 5px; margin-top: 20px; }}
            .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #666; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Welcome to {settings.APP_NAME}!</h1>
            </div>
            <div class="content">
                <h2>Hello {user_name},</h2>
                <p>Your email has been successfully verified. You can now enjoy all the features of our platform.</p>
                <p>Get started by:</p>
                <ul>
                    <li>Completing your profile</li>
                    <li>Exploring our features</li>
                    <li>Connecting with other users</li>
                </ul>
                <a href="{settings.FRONTEND_URL}/dashboard" class="button">Go to Dashboard</a>
            </div>
            <div class="footer">
                <p>&copy; {settings.APP_NAME}. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    text_content = f"""
    Hello {user_name},
    
    Welcome to {settings.APP_NAME}!
    
    Your email has been successfully verified. You can now enjoy all the features of our platform.
    
    Get started by:
    - Completing your profile
    - Exploring our features
    - Connecting with other users
    
    Visit your dashboard: {settings.FRONTEND_URL}/dashboard
    
    Best regards,
    {settings.APP_NAME} Team
    """
    
    subject = f"Welcome to {settings.APP_NAME}!"
    
    return email_sender.send_email(
        to_email=to_email,
        subject=subject,
        html_content=html_content,
        text_content=text_content
    )

def send_subcontractor_invite_email(
    to_email: str,
    user_name: str,
    reset_token: str
):
    """Send invitation email to new subcontractor to set password"""
    
    # Note: We use /set-password on the frontend for new accounts
    # to distinguish it from a standard /reset-password flow
    setup_url = f"{settings.FRONTEND_URL}/set-password?token={reset_token}"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #000000; color: white; padding: 20px; text-align: center; }}
            .content {{ background-color: #f9f9f9; padding: 20px; margin-top: 20px; }}
            .button {{ display: inline-block; padding: 10px 20px; background-color: #000000; color: white; text-decoration: none; border-radius: 5px; margin-top: 20px; }}
            .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #666; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Welcome to {settings.APP_NAME}</h1>
            </div>
            <div class="content">
                <h2>Hello {user_name},</h2>
                <p>You have been added to a project on {settings.APP_NAME}.</p>
                <p>To access your dashboard and view project details, please set your password by clicking the button below:</p>
                <a href="{setup_url}" class="button">Set My Password</a>
                <p>Or copy and paste this link into your browser:</p>
                <p style="word-break: break-all;">{setup_url}</p>
                <p>This link is valid for 24 hours.</p>
            </div>
            <div class="footer">
                <p>&copy; {settings.APP_NAME}. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    text_content = f"""
    Hello {user_name},
    
    You have been added to a project on {settings.APP_NAME}.
    
    To access your dashboard, please set your password here:
    {setup_url}
    
    Best regards,
    {settings.APP_NAME} Team
    """
    
    subject = f"You've been invited to {settings.APP_NAME}"
    
    return email_sender.send_email(
        to_email=to_email,
        subject=subject,
        html_content=html_content,
        text_content=text_content
    )
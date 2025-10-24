# core/email.py
from typing import Optional
from pathlib import Path
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from jinja2 import Template
import logging
from .config import settings

logger = logging.getLogger(__name__)

class EmailSender:
    """Email sender utility class"""
    
    def __init__(self):
        self.smtp_host = settings.SMTP_HOST
        self.smtp_port = settings.SMTP_PORT
        self.smtp_user = settings.SMTP_USER
        self.smtp_password = settings.SMTP_PASSWORD
        self.from_email = settings.FROM_EMAIL
        self.from_name = settings.FROM_NAME
        self.use_tls = settings.SMTP_TLS
        
    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ):
        """Send email using SMTP"""
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = f"{self.from_name} <{self.from_email}>"
            msg['To'] = to_email
            
            # Add text and HTML parts
            if text_content:
                text_part = MIMEText(text_content, 'plain')
                msg.attach(text_part)
            
            html_part = MIMEText(html_content, 'html')
            msg.attach(html_part)
            
            # Send email
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if self.use_tls:
                    server.starttls()
                if self.smtp_user and self.smtp_password:
                    server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
                
            logger.info(f"Email sent successfully to {to_email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}")
            return False

# Create email sender instance
email_sender = EmailSender()

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
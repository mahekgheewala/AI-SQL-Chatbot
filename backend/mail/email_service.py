import os
import smtplib
from email.message import EmailMessage

class EmailService:
    def __init__(self):
        self.smtp_server = os.getenv("SMTP_SERVER", "localhost")
        self.smtp_port = int(os.getenv("SMTP_PORT", 1025))
        self.smtp_username = os.getenv("SMTP_USERNAME", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.from_email = os.getenv("SMTP_FROM", "noreply@aisqlassistant.com")
        self.frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")

    def _send_email(self, to: str, subject: str, content: str):
        msg = EmailMessage()
        msg.set_content(content)
        msg['Subject'] = subject
        msg['From'] = self.from_email
        msg['To'] = to
        
        # In a real production setup, handle exceptions and use TLS
        try:
            # Using simple SMTP for local development/testing like Mailhog
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                if self.smtp_username and self.smtp_password:
                    server.starttls()
                    server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)
        except Exception as e:
            print(f"Failed to send email to {to}: {e}")

    def send_verification_email(self, to: str, token: str):
        link = f"{self.frontend_url}/verify-email?token={token}"
        content = f"Please verify your email by clicking on the following link: {link}"
        self._send_email(to, "Verify your Email", content)

    def send_password_reset_email(self, to: str, token: str):
        link = f"{self.frontend_url}/reset-password?token={token}"
        content = f"You requested a password reset. Click here to reset: {link}"
        self._send_email(to, "Password Reset Request", content)

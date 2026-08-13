"""
Outreach agent - Composes and sends personalized pitches

CRITICAL: This agent is gated behind human approval. No messages are sent
automatically in v1.
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any, Dict, List, Optional

from agents.base import BaseAgent
from utils import config, settings, console


class OutreachAgent(BaseAgent):
    """
    Composes personalized outreach messages and sends them (after approval).
    
    COMPLIANCE REQUIREMENTS:
    
    US Contacts (CAN-SPAM):
    - Accurate sender identity
    - Working opt-out/unsubscribe mechanism
    - Physical address in footer
    
    EU/UK Contacts (GDPR / ePrivacy):
    - Lawful basis required (legitimate interest must be documented)
    - Cold emailing personal addresses often not permitted
    - Right to erasure, data portability
    
    DELIVERABILITY:
    - Use warmed sending domain
    - Throttle volume
    - Avoid spam triggers
    """
    
    def __init__(self):
        super().__init__("Outreach")
        self.smtp_host = settings.smtp_host
        self.smtp_port = settings.smtp_port
        self.smtp_user = settings.smtp_user
        self.smtp_password = settings.smtp_password
        self.sender_email = settings.sender_email
        self.sender_name = settings.sender_name
    
    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compose and send outreach messages.
        
        Args:
            context: Must contain 'leads_with_videos'
        
        Returns:
            Dictionary with:
                - drafted_messages: All composed messages
                - sent_messages: Messages sent after approval
                - send_summary: Stats on sending
        """
        self.log_start()
        
        try:
            self.validate_context(context, ['leads_with_videos'])
            leads = context['leads_with_videos']
            
            # Filter leads with both contact and video
            qualified_leads = [
                l for l in leads 
                if l.get('contact') and l.get('video')
            ]
            
            self.logger.info(f"Composing messages for {len(qualified_leads)} qualified leads")
            
            # Compose messages
            drafted_messages = self._compose_messages(qualified_leads)
            
            # Human approval gate
            approved_messages = await self._approval_gate(drafted_messages)
            
            # Send approved messages
            sent_messages = []
            if approved_messages:
                # TODO: gated behind human approval
                # Sending is disabled in v1 - implement only after thorough testing
                # and compliance review
                self.logger.warning("⚠️  Message sending not yet implemented (requires approval)")
                # sent_messages = await self._send_messages(approved_messages)
            
            result = {
                'drafted_messages': drafted_messages,
                'approved_messages': approved_messages,
                'sent_messages': sent_messages,
                'send_summary': {
                    'total_drafted': len(drafted_messages),
                    'approved_count': len(approved_messages),
                    'sent_count': len(sent_messages),
                },
            }
            
            self.log_complete(
                f"Drafted {len(drafted_messages)} messages, "
                f"{len(approved_messages)} approved, "
                f"{len(sent_messages)} sent"
            )
            return result
            
        except Exception as e:
            self.log_error(e)
            raise
    
    def _compose_messages(self, leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compose personalized messages for each lead."""
        messages = []
        
        for lead in leads:
            message = self._compose_single_message(lead)
            messages.append(message)
        
        return messages
    
    def _compose_single_message(self, lead: Dict[str, Any]) -> Dict[str, Any]:
        """Compose a personalized message for a single lead."""
        contact = lead['contact']
        video = lead['video']
        brand_name = lead['brand_name']
        industry = lead.get('industry', 'your industry')
        
        # Personalization
        first_name = contact['name'].split()[0]
        
        # Build message
        subject = f"Video ad sample for {brand_name}"
        
        body = f"""Hi {first_name},

I noticed {brand_name} is doing great work in {industry}, but you might not be leveraging video ads as much as some of your competitors.

I put together a quick sample video ad for {brand_name} to show what's possible:
{video['url']}

This took just a few minutes to create using AI - imagine what a full campaign could do.

Would you be interested in exploring how video ads could help {brand_name} grow?

Best regards,
{self.sender_name or 'The Team'}

---
{self._get_footer(lead)}
"""
        
        message = {
            'lead_id': lead.get('brand_name'),
            'to_email': contact['email'],
            'to_name': contact['name'],
            'subject': subject,
            'body': body,
            'video_url': video['url'],
            'region': lead.get('region'),
            'approved': False,
        }
        
        return message
    
    def _get_footer(self, lead: Dict[str, Any]) -> str:
        """Get email footer with compliance information."""
        region = lead.get('region', 'US')
        
        footer = []
        
        # Unsubscribe link (required for CAN-SPAM)
        footer.append("To unsubscribe, reply with 'UNSUBSCRIBE'")
        
        # Physical address (required for CAN-SPAM)
        if region in ['US', 'CA']:
            footer.append("Your Company Name\n123 Business St, City, State, ZIP")
        
        # GDPR notice
        if region in ['GB', 'DE', 'FR', 'EU']:
            footer.append(
                "This email is sent based on legitimate business interest. "
                "You have the right to request erasure of your data at any time."
            )
        
        return '\n\n'.join(footer)
    
    async def _approval_gate(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Human approval gate - required before sending.
        
        Returns only approved messages.
        """
        if not config.approval.enabled:
            self.logger.warning("⚠️  Approval gate is DISABLED - this should only be used for testing")
            return messages
        
        if config.approval.auto_approve:
            self.logger.warning("⚠️  Auto-approve is ENABLED - this is DANGEROUS")
            for msg in messages:
                msg['approved'] = True
            return messages
        
        # CLI approval method
        if config.approval.method == 'cli':
            return self._cli_approval(messages)
        
        # Other methods (web, email) not yet implemented
        self.logger.warning("Non-CLI approval methods not yet implemented")
        return []
    
    def _cli_approval(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Show messages in CLI and ask for approval."""
        console.print("\n[bold yellow]📧 Outreach Messages for Approval[/bold yellow]\n")
        
        approved = []
        
        for i, msg in enumerate(messages, 1):
            console.print(f"[cyan]Message {i}/{len(messages)}[/cyan]")
            console.print(f"To: {msg['to_name']} <{msg['to_email']}>")
            console.print(f"Subject: {msg['subject']}")
            console.print(f"\n{msg['body']}\n")
            console.print("-" * 60)
            
            # Ask for approval
            response = console.input("[bold]Approve this message? (y/n/q to quit): [/bold]").lower()
            
            if response == 'y':
                msg['approved'] = True
                approved.append(msg)
                console.print("[green]✅ Approved[/green]\n")
            elif response == 'q':
                console.print("[yellow]Approval process cancelled[/yellow]")
                break
            else:
                console.print("[red]❌ Skipped[/red]\n")
        
        return approved
    
    async def _send_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Send approved messages via SMTP.
        
        TODO: Implement actual sending with:
        - Rate limiting / throttling
        - Bounce handling
        - Delivery tracking
        - Error handling and retries
        """
        # Check SMTP configuration
        if not all([self.smtp_host, self.smtp_user, self.smtp_password, self.sender_email]):
            raise ValueError("SMTP settings not fully configured")
        
        sent = []
        
        for msg in messages:
            if not msg.get('approved'):
                continue
            
            try:
                await self._send_single_message(msg)
                msg['sent'] = True
                sent.append(msg)
                self.logger.info(f"✅ Sent message to {msg['to_email']}")
            except Exception as e:
                self.logger.error(f"Failed to send to {msg['to_email']}: {e}")
                msg['sent'] = False
                msg['error'] = str(e)
        
        return sent
    
    async def _send_single_message(self, message: Dict[str, Any]) -> None:
        """Send a single email via SMTP."""
        msg = MIMEMultipart('alternative')
        msg['From'] = f"{self.sender_name} <{self.sender_email}>"
        msg['To'] = message['to_email']
        msg['Subject'] = message['subject']
        
        # Add body
        text_part = MIMEText(message['body'], 'plain')
        msg.attach(text_part)
        
        # Send via SMTP
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)

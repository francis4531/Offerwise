"""
OfferWise Email Service
Transactional emails for key user touchpoints

Uses Resend for reliable delivery with beautiful HTML templates.
Fallback gracefully if email service unavailable.

Email Types:
1. Welcome - After first signup
2. Purchase Receipt - After buying credits  
3. Analysis Complete - When analysis finishes
4. Credits Reminder - If unused credits (7 days)
"""

import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Try to import resend, gracefully handle if not available
try:
    import resend
    RESEND_AVAILABLE = True
except ImportError:
    RESEND_AVAILABLE = False
    logger.warning("⚠️ Resend not installed - emails disabled")

# Configuration
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
FROM_EMAIL = "OfferWise <hello@getofferwise.ai>"
SUPPORT_EMAIL = "support@getofferwise.ai"

# Initialize Resend
if RESEND_AVAILABLE and RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY
    EMAIL_ENABLED = True
    logger.info("✅ Email service enabled (Resend)")
else:
    EMAIL_ENABLED = False
    if not RESEND_API_KEY:
        logger.warning("⚠️ RESEND_API_KEY not set - emails disabled")


# =============================================================================
# EMAIL TEMPLATES
# =============================================================================

def get_base_template(content: str, preview_text: str = "") -> str:
    """Wrap content in beautiful base email template"""
    return f'''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <title>OfferWise</title>
    <!--[if mso]>
    <style type="text/css">
        table {{border-collapse: collapse;}}
        .button {{padding: 12px 24px !important;}}
    </style>
    <![endif]-->
</head>
<body style="margin: 0; padding: 0; background-color: #0f172a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
    <!-- Preview text -->
    <div style="display: none; max-height: 0; overflow: hidden;">
        {preview_text}
    </div>
    
    <!-- Email wrapper -->
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #0f172a;">
        <tr>
            <td align="center" style="padding: 40px 20px;">
                <!-- Content container -->
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="600" style="max-width: 600px; background-color: #1e293b; border-radius: 16px; overflow: hidden; box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);">
                    
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%); padding: 32px 40px; text-align: center;">
                            <h1 style="margin: 0; color: #ffffff; font-size: 28px; font-weight: 800; letter-spacing: -0.5px;">
                                OfferWise
                            </h1>
                        </td>
                    </tr>
                    
                    <!-- Body -->
                    <tr>
                        <td style="padding: 40px;">
                            {content}
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #0f172a; padding: 24px 40px; text-align: center; border-top: 1px solid rgba(255, 255, 255, 0.1);">
                            <p style="margin: 0 0 8px 0; color: #64748b; font-size: 13px;">
                                © 2026 OfferWise. All rights reserved.
                            </p>
                            <p style="margin: 0; color: #64748b; font-size: 13px;">
                                <a href="https://www.getofferwise.ai/privacy" style="color: #60a5fa; text-decoration: none;">Privacy</a>
                                &nbsp;·&nbsp;
                                <a href="https://www.getofferwise.ai/terms" style="color: #60a5fa; text-decoration: none;">Terms</a>
                                &nbsp;·&nbsp;
                                <a href="mailto:{SUPPORT_EMAIL}" style="color: #60a5fa; text-decoration: none;">Support</a>
                            </p>
                        </td>
                    </tr>
                    
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
'''


def get_button(text: str, url: str, color: str = "#3b82f6") -> str:
    """Generate email-safe button"""
    return f'''
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin: 24px auto;">
        <tr>
            <td style="background-color: {color}; border-radius: 8px;">
                <a href="{url}" target="_blank" style="display: inline-block; padding: 14px 32px; color: #ffffff; text-decoration: none; font-weight: 600; font-size: 16px;">
                    {text}
                </a>
            </td>
        </tr>
    </table>
    '''


# =============================================================================
# EMAIL CONTENT GENERATORS
# =============================================================================

def get_welcome_email(user_name: str) -> tuple:
    """Generate welcome email content"""
    first_name = user_name.split()[0] if user_name else "there"
    
    subject = "Welcome to OfferWise! 🏠"
    
    content = f'''
        <h2 style="margin: 0 0 16px 0; color: #f8fafc; font-size: 24px; font-weight: 700;">
            Welcome, {first_name}! 👋
        </h2>
        
        <p style="margin: 0 0 20px 0; color: #cbd5e1; font-size: 16px; line-height: 1.6;">
            You've just gained an unfair advantage in real estate negotiations.
        </p>
        
        <p style="margin: 0 0 20px 0; color: #cbd5e1; font-size: 16px; line-height: 1.6;">
            OfferWise uses AI to analyze property documents and tell you exactly what to offer. Here's what you can do:
        </p>
        
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin: 24px 0;">
            <tr>
                <td width="40" valign="top" style="padding-right: 16px;">
                    <div style="width: 32px; height: 32px; background: rgba(59, 130, 246, 0.2); border-radius: 8px; text-align: center; line-height: 32px; font-size: 16px;">📄</div>
                </td>
                <td style="color: #cbd5e1; font-size: 15px; padding-bottom: 16px;">
                    <strong style="color: #f8fafc;">Upload your documents</strong><br>
                    Seller disclosure + inspection report (any PDF, even scanned)
                </td>
            </tr>
            <tr>
                <td width="40" valign="top" style="padding-right: 16px;">
                    <div style="width: 32px; height: 32px; background: rgba(168, 85, 247, 0.2); border-radius: 8px; text-align: center; line-height: 32px; font-size: 16px;">🤖</div>
                </td>
                <td style="color: #cbd5e1; font-size: 15px; padding-bottom: 16px;">
                    <strong style="color: #f8fafc;">AI analyzes everything</strong><br>
                    We cross-reference disclosures vs inspection findings
                </td>
            </tr>
            <tr>
                <td width="40" valign="top" style="padding-right: 16px;">
                    <div style="width: 32px; height: 32px; background: rgba(16, 185, 129, 0.2); border-radius: 8px; text-align: center; line-height: 32px; font-size: 16px;">💰</div>
                </td>
                <td style="color: #cbd5e1; font-size: 15px;">
                    <strong style="color: #f8fafc;">Get your number</strong><br>
                    Recommended offer price + negotiation talking points
                </td>
            </tr>
        </table>
        
        {get_button("Analyze Your First Property →", "https://www.getofferwise.ai/app")}
        
        <p style="margin: 24px 0 0 0; color: #94a3b8; font-size: 14px; text-align: center;">
            Questions? Reply to this email or contact <a href="mailto:{SUPPORT_EMAIL}" style="color: #60a5fa;">{SUPPORT_EMAIL}</a>
        </p>
    '''
    
    html = get_base_template(content, "Welcome to OfferWise - your AI-powered real estate analysis tool")
    return subject, html


def get_purchase_receipt_email(user_name: str, plan_name: str, credits: int, amount: float) -> tuple:
    """Generate purchase receipt email"""
    first_name = user_name.split()[0] if user_name else "there"
    
    subject = f"Receipt: {plan_name} - ${amount:.2f}"
    
    content = f'''
        <h2 style="margin: 0 0 16px 0; color: #f8fafc; font-size: 24px; font-weight: 700;">
            Thank you for your purchase! 🎉
        </h2>
        
        <p style="margin: 0 0 24px 0; color: #cbd5e1; font-size: 16px; line-height: 1.6;">
            Hi {first_name}, your credits have been added to your account.
        </p>
        
        <!-- Receipt Box -->
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: rgba(15, 23, 42, 0.6); border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.1); margin: 24px 0;">
            <tr>
                <td style="padding: 24px;">
                    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                        <tr>
                            <td style="color: #94a3b8; font-size: 14px; padding-bottom: 12px;">Plan</td>
                            <td align="right" style="color: #f8fafc; font-size: 14px; font-weight: 600; padding-bottom: 12px;">{plan_name}</td>
                        </tr>
                        <tr>
                            <td style="color: #94a3b8; font-size: 14px; padding-bottom: 12px;">Credits Added</td>
                            <td align="right" style="color: #10b981; font-size: 14px; font-weight: 600; padding-bottom: 12px;">+{credits} analyses</td>
                        </tr>
                        <tr>
                            <td style="color: #94a3b8; font-size: 14px; padding-bottom: 12px;">Date</td>
                            <td align="right" style="color: #f8fafc; font-size: 14px; padding-bottom: 12px;">{datetime.now().strftime('%B %d, %Y')}</td>
                        </tr>
                        <tr>
                            <td colspan="2" style="border-top: 1px solid rgba(255, 255, 255, 0.1); padding-top: 12px;"></td>
                        </tr>
                        <tr>
                            <td style="color: #f8fafc; font-size: 16px; font-weight: 600;">Total</td>
                            <td align="right" style="color: #f8fafc; font-size: 20px; font-weight: 700;">${amount:.2f}</td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
        
        <p style="margin: 0 0 8px 0; color: #cbd5e1; font-size: 16px; line-height: 1.6;">
            Your credits never expire. Use them whenever you're ready to analyze a property.
        </p>
        
        {get_button("Use Your Credits →", "https://www.getofferwise.ai/app", "#10b981")}
        
        <p style="margin: 24px 0 0 0; color: #94a3b8; font-size: 13px; text-align: center;">
            Need a refund or have billing questions? Contact <a href="mailto:billing@getofferwise.ai" style="color: #60a5fa;">billing@getofferwise.ai</a>
        </p>
    '''
    
    html = get_base_template(content, f"Receipt for {plan_name} - {credits} analysis credits")
    return subject, html


def get_analysis_complete_email(user_name: str, property_address: str, offer_score: int, recommended_offer, asking_price, property_id: int = None) -> tuple:
    """Generate analysis complete email"""
    first_name = user_name.split()[0] if user_name else "there"
    
    # Ensure clean integers — no floating point decimals in dollar amounts
    recommended_offer = round(recommended_offer)
    asking_price = round(asking_price)
    offer_score = round(offer_score)
    
    # Calculate potential savings
    savings = max(0, asking_price - recommended_offer)
    
    # Right box: Show recommended offer with savings context
    if savings > 0:
        right_label = "Recommended Offer"
        right_value = f"${recommended_offer:,}"
        right_sub = f"Save ${savings:,} vs asking"
        right_color = "#10b981"
    else:
        right_label = "Recommended Offer"
        right_value = f"${recommended_offer:,}"
        right_sub = "Fair at asking price"
        right_color = "#3b82f6"
    
    # Score color — OfferScore is quality (higher = better)
    if offer_score >= 80:
        score_color = "#10b981"
        score_label = "Strong Buy"
    elif offer_score >= 60:
        score_color = "#3b82f6"
        score_label = "Good"
    elif offer_score >= 40:
        score_color = "#f59e0b"
        score_label = "Negotiate"
    elif offer_score >= 25:
        score_color = "#f97316"
        score_label = "Caution"
    else:
        score_color = "#ef4444"
        score_label = "High Risk"
    
    subject = f"Your Analysis is Ready: {property_address[:30]}..."
    
    content = f'''
        <h2 style="margin: 0 0 16px 0; color: #f8fafc; font-size: 24px; font-weight: 700;">
            Your Analysis is Ready! 📊
        </h2>
        
        <p style="margin: 0 0 8px 0; color: #94a3b8; font-size: 14px;">
            Property Address
        </p>
        <p style="margin: 0 0 24px 0; color: #f8fafc; font-size: 18px; font-weight: 600;">
            {property_address}
        </p>
        
        <!-- Results Summary -->
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin: 24px 0;">
            <tr>
                <td width="50%" style="padding-right: 8px;">
                    <div style="background: rgba(15, 23, 42, 0.6); border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.1); padding: 20px; text-align: center;">
                        <div style="font-size: 14px; color: #94a3b8; margin-bottom: 8px;">OfferScore™</div>
                        <div style="font-size: 36px; font-weight: 800; color: {score_color};">{offer_score}</div>
                        <div style="font-size: 13px; color: {score_color};">{score_label}</div>
                    </div>
                </td>
                <td width="50%" style="padding-left: 8px;">
                    <div style="background: rgba(15, 23, 42, 0.6); border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.1); padding: 20px; text-align: center;">
                        <div style="font-size: 14px; color: #94a3b8; margin-bottom: 8px;">{right_label}</div>
                        <div style="font-size: 36px; font-weight: 800; color: {right_color};">{right_value}</div>
                        <div style="font-size: 13px; color: #94a3b8;">{right_sub}</div>
                    </div>
                </td>
            </tr>
        </table>
        
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: rgba(59, 130, 246, 0.1); border-radius: 12px; border: 1px solid rgba(59, 130, 246, 0.3); margin: 24px 0;">
            <tr>
                <td style="padding: 20px;">
                    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                        <tr>
                            <td style="color: #94a3b8; font-size: 14px;">Asking Price</td>
                            <td align="right" style="color: #f8fafc; font-size: 16px;">${asking_price:,}</td>
                        </tr>
                        <tr>
                            <td style="color: #60a5fa; font-size: 14px; font-weight: 600; padding-top: 8px;">Recommended Offer</td>
                            <td align="right" style="color: #60a5fa; font-size: 18px; font-weight: 700; padding-top: 8px;">${recommended_offer:,}</td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
        
        <p style="margin: 0 0 8px 0; color: #cbd5e1; font-size: 16px; line-height: 1.6;">
            Your full report includes Risk DNA™, Seller Transparency Report™, and negotiation talking points.
        </p>
        
        {get_button("View Full Report →", f"https://www.getofferwise.ai/app?analysis={property_id}" if property_id else "https://www.getofferwise.ai/app")}
        
        <p style="margin: 24px 0 0 0; color: #94a3b8; font-size: 14px; text-align: center;">
            💡 <strong>Pro tip:</strong> Use the Negotiation Coach to generate offer letters and repair requests.
        </p>
    '''
    
    html = get_base_template(content, f"Analysis ready for {property_address} - OfferScore {offer_score}")
    return subject, html


def get_credits_reminder_email(user_name: str, credits: int, days_unused: int) -> tuple:
    """Generate unused credits reminder email"""
    first_name = user_name.split()[0] if user_name else "there"
    
    subject = f"You have {credits} unused analysis credits 🏠"
    
    content = f'''
        <h2 style="margin: 0 0 16px 0; color: #f8fafc; font-size: 24px; font-weight: 700;">
            Don't forget your credits! 💫
        </h2>
        
        <p style="margin: 0 0 20px 0; color: #cbd5e1; font-size: 16px; line-height: 1.6;">
            Hi {first_name}, you have <strong style="color: #10b981;">{credits} analysis credit{'s' if credits != 1 else ''}</strong> waiting for you.
        </p>
        
        <p style="margin: 0 0 20px 0; color: #cbd5e1; font-size: 16px; line-height: 1.6;">
            Looking at a property? Upload your inspection report and seller disclosure to get:
        </p>
        
        <ul style="margin: 0 0 24px 0; padding-left: 20px; color: #cbd5e1; font-size: 15px; line-height: 1.8;">
            <li>Your personalized <strong style="color: #f8fafc;">OfferScore™</strong></li>
            <li>Complete <strong style="color: #f8fafc;">Property Risk DNA™</strong></li>
            <li><strong style="color: #f8fafc;">Seller Transparency Report™</strong> (what they disclosed vs what inspectors found)</li>
            <li>Specific <strong style="color: #f8fafc;">offer recommendation</strong></li>
            <li><strong style="color: #f8fafc;">Negotiation talking points</strong></li>
        </ul>
        
        <p style="margin: 0 0 24px 0; color: #94a3b8; font-size: 14px;">
            Your credits never expire — use them whenever you're ready.
        </p>
        
        {get_button("Analyze a Property →", "https://www.getofferwise.ai/app")}
    '''
    
    html = get_base_template(content, f"You have {credits} unused OfferWise credits")
    return subject, html


# =============================================================================
# EMAIL SENDING FUNCTIONS
# =============================================================================

def send_email(to_email: str, subject: str, html_content: str, reply_to: str = None) -> bool:
    """
    Send an email via Resend.
    
    Args:
        to_email: Recipient email address
        subject: Email subject
        html_content: HTML content of email
        reply_to: Optional reply-to address
        
    Returns:
        True if sent successfully, False otherwise
    """
    if not EMAIL_ENABLED:
        logger.info(f"📧 Email disabled - would send '{subject}' to {to_email}")
        return False
    
    try:
        params = {
            "from": FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": html_content,
        }
        
        if reply_to:
            params["reply_to"] = reply_to
        
        response = resend.Emails.send(params)
        logger.info(f"✅ Email sent: '{subject}' to {to_email} (ID: {response.get('id', 'unknown')})")
        return True
        
    except Exception as e:
        logger.error(f"❌ Email failed: '{subject}' to {to_email} - {str(e)}")
        return False


# =============================================================================
# HIGH-LEVEL EMAIL FUNCTIONS (Call these from app.py)
# =============================================================================

def send_welcome_email(to_email: str, user_name: str) -> bool:
    """Send welcome email to new user"""
    subject, html = get_welcome_email(user_name)
    return send_email(to_email, subject, html)


def send_purchase_receipt(to_email: str, user_name: str, plan_name: str, credits: int, amount: float) -> bool:
    """Send purchase receipt after successful payment"""
    subject, html = get_purchase_receipt_email(user_name, plan_name, credits, amount)
    return send_email(to_email, subject, html)


def send_analysis_complete(to_email: str, user_name: str, property_address: str, 
                           offer_score: int, recommended_offer: int, asking_price: int,
                           property_id: int = None) -> bool:
    """Send notification when analysis completes"""
    subject, html = get_analysis_complete_email(
        user_name, property_address, offer_score, recommended_offer, asking_price, property_id
    )
    return send_email(to_email, subject, html)


def send_credits_reminder(to_email: str, user_name: str, credits: int, days_unused: int = 7) -> bool:
    """Send reminder about unused credits"""
    subject, html = get_credits_reminder_email(user_name, credits, days_unused)
    return send_email(to_email, subject, html)


# =============================================================================
# TEST FUNCTION
# =============================================================================

def test_email_templates():
    """Generate test emails and print to console (for development)"""
    print("\n" + "="*60)
    print("TESTING EMAIL TEMPLATES")
    print("="*60)
    
    # Test Welcome
    subject, html = get_welcome_email("John Smith")
    print(f"\n📧 WELCOME EMAIL\nSubject: {subject}\nLength: {len(html)} chars")
    
    # Test Receipt
    subject, html = get_purchase_receipt_email("John Smith", "5-Pack Bundle", 5, 99.00)
    print(f"\n📧 RECEIPT EMAIL\nSubject: {subject}\nLength: {len(html)} chars")
    
    # Test Analysis Complete
    subject, html = get_analysis_complete_email(
        "John Smith", "123 Main Street, San Jose, CA 95123", 
        78, 725000, 799000
    )
    print(f"\n📧 ANALYSIS COMPLETE EMAIL\nSubject: {subject}\nLength: {len(html)} chars")
    
    # Test Credits Reminder
    subject, html = get_credits_reminder_email("John Smith", 3, 7)
    print(f"\n📧 CREDITS REMINDER EMAIL\nSubject: {subject}\nLength: {len(html)} chars")
    
    print("\n" + "="*60)
    print(f"EMAIL_ENABLED: {EMAIL_ENABLED}")
    print(f"RESEND_API_KEY set: {bool(RESEND_API_KEY)}")
    print("="*60 + "\n")


if __name__ == "__main__":
    test_email_templates()

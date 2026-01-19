"""
Legal Disclaimers and Consent Text

CRITICAL: These disclaimers provide legal protection.
Update VERSION when text changes - this triggers re-consent.
"""

# Current versions - increment when text changes
ANALYSIS_DISCLAIMER_VERSION = "2.0"
TERMS_VERSION = "1.0"
PRIVACY_VERSION = "1.0"

# ============================================================================
# ANALYSIS DISCLAIMER - Shown before every analysis
# ============================================================================

ANALYSIS_DISCLAIMER_TEXT = """
⚖️ CRITICAL LEGAL DISCLAIMER

By proceeding with this property analysis, you acknowledge and agree:

1. NOT PROFESSIONAL ADVICE
   This analysis is NOT a substitute for professional services. You MUST consult:
   • Licensed home inspectors
   • Real estate attorneys
   • Real estate agents
   • Structural engineers
   • Contractors
   • Financial advisors
   
2. NO GUARANTEES
   We make NO WARRANTIES regarding accuracy, completeness, or reliability. 
   Analysis may contain errors. Source documents may be incomplete or inaccurate.

3. YOUR RESPONSIBILITY
   You are solely responsible for:
   • Verifying all information independently
   • Conducting your own inspections
   • Making your own decisions
   • Any consequences of those decisions

4. LIMITATION OF LIABILITY
   OfferWise, its owners, employees, and affiliates SHALL NOT BE LIABLE for:
   • Property damage
   • Financial losses
   • Lost opportunities
   • Legal fees
   • Repair costs
   • Any other damages whatsoever
   
   Maximum liability: Amount you paid for this analysis.

5. USE AT YOUR OWN RISK
   You assume ALL RISK. This analysis is informational only.
   Real estate transactions involve significant financial risk.
   Always conduct proper due diligence.

6. NO PROFESSIONAL RELATIONSHIP
   This does not create any attorney-client, agent-client, inspector-client,
   or other professional relationship.

BY CLICKING "I AGREE", YOU CONFIRM:
✓ You have read and understand this disclaimer
✓ You accept all risks associated with using this analysis
✓ You will verify all information independently
✓ You will consult appropriate professionals
✓ You agree to the limitation of liability

IF YOU DO NOT AGREE, DO NOT PROCEED WITH THE ANALYSIS.
"""

# Short version for repeated consent
ANALYSIS_DISCLAIMER_SHORT = """
⚖️ REMINDER: This analysis is informational only and NOT professional advice.
You must verify all information and consult professionals before making decisions.
Maximum liability: Amount paid for analysis. By proceeding, you accept all risks.
"""

# ============================================================================
# TERMS OF SERVICE
# ============================================================================

TERMS_OF_SERVICE_TEXT = """
OFFERWISE TERMS OF SERVICE

1. ACCEPTANCE OF TERMS
   By using OfferWise, you agree to these Terms of Service.

2. SERVICE DESCRIPTION
   OfferWise provides automated property analysis tools.
   We analyze documents you provide and generate reports.

3. NO PROFESSIONAL ADVICE
   Our service does NOT provide professional advice of any kind.
   Always consult licensed professionals.

4. USER RESPONSIBILITIES
   • Provide accurate information
   • Use service lawfully
   • Maintain account security
   • Verify all information independently

5. LIMITATION OF LIABILITY
   See Analysis Disclaimer for full liability limitations.
   We are not responsible for decisions you make based on our analysis.

6. REFUND POLICY
   • Free tier: No refunds (no payment required)
   • Paid analyses: Refunds at our discretion within 7 days
   • Credits: Non-refundable once purchased

7. PRIVACY
   See our Privacy Policy for how we handle your data.

8. CHANGES TO TERMS
   We may update these terms. Continued use = acceptance.

9. TERMINATION
   We may terminate accounts for violation of terms.

10. GOVERNING LAW
    These terms governed by laws of [Your State/Country].
"""

# ============================================================================
# PRIVACY POLICY
# ============================================================================

PRIVACY_POLICY_TEXT = """
OFFERWISE PRIVACY POLICY

1. INFORMATION WE COLLECT
   • Email address
   • Property documents you upload
   • Analysis results
   • Payment information (via Stripe)
   • Usage data

2. HOW WE USE INFORMATION
   • Provide analysis services
   • Improve our algorithms
   • Process payments
   • Send service updates

3. DATA STORAGE
   • Documents stored securely
   • Encrypted in transit and at rest
   • Retained for service provision

4. DATA SHARING
   • We do NOT sell your data
   • Payment processing via Stripe
   • May share if legally required

5. YOUR RIGHTS
   • Access your data
   • Delete your account
   • Export your data

6. SECURITY
   • Industry-standard encryption
   • Regular security audits
   • Secure authentication

7. COOKIES
   • Essential cookies for functionality
   • No advertising/tracking cookies

8. CHANGES TO POLICY
   We may update this policy. Continued use = acceptance.

Contact: [Your Email]
"""

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_disclaimer_text(consent_type):
    """Get the full text for a consent type"""
    if consent_type == 'analysis_disclaimer':
        return ANALYSIS_DISCLAIMER_TEXT
    elif consent_type in ['terms', 'terms_of_service']:
        return TERMS_OF_SERVICE_TEXT
    elif consent_type in ['privacy', 'privacy_policy']:
        return PRIVACY_POLICY_TEXT
    else:
        return None

def get_disclaimer_version(consent_type):
    """Get the current version for a consent type"""
    if consent_type == 'analysis_disclaimer':
        return ANALYSIS_DISCLAIMER_VERSION
    elif consent_type in ['terms', 'terms_of_service']:
        return TERMS_VERSION
    elif consent_type in ['privacy', 'privacy_policy']:
        return PRIVACY_VERSION
    else:
        return None

def get_all_disclaimers():
    """Get all disclaimer types, texts, and versions"""
    return {
        'analysis_disclaimer': {
            'text': ANALYSIS_DISCLAIMER_TEXT,
            'version': ANALYSIS_DISCLAIMER_VERSION,
            'title': 'Analysis Disclaimer'
        },
        'terms': {
            'text': TERMS_OF_SERVICE_TEXT,
            'version': TERMS_VERSION,
            'title': 'Terms of Service'
        },
        'privacy': {
            'text': PRIVACY_POLICY_TEXT,
            'version': PRIVACY_VERSION,
            'title': 'Privacy Policy'
        }
    }

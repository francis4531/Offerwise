# OfferWise Email Setup Guide

## Quick Start (15 minutes)

### Step 1: Create Resend Account
1. Go to https://resend.com
2. Sign up (free tier: 3,000 emails/month)
3. Verify your email

### Step 2: Add Your Domain
1. In Resend dashboard → "Domains" → "Add Domain"
2. Add `getofferwise.ai`
3. Add the DNS records Resend shows you to your domain registrar
4. Wait for verification (usually 5-10 minutes)

### Step 3: Create API Key
1. In Resend dashboard → "API Keys" → "Create API Key"
2. Name it "OfferWise Production"
3. Copy the key (starts with `re_`)

### Step 4: Add to Render Environment
1. Go to Render dashboard → Your OfferWise service
2. "Environment" → Add Environment Variable:
   - Key: `RESEND_API_KEY`
   - Value: `re_your_key_here`
3. Click "Save Changes"
4. Service will auto-redeploy

## Emails That Will Be Sent

| Trigger | Email | Subject |
|---------|-------|---------|
| New user signup | Welcome | "Welcome to OfferWise! 🏠" |
| Stripe payment success | Receipt | "Receipt: [Plan] - $X.XX" |
| Analysis completes | Notification | "Your Analysis is Ready: [Address]..." |

## Testing Emails

After setup, emails will automatically send. To verify:

1. Create a new account → Welcome email
2. Buy credits → Receipt email  
3. Run an analysis → Analysis complete email

Check Resend dashboard → "Emails" to see delivery status.

## Troubleshooting

**Emails not sending?**
- Check Render logs for `📧 Email sent` or `📧 Could not send`
- Verify API key is correct in Render environment
- Check Resend dashboard for errors

**Emails going to spam?**
- Complete domain verification in Resend
- Add SPF, DKIM, and DMARC records
- Use consistent "From" address

## Email Addresses

Make sure these email addresses are monitored:
- `hello@getofferwise.ai` - Welcome emails sent FROM here
- `support@getofferwise.ai` - Users may reply here
- `billing@getofferwise.ai` - Receipt emails reference this

## Future Enhancements

Once launched, consider adding:
- [ ] Credits reminder email (7 days after purchase if unused)
- [ ] Weekly digest for users with multiple analyses
- [ ] Referral program emails

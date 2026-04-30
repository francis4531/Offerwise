"""
flywheel_notifications.py — Closing the open loops across all four personas.

Functions:
  _send_inspector_loop_email()   — notifies inspector when buyer uses their report
  _send_agent_postclose_email()  — notifies agent when buyer's deal closes
  send_contractor_lead_email()   — sends pre-scoped lead to matched contractor
  _send_contractor_thankyou()    — confirms job completion receipt
  get_inspector_impact_stats()   — portal stats for inspector dashboard
  get_agent_pipeline_stats()     — portal stats for agent dashboard
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _fmt(n: float) -> str:
    """Format dollar amount for email display."""
    if n >= 1_000_000:
        return f"${n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"${round(n/1_000)}K"
    return f"${round(n):,}"


def _send(to_email: str, subject: str, html: str, email_type: str = 'flywheel') -> bool:
    """Send via Resend. Non-blocking — logs warning on failure."""
    try:
        from email_service import send_email
        send_email(to_email, subject, html, email_type=email_type)
        return True
    except Exception as e:
        logger.warning(f"📧 Flywheel email to {to_email} failed: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# 1. INSPECTOR LOOP-BACK
# ──────────────────────────────────────────────────────────────────────────────

def _send_inspector_loop_email(
    inspector_report,
    result_dict: dict,
    buyer_email: str,
    savings: int,
) -> bool:
    """
    Notify the inspector when a buyer completes an analysis using their report.
    Tells them which findings were used, savings achieved, and links to portal.
    """
    try:
        from models import Inspector, User, db
        inspector = Inspector.query.get(inspector_report.inspector_id)
        if not inspector:
            return False
        inspector_user = User.query.get(inspector.user_id)
        if not inspector_user or not inspector_user.email:
            return False

        address = inspector_report.property_address or 'the property'
        insp_name = inspector.business_name or inspector_user.name or 'there'

        # Extract findings used
        offer_strategy = result_dict.get('offer_strategy', {})
        cross_ref = result_dict.get('cross_reference', {})
        risk_score = result_dict.get('risk_score', {})

        contradictions = cross_ref.get('contradictions', []) if isinstance(cross_ref, dict) else []
        deal_breakers = risk_score.get('deal_breakers', []) if isinstance(risk_score, dict) else []
        rec_offer = offer_strategy.get('recommended_offer', 0) if isinstance(offer_strategy, dict) else 0
        asking = inspector_report.property_price or 0

        # Build findings rows HTML
        findings_html = ''
        findings_used = 0
        for db_item in deal_breakers[:3]:
            title = db_item.get('system', db_item.get('title', 'Issue')) if isinstance(db_item, dict) else str(db_item)
            findings_html += f'''
            <tr>
              <td style="padding:10px 0;border-bottom:1px solid rgba(52,211,153,0.1);color:#4ade80;font-size:16px;width:28px;">✓</td>
              <td style="padding:10px 0 10px 8px;border-bottom:1px solid rgba(52,211,153,0.1);font-size:14px;color:#e2e8f0;">{title[:80]}</td>
              <td style="padding:10px 0;border-bottom:1px solid rgba(52,211,153,0.1);text-align:right;">
                <span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;background:rgba(74,222,128,0.15);color:#4ade80;text-transform:uppercase;">Used in offer</span>
              </td>
            </tr>'''
            findings_used += 1

        for c_item in contradictions[:2]:
            title = c_item.get('title', 'Disclosure contradiction') if isinstance(c_item, dict) else str(c_item)
            findings_html += f'''
            <tr>
              <td style="padding:10px 0;border-bottom:1px solid rgba(52,211,153,0.1);color:#4ade80;font-size:16px;width:28px;">✓</td>
              <td style="padding:10px 0 10px 8px;border-bottom:1px solid rgba(52,211,153,0.1);font-size:14px;color:#e2e8f0;">{str(title)[:80]}</td>
              <td style="padding:10px 0;border-bottom:1px solid rgba(52,211,153,0.1);text-align:right;">
                <span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;background:rgba(251,191,36,0.15);color:#fbbf24;text-transform:uppercase;">Contradiction surfaced</span>
              </td>
            </tr>'''
            findings_used += 1

        if not findings_html:
            findings_html = '''
            <tr>
              <td colspan="3" style="padding:12px 0;font-size:14px;color:#64748b;text-align:center;">Findings contributed to overall risk score and offer recommendation.</td>
            </tr>'''

        # Inspector portal stats
        stats = get_inspector_impact_stats(inspector.id)
        total_savings = _fmt(stats.get('total_savings', 0))
        total_analyses = stats.get('total_analyses', 1)

        savings_line = f'<strong style="color:#4ade80;">{_fmt(max(savings, 0))}</strong>' if savings > 0 else 'pricing analysis completed'

        subject = f"🔍 Your inspection of {address[:40]} just helped a buyer"
        if savings > 0:
            subject = f"🔍 Your inspection of {address[:40]} helped a buyer save {_fmt(savings)}"

        html = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#09090f;color:#e2e8f0;">
<div style="max-width:560px;margin:0 auto;padding:24px 16px;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#0d1a12,#111118);border:1px solid rgba(52,211,153,0.2);border-radius:16px;padding:28px;margin-bottom:16px;">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;color:#34d399;margin-bottom:16px;">
      Inspector Impact Report
    </div>
    <div style="font-size:22px;font-weight:800;color:#f1f5f9;line-height:1.2;margin-bottom:8px;">
      Your report changed what they paid.
    </div>
    <div style="font-size:14px;color:#64748b;line-height:1.6;">
      Hi {insp_name} — a buyer completed an OfferWise analysis using your inspection of
      <strong style="color:#e2e8f0;">{address}</strong>. Here's what your findings contributed.
    </div>
  </div>

  <!-- Stats row -->
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:16px;">
    <div style="background:#111118;border:1px solid rgba(51,65,85,0.5);border-radius:12px;padding:16px;text-align:center;">
      <div style="font-size:22px;font-weight:800;color:#34d399;">{findings_used}</div>
      <div style="font-size:11px;color:#475569;margin-top:4px;">Findings used</div>
    </div>
    <div style="background:#111118;border:1px solid rgba(51,65,85,0.5);border-radius:12px;padding:16px;text-align:center;">
      <div style="font-size:22px;font-weight:800;color:#4ade80;">{savings_line}</div>
      <div style="font-size:11px;color:#475569;margin-top:4px;">Buyer savings</div>
    </div>
    <div style="background:#111118;border:1px solid rgba(51,65,85,0.5);border-radius:12px;padding:16px;text-align:center;">
      <div style="font-size:22px;font-weight:800;color:#60a5fa;">{total_analyses}</div>
      <div style="font-size:11px;color:#475569;margin-top:4px;">Analyses this month</div>
    </div>
  </div>

  <!-- Findings table -->
  <div style="background:#111118;border:1px solid rgba(52,211,153,0.15);border-radius:12px;padding:20px;margin-bottom:16px;">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#64748b;margin-bottom:14px;">
      Findings from your report used in this analysis
    </div>
    <table style="width:100%;border-collapse:collapse;">
      {findings_html}
    </table>
  </div>

  <!-- Body -->
  <div style="background:#111118;border:1px solid rgba(51,65,85,0.4);border-radius:12px;padding:20px;margin-bottom:16px;">
    <p style="font-size:14px;color:#94a3b8;line-height:1.8;margin:0 0 12px;">
      Your report found what it needed to find. OfferWise translated it into a specific number
      the buyer could use at the table.
    </p>
    <p style="font-size:14px;color:#94a3b8;line-height:1.8;margin:0;">
      You've generated <strong style="color:#e2e8f0;">{total_analyses} OfferWise {('analyses' if total_analyses != 1 else 'analysis')}</strong> this month.
      Buyers who run an analysis are <strong style="color:#e2e8f0;">3× more likely</strong> to act on inspection findings
      — and more likely to refer their agent to you.
    </p>
  </div>

  <!-- CTA -->
  <div style="text-align:center;margin-bottom:20px;">
    <a href="https://www.getofferwise.ai/settings?tab=inspector&utm_source=inspector_loop&utm_medium=email"
       style="display:inline-block;padding:14px 32px;background:linear-gradient(135deg,#059669,#34d399);color:#fff;font-weight:700;font-size:15px;border-radius:10px;text-decoration:none;">
      View your impact in the Inspector Portal →
    </a>
  </div>

  <!-- Footer -->
  <div style="text-align:center;font-size:12px;color:#334155;padding:16px 0;">
    OfferWise AI · <a href="https://www.getofferwise.ai" style="color:#475569;">getofferwise.ai</a>
    &nbsp;·&nbsp; <a href="https://www.getofferwise.ai/unsubscribe?type=inspector_loop" style="color:#475569;">Unsubscribe</a>
  </div>
</div>
</body>
</html>"""

        sent = _send(inspector_user.email, subject, html, email_type='inspector_loop')
        if sent:
            logger.info(f"📋 Inspector loop email sent to {inspector_user.email} for {address}")
        return sent

    except Exception as e:
        logger.warning(f"_send_inspector_loop_email failed: {e}")
        return False


def get_inspector_impact_stats(inspector_id: int) -> dict:
    """Return monthly impact stats for the inspector portal."""
    try:
        from models import InspectorReport, Analysis, db
        from datetime import timedelta
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)

        reports_this_month = InspectorReport.query.filter(
            InspectorReport.inspector_id == inspector_id,
            InspectorReport.created_at >= month_start
        ).all()

        total_analyses = 0
        total_savings = 0

        for r in reports_this_month:
            # Find analyses linked to this report via inspector_report_id
            linked = Analysis.query.filter_by(inspector_report_id=r.id).all()
            total_analyses += len(linked)
            for a in linked:
                try:
                    import json
                    rd = json.loads(a.result_json or '{}')
                    asking = rd.get('property_price', 0) or 0
                    rec = rd.get('offer_strategy', {}).get('recommended_offer', asking) or asking
                    total_savings += max(0, asking - rec)
                except Exception:
                    pass

        return {
            'total_analyses': total_analyses,
            'total_savings': total_savings,
            'reports_this_month': len(reports_this_month),
        }
    except Exception as e:
        logger.warning(f"get_inspector_impact_stats: {e}")
        return {'total_analyses': 0, 'total_savings': 0, 'reports_this_month': 0}


# ──────────────────────────────────────────────────────────────────────────────
# 2. AGENT POST-CLOSE SIGNAL
# ──────────────────────────────────────────────────────────────────────────────

def send_agent_postclose_email(
    agent_share_id: int,
    final_sale_price: Optional[float] = None,
) -> bool:
    """
    Send the agent a post-close summary after their buyer's deal closes.
    Call this from: Stripe webhook on escrow close event, or manual admin trigger.
    """
    try:
        from models import AgentShare, Agent, User, db
        share = AgentShare.query.get(agent_share_id)
        if not share:
            logger.warning(f"send_agent_postclose_email: share {agent_share_id} not found")
            return False

        agent = Agent.query.get(share.agent_id)
        if not agent:
            return False
        agent_user = User.query.get(agent.user_id)
        if not agent_user or not agent_user.email:
            return False

        # Record the close
        share.deal_closed_at = datetime.utcnow()
        if final_sale_price:
            share.final_sale_price = final_sale_price
        db.session.commit()

        address = share.property_address or 'the property'
        buyer_name = share.buyer_name or 'Your buyer'
        asking = share.property_price or 0
        final = final_sale_price or 0
        saved = max(0, asking - final) if asking and final else 0
        agent_name = agent.agent_name or agent_user.name or 'there'

        # Quarter stats
        stats = get_agent_pipeline_stats(agent.id)
        q_closed = stats.get('closed_this_quarter', 0) + 1
        q_savings = stats.get('total_savings_quarter', 0) + saved
        avg_discount = stats.get('avg_discount_pct', 0)

        # Timeline HTML
        analysis_date = share.created_at.strftime('%b %d, %Y') if share.created_at else 'Recently'
        close_date = datetime.utcnow().strftime('%b %d, %Y')

        subject = f"🎉 {buyer_name} just closed — OfferWise helped"
        if saved > 0:
            subject = f"🎉 {buyer_name} closed on {address[:35]}. They saved {_fmt(saved)}."

        html = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#09090f;color:#e2e8f0;">
<div style="max-width:560px;margin:0 auto;padding:24px 16px;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#1c1810,#111118);border:1px solid rgba(245,158,11,0.2);border-radius:16px;padding:28px;margin-bottom:16px;">
    <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px;">
      <div style="width:52px;height:52px;border-radius:14px;background:linear-gradient(135deg,#d97706,#f59e0b);display:flex;align-items:center;justify-content:center;font-size:24px;flex-shrink:0;">🎉</div>
      <div>
        <div style="font-size:20px;font-weight:800;color:#f1f5f9;margin-bottom:4px;">Congratulations — your client just closed.</div>
        <div style="font-size:13px;color:#64748b;">Hi {agent_name} · {close_date}</div>
      </div>
    </div>
  </div>

  <!-- Stats -->
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:16px;">
    <div style="background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.15);border-radius:12px;padding:16px;">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#64748b;margin-bottom:6px;">Property</div>
      <div style="font-size:13px;font-weight:700;color:#e2e8f0;">{address[:35]}</div>
    </div>
    <div style="background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.15);border-radius:12px;padding:16px;">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#64748b;margin-bottom:6px;">Final Price</div>
      <div style="font-size:18px;font-weight:800;color:#f59e0b;">{_fmt(final) if final else '—'}</div>
    </div>
    <div style="background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.15);border-radius:12px;padding:16px;">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#64748b;margin-bottom:6px;">vs. Asking</div>
      <div style="font-size:18px;font-weight:800;color:#4ade80;">{('−' + _fmt(saved)) if saved > 0 else '—'}</div>
    </div>
  </div>

  <!-- Body -->
  <div style="background:#111118;border:1px solid rgba(51,65,85,0.4);border-radius:12px;padding:20px;margin-bottom:16px;">
    <p style="font-size:14px;color:#94a3b8;line-height:1.8;margin:0 0 16px;">
      <strong style="color:#e2e8f0;">{buyer_name} used the OfferWise analysis you sent them in this negotiation.</strong>
      The report identified key leverage points and produced a recommended offer that held through escrow.
    </p>

    <!-- Timeline -->
    <div style="border-left:1px solid rgba(245,158,11,0.2);padding-left:20px;margin:16px 0 0;">
      <div style="position:relative;padding-bottom:14px;">
        <div style="position:absolute;left:-25px;top:4px;width:8px;height:8px;border-radius:50%;background:#f59e0b;"></div>
        <strong style="font-size:13px;color:#e2e8f0;display:block;">You sent the OfferWise analysis</strong>
        <span style="font-size:12px;color:#475569;">{analysis_date}</span>
      </div>
      <div style="position:relative;padding-bottom:14px;">
        <div style="position:absolute;left:-25px;top:4px;width:8px;height:8px;border-radius:50%;background:#f59e0b;"></div>
        <strong style="font-size:13px;color:#e2e8f0;display:block;">{buyer_name} completed the analysis</strong>
        <span style="font-size:12px;color:#475569;">Leverage points identified</span>
      </div>
      <div style="position:relative;padding-bottom:14px;">
        <div style="position:absolute;left:-25px;top:4px;width:8px;height:8px;border-radius:50%;background:#f59e0b;"></div>
        <strong style="font-size:13px;color:#e2e8f0;display:block;">Offer submitted{(': ' + _fmt(final)) if final else ''}</strong>
        <span style="font-size:12px;color:#475569;">Data-backed negotiation</span>
      </div>
      <div style="position:relative;">
        <div style="position:absolute;left:-25px;top:4px;width:8px;height:8px;border-radius:50%;background:#4ade80;"></div>
        <strong style="font-size:13px;color:#4ade80;display:block;">Deal closed. 🏠</strong>
        <span style="font-size:12px;color:#475569;">{close_date}</span>
      </div>
    </div>
  </div>

  <!-- Quarter stats -->
  <div style="background:rgba(245,158,11,0.04);border:1px solid rgba(245,158,11,0.12);border-radius:12px;padding:20px;margin-bottom:16px;">
    <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#64748b;margin-bottom:14px;">Your Q1 2026 with OfferWise</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
      <div>
        <div style="font-size:24px;font-weight:800;color:#f59e0b;">{q_closed}</div>
        <div style="font-size:12px;color:#64748b;">Closed deals this quarter</div>
      </div>
      <div>
        <div style="font-size:24px;font-weight:800;color:#4ade80;">{_fmt(q_savings)}</div>
        <div style="font-size:12px;color:#64748b;">Total client savings</div>
      </div>
    </div>
    <p style="font-size:13px;color:#64748b;margin:14px 0 0;line-height:1.6;">
      That's the number worth putting in your bio.
    </p>
  </div>

  <!-- CTA -->
  <div style="display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap;">
    <a href="https://www.getofferwise.ai/settings?tab=agent&utm_source=agent_postclose&utm_medium=email"
       style="flex:1;display:block;padding:14px;background:linear-gradient(135deg,#d97706,#f59e0b);color:#0a0500;font-weight:700;font-size:14px;border-radius:10px;text-decoration:none;text-align:center;min-width:180px;">
      View Full Pipeline →
    </a>
    <a href="https://www.getofferwise.ai/app?utm_source=agent_postclose&utm_medium=email"
       style="flex:1;display:block;padding:14px;background:transparent;color:#64748b;font-weight:600;font-size:14px;border-radius:10px;text-decoration:none;text-align:center;border:1px solid rgba(51,65,85,0.6);min-width:180px;">
      Send to next buyer →
    </a>
  </div>

  <div style="text-align:center;font-size:12px;color:#334155;padding:16px 0;">
    OfferWise AI · <a href="https://www.getofferwise.ai" style="color:#475569;">getofferwise.ai</a>
    &nbsp;·&nbsp; <a href="https://www.getofferwise.ai/unsubscribe?type=agent_postclose" style="color:#475569;">Unsubscribe</a>
  </div>
</div>
</body>
</html>"""

        sent = _send(agent_user.email, subject, html, email_type='agent_postclose')
        if sent:
            logger.info(f"🎉 Agent post-close email sent to {agent_user.email} for {address}")
        return sent

    except Exception as e:
        logger.warning(f"send_agent_postclose_email failed: {e}")
        return False


def get_agent_pipeline_stats(agent_id: int) -> dict:
    """Return pipeline stats for the agent portal."""
    try:
        from models import AgentShare, db
        from datetime import timedelta

        q_start = datetime.utcnow().replace(month=((datetime.utcnow().month - 1) // 3) * 3 + 1,
                                             day=1, hour=0, minute=0, second=0)

        shares = AgentShare.query.filter(
            AgentShare.agent_id == agent_id,
            AgentShare.created_at >= q_start,
        ).all()

        closed = [s for s in shares if getattr(s, 'deal_closed_at', None)]
        total_savings = 0
        for s in closed:
            asking = s.property_price or 0
            final = getattr(s, 'final_sale_price', None) or 0
            total_savings += max(0, asking - final)

        avg_discount = 0
        if closed and total_savings:
            total_asking = sum(s.property_price or 0 for s in closed if s.property_price)
            avg_discount = round(total_savings / total_asking * 100, 1) if total_asking else 0

        return {
            'closed_this_quarter': len(closed),
            'total_savings_quarter': total_savings,
            'avg_discount_pct': avg_discount,
            'active_analyses': len([s for s in shares if not getattr(s, 'deal_closed_at', None)]),
        }
    except Exception as e:
        logger.warning(f"get_agent_pipeline_stats: {e}")
        return {'closed_this_quarter': 0, 'total_savings_quarter': 0, 'avg_discount_pct': 0}


# ──────────────────────────────────────────────────────────────────────────────
# 3. CONTRACTOR LEAD + COMPLETION
# ──────────────────────────────────────────────────────────────────────────────

def send_contractor_lead_email(lead_id: int) -> bool:
    """
    Send a pre-scoped lead email to matched contractors.
    Called from the existing lead-matching scheduler job.
    """
    try:
        from models import ContractorLead, ContractorLeadClaim, Contractor, db

        lead = ContractorLead.query.get(lead_id)
        if not lead:
            return False

        # Find matching contractors
        claims = ContractorLeadClaim.query.filter_by(lead_id=lead_id).all()
        for claim in claims:
            contractor = Contractor.query.get(claim.contractor_id)
            if not contractor or not contractor.email:
                continue
            _send_lead_to_contractor(lead, contractor, claim)

        return True
    except Exception as e:
        logger.warning(f"send_contractor_lead_email: {e}")
        return False


def _send_lead_to_contractor(lead, contractor, claim) -> bool:
    """Send a single lead card email to one contractor."""
    try:
        import json
        address = lead.property_address or 'Property'
        scope_raw = lead.scope_json or '{}'
        scope = json.loads(scope_raw) if isinstance(scope_raw, str) else scope_raw
        systems = scope.get('systems', []) if isinstance(scope, dict) else []

        # Build scope rows
        scope_html = ''
        for s in systems[:4]:
            sev_color = '#ef4444' if s.get('severity') == 'critical' else '#fbbf24'
            sev_label = s.get('severity', 'major').upper()
            est_low = s.get('estimate_low', 0)
            est_high = s.get('estimate_high', 0)
            range_str = f"${round(est_low/1000)}K–${round(est_high/1000)}K" if est_low and est_high else ''
            scope_html += f"""
            <tr>
              <td style="padding:10px 12px;font-size:13px;font-weight:600;color:#e2e8f0;">{s.get('system_name', s.get('trade', 'Work'))[:50]}</td>
              <td style="padding:10px 12px;">
                <span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:8px;background:rgba(239,68,68,0.12);color:{sev_color};text-transform:uppercase;">{sev_label}</span>
              </td>
              <td style="padding:10px 12px;font-size:13px;font-weight:700;color:#e879f9;text-align:right;">{range_str}</td>
            </tr>"""

        if not scope_html:
            scope_html = '<tr><td colspan="3" style="padding:12px;font-size:13px;color:#64748b;">Contact buyer for full scope details.</td></tr>'

        # Lead quality score
        quality = min(99, 80 + len(systems) * 4)
        close_date = lead.close_date.strftime('%b %d') if getattr(lead, 'close_date', None) else 'Soon'
        portal_url = f"https://www.getofferwise.ai/contractor/leads/{lead.id}?claim={claim.id}"

        subject = f"⚡ New lead matched to your trades — {address[:45]}"

        html = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#09090f;color:#e2e8f0;">
<div style="max-width:560px;margin:0 auto;padding:24px 16px;">

  <!-- Lead card -->
  <div style="background:linear-gradient(160deg,#160d1a,#111118);border:1px solid rgba(232,121,249,0.2);border-radius:16px;overflow:hidden;margin-bottom:16px;">
    <div style="height:3px;background:linear-gradient(90deg,#9333ea,#e879f9,#9333ea);"></div>
    <div style="padding:22px 24px;display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
      <div>
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;color:#e879f9;margin-bottom:8px;">
          🟣 New Lead · Matched to your trades
        </div>
        <div style="font-size:18px;font-weight:700;margin-bottom:4px;">{address}</div>
        <div style="font-size:12px;color:#64748b;">Buyer is active · Closes {close_date}</div>
      </div>
      <div style="text-align:center;padding:10px 16px;background:rgba(232,121,249,0.1);border:1px solid rgba(232,121,249,0.2);border-radius:10px;flex-shrink:0;">
        <div style="font-size:28px;font-weight:900;color:#e879f9;line-height:1;">{quality}</div>
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#64748b;margin-top:4px;">Lead Quality</div>
      </div>
    </div>

    <!-- Scope table -->
    <div style="padding:0 24px 18px;">
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#64748b;margin-bottom:10px;">Scope — matched to your license types</div>
      <table style="width:100%;background:rgba(255,255,255,0.03);border:1px solid rgba(51,65,85,0.4);border-radius:10px;border-collapse:collapse;">
        {scope_html}
      </table>
    </div>

    <!-- CTA -->
    <div style="padding:0 24px 22px;">
      <a href="{portal_url}" style="display:block;width:100%;padding:14px;background:linear-gradient(135deg,#9333ea,#e879f9);color:#fff;font-weight:700;font-size:15px;border-radius:10px;text-decoration:none;text-align:center;box-sizing:border-box;">
        Request buyer contact info →
      </a>
      <div style="display:flex;justify-content:space-between;margin-top:10px;font-size:12px;color:#475569;">
        <span>Flat subscription · No referral fees · No commissions</span>
        <span>Buyer needs quote ASAP</span>
      </div>
    </div>
  </div>

  <div style="text-align:center;font-size:12px;color:#334155;padding:16px 0;">
    OfferWise AI · <a href="https://www.getofferwise.ai" style="color:#475569;">getofferwise.ai</a>
    &nbsp;·&nbsp; <a href="https://www.getofferwise.ai/contractor/unsubscribe" style="color:#475569;">Unsubscribe</a>
  </div>
</div>
</body>
</html>"""

        sent = _send(contractor.email, subject, html, email_type='contractor_lead')
        if sent:
            logger.info(f"⚡ Contractor lead email sent to {contractor.email} for {address}")
        return sent

    except Exception as e:
        logger.warning(f"_send_lead_to_contractor: {e}")
        return False


def process_contractor_completion(
    lead_id: int,
    contractor_id: int,
    won_job: bool,
    final_price: Optional[float],
    work_completed: str,
    permit_number: Optional[str] = None,
) -> dict:
    """
    Process a job completion submission.
    Saves record, updates estimate engine, sends thank-you email.
    Returns {'success': bool, 'message': str}.
    """
    try:
        from models import ContractorLead, ContractorLeadClaim, Contractor, ContractorJobCompletion, db

        lead = ContractorLead.query.get(lead_id)
        if not lead:
            return {'success': False, 'message': 'Lead not found'}

        contractor = Contractor.query.get(contractor_id)
        if not contractor:
            return {'success': False, 'message': 'Contractor not found'}

        # Find or create the claim
        claim = ContractorLeadClaim.query.filter_by(
            lead_id=lead_id, contractor_id=contractor_id
        ).first()

        # Parse the original estimate for variance tracking
        import json
        scope_raw = lead.scope_json or '{}'
        scope = json.loads(scope_raw) if isinstance(scope_raw, str) else scope_raw
        systems = scope.get('systems', []) if isinstance(scope, dict) else []

        est_low = sum(s.get('estimate_low', 0) for s in systems)
        est_high = sum(s.get('estimate_high', 0) for s in systems)
        est_mid = (est_low + est_high) / 2 if est_low and est_high else 0

        variance_pct = None
        if won_job and final_price and est_mid:
            variance_pct = round((final_price - est_mid) / est_mid * 100, 1)

        # Create completion record
        completion = ContractorJobCompletion(
            lead_id=lead_id,
            claim_id=claim.id if claim else None,
            contractor_id=contractor_id,
            property_address=lead.property_address,
            zip_code=str(lead.zip_code or '')[:10],
            won_job=won_job,
            final_price=final_price if won_job else None,
            work_completed=work_completed,
            permit_number=permit_number,
            permit_uploaded=bool(permit_number),
            original_estimate_low=est_low or None,
            original_estimate_high=est_high or None,
            variance_pct=variance_pct,
        )
        db.session.add(completion)

        # Update claim status
        if claim:
            claim.status = 'closed' if won_job else 'passed'
            if won_job:
                claim.closed_at = datetime.utcnow()
                claim.job_value = final_price

        # Update contractor stats
        if won_job:
            contractor.jobs_closed = (contractor.jobs_closed or 0) + 1

        db.session.commit()

        # Feed data back into estimate engine
        if won_job and final_price and lead.zip_code:
            _update_estimate_from_completion(completion)

        # Send thank-you
        _send_contractor_thankyou(contractor, completion, variance_pct)

        msg = f"Job {'completion' if won_job else 'pass'} recorded. Thank you!"
        if won_job and variance_pct is not None:
            direction = 'above' if variance_pct > 0 else 'below'
            msg += f" Your price was {abs(variance_pct):.0f}% {direction} the OfferWise estimate — this data helps future buyers."
        logger.info(f"✅ Contractor completion recorded: lead={lead_id} contractor={contractor_id} won={won_job} price={final_price}")
        return {'success': True, 'message': msg}

    except Exception as e:
        logger.error(f"process_contractor_completion failed: {e}", exc_info=True)
        return {'success': False, 'message': 'Error recording completion. Please try again.'}


def _update_estimate_from_completion(completion) -> None:
    """Feed real completion price back into ZIP-level estimate data."""
    try:
        if not (completion.won_job and completion.final_price and completion.zip_code):
            return

        systems = (completion.work_completed or '').split(',')
        price_per_system = completion.final_price / max(len(systems), 1)

        from models import RepairCostLog, db
        log = RepairCostLog(
            zip_code=completion.zip_code[:5],
            metro_name='',
            cost_multiplier=1.0,
            total_low=completion.final_price * 0.9,
            total_high=completion.final_price * 1.1,
            breakdown_json='[]',
            source='contractor_completion',
        )
        db.session.add(log)
        db.session.commit()
        logger.info(f"📊 Estimate engine updated from completion: ZIP={completion.zip_code} price=${completion.final_price:,.0f}")
    except Exception as e:
        logger.warning(f"_update_estimate_from_completion: {e}")


def _send_contractor_thankyou(contractor, completion, variance_pct) -> None:
    """Send a short thank-you confirming the job completion was recorded."""
    try:
        if not contractor.email:
            return

        status = "won" if completion.won_job else "passed on"
        price_line = f"${completion.final_price:,.0f}" if completion.won_job and completion.final_price else "—"
        variance_line = ""
        if variance_pct is not None:
            direction = "above" if variance_pct > 0 else "below"
            variance_line = f"<p style='font-size:13px;color:#94a3b8;line-height:1.7;margin:12px 0 0;'>Your price was <strong style='color:#e2e8f0;'>{abs(variance_pct):.0f}% {direction}</strong> the OfferWise estimate for this ZIP. This data helps us give future buyers more accurate repair ranges.</p>"

        subject = f"✅ Job completion recorded — {completion.property_address or 'your lead'}"
        html = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#09090f;color:#e2e8f0;">
<div style="max-width:480px;margin:0 auto;padding:24px 16px;">
  <div style="background:#111118;border:1px solid rgba(232,121,249,0.15);border-radius:14px;padding:24px;">
    <div style="font-size:22px;margin-bottom:12px;">✅</div>
    <div style="font-size:17px;font-weight:700;margin-bottom:8px;">Completion recorded.</div>
    <p style="font-size:14px;color:#94a3b8;line-height:1.7;margin:0;">
      We've recorded that you <strong style="color:#e2e8f0;">{status}</strong> the job at
      <strong style="color:#e2e8f0;">{completion.property_address or 'the property'}</strong>.
      Final price: <strong style="color:#e879f9;">{price_line}</strong>.
    </p>
    {variance_line}
    <div style="margin-top:20px;">
      <a href="https://www.getofferwise.ai/contractor/portal"
         style="display:inline-block;padding:11px 24px;background:linear-gradient(135deg,#9333ea,#e879f9);color:#fff;font-weight:700;font-size:13px;border-radius:8px;text-decoration:none;">
        View your leads →
      </a>
    </div>
  </div>
  <div style="text-align:center;font-size:12px;color:#334155;padding:16px 0;">
    OfferWise AI · <a href="https://www.getofferwise.ai" style="color:#475569;">getofferwise.ai</a>
  </div>
</div>
</body>
</html>"""
        _send(contractor.email, subject, html, email_type='contractor_completion')
    except Exception as e:
        logger.warning(f"_send_contractor_thankyou: {e}")

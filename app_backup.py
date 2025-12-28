"""
OfferWise API Server for Render.com
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import base64
import os

from document_parser import DocumentParser
from risk_scoring_model import BuyerProfile
from offerwise_intelligence import OfferWiseIntelligence
from pdf_handler import PDFHandler

# Initialize Flask app
app = Flask(__name__, static_folder='static')
CORS(app)

# Initialize components
parser = DocumentParser()
intelligence = OfferWiseIntelligence()
pdf_handler = PDFHandler()

# Health check endpoint
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'service': 'offerwise-api',
        'version': '1.0.0'
    })

# PDF upload endpoint
@app.route('/api/upload-pdf', methods=['POST', 'OPTIONS'])
def upload_pdf():
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        data = request.get_json()
        pdf_base64 = data.get('pdf_base64', '')
        
        if not pdf_base64:
            return jsonify({'error': 'No pdf_base64 provided'}), 400
        
        # Remove data URL prefix if present
        if ',' in pdf_base64:
            pdf_base64 = pdf_base64.split(',')[1]
        
        # Decode base64
        pdf_bytes = base64.b64decode(pdf_base64)
        
        # Extract text
        extraction = pdf_handler.extract_text_from_bytes(pdf_bytes)
        
        if extraction['method'] == 'failed':
            error_detail = extraction.get('error', 'Unknown error')
            return jsonify({
                'error': f'Failed to extract text from PDF: {error_detail}'
            }), 400
        
        # Detect document type
        doc_type = pdf_handler.detect_document_type(extraction['text'])
        
        # Extract property address
        property_address = parser._extract_address(extraction['text'])
        
        return jsonify({
            'success': True,
            'text': extraction['text'],
            'document_type': doc_type,
            'property_address': property_address,
            'page_count': extraction['page_count'],
            'text_length': len(extraction['text'])
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Analysis endpoint
@app.route('/api/analyze', methods=['POST', 'OPTIONS'])
def analyze():
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        data = request.get_json()
        
        seller_disclosure_text = data.get('seller_disclosure_text', '')
        inspection_report_text = data.get('inspection_report_text', '')
        property_price = data.get('property_price')
        property_address = data.get('property_address')
        buyer_profile_data = data.get('buyer_profile', {})
        
        # Validate
        if not seller_disclosure_text:
            return jsonify({'error': 'seller_disclosure_text is required'}), 400
        
        if not property_price:
            return jsonify({'error': 'property_price is required'}), 400
        
        # Create buyer profile
        buyer_profile = BuyerProfile(
            max_budget=buyer_profile_data.get('max_budget'),
            repair_tolerance=buyer_profile_data.get('repair_tolerance', 'moderate'),
            ownership_duration=buyer_profile_data.get('ownership_duration', '7-10'),
            biggest_regret=buyer_profile_data.get('biggest_regret', 'hidden_issues'),
            replaceability=buyer_profile_data.get('replaceability', 'somewhat_unique'),
            deal_breakers=buyer_profile_data.get('deal_breakers', [])
        )
        
        # If no inspection, return initial brief
        if not inspection_report_text:
            disclosure_doc = parser.parse_seller_disclosure(seller_disclosure_text, property_address)
            disclosed_issues = [item for item in disclosure_doc.disclosure_items if item.disclosed]
            
            return jsonify({
                'property_address': disclosure_doc.property_address,
                'analysis_type': 'initial_brief',
                'total_disclosures': len(disclosure_doc.disclosure_items),
                'issues_disclosed': len(disclosed_issues),
                'recommendation': 'Proceed to inspection. Seller has disclosed items that should be verified.'
            })
        
        # Full analysis
        analysis = intelligence.analyze_property(
            seller_disclosure_text=seller_disclosure_text,
            inspection_report_text=inspection_report_text,
            property_price=property_price,
            buyer_profile=buyer_profile,
            property_address=property_address
        )
        
        # Convert to JSON
        result = {
            'property_address': analysis.property_address,
            'risk_score': {
                'overall': analysis.risk_score.overall_risk_score,
                'buyer_adjusted': analysis.risk_score.buyer_adjusted_score,
                'risk_tier': analysis.risk_score.risk_tier,
                'total_repair_cost_low': analysis.risk_score.total_repair_cost_low,
                'total_repair_cost_high': analysis.risk_score.total_repair_cost_high,
                'deal_breakers': _clean_deal_breakers(analysis.risk_score.deal_breakers),
                'walk_away_threshold': analysis.risk_score.walk_away_threshold,
                'category_scores': [
                    {
                        'category': cs.category.value.replace('_', ' ').title(),
                        'score': cs.score,
                        'estimated_cost_low': cs.estimated_cost_low,
                        'estimated_cost_high': cs.estimated_cost_high,
                        'key_issues': _clean_key_issues(cs.key_issues)
                    }
                    for cs in analysis.risk_score.category_scores if cs.score > 0
                ]
            },
            'cross_reference': {
                'transparency_score': analysis.cross_reference.transparency_score,
                'contradictions_count': len(analysis.cross_reference.contradictions),
                'undisclosed_count': len(analysis.cross_reference.undisclosed_issues),
                'summary': analysis.cross_reference.summary
            },
            'offer_strategy': _format_offer_strategy(analysis.offer_strategy),
            'negotiation_strategy': _format_negotiation_strategy(analysis.negotiation_strategy),
            'decision_framework': _format_decision_framework(analysis.decision_framework)
        }
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def _format_offer_strategy(strategy):
    """Convert offer strategy dict to readable string"""
    lines = []
    lines.append(f"RECOMMENDED OFFER: ${strategy.get('recommended_offer', 0):,.0f}")
    
    if strategy.get('list_price'):
        discount = strategy.get('total_discount', 0)
        lines.append(f"Asking Price: ${strategy.get('list_price'):,.0f}")
        lines.append(f"Discount: ${discount:,.0f}")
    
    if 'rationale' in strategy:
        lines.append("")
        lines.append(strategy['rationale'])
    
    if 'contingencies' in strategy:
        lines.append("")
        lines.append("Recommended Contingencies:")
        cont = strategy['contingencies']
        if cont.get('inspection'): lines.append(f"  • Inspection: {cont['inspection']}")
        if cont.get('financing'): lines.append(f"  • Financing: {cont['financing']}")
        if cont.get('appraisal'): lines.append(f"  • Appraisal: {cont.get('appraisal', 'Standard')}")
    
    return "\n".join(lines)

def _clean_text(text, max_length=100):
    """Clean and truncate text for display"""
    if not text:
        return ""
    # Remove excessive whitespace
    text = ' '.join(text.split())
    # Truncate if too long
    if len(text) > max_length:
        text = text[:max_length].rsplit(' ', 1)[0] + '...'
    return text

def _clean_deal_breakers(deal_breakers):
    """Clean up deal-breaker text for display"""
    cleaned = []
    seen = set()
    
    for breaker in deal_breakers:
        # Remove category prefix if present
        if ': ' in breaker:
            breaker = breaker.split(': ', 1)[1]
        
        # Clean up the text
        breaker = _clean_text(breaker, 150)
        
        # Avoid duplicates
        if breaker not in seen and breaker:
            seen.add(breaker)
            cleaned.append(breaker)
    
    return cleaned[:10]  # Limit to 10 most important

def _clean_key_issues(issues):
    """Clean up key issues list"""
    cleaned = []
    seen = set()
    
    for issue in issues[:5]:  # Limit to 5
        issue = _clean_text(issue, 120)
        if issue not in seen and issue:
            seen.add(issue)
            cleaned.append(issue)
    
    return cleaned

def _format_negotiation_strategy(strategy):
    """Convert negotiation strategy dict to readable string"""
    lines = []
    lines.append(f"NEGOTIATION POSTURE: {strategy.get('posture', 'N/A')}")
    lines.append("")
    
    if 'talking_points' in strategy and strategy['talking_points']:
        lines.append("KEY TALKING POINTS:")
        for point in strategy['talking_points']:
            lines.append(f"  • {point}")
        lines.append("")
    
    if 'negotiation_options' in strategy:
        lines.append("NEGOTIATION OPTIONS:")
        opts = strategy['negotiation_options']
        
        if 'option_1_price_reduction' in opts:
            opt = opts['option_1_price_reduction']
            lines.append(f"  Option 1 - Price Reduction:")
            lines.append(f"    Ask: ${opt.get('ask', 0):,.0f}")
            lines.append(f"    Fallback: ${opt.get('fallback', 0):,.0f}")
            lines.append(f"    Rationale: {opt.get('rationale', '')}")
            lines.append("")
        
        if 'option_2_repair_credit' in opts:
            opt = opts['option_2_repair_credit']
            lines.append(f"  Option 2 - Repair Credit:")
            lines.append(f"    Ask: ${opt.get('ask', 0):,.0f}")
            lines.append(f"    Fallback: ${opt.get('fallback', 0):,.0f}")
            lines.append(f"    Rationale: {opt.get('rationale', '')}")
            lines.append("")
        
        if 'option_3_seller_repairs' in opts:
            opt = opts['option_3_seller_repairs']
            lines.append(f"  Option 3 - Seller Repairs:")
            if opt.get('must_fix'):
                lines.append(f"    Must Fix: {', '.join(opt['must_fix'])}")
            if opt.get('optional_fix'):
                lines.append(f"    Optional: {', '.join(opt['optional_fix'][:3])}")
            lines.append(f"    Rationale: {opt.get('rationale', '')}")
    
    return "\n".join(lines)

def _format_decision_framework(framework):
    """Convert decision framework dict to readable string"""
    lines = []
    
    if 'recommendation' in framework:
        lines.append(f"RECOMMENDATION: {framework['recommendation']}")
        lines.append("")
    
    if 'confidence' in framework:
        lines.append(f"Confidence Level: {framework['confidence']}")
        lines.append("")
    
    if 'key_decision_points' in framework and framework['key_decision_points']:
        lines.append("KEY DECISION POINTS:")
        for point in framework['key_decision_points']:
            lines.append(f"  • {point}")
        lines.append("")
    
    if 'red_flags' in framework and framework['red_flags']:
        lines.append("RED FLAGS:")
        for flag in framework['red_flags']:
            lines.append(f"  ⚠️  {flag}")
        lines.append("")
    
    if 'green_flags' in framework and framework['green_flags']:
        lines.append("POSITIVE FACTORS:")
        for flag in framework['green_flags']:
            lines.append(f"  ✓  {flag}")
    
    return "\n".join(lines)

# Serve React app
@app.route('/app')
def serve_app():
    return send_from_directory('static', 'app.html')

# Serve diagnostic page
@app.route('/diagnostic')
def serve_diagnostic():
    return send_from_directory('static', 'diagnostic.html')

# Serve app diagnostic page
@app.route('/app-diagnostic')
def serve_app_diagnostic():
    return send_from_directory('static', 'app-diagnostic.html')

# Serve landing page
@app.route('/')
def serve_landing():
    return send_from_directory('static', 'index.html')

# Serve static files
@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

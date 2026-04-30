"""
OfferWise Document Parser v3.0 - COMPLETE REWRITE
Problem-first extraction: Only identifies actual issues, not noise.
"""

from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from enum import Enum
import re
import logging
from datetime import datetime


class Severity(Enum):
    """Severity levels for inspection findings"""
    CRITICAL = "critical"
    MAJOR = "major"
    MODERATE = "moderate"
    MINOR = "minor"
    INFORMATIONAL = "informational"


class IssueCategory(Enum):
    """Categories for property issues"""
    FOUNDATION_STRUCTURE = "foundation_structure"
    ROOF_EXTERIOR = "roof_exterior"
    PLUMBING = "plumbing"
    ELECTRICAL = "electrical"
    HVAC = "hvac_systems"
    ENVIRONMENTAL = "environmental"
    LEGAL_TITLE = "legal_title"
    HOA = "insurance_hoa"


@dataclass
class InspectionFinding:
    """A single issue found during inspection"""
    category: IssueCategory
    severity: Severity
    location: str
    description: str
    recommendation: str
    estimated_cost_low: Optional[float] = None
    estimated_cost_high: Optional[float] = None
    safety_concern: bool = False
    requires_specialist: bool = False
    raw_text: str = ""
    
    # AI-enhanced fields (Phase 1)
    confidence: float = 1.0  # 0.0 to 1.0
    confidence_explanation: str = ""
    verified: bool = False  # Verified against source document?
    evidence: List[str] = None  # Supporting quotes from source
    
    # SOURCE TRACKING (v5.50.0 - Screenshot Evidence Feature)
    source_page: Optional[int] = None  # Page number in source document (1-indexed)
    source_document: Optional[str] = None  # 'disclosure' or 'inspection'
    source_quote: Optional[str] = None  # Exact quote from document (for highlighting)
    
    # COST SOURCE TRACKING (v5.55.8 - Credibility)
    cost_from_document: bool = False  # True = extracted from text, False = industry estimate
    
    def __post_init__(self):
        if self.evidence is None:
            self.evidence = []


@dataclass
class CrossReferenceMatch:
    """Match between disclosure and inspection finding"""
    disclosure_item: 'DisclosureItem'
    inspection_finding: InspectionFinding
    match_type: str  # "consistent", "contradiction", "undisclosed", "disclosed_not_found"
    confidence: float  # 0.0 to 1.0
    explanation: str
    risk_impact: str  # "increases_risk", "neutral", "decreases_risk"


@dataclass
class DisclosureItem:
    """Item from seller disclosure"""
    category: str
    question: str
    disclosed: bool  # True = seller said yes/issue exists
    details: Optional[str] = None
    raw_text: str = ""
    
    # SOURCE TRACKING (v5.50.0 - Screenshot Evidence Feature)
    source_page: Optional[int] = None  # Page number in source document (1-indexed)
    source_quote: Optional[str] = None  # Exact quote from document


@dataclass
class PropertyDocument:
    """Parsed property document"""
    property_address: str
    document_type: str
    parse_date: datetime
    inspection_findings: List[InspectionFinding] = None
    disclosure_items: List[DisclosureItem] = None
    content: str = ""
    
    def __post_init__(self):
        if self.inspection_findings is None:
            self.inspection_findings = []
        if self.disclosure_items is None:
            self.disclosure_items = []


class DocumentParser:
    """
    Completely rewritten parser focused on PROBLEM DETECTION.
    
    Philosophy:
    - Only extract actual issues/problems
    - Ignore maintenance recommendations
    - Skip positive statements entirely
    - Focus on quality over quantity (5-10 real issues vs 166 noise)
    """
    
    def __init__(self):
        self.problem_indicators = self._load_problem_indicators()
        self.positive_indicators = self._load_positive_indicators()
        self.noise_patterns = self._load_noise_patterns()
        self.severity_keywords = self._load_severity_keywords()
        self.cost_pattern = re.compile(r'\$\s*(\d[\d,]*)\s*(?:to|-)\s*\$?\s*(\d[\d,]*)')
        
    def _load_problem_indicators(self) -> List[str]:
        """Keywords that indicate an actual PROBLEM (not just observation)"""
        return [
            # Damage/deterioration
            'damage', 'damaged', 'deterioration', 'deteriorated', 'rot', 'rotted',
            'decay', 'corroded', 'corrosion', 'rust', 'rusted',
            
            # Structural issues
            'crack', 'cracked', 'cracking', 'settlement', 'settling', 'sagging',
            'slope', 'unlevel', 'uneven', 'bowing', 'bulging',
            
            # Failures/malfunctions
            'fail', 'failed', 'failing', 'not working', 'not functioning',
            'inoperable', 'broken', 'defective', 'malfunction',
            
            # Leaks/moisture
            'leak', 'leaking', 'leakage', 'water intrusion', 'moisture intrusion',
            'standing water', 'pooling', 'stain', 'staining',
            
            # Missing/improper
            'missing', 'absent', 'improper', 'improperly', 'incorrect', 'inadequate',
            'insufficient', 'lack of', 'not present',
            
            # Safety concerns
            'hazard', 'dangerous', 'unsafe', 'safety concern', 'fire hazard',
            'shock hazard', 'trip hazard',
            
            # Code violations
            'not to code', 'code violation', 'unpermitted', 'illegal',
            
            # End of life
            'past its useful life', 'end of life', 'needs replacement', 'should be replaced',
            'requires replacement',
            
            # Professional assessment needed
            'requires immediate attention', 'significant concern', 'major concern',
            'recommend evaluation', 'recommend assessment', 'further evaluation needed',
            
            # Specific problems
            'mold', 'mildew', 'pest', 'termite', 'infestation',
            'foundation crack', 'roof damage', 'electrical hazard',
            
            # NEW: Additional problem indicators
            'intrusion', 'blockage', 'blocked', 'clogged', 'obstruction',
            'overheating', 'burning', 'burn', 'charring', 'scorching',
            'slow drainage', 'poor drainage', 'drainage problem',
            'sewer problem', 'sewage', 'backup',
            'tree root', 'root damage',
            'active leak', 'active water', 'water damage',
            'panel issue', 'wiring issue', 'electrical problem',
            'extensive', 'severe', 'serious', 'critical',
            'needs repair', 'requires repair', 'repair needed',
            
            # v5.59.24: Missing common problem words
            'failure', 'loose', 'disconnected', 'detached', 'worn',
            'exposed wiring', 'exposed wire', 'signs of failure'
        ]
    
    def _load_positive_indicators(self) -> List[str]:
        """Phrases that indicate NO PROBLEMS - skip these entirely"""
        return [
            # Explicit "no issues"
            'no issues', 'no problems', 'no concerns', 'no defects', 'no damage',
            'no cracks', 'no leaks', 'no evidence', 'no signs', 'no visible',
            
            # Good condition
            'in good condition', 'in excellent condition', 'in very good condition',
            'in satisfactory condition', 'appears good', 'appears excellent',
            
            # Functioning properly
            'functioning properly', 'working properly', 'operating normally',
            'working as intended', 'operating correctly', 'functions well',
            
            # Proper installation/maintenance
            'properly installed', 'properly maintained', 'well maintained',
            'adequate', 'acceptable', 'satisfactory',
            
            # Meets standards
            'meets code', 'meets standards', 'up to code', 'passes inspection',
            'within normal', 'typical for age',
            
            # Professional confirmation
            'engineer confirms', 'specialist confirms', 'inspection confirms',
            'report confirms', 'reviewed - good', 'reviewed - excellent'
        ]
    
    def _load_noise_patterns(self) -> List[re.Pattern]:
        """Patterns that indicate noise (headers, photos, etc) - skip these"""
        return [
            re.compile(r'^={3,}'),  # Dividers
            re.compile(r'^-{3,}'),  # Dashes
            re.compile(r'^Photos?:', re.IGNORECASE),  # Photo references
            re.compile(r'^[A-Z\s&]{10,}:?\s*$'),  # Section headers (all caps)
            re.compile(r'Page\s+\d+\s+of\s+\d+', re.IGNORECASE),  # Page numbers
            re.compile(r'^TYPE:|^AGE:|^CONDITION:|^SIZE:', re.IGNORECASE),  # Spec lines
            re.compile(r'^Continue|^Monitor|^Maintain|^Keep', re.IGNORECASE),  # Maintenance
            re.compile(r'Recommendations?:\s*$', re.IGNORECASE),  # Recommendation headers
            re.compile(r'^Budget for', re.IGNORECASE),  # Future budgeting
            re.compile(r'^Consider\s+', re.IGNORECASE),  # Considerations
            re.compile(r'www\.|@|\.com', re.IGNORECASE),  # Contact info
            re.compile(r'Inspector:|Client:|Report Date:', re.IGNORECASE),  # Report metadata
            # Form field labels with None/N/A values (v5.52.6)
            re.compile(r'^[A-Z\s]+:\s*(None|N/?A|No|n/a)\s*$', re.IGNORECASE),  # "FINDINGS: None", "CONCERNS: N/A"
            re.compile(r':\s*(None|N/?A)\s*$', re.IGNORECASE),  # Any field ending with ": None" or ": N/A"
        ]
    
    def _load_severity_keywords(self) -> Dict[Severity, List[str]]:
        """Keywords that indicate severity level"""
        return {
            Severity.CRITICAL: [
                'immediate', 'critical', 'severe', 'major concern', 'safety hazard',
                'dangerous', 'unsafe', 'emergency', 'requires immediate',
                'structural failure', 'imminent', 'catastrophic'
            ],
            Severity.MAJOR: [
                'significant', 'substantial', 'major', 'serious', 'extensive',
                'large', 'widespread', 'considerable', 'severe', 'important'
            ],
            Severity.MODERATE: [
                'moderate', 'noticeable', 'visible', 'apparent', 'typical',
                'common', 'minor but', 'should be addressed'
            ],
            Severity.MINOR: [
                'minor', 'small', 'cosmetic', 'slight', 'minimal',
                'easy fix', 'simple repair', 'inexpensive'
            ]
        }
    
    def parse_inspection_report(
        self, 
        pdf_text: str, 
        property_address: str = None
    ) -> PropertyDocument:
        """
        Parse inspection report — AI-first, rules-validated.
        
        Architecture (inverted in v5.85.55):
        1. Claude reads the full document and extracts structured findings
        2. Regex parser runs independently as a safety net
        3. Results are merged: Claude findings are primary, regex fills gaps
        4. If Claude fails (529, timeout), falls back to regex-only
        """
        
        if not property_address:
            property_address = self._extract_address(pdf_text)
        
        doc = PropertyDocument(
            property_address=property_address or "Unknown",
            document_type="inspection_report",
            parse_date=datetime.now(),
            content=pdf_text
        )
        
        # ── Step 1: Claude reads the document (primary) ──────────────────
        ai_findings = []
        ai_succeeded = False
        try:
            ai_findings = self._ai_extract_findings(pdf_text)
            if ai_findings:
                ai_succeeded = True
                logging.info(f"🧠 AI parser: {len(ai_findings)} findings extracted")
        except Exception as e:
            logging.warning(f"🧠 AI parser unavailable ({e}), falling back to rules")
        
        # ── Step 2: Regex parser runs independently (safety net) ─────────
        rules_findings = self._extract_problems(pdf_text)
        logging.info(f"📏 Rules parser: {len(rules_findings)} findings extracted")
        
        # ── Step 3: Merge — AI primary, rules fill gaps ──────────────────
        if ai_succeeded and ai_findings:
            doc.inspection_findings = self._merge_ai_and_rules(ai_findings, rules_findings)
            logging.info(f"🔀 Merged: {len(doc.inspection_findings)} total findings "
                        f"(AI: {len(ai_findings)}, Rules: {len(rules_findings)}, "
                        f"unique rules additions: {len(doc.inspection_findings) - len(ai_findings)})")
        else:
            # Fallback: rules-only
            doc.inspection_findings = rules_findings
            logging.info(f"📏 Using rules-only: {len(rules_findings)} findings")
        
        return doc
    
    def _ai_extract_findings(self, text: str) -> List[InspectionFinding]:
        """
        Claude reads the inspection report and extracts structured findings.
        Returns InspectionFinding objects compatible with the rest of the pipeline.
        """
        import os, json
        
        # Strip NUL bytes and other control characters that break JSON/DB
        text = text.replace('\x00', '')
        
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            return []
        
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
        except Exception:
            return []
        
        # Truncate but keep beginning and end (most important sections)
        max_chars = 14000
        if len(text) > max_chars:
            half = max_chars // 2
            text = text[:half] + "\n\n[...middle section truncated for analysis...]\n\n" + text[-half:]
        
        prompt = f"""You are a home inspection report analyst. Read this inspection report and extract every defect, problem, safety concern, and item needing repair or further evaluation.

CRITICAL RULES:
- Extract ONLY actual problems and defects. Do NOT include positive observations, general descriptions, or normal conditions.
- A "recommend further evaluation by a specialist" IS a finding — the inspector is flagging something they cannot fully assess.
- Photo descriptions like [PHOTO: ...] that show damage or defects should be captured as findings.
- Be thorough — missing a real finding is worse than including a borderline one.

CATEGORIES (use exactly one):
foundation_structure, roof_exterior, plumbing, electrical, hvac_systems, environmental, legal_title, insurance_hoa

SEVERITY LEVELS:
critical = structural failure, active safety hazard, immediate action needed
major = significant defect, will worsen without repair, costly
moderate = needs repair but not urgent, standard maintenance issue
minor = cosmetic, low priority, monitoring sufficient

For each finding, return:
- category: one of the categories above
- severity: critical, major, moderate, or minor
- description: clear complete sentence describing the problem
- location: where in the property (if stated in the report)
- recommendation: what should be done (from the report or your assessment)
- safety_concern: true ONLY for immediate life-safety hazards (fire risk, electrocution, structural collapse, gas leak, fall hazard). Normal repair items are NOT safety concerns. Most findings should be false.
- requires_specialist: true ONLY when the inspector specifically recommends a licensed specialist beyond a general contractor (structural engineer, licensed electrician, environmental tester). Standard "recommend repair by qualified contractor" is NOT a specialist referral. Most findings should be false.
- source_quote: the exact words from the report (max 120 chars) that support this finding

Respond with ONLY a JSON object: {{"findings": [...]}}

INSPECTION REPORT:
{text}"""

        import time
        t0 = time.time()
        
        try:
            response = client.messages.create(
                model='claude-sonnet-4-20250514',
                max_tokens=4000,
                messages=[{'role': 'user', 'content': prompt}],
            )
            
            latency = (time.time() - t0) * 1000
            
            # Track AI usage
            try:
                from ai_cost_tracker import track_ai_call
                track_ai_call(response, 'document-parsing', latency)
            except Exception:
                pass
            
            raw = response.content[0].text if response.content else ''
            
            if not raw or not raw.strip():
                logging.warning("🧠 AI parser: empty response from Claude")
                return []
            
            # Parse JSON — handle preamble text, markdown fences, and malformed responses
            raw = raw.strip()
            if raw.startswith('```'):
                raw = raw.split('\n', 1)[-1] if '\n' in raw else raw[3:]
            if raw.endswith('```'):
                raw = raw[:-3]
            raw = raw.strip()
            if raw.startswith('json'):
                raw = raw[4:].strip()
            
            # If Claude added text before/after JSON, extract the JSON object
            if not raw.startswith('{'):
                json_start = raw.find('{')
                if json_start >= 0:
                    raw = raw[json_start:]
                else:
                    logging.warning(f"🧠 AI parser: no JSON object found in response ({len(raw)} chars)")
                    return []
            # Find matching closing brace
            brace_depth = 0
            json_end = -1
            for i, ch in enumerate(raw):
                if ch == '{': brace_depth += 1
                elif ch == '}': brace_depth -= 1
                if brace_depth == 0:
                    json_end = i + 1
                    break
            if json_end > 0:
                raw = raw[:json_end]
            
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as je:
                logging.warning(f"🧠 AI parser: JSON parse failed ({je}), falling back to rules")
                return []
            
            findings_data = data.get('findings', [])
            
            results = []
            for f in findings_data:
                try:
                    # Map AI category to IssueCategory enum
                    cat_map = {
                        'foundation_structure': IssueCategory.FOUNDATION_STRUCTURE,
                        'foundation': IssueCategory.FOUNDATION_STRUCTURE,
                        'roof_exterior': IssueCategory.ROOF_EXTERIOR,
                        'roof': IssueCategory.ROOF_EXTERIOR,
                        'plumbing': IssueCategory.PLUMBING,
                        'electrical': IssueCategory.ELECTRICAL,
                        'hvac_systems': IssueCategory.HVAC,
                        'hvac': IssueCategory.HVAC,
                        'environmental': IssueCategory.ENVIRONMENTAL,
                        'water_damage': IssueCategory.ENVIRONMENTAL,
                        'pest': IssueCategory.ENVIRONMENTAL,
                        'safety': IssueCategory.FOUNDATION_STRUCTURE,
                        'legal_title': IssueCategory.LEGAL_TITLE,
                        'insurance_hoa': IssueCategory.HOA,
                        'permits': IssueCategory.LEGAL_TITLE,
                        'general': IssueCategory.FOUNDATION_STRUCTURE,
                    }
                    sev_map = {
                        'critical': Severity.CRITICAL,
                        'major': Severity.MAJOR,
                        'moderate': Severity.MODERATE,
                        'minor': Severity.MINOR,
                    }
                    
                    category = cat_map.get(f.get('category', 'general'), IssueCategory.FOUNDATION_STRUCTURE)
                    severity = sev_map.get(f.get('severity', 'moderate'), Severity.MODERATE)
                    
                    finding = InspectionFinding(
                        category=category,
                        severity=severity,
                        location=f.get('location', ''),
                        description=f.get('description', ''),
                        recommendation=f.get('recommendation', ''),
                        safety_concern=bool(f.get('safety_concern', False)),
                        requires_specialist=bool(f.get('requires_specialist', False)),
                        raw_text=f.get('source_quote', '')[:200],
                        confidence=0.90,  # AI-parsed findings start at high confidence
                        verified=True,
                        source_document='inspection',
                    )
                    
                    if finding.description:
                        results.append(finding)
                except Exception:
                    continue
            
            logging.info(f"🧠 AI document parsing: {len(results)} findings in {latency:.0f}ms")
            return results
            
        except Exception as e:
            status = getattr(e, 'status_code', None)
            if status in (429, 500, 503, 529):
                logging.warning(f"🧠 AI parser: Anthropic unavailable (status {status})")
            else:
                logging.warning(f"🧠 AI parser: {type(e).__name__}: {e}")
            return []
    
    def _merge_ai_and_rules(
        self,
        ai_findings: List[InspectionFinding],
        rules_findings: List[InspectionFinding],
    ) -> List[InspectionFinding]:
        """
        Merge AI and rules findings. AI is primary. Rules fill gaps.
        
        Strategy:
        1. Start with all AI findings (they understand context better)
        2. For each rules finding, check if AI already caught it
        3. If rules found something AI missed, add it (AI may have truncated the doc)
        4. If both found the same thing, keep AI version (better severity/description)
        """
        merged = list(ai_findings)
        added = 0
        
        for rf in rules_findings:
            rf_words = set(rf.description.lower().split())
            if len(rf_words) < 3:
                continue
            
            # Check if any AI finding overlaps
            is_duplicate = False
            for af in ai_findings:
                af_words = set(af.description.lower().split())
                if not af_words:
                    continue
                overlap = len(rf_words & af_words) / max(len(rf_words), len(af_words))
                
                # Also check category match
                cat_match = rf.category == af.category
                if overlap > 0.35 or (overlap > 0.2 and cat_match):
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                # Rules caught something AI missed — add it
                rf.confidence = 0.70  # Lower confidence since AI didn't find it
                rf.confidence_explanation = "Found by rules engine only — AI did not flag this item"
                merged.append(rf)
                added += 1
        
        if added:
            logging.info(f"🔀 Rules added {added} findings that AI missed")
        
        return merged
    
    def _extract_problems(self, text: str) -> List[InspectionFinding]:
        """
        Extract ONLY actual problems from inspection text.
        This is the core of the new approach.
        
        v5.50.0: Now tracks page numbers for screenshot evidence feature.
        v5.59.58: Attaches [PHOTO:] descriptions to adjacent findings as evidence.
                   Photos attach to the PREVIOUS finding (the one they document).
        """
        problems = []
        
        # Split into sentences WITH page tracking
        sentences_with_pages = self._split_into_sentences_with_pages(text)
        
        for sentence, page_num in sentences_with_pages:
            # Check if this is a photo description from vision extraction
            stripped = sentence.strip()
            if stripped.startswith('[PHOTO:') and stripped.endswith(']'):
                # Attach to the most recent finding (photos follow their subject)
                if problems:
                    problems[-1].description = problems[-1].description + ' ' + stripped
                    problems[-1].raw_text = problems[-1].raw_text + ' ' + stripped
                continue
            
            # Skip if this is noise
            if self._is_noise(sentence):
                continue
            
            # Skip if this is a positive statement
            if self._is_positive(sentence):
                continue
            
            # Only process if it indicates a problem
            if self._indicates_problem(sentence):
                problem = self._create_finding_from_sentence(sentence, page_num)
                if problem:
                    problems.append(problem)
        
        # Deduplicate and clean
        problems = self._deduplicate_problems(problems)
        
        return problems
    
    def _split_into_sentences_with_pages(self, text: str) -> List[tuple]:
        """
        Split text into sentences while tracking page numbers.
        
        Returns list of tuples: [(sentence, page_num), ...]
        
        v5.50.0: Parses page markers inserted by PDF handler (e.g., "--- Page 5 ---")
        v5.51.0: Also recognizes "=== Page X ===" format from client-side extraction
        """
        import re
        
        # Replace tabs with spaces
        text = text.replace('\t', ' ')
        
        # Split on page markers first to track pages
        # Pattern matches both "--- Page X ---" and "=== Page X ===" variations
        page_pattern = re.compile(r'(?:---|\=\=\=)\s*Page\s*(\d+)\s*(?:---|\=\=\=)', re.IGNORECASE)
        
        # Split text by page markers, keeping track of page numbers
        parts = page_pattern.split(text)
        
        sentences_with_pages = []
        current_page = 1  # Default to page 1 if no markers found
        
        # parts alternates between: [text_before_first_marker, page_num, text, page_num, text, ...]
        for i, part in enumerate(parts):
            if i % 2 == 1:
                # This is a page number
                try:
                    current_page = int(part)
                except ValueError:
                    pass
            else:
                # This is text content - split into sentences
                lines = part.split('\n')
                
                for line in lines:
                    line = line.strip()
                    
                    # Skip empty lines
                    if not line:
                        continue
                    
                    # If line has periods, split on them too
                    if '.' in line:
                        sentence_parts = re.split(r'[.!?]\s+', line)
                        for sentence_part in sentence_parts:
                            sentence_part = sentence_part.strip()
                            if len(sentence_part) > 15:
                                sentences_with_pages.append((sentence_part, current_page))
                    else:
                        # No periods - treat whole line as sentence if long enough
                        if len(line) > 15:
                            sentences_with_pages.append((line, current_page))
        
        return sentences_with_pages
    
    def _split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences, handling special cases"""
        # Replace tabs with spaces
        text = text.replace('\t', ' ')
        
        # Split on BOTH newlines AND sentence boundaries
        # This handles both paragraph-style and bullet-point style content
        lines = text.split('\n')
        
        sentences = []
        for line in lines:
            line = line.strip()
            
            # Skip empty lines
            if not line:
                continue
            
            # If line has periods, split on them too
            if '.' in line:
                parts = re.split(r'[.!?]\s+', line)
                for part in parts:
                    part = part.strip()
                    if len(part) > 15:
                        sentences.append(part)
            else:
                # No periods - treat whole line as sentence if long enough
                if len(line) > 15:
                    sentences.append(line)
        
        return sentences
    
    def _is_noise(self, text: str) -> bool:
        """Check if text is noise (headers, photos, maintenance, boilerplate, etc)"""
        text_lower = text.lower()
        
        # Check regex patterns
        for pattern in self.noise_patterns:
            if pattern.search(text):
                return True
        
        # Too short
        if len(text) < 20:
            return True
        
        # All caps (likely header)
        if text.isupper() and len(text) < 50:
            return True
        
        # v5.59.24: Inspection report BOILERPLATE / TEMPLATE language
        # These are severity definitions, disclaimers, and instructional text — not findings
        boilerplate_patterns = [
            # Severity rating definitions ("Items that inevitably lead to...")
            r'items that (inevitably|may) lead to',
            r'adverse impact on the value',
            r'unreasonable risk.{0,20}(unsafe|to people|to property)',
            # Generic safety statements without specifics
            r'^this poses a safety hazard',
            # Report methodology meta-text
            r'will be called out in the report',
            r'will be (noted|documented|reported) (in|throughout)',
            r'are (noted|documented|reported) (in|throughout|elsewhere)',
            r'more serious .{0,30} will be',
            r'large amounts of .{0,20} will be',
            # Inspection access/limitation disclaimers
            r'not readily accessible',
            r'enter the attic or any',
            r'unable to (access|inspect|evaluate|observe)',
            r'was not (accessible|visible|available)',
            r'inaccessible (area|space|location)',
            r'in the inspector.s opinion.{0,20}(pose|safety)',
            r'could cause damage or.{0,15}in the inspector',
            # UI/navigation artifacts from digital reports
            r'click here to view',
            r'view on web',
            r'click to (view|see|open)',
            r'video \(click',
            # Report section number headers parsed as content
            r'^\d+\.\d+\.\d+\s+[A-Z]',
            # Educational/definitional text (not about this property)
            r'may seem like a minor',
            r'it is known as the',
            r'is known as',
            # Inspection exclusion disclaimers
            r'the inspection did not include',
            r'inspection (does|did) not (cover|include|address)',
            r'not (part|included) (of|in) (this|the) inspection',
            r'unless otherwise purchased',
            # Recommendation category headers
            r'^(prioritized observation|maintenance item|recommendation recommended)',
            # Generic component descriptions (not defects)
            r'^a functional component that',
            r'not operating as intended or defective$',
            # Educational/generic text explaining what COULD happen (not about THIS property)
            r'can sometimes have',
            r'pre-fab (fireplaces?|replaces)',
            r'thermal shock.{0,20}(cracking|leading)',
            r'^(extinguishing|putting out) ',
        ]
        for pattern in boilerplate_patterns:
            if re.search(pattern, text_lower):
                return True
        
        # Maintenance recommendations - not problems
        maintenance_phrases = [
            'continue monitoring', 'continue to monitor', 'monitor for',
            'maintain', 'keep', 'regular maintenance',
            'annual inspection', 'quarterly', 'seasonally',
            'budget for future', 'plan for', 'expect', 'consider budgeting'
        ]
        for phrase in maintenance_phrases:
            if phrase in text_lower:
                return True
        
        # Observations without problems - just noting something exists
        observation_phrases = [
            'observed', 'noted', 'present', 'installed', 'reviewed',
            'protection in', 'protection present',
            'slopes away', 'directing water away',
            'pest control', 'pest service',  # Having pest control is good!
            'inspection report', 'engineer report', 'specialist report'
        ]
        for phrase in observation_phrases:
            if phrase in text_lower and not any(problem in text_lower for problem in 
                ['damage', 'leak', 'crack', 'fail', 'broken', 'defect', 'hazard']):
                return True
        
        return False
    
    def _is_positive(self, text: str) -> bool:
        """Check if text indicates everything is fine"""
        text_lower = text.lower()
        
        # Check explicit positive indicators
        for indicator in self.positive_indicators:
            if indicator in text_lower:
                return True
        
        # Additional positive patterns
        positive_patterns = [
            r'\bno\s+(visible|apparent|signs?|evidence)\s+of',  # "no signs of damage"
            r'\bnone\s+observed',  # "none observed"
            r'- clear\b',  # "report - clear"
            r'\bclear\s*$',  # ends with "clear"
            r'normal\s+wear',  # "normal wear"
            r'typical\s+for\s+age',  # "typical for age"
        ]
        for pattern in positive_patterns:
            if re.search(pattern, text_lower):
                return True
        
        return False
    
    def _indicates_problem(self, text: str) -> bool:
        """Check if text indicates an actual problem"""
        text_lower = text.lower()
        
        # CRITICAL: Expanded negation detection
        # Must catch all variations: "no X", "not X", "without X", "no broken/missing/damaged"
        negation_patterns = [
            r'\bno\s+(?:visible|apparent|signs?|evidence|indication)?\s*(?:of\s+)?(?:\w+\s+){0,3}(crack|damage|leak|issue|problem|concern|defect|deterioration|rot|missing|fail|broken)',
            r'\bnot?\s+(?:\w+\s+){0,2}(crack|damage|leak|issue|problem|concern|defect|deterioration|rot|missing|fail|broken)',
            r'\bwithout\s+(?:any\s+)?(?:\w+\s+){0,2}(crack|damage|leak|issue|problem|concern|defect|deterioration|rot|missing|fail|broken)',
            r'\bno\s+(broken|missing|damaged|cracked|leaking|failed|defective)',  # Direct: "no broken"
            r'\bnone\s+(?:observed|noted|found|detected|identified)',  # "none observed"
        ]
        
        for pattern in negation_patterns:
            if re.search(pattern, text_lower):
                return False  # Negation detected - NOT a problem
        
        # Now check for problem indicators with WORD BOUNDARIES
        # This prevents "burn" from matching "burner"
        for indicator in self.problem_indicators:
            # Create pattern with word boundaries for single words
            # For multi-word phrases, match them as-is
            if ' ' in indicator:
                # Multi-word phrase - match exactly
                if indicator in text_lower:
                    return True
            else:
                # Single word - use word boundaries
                pattern = r'\b' + re.escape(indicator) + r'\b'
                if re.search(pattern, text_lower):
                    return True
        
        return False
    
    def _create_finding_from_sentence(self, sentence: str, page_num: int = None) -> Optional[InspectionFinding]:
        """
        Create a finding from a problem-indicating sentence.
        
        Args:
            sentence: The text containing the problem
            page_num: Page number where this was found (v5.50.0 - for screenshot evidence)
        """
        
        # Determine category
        category = self._categorize_text(sentence)
        
        # Determine severity
        severity = self._determine_severity(sentence)
        
        # Extract costs if present
        cost_low, cost_high = self._extract_costs(sentence)
        
        # Extract location if present
        location = self._extract_location(sentence)
        
        # Generate description (use the sentence itself, cleaned)
        description = self._clean_description(sentence)
        
        # Check for safety concern
        safety_concern = any(word in sentence.lower() for word in 
                           ['hazard', 'dangerous', 'unsafe', 'safety'])
        
        # Check if specialist needed
        requires_specialist = any(phrase in sentence.lower() for phrase in 
                                ['specialist', 'engineer', 'professional evaluation',
                                 'further evaluation', 'recommend assessment'])
        
        return InspectionFinding(
            category=category,
            severity=severity,
            location=location or "Not specified",
            description=description,
            recommendation="",  # We'll extract this separately if needed
            estimated_cost_low=cost_low,
            estimated_cost_high=cost_high,
            safety_concern=safety_concern,
            requires_specialist=requires_specialist,
            raw_text=sentence,
            # SOURCE TRACKING (v5.50.0)
            source_page=page_num,
            source_document='inspection',
            source_quote=sentence[:200] if len(sentence) > 200 else sentence,  # Store quote for highlighting
            # COST SOURCE (v5.55.8) - True if we extracted cost from document text
            cost_from_document=(cost_low is not None)
        )
    
    def _categorize_text(self, text: str) -> IssueCategory:
        """Determine category from text content"""
        text_lower = text.lower()
        
        # Foundation/structure
        if any(word in text_lower for word in ['foundation', 'structural', 'beam', 'joist', 'slab']):
            return IssueCategory.FOUNDATION_STRUCTURE
        
        # Roof/exterior
        elif any(word in text_lower for word in ['roof', 'shingle', 'gutter', 'siding', 'exterior']):
            return IssueCategory.ROOF_EXTERIOR
        
        # Plumbing
        elif any(word in text_lower for word in ['plumbing', 'pipe', 'drain', 'sewer', 'water']):
            return IssueCategory.PLUMBING
        
        # Electrical
        elif any(word in text_lower for word in ['electrical', 'wiring', 'panel', 'outlet', 'circuit']):
            return IssueCategory.ELECTRICAL
        
        # HVAC
        elif any(word in text_lower for word in ['hvac', 'heating', 'cooling', 'furnace', 'ac']):
            return IssueCategory.HVAC
        
        # Environmental
        elif any(word in text_lower for word in ['mold', 'pest', 'termite', 'radon', 'asbestos']):
            return IssueCategory.ENVIRONMENTAL
        
        else:
            return IssueCategory.FOUNDATION_STRUCTURE  # Default
    
    def _determine_severity(self, text: str) -> Severity:
        """Determine severity from text"""
        text_lower = text.lower()
        
        # Check each severity level
        for severity, keywords in self.severity_keywords.items():
            for keyword in keywords:
                if keyword in text_lower:
                    return severity
        
        # Default based on problem indicators
        if any(word in text_lower for word in ['damage', 'leak', 'crack', 'fail']):
            return Severity.MODERATE
        else:
            return Severity.MINOR
    
    def _extract_costs(self, text: str) -> Tuple[Optional[float], Optional[float]]:
        """Extract cost range from text"""
        match = self.cost_pattern.search(text)
        if match:
            try:
                low = float(match.group(1).replace(',', ''))
                high = float(match.group(2).replace(',', ''))
                return (low, high)
            except Exception:
                pass
        
        return (None, None)
    
    def _extract_location(self, text: str) -> Optional[str]:
        """Extract location from text"""
        # Look for "in/at/on [location]"
        pattern = r'(?:in|at|on)\s+(?:the\s+)?([a-zA-Z\s]+(?:room|bath|bedroom|kitchen|basement|attic|garage))'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None
    
    def _clean_description(self, text: str) -> str:
        """
        Clean up description text — ensures complete, professional sentences.
        v5.59.26: NEVER truncate with '...' — always end at a sentence boundary.
        Every description we produce must read as a complete statement.
        """
        # Remove severity keywords at start
        text = re.sub(r'^(CRITICAL|MAJOR|MODERATE|MINOR)[\s:-]*', '', text, flags=re.IGNORECASE)
        
        # Clean up common OCR/parsing artifacts
        text = re.sub(r'\s+', ' ', text)  # Multiple spaces to single
        text = text.strip()
        
        # Ensure the text ends as a complete sentence
        if len(text) > 300:
            # Find the last complete sentence within 300 chars
            for end_char in ['. ', '! ', '? ']:
                last_break = text[:300].rfind(end_char)
                if last_break > 40:
                    return text[:last_break + 1].strip()
            
            # No sentence break found — find the last clause boundary and add a period
            for delimiter in [', ', '; ', ' - ', ' and ', ' or ']:
                last_break = text[:300].rfind(delimiter)
                if last_break > 40:
                    fragment = text[:last_break].strip()
                    # Make sure it doesn't end with a conjunction or preposition
                    if not fragment.lower().endswith((' and', ' or', ' but', ' the', ' a', ' an', ' of', ' in', ' to', ' for', ' with')):
                        if not fragment.endswith('.'):
                            fragment += '.'
                        return fragment
            
            # Last resort: find last word boundary and end cleanly
            last_space = text[:300].rfind(' ')
            if last_space > 40:
                fragment = text[:last_space].strip()
                if not fragment.endswith('.'):
                    fragment += '.'
                return fragment
        
        # Ensure text ends with proper punctuation
        if text and not text[-1] in '.!?':
            text = text + '.'
        
        return text.strip()
    
    def _deduplicate_problems(self, problems: List[InspectionFinding]) -> List[InspectionFinding]:
        """Remove duplicate problems"""
        seen = set()
        unique = []
        
        for problem in problems:
            # Create a fingerprint
            fingerprint = f"{problem.category.value}_{problem.description[:50]}"
            if fingerprint not in seen:
                seen.add(fingerprint)
                unique.append(problem)
        
        return unique
    
    def _extract_address(self, text: str) -> Optional[str]:
        """Extract property address from text"""
        # Look for Address: or Property:
        pattern = r'(?:Address|Property):\s*([^\n]+)'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None
    
    def parse_seller_disclosure(
        self, 
        pdf_text: str, 
        property_address: str = None
    ) -> PropertyDocument:
        """Parse seller disclosure - simplified approach"""
        
        if not property_address:
            property_address = self._extract_address(pdf_text)
        
        doc = PropertyDocument(
            property_address=property_address or "Unknown",
            document_type="seller_disclosure",
            parse_date=datetime.now(),
            content=pdf_text
        )
        
        # Extract disclosure items
        doc.disclosure_items = self._extract_disclosures(pdf_text)
        
        logging.info(f"📋 Extracted {len(doc.disclosure_items)} disclosure items:")
        for item in doc.disclosure_items[:5]:  # Show first 5
            logging.info(f"  • {item.category}: {item.question[:50]}... | disclosed={item.disclosed}")
        
        return doc
    
    def _extract_disclosures(self, text: str) -> List[DisclosureItem]:
        """
        Extract disclosure items from seller disclosure.
        
        v5.50.0: Now tracks page numbers for screenshot evidence feature.
        """
        items = []
        
        # Split text by page markers to track page numbers
        # v5.51.0: Matches both "--- Page X ---" and "=== Page X ===" formats
        page_pattern = re.compile(r'(?:---|\=\=\=)\s*Page\s*(\d+)\s*(?:---|\=\=\=)', re.IGNORECASE)
        parts = page_pattern.split(text)
        
        # Process each page section
        current_page = 1
        
        for i, part in enumerate(parts):
            if i % 2 == 1:
                # This is a page number
                try:
                    current_page = int(part)
                except ValueError:
                    pass
            else:
                # This is text content - extract disclosures
                page_items = self._extract_disclosures_from_text(part, current_page)
                items.extend(page_items)
        
        # Deduplicate: collapse items from same category with overlapping text
        # Priority: keep items with longer questions (more context) and correct Yes/No parsing
        from collections import defaultdict as _dedup_dd
        by_category = _dedup_dd(list)
        for item in items:
            by_category[item.category].append(item)
        
        deduped = []
        for category, cat_items in by_category.items():
            if len(cat_items) == 1:
                deduped.append(cat_items[0])
                continue
            
            # Within same category, remove items whose question is a substring of another
            # or that share >70% of words with another item
            kept = []
            for item in cat_items:
                is_subset = False
                q_lower = item.question.strip().lower()
                q_words = set(q_lower.split())
                
                for other in cat_items:
                    if other is item:
                        continue
                    o_lower = other.question.strip().lower()
                    o_words = set(o_lower.split())
                    
                    # Skip if this question is a prefix/substring of a longer one
                    if q_lower in o_lower and len(q_lower) < len(o_lower):
                        is_subset = True
                        break
                    
                    # Skip if >70% word overlap with a longer question
                    if q_words and o_words:
                        overlap = len(q_words & o_words) / len(q_words)
                        if overlap > 0.7 and len(q_lower) < len(o_lower):
                            is_subset = True
                            break
                
                if not is_subset:
                    kept.append(item)
            
            deduped.extend(kept if kept else [cat_items[0]])
        
        return deduped
    
    def _extract_disclosures_from_text(self, text: str, page_num: int = None) -> List[DisclosureItem]:
        """
        Extract disclosure items from a single page of text.
        
        Args:
            text: The text content to parse
            page_num: Page number for source tracking
        """
        items = []
        
        # Look for checkbox patterns - MULTIPLE FORMATS
        
        # Format 1: Question [X] Yes [ ] No or [ ] Yes [X] No (traditional)
        yes_pattern1 = r'([^\[\n]+)[\[\(]([Xx✓✔])[\]\)]\s*(?:Yes|Y)\s*[\[\(]\s*[\]\)]\s*(?:No|N)'
        no_pattern1 = r'([^\[\n]+)[\[\(]\s*[\]\)]\s*(?:Yes|Y)\s*[\[\(]([Xx✓✔])[\]\)]\s*(?:No|N)'
        
        # Format 2: Question Yes [X] No [ ] or Yes [ ] No [X] (reverse order)
        yes_pattern2 = r'([^\[\n]+)\s*(?:Yes|Y)\s*[\[\(]([Xx✓✔])[\]\)]\s*(?:No|N)\s*[\[\(]\s*[\]\)]'
        no_pattern2 = r'([^\[\n]+)\s*(?:Yes|Y)\s*[\[\(]\s*[\]\)]\s*(?:No|N)\s*[\[\(]([Xx✓✔])[\]\)]'
        
        # Format 3: [X] Question (checked = yes)
        checked_question = r'[\[\(]([Xx✓✔])[\]\)]\s*([^\[\n]{15,}?)(?=\[|\n|$)'
        
        # Format 4: [ ] Question (unchecked = no)
        unchecked_question = r'[\[\(]\s*[\]\)]\s*([^\[\n]{15,}?)(?=\[|\n|$)'
        
        # Extract Format 1 & 2 (Yes/No pairs)
        for pattern in [yes_pattern1, yes_pattern2]:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                question = match.group(1).strip()
                if len(question) > 15:
                    items.append(DisclosureItem(
                        category=self._categorize_disclosure(question),
                        question=question,
                        disclosed=True,
                        raw_text=match.group(0),
                        source_page=page_num,
                        source_quote=match.group(0)[:200] if len(match.group(0)) > 200 else match.group(0)
                    ))
        
        for pattern in [no_pattern1, no_pattern2]:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                question = match.group(1).strip()
                if len(question) > 15:
                    items.append(DisclosureItem(
                        category=self._categorize_disclosure(question),
                        question=question,
                        disclosed=False,
                        raw_text=match.group(0),
                        source_page=page_num,
                        source_quote=match.group(0)[:200] if len(match.group(0)) > 200 else match.group(0)
                    ))
        
        # If we found very few items with traditional patterns, try standalone checkboxes
        if len(items) < 5:
            logging.info(f"⚠️ Only found {len(items)} disclosure items with traditional patterns, trying standalone checkboxes")
            
            # Track already-extracted items to avoid duplicates
            existing_questions = set(item.question.strip().lower()[:50] for item in items)
            
            # Try to extract from full text by looking for property-related questions
            # Split by lines and look for Yes/No indicators
            lines = text.split('\n')
            for i, line in enumerate(lines):
                line = line.strip()
                if len(line) < 20:
                    continue
                    
                # Check for Yes/No keywords
                has_yes = bool(re.search(r'\b(?:Yes|Y)\b', line, re.IGNORECASE))
                has_no = bool(re.search(r'\b(?:No|N)\b', line, re.IGNORECASE))
                # FIX: Check for Yes/No BEFORE the checkbox mark, not after
                # "Yes (X)" means Yes is checked, "No (X)" means No is checked
                has_checkbox_yes = bool(re.search(r'(?:Yes|Y)\s*[\[\(][Xx✓✔][\]\)]', line, re.IGNORECASE))
                has_checkbox_no = bool(re.search(r'(?:No|N)\s*[\[\(][Xx✓✔][\]\)]', line, re.IGNORECASE))
                
                # Check if line is property-related question
                is_question = any(word in line.lower() for word in [
                    'foundation', 'roof', 'plumbing', 'electrical', 'hvac', 
                    'structural', 'damage', 'repair', 'issue', 'defect',
                    'leak', 'crack', 'system', 'condition', 'problem'
                ])
                
                if is_question and (has_yes or has_no):
                    # Determine if disclosed — prefer checkbox mark over word presence
                    if has_checkbox_yes or has_checkbox_no:
                        disclosed = has_checkbox_yes and not has_checkbox_no
                    else:
                        disclosed = has_yes and not has_no
                    
                    # Skip if this line was already captured by regex patterns
                    line_key = line.strip().lower()[:50]
                    if line_key in existing_questions:
                        continue
                    existing_questions.add(line_key)
                    
                    items.append(DisclosureItem(
                        category=self._categorize_disclosure(line),
                        question=line,
                        disclosed=disclosed,
                        raw_text=line,
                        source_page=page_num,
                        source_quote=line[:200] if len(line) > 200 else line
                    ))
            
            logging.info(f"✅ After fallback extraction: {len(items)} total disclosure items")
        
        return items
    
    def _categorize_disclosure(self, text: str) -> str:
        """Categorize disclosure item"""
        text_lower = text.lower()
        
        if any(word in text_lower for word in ['foundation', 'structural']):
            return 'foundation'
        elif any(word in text_lower for word in ['roof', 'exterior']):
            return 'roof'
        elif any(word in text_lower for word in ['plumbing', 'water', 'sewer']):
            return 'plumbing'
        elif any(word in text_lower for word in ['electrical', 'wiring']):
            return 'electrical'
        elif any(word in text_lower for word in ['heating', 'cooling', 'hvac']):
            return 'hvac'
        else:
            return 'other'


# Test the new parser
if __name__ == "__main__":
    import PyPDF2
    
    parser = DocumentParser()
    
    # Load test PDF
    pdf_path = '/mnt/user-data/outputs/4578_Maplewood_Sunnyvale_Inspection.pdf'
    
    with open(pdf_path, 'rb') as file:
        pdf = PyPDF2.PdfReader(file)
        text = ''
        for page in pdf.pages:
            text += page.extract_text()
    
    print("=" * 80)
    print("TESTING NEW PARSER")
    print("=" * 80)
    
    doc = parser.parse_inspection_report(text, "4578 Maplewood Lane")
    
    print(f"\nTOTAL FINDINGS EXTRACTED: {len(doc.inspection_findings)}")
    print("=" * 80)
    
    if len(doc.inspection_findings) == 0:
        print("\n✅ SUCCESS: No fake findings created from good property!")
    else:
        print(f"\n⚠️ Found {len(doc.inspection_findings)} problems:")
        for i, finding in enumerate(doc.inspection_findings, 1):
            print(f"\n{i}. {finding.description}")
            print(f"   Category: {finding.category.value}")
            print(f"   Severity: {finding.severity.value}")
            if finding.estimated_cost_low:
                print(f"   Cost: ${finding.estimated_cost_low:,.0f} - ${finding.estimated_cost_high:,.0f}")

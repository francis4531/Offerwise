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
            'needs repair', 'requires repair', 'repair needed'
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
        Parse inspection report - NEW APPROACH.
        Only extracts actual problems, not observations or positive statements.
        """
        
        # Extract address if not provided
        if not property_address:
            property_address = self._extract_address(pdf_text)
        
        # Create document
        doc = PropertyDocument(
            property_address=property_address or "Unknown",
            document_type="inspection_report",
            parse_date=datetime.now(),
            content=pdf_text
        )
        
        # Extract ONLY actual problems
        doc.inspection_findings = self._extract_problems(pdf_text)
        
        return doc
    
    def _extract_problems(self, text: str) -> List[InspectionFinding]:
        """
        Extract ONLY actual problems from inspection text.
        This is the core of the new approach.
        
        v5.50.0: Now tracks page numbers for screenshot evidence feature.
        """
        problems = []
        
        # Split into sentences WITH page tracking
        sentences_with_pages = self._split_into_sentences_with_pages(text)
        
        for sentence, page_num in sentences_with_pages:
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
        """Check if text is noise (headers, photos, maintenance, etc)"""
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
            except:
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
        """Clean up description text - ensures complete sentences (v5.55.10)"""
        # Remove severity keywords at start
        text = re.sub(r'^(CRITICAL|MAJOR|MODERATE|MINOR)[\s:-]*', '', text, flags=re.IGNORECASE)
        
        # Clean up common OCR/parsing artifacts
        text = re.sub(r'\s+', ' ', text)  # Multiple spaces to single
        text = text.strip()
        
        # Limit length at sentence/word boundary for complete sentences
        if len(text) > 200:
            # Try to find sentence break first
            for end_char in ['. ', '! ', '? ']:
                last_break = text[:200].rfind(end_char)
                if last_break > 60:  # At least 60 chars for meaningful content
                    return text[:last_break + 1].strip()
            
            # No sentence break - truncate at last word boundary
            last_space = text[:200].rfind(' ')
            if last_space > 60:
                return text[:last_space].strip() + '...'
            
            # Last resort - just truncate
            return text[:197] + '...'
        
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
        
        return items
    
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
                has_checkbox_yes = bool(re.search(r'[\[\(]([Xx✓✔])[\]\)].*?(?:Yes|Y)', line, re.IGNORECASE))
                has_checkbox_no = bool(re.search(r'[\[\(]([Xx✓✔])[\]\)].*?(?:No|N)', line, re.IGNORECASE))
                
                # Check if line is property-related question
                is_question = any(word in line.lower() for word in [
                    'foundation', 'roof', 'plumbing', 'electrical', 'hvac', 
                    'structural', 'damage', 'repair', 'issue', 'defect',
                    'leak', 'crack', 'system', 'condition', 'problem'
                ])
                
                if is_question and (has_yes or has_no):
                    # Determine if disclosed
                    disclosed = has_checkbox_yes or (has_yes and not has_no)
                    
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

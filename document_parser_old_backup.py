"""
OfferWise Document Intelligence Engine
Core module for extracting structured data from real estate documents
"""

import re
import json
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Any
from enum import Enum
from datetime import datetime


# ============================================================================
# DATA SCHEMAS
# ============================================================================

class Severity(Enum):
    """Issue severity levels"""
    CRITICAL = "critical"           # Safety hazard, structural, immediate attention
    MAJOR = "major"                 # Significant cost or functionality impact
    MODERATE = "moderate"           # Deferred maintenance, manageable
    MINOR = "minor"                 # Cosmetic, low impact
    INFORMATIONAL = "informational" # FYI only


class IssueCategory(Enum):
    """Standard real estate issue categories"""
    FOUNDATION_STRUCTURE = "foundation_structure"
    ROOF_EXTERIOR = "roof_exterior"
    PLUMBING = "plumbing"
    ELECTRICAL = "electrical"
    HVAC = "hvac"
    INTERIOR = "interior"
    APPLIANCES = "appliances"
    SAFETY = "safety"
    ENVIRONMENTAL = "environmental"
    LEGAL_TITLE = "legal_title"
    HOA = "hoa"
    PEST_WOOD = "pest_wood"
    OTHER = "other"


@dataclass
class DisclosureItem:
    """Single disclosure item from seller disclosure form"""
    category: str
    question: str
    disclosed: bool  # True = Yes/issue exists, False = No/no issue
    details: Optional[str] = None
    page_reference: Optional[int] = None
    location: Optional[str] = None  # e.g., "basement", "master bedroom"
    date_of_issue: Optional[str] = None
    repaired: Optional[bool] = None
    repair_date: Optional[str] = None
    raw_text: Optional[str] = None


@dataclass
class InspectionFinding:
    """Single finding from inspection report"""
    category: IssueCategory
    severity: Severity
    location: str
    description: str
    recommendation: str
    estimated_cost_low: Optional[float] = None
    estimated_cost_high: Optional[float] = None
    page_reference: Optional[int] = None
    photo_references: List[str] = None
    requires_specialist: bool = False
    specialist_type: Optional[str] = None  # e.g., "structural engineer", "roofer"
    safety_concern: bool = False
    raw_text: Optional[str] = None

    def __post_init__(self):
        if self.photo_references is None:
            self.photo_references = []


@dataclass
class CrossReferenceMatch:
    """Match between disclosure and inspection finding"""
    disclosure_item: DisclosureItem
    inspection_finding: InspectionFinding
    match_type: str  # "consistent", "contradiction", "undisclosed", "disclosed_not_found"
    confidence: float  # 0.0 to 1.0
    explanation: str
    risk_impact: str  # "increases_risk", "neutral", "decreases_risk"


@dataclass
class PropertyDocument:
    """Container for a parsed property document"""
    document_type: str  # "seller_disclosure", "inspection_report", "hoa_docs", "permit_records"
    property_address: Optional[str] = None
    document_date: Optional[datetime] = None
    page_count: int = 0
    raw_text: str = ""
    disclosure_items: List[DisclosureItem] = None
    inspection_findings: List[InspectionFinding] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.disclosure_items is None:
            self.disclosure_items = []
        if self.inspection_findings is None:
            self.inspection_findings = []
        if self.metadata is None:
            self.metadata = {}


# ============================================================================
# DOCUMENT PARSER
# ============================================================================

class DocumentParser:
    """
    Core parser for extracting structured data from real estate documents.
    Handles seller disclosures, inspection reports, and HOA documents.
    """

    def __init__(self):
        self.disclosure_patterns = self._load_disclosure_patterns()
        self.inspection_patterns = self._load_inspection_patterns()
        self.cost_patterns = self._load_cost_patterns()

    def _load_disclosure_patterns(self) -> Dict[str, List[str]]:
        """
        Common disclosure form patterns and questions.
        In production, this would be a comprehensive database.
        """
        return {
            "foundation_structure": [
                r"foundation\s+(?:cracks|damage|issues|problems|settling)",
                r"structural\s+(?:damage|issues|problems|defects)",
                r"basement\s+(?:water|flooding|moisture|leaks)",
                r"retaining\s+walls?",
                r"soil\s+(?:settlement|subsidence)",
            ],
            "roof_exterior": [
                r"roof\s+(?:leak|damage|age|condition|replacement)",
                r"gutters?\s+(?:and\s+downspouts?)?",
                r"siding\s+(?:damage|condition)",
                r"windows?\s+(?:leak|damage|condition)",
                r"exterior\s+(?:paint|condition)",
            ],
            "plumbing": [
                r"plumbing\s+(?:leak|issues|problems|repairs)",
                r"sewer\s+(?:line|backup|lateral|issues)",
                r"septic\s+(?:system|tank)",
                r"water\s+(?:heater|pressure|quality)",
                r"polybutylene\s+pipes?",
            ],
            "electrical": [
                r"electrical\s+(?:system|panel|wiring|issues)",
                r"aluminum\s+wiring",
                r"knob\s+and\s+tube",
                r"circuit\s+breaker",
                r"gfci|afci",
            ],
            "hvac": [
                r"heating\s+(?:system|furnace|boiler)",
                r"air\s+conditioning",
                r"hvac",
                r"ventilation",
            ],
            "environmental": [
                r"asbestos",
                r"lead\s+(?:based\s+)?paint",
                r"radon",
                r"mold|mildew",
                r"underground\s+storage\s+tank",
            ],
            "legal_title": [
                r"boundary\s+(?:dispute|issue)",
                r"easement",
                r"encroachment",
                r"violation",
                r"permit",
            ],
            "hoa": [
                r"homeowners?\s+association",
                r"hoa",
                r"special\s+assessment",
                r"pending\s+litigation",
            ],
        }

    def _load_inspection_patterns(self) -> Dict[str, Any]:
        """
        Patterns for parsing inspection reports.
        Maps common inspection language to severity and cost.
        """
        return {
            "severity_keywords": {
                Severity.CRITICAL: [
                    "immediate attention", "safety hazard", "dangerous", "critical",
                    "structural failure", "major defect", "unsafe"
                ],
                Severity.MAJOR: [
                    "significant", "major repair", "substantial", "replacement recommended",
                    "end of life", "functional issue"
                ],
                Severity.MODERATE: [
                    "monitor", "maintenance needed", "minor repair", "recommend repair",
                    "typical wear"
                ],
                Severity.MINOR: [
                    "cosmetic", "aesthetic", "minor", "surface", "small"
                ],
            },
            "cost_indicators": {
                "foundation": (15000, 75000),
                "roof replacement": (12000, 35000),
                "hvac replacement": (5000, 15000),
                "water heater": (1200, 3000),
                "electrical panel": (1500, 4000),
                "sewer line": (3000, 20000),
            }
        }

    def _load_cost_patterns(self) -> Dict[str, re.Pattern]:
        """Patterns for extracting cost estimates from text"""
        return {
            "range": re.compile(r'\$?([\d,]+)\s*(?:to|-)\s*\$?([\d,]+)'),
            "single": re.compile(r'\$\s*([\d,]+)'),
            "approximate": re.compile(r'approximately\s*\$\s*([\d,]+)'),
        }

    def parse_seller_disclosure(self, pdf_text: str, property_address: str = None) -> PropertyDocument:
        """
        Parse a seller disclosure form into structured data.
        
        Args:
            pdf_text: Raw text extracted from PDF
            property_address: Property address (if known)
            
        Returns:
            PropertyDocument with parsed disclosure items
        """
        doc = PropertyDocument(
            document_type="seller_disclosure",
            property_address=property_address,
            raw_text=pdf_text,
            page_count=pdf_text.count('\f') + 1
        )

        # Extract address if not provided
        if not property_address:
            doc.property_address = self._extract_address(pdf_text)

        # Extract date
        doc.document_date = self._extract_date(pdf_text)

        # Parse yes/no questions
        doc.disclosure_items = self._parse_disclosure_questions(pdf_text)

        # Extract written explanations
        doc.disclosure_items.extend(self._parse_written_disclosures(pdf_text))

        return doc

    def parse_inspection_report(self, pdf_text: str, property_address: str = None) -> PropertyDocument:
        """
        Parse an inspection report into structured findings.
        
        Args:
            pdf_text: Raw text extracted from PDF
            property_address: Property address (if known)
            
        Returns:
            PropertyDocument with parsed inspection findings
        """
        doc = PropertyDocument(
            document_type="inspection_report",
            property_address=property_address,
            raw_text=pdf_text,
            page_count=pdf_text.count('\f') + 1
        )

        # Extract address if not provided
        if not property_address:
            doc.property_address = self._extract_address(pdf_text)

        # Extract inspection date
        doc.document_date = self._extract_date(pdf_text)

        # Parse findings by section
        doc.inspection_findings = self._parse_inspection_findings(pdf_text)

        return doc

    def _extract_address(self, text: str) -> Optional[str]:
        """Extract property address from document text"""
        # Look for common address patterns
        patterns = [
            r'(?:Property|Subject|Address):\s*([^\n]+)',
            r'(\d+\s+[A-Za-z\s]+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Way|Court|Ct|Boulevard|Blvd)[^\n]*)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        return None

    def _extract_date(self, text: str) -> Optional[datetime]:
        """Extract date from document text"""
        # Common date patterns
        patterns = [
            r'(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
            r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    date_str = match.group(1)
                    # Try multiple date formats
                    for fmt in ['%m/%d/%Y', '%m-%d-%Y', '%B %d, %Y', '%b %d, %Y']:
                        try:
                            return datetime.strptime(date_str, fmt)
                        except ValueError:
                            continue
                except:
                    pass
        
        return None

    def _parse_disclosure_questions(self, text: str) -> List[DisclosureItem]:
        """Parse yes/no checkbox questions from disclosure form"""
        items = []
        
        # Pattern 1: [X] Yes [ ] No - Yes is checked
        pattern1 = r'(.*?)[\[\(]([Xx✓✔])[\]\)]\s*(?:Yes|Y)\s*[\[\(]\s*[\]\)]\s*(?:No|N)'
        for match in re.finditer(pattern1, text, re.IGNORECASE):
            question = match.group(1).strip()
            if len(question) > 10:
                category = self._categorize_disclosure(question)
                items.append(DisclosureItem(
                    category=category,
                    question=question,
                    disclosed=True,  # Yes is checked = issue disclosed
                    raw_text=match.group(0)
                ))
        
        # Pattern 2: [ ] Yes [X] No - No is checked
        pattern2 = r'(.*?)[\[\(]\s*[\]\)]\s*(?:Yes|Y)\s*[\[\(]([Xx✓✔])[\]\)]\s*(?:No|N)'
        for match in re.finditer(pattern2, text, re.IGNORECASE):
            question = match.group(1).strip()
            if len(question) > 10:
                category = self._categorize_disclosure(question)
                items.append(DisclosureItem(
                    category=category,
                    question=question,
                    disclosed=False,  # No is checked = no issue disclosed
                    raw_text=match.group(0)
                ))
        
        # NEW: Pattern 3: Narrative Q&A format
        # "Are you aware of X? No, everything is fine." or "Have you noticed Y? Yes, there is an issue."
        narrative_pattern = r'(?:Are you aware|Have you|Do you know|Is there|Has there been)[^?]+\?\s*([^\n.]+)'
        for match in re.finditer(narrative_pattern, text, re.IGNORECASE):
            question = match.group(0).split('?')[0].strip() + '?'
            answer = match.group(1).strip().lower()
            
            if len(question) > 15:
                category = self._categorize_disclosure(question)
                
                # Determine if seller disclosed an issue
                disclosed = False
                if answer.startswith('yes') or 'there is' in answer or 'there are' in answer:
                    disclosed = True
                elif answer.startswith('no') or 'no issues' in answer or 'in good condition' in answer:
                    disclosed = False  # Seller says no problems
                else:
                    # Ambiguous - skip it
                    continue
                
                items.append(DisclosureItem(
                    category=category,
                    question=question,
                    disclosed=disclosed,
                    details=match.group(1).strip() if disclosed else None,
                    raw_text=match.group(0)
                ))
        
        return items

    def _parse_written_disclosures(self, text: str) -> List[DisclosureItem]:
        """Extract written explanations and additional disclosures"""
        items = []
        
        # Look for explanation sections
        explanation_pattern = r'(?:Explanation|Details|Please\s+Explain):\s*([^\n]{20,})'
        
        for match in re.finditer(explanation_pattern, text, re.IGNORECASE):
            explanation = match.group(1).strip()
            category = self._categorize_disclosure(explanation)
            
            items.append(DisclosureItem(
                category=category,
                question="Written disclosure",
                disclosed=True,
                details=explanation,
                raw_text=match.group(0)
            ))
        
        return items

    def _categorize_disclosure(self, text: str) -> str:
        """Categorize a disclosure based on keywords"""
        text_lower = text.lower()
        
        for category, patterns in self.disclosure_patterns.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    return category
        
        return "other"

    def _parse_inspection_findings(self, text: str) -> List[InspectionFinding]:
        """Parse inspection findings into structured data"""
        findings = []
        
        # Split text into sections (many inspections are organized this way)
        sections = self._split_inspection_sections(text)
        
        for section_name, section_text in sections.items():
            category = self._map_section_to_category(section_name)
            
            # Find individual findings within section
            finding_blocks = self._extract_finding_blocks(section_text)
            
            for block in finding_blocks:
                # CRITICAL FIX: Skip "no issues" positive statements
                if self._is_positive_statement(block):
                    continue  # Don't create a finding for "everything is fine"
                
                severity = self._determine_severity(block)
                location = self._extract_location(block)
                description = self._extract_description(block)
                recommendation = self._extract_recommendation(block)
                costs = self._extract_cost_estimate(block)
                
                if description:  # Only add if we extracted meaningful content
                    findings.append(InspectionFinding(
                        category=category,
                        severity=severity,
                        location=location or "Not specified",
                        description=description,
                        recommendation=recommendation,
                        estimated_cost_low=costs[0] if costs else None,
                        estimated_cost_high=costs[1] if costs else None,
                        safety_concern=self._is_safety_concern(block),
                        requires_specialist=self._requires_specialist(block),
                        raw_text=block
                    ))
        
        return findings

    def _split_inspection_sections(self, text: str) -> Dict[str, str]:
        """Split inspection report into sections by headers"""
        sections = {}
        
        # Common section headers
        header_pattern = r'^([A-Z\s]{10,}):?\s*$'
        
        lines = text.split('\n')
        current_section = "General"
        current_content = []
        
        for line in lines:
            if re.match(header_pattern, line.strip()):
                # Save previous section
                if current_content:
                    sections[current_section] = '\n'.join(current_content)
                
                # Start new section
                current_section = line.strip().rstrip(':')
                current_content = []
            else:
                current_content.append(line)
        
        # Save last section
        if current_content:
            sections[current_section] = '\n'.join(current_content)
        
        return sections

    def _map_section_to_category(self, section_name: str) -> IssueCategory:
        """Map inspection section name to standard category"""
        section_lower = section_name.lower()
        
        mapping = {
            'foundation': IssueCategory.FOUNDATION_STRUCTURE,
            'structure': IssueCategory.FOUNDATION_STRUCTURE,
            'roof': IssueCategory.ROOF_EXTERIOR,
            'exterior': IssueCategory.ROOF_EXTERIOR,
            'plumbing': IssueCategory.PLUMBING,
            'electrical': IssueCategory.ELECTRICAL,
            'heating': IssueCategory.HVAC,
            'cooling': IssueCategory.HVAC,
            'hvac': IssueCategory.HVAC,
            'interior': IssueCategory.INTERIOR,
            'appliance': IssueCategory.APPLIANCES,
        }
        
        for keyword, category in mapping.items():
            if keyword in section_lower:
                return category
        
        return IssueCategory.OTHER

    def _extract_finding_blocks(self, section_text: str) -> List[str]:
        """Extract individual findings from a section"""
        # Split on common separators
        blocks = []
        
        # Look for bullet points, numbers, or "Observation:" style markers
        pattern = r'(?:^|\n)(?:[-•*]|\d+\.?|\b(?:Observation|Finding|Issue|Defect|Recommendation):)\s+'
        
        parts = re.split(pattern, section_text, flags=re.MULTILINE)
        
        for part in parts:
            part = part.strip()
            if len(part) > 30:  # Filter noise
                blocks.append(part)
        
        return blocks if blocks else [section_text]

    def _is_positive_statement(self, text: str) -> bool:
        """
        Determine if text indicates NO ISSUES (positive/good condition).
        These should NOT be treated as findings.
        """
        text_lower = text.lower()
        
        # Positive indicators - no actual problems
        positive_phrases = [
            'no issues', 'no problems', 'no concerns', 'no defects',
            'no damage', 'no cracks', 'no leaks', 'no evidence',
            'in good condition', 'in excellent condition', 'in satisfactory condition',
            'appears to be in good', 'appears to be in excellent', 'appears satisfactory',
            'functioning properly', 'operating normally', 'working as intended',
            'no significant', 'no visible', 'no major', 'no apparent',
            'well maintained', 'properly maintained', 'adequate condition',
            'no safety concerns', 'no immediate concerns', 'acceptable condition',
            'meets standards', 'passes inspection', 'within normal'
        ]
        
        # Check if text contains positive phrases
        for phrase in positive_phrases:
            if phrase in text_lower:
                return True
        
        return False

    def _determine_severity(self, text: str) -> Severity:
        """Determine severity of finding based on keywords"""
        text_lower = text.lower()
        
        for severity, keywords in self.inspection_patterns['severity_keywords'].items():
            for keyword in keywords:
                if keyword in text_lower:
                    return severity
        
        return Severity.MODERATE  # Default

    def _extract_location(self, text: str) -> Optional[str]:
        """Extract location mentioned in finding"""
        # Common location indicators
        pattern = r'(?:in|at|on|near)\s+(?:the\s+)?([a-z\s]+(?:room|bath|bedroom|kitchen|basement|attic|garage|crawlspace))'
        
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        return None

    def _extract_description(self, text: str) -> str:
        """Extract main description from finding block"""
        # Take first sentence or two
        sentences = re.split(r'[.!?]\s+', text)
        return '. '.join(sentences[:2]).strip() if sentences else text[:200]

    def _extract_recommendation(self, text: str) -> str:
        """Extract recommendation from finding"""
        # Look for recommendation keywords
        pattern = r'(?:Recommend|Recommendation|Should|Suggest):\s*([^\n.]+)'
        
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        # If no explicit recommendation, look for action verbs
        action_pattern = r'((?:Replace|Repair|Monitor|Contact|Install|Upgrade)[^\n.]+)'
        match = re.search(action_pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        return "Review with qualified contractor"

    def _extract_cost_estimate(self, text: str) -> Optional[tuple]:
        """Extract cost estimate range from text"""
        # Look for cost range
        match = self.cost_patterns['range'].search(text)
        if match:
            low = float(match.group(1).replace(',', ''))
            high = float(match.group(2).replace(',', ''))
            return (low, high)
        
        # Look for single cost
        match = self.cost_patterns['single'].search(text)
        if match:
            cost = float(match.group(1).replace(',', ''))
            return (cost * 0.8, cost * 1.2)  # Add 20% buffer
        
        return None

    def _is_safety_concern(self, text: str) -> bool:
        """Determine if finding is a safety concern"""
        safety_keywords = ['safety', 'hazard', 'dangerous', 'risk', 'unsafe', 'fire', 'electrical shock']
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in safety_keywords)

    def _requires_specialist(self, text: str) -> bool:
        """Determine if finding requires specialist evaluation"""
        specialist_keywords = ['structural engineer', 'licensed contractor', 'hvac specialist', 
                             'electrician', 'plumber', 'roofer', 'further evaluation']
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in specialist_keywords)

    def to_json(self, document: PropertyDocument) -> str:
        """Convert PropertyDocument to JSON string"""
        
        def convert_to_dict(obj):
            """Recursively convert dataclass to dict"""
            if hasattr(obj, '__dataclass_fields__'):
                result = {}
                for field_name, field_def in obj.__dataclass_fields__.items():
                    value = getattr(obj, field_name)
                    if isinstance(value, list):
                        result[field_name] = [convert_to_dict(item) for item in value]
                    elif isinstance(value, Enum):
                        result[field_name] = value.value
                    elif isinstance(value, datetime):
                        result[field_name] = value.isoformat()
                    elif hasattr(value, '__dataclass_fields__'):
                        result[field_name] = convert_to_dict(value)
                    else:
                        result[field_name] = value
                return result
            return obj
        
        return json.dumps(convert_to_dict(document), indent=2)


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

if __name__ == "__main__":
    # Example usage
    parser = DocumentParser()
    
    # Simulate seller disclosure text
    sample_disclosure = """
    SELLER'S REAL PROPERTY DISCLOSURE STATEMENT
    Property Address: 123 Elm Street, San Jose, CA 95110
    Date: December 1, 2024
    
    STRUCTURAL
    1. Are you aware of any foundation cracks? [X] Yes [ ] No
       Explanation: Minor hairline cracks in basement noted during 2020 inspection
    
    2. Are you aware of any roof leaks? [ ] Yes [X] No
    
    PLUMBING
    3. Are you aware of any sewer line issues? [X] Yes [ ] No
       Explanation: Partial sewer line replacement in 2019, now functioning properly
    """
    
    disclosure_doc = parser.parse_seller_disclosure(sample_disclosure)
    
    print("=== PARSED SELLER DISCLOSURE ===")
    print(f"Property: {disclosure_doc.property_address}")
    print(f"Date: {disclosure_doc.document_date}")
    print(f"\nDisclosure Items Found: {len(disclosure_doc.disclosure_items)}")
    
    for item in disclosure_doc.disclosure_items:
        print(f"\nCategory: {item.category}")
        print(f"Question: {item.question}")
        print(f"Disclosed: {item.disclosed}")
        if item.details:
            print(f"Details: {item.details}")
    
    # Simulate inspection report text
    sample_inspection = """
    RESIDENTIAL INSPECTION REPORT
    Property: 123 Elm Street, San Jose, CA 95110
    Inspection Date: December 15, 2024
    
    FOUNDATION
    Observation: Multiple vertical cracks observed in foundation walls, particularly 
    on south side. Cracks are 1/4" wide and extend from floor to ceiling. This is a 
    significant structural concern. Recommend immediate evaluation by structural engineer.
    Estimated Cost: $15,000 - $40,000 for foundation repair
    
    ROOF
    Observation: Roof shows typical wear for age. Minor granule loss on south slope.
    Life expectancy 3-5 years. Recommend monitoring and budgeting for replacement.
    
    PLUMBING
    Observation: Main sewer line shows signs of root intrusion via camera inspection.
    Partial clearing attempted. Recommend full replacement of compromised section.
    Estimated Cost: $8,000 - $12,000
    """
    
    inspection_doc = parser.parse_inspection_report(sample_inspection)
    
    print("\n\n=== PARSED INSPECTION REPORT ===")
    print(f"Property: {inspection_doc.property_address}")
    print(f"Date: {inspection_doc.document_date}")
    print(f"\nInspection Findings: {len(inspection_doc.inspection_findings)}")
    
    for finding in inspection_doc.inspection_findings:
        print(f"\nCategory: {finding.category.value}")
        print(f"Severity: {finding.severity.value}")
        print(f"Location: {finding.location}")
        print(f"Description: {finding.description}")
        print(f"Recommendation: {finding.recommendation}")
        if finding.estimated_cost_low:
            print(f"Estimated Cost: ${finding.estimated_cost_low:,.0f} - ${finding.estimated_cost_high:,.0f}")
        print(f"Safety Concern: {finding.safety_concern}")

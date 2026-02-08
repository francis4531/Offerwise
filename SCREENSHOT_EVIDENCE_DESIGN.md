# Screenshot Evidence Feature Design
## "Show Me The Proof"

---

## The Vision

When OfferWise says "Seller didn't disclose roof damage", the user can click and see:

```
┌─────────────────────────────────────────────────────────────────────┐
│ 🔍 EVIDENCE: Undisclosed Roof Damage                                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────────────┐    ┌──────────────────────────────────┐  │
│  │ SELLER DISCLOSURE    │    │ INSPECTION REPORT                │  │
│  │ Page 3               │    │ Page 12                          │  │
│  │                      │    │                                  │  │
│  │ ┌──────────────────┐ │    │ ┌──────────────────────────────┐ │  │
│  │ │ 5. ROOF          │ │    │ │ ROOF INSPECTION              │ │  │
│  │ │                  │ │    │ │                              │ │  │
│  │ │ Any known issues │ │    │ │ Significant wear observed.   │ │  │
│  │ │ with the roof?   │ │    │ │ Missing shingles in 3 areas. │ │  │
│  │ │                  │ │    │ │ Flashing deteriorated.       │ │  │
│  │ │ [X] No  [ ] Yes  │ │    │ │ RECOMMEND: Full replacement  │ │  │
│  │ │     ⬆️ HIGHLIGHTED│ │    │ │ within 2-3 years.           │ │  │
│  │ └──────────────────┘ │    │ └──────────────────────────────┘ │  │
│  └──────────────────────┘    └──────────────────────────────────┘  │
│                                                                     │
│  ❌ DISCREPANCY: Seller marked "No issues" but inspector found      │
│     significant damage requiring $12,000-$18,000 in repairs.        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**This is irrefutable proof.** The user sees EXACTLY what we saw.

---

## Implementation Architecture

### Phase 1: Page-Level Screenshots

**Minimum Viable Feature:**
1. Store page numbers during document parsing
2. Generate page screenshots on-demand (not stored)
3. Display full page when user clicks "View Source"
4. Quote the relevant text below the image

**Data Model Changes:**

```python
@dataclass
class InspectionFinding:
    category: InspectionCategory
    description: str
    severity: Severity
    location: str
    estimated_cost_low: float
    estimated_cost_high: float
    verified: bool = False
    evidence: List[str] = None
    # NEW FIELDS
    source_page: int = None          # Page number in source document
    source_document: str = None      # 'disclosure' or 'inspection'
    source_text: str = None          # Exact quote from document
```

**API Endpoint:**

```python
@app.route('/api/document-screenshot/<doc_type>/<int:page_num>')
def get_document_screenshot(doc_type, page_num):
    """
    Returns a screenshot of the specified page.
    
    doc_type: 'disclosure' or 'inspection'
    page_num: 1-indexed page number
    
    Returns: PNG image
    """
    # Get the stored PDF for this property
    # Convert specific page to image
    # Return as base64 or direct image
```

---

### Phase 2: Highlighted Screenshots

**Enhanced Feature:**
1. Track bounding boxes during OCR (PaddleOCR provides this)
2. Draw highlight rectangles around relevant text
3. Add annotation arrows/labels

**Technical Approach:**

PaddleOCR returns:
```python
[
    [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]], ('text content', 0.95)]
]
```

We can use these coordinates to:
1. Draw a yellow highlight box
2. Add a red border around the key finding
3. Number multiple highlights (1, 2, 3...)

**Example with PIL/Pillow:**
```python
from PIL import Image, ImageDraw

def highlight_text(image, bounding_boxes, highlight_indices):
    draw = ImageDraw.Draw(image, 'RGBA')
    
    for idx, box in enumerate(bounding_boxes):
        if idx in highlight_indices:
            # Yellow highlight with transparency
            draw.polygon(box, fill=(255, 255, 0, 100))
            # Red border
            draw.polygon(box, outline='red', width=2)
    
    return image
```

---

### Phase 3: Side-by-Side Comparison View

**Full Feature:**
1. Automatically pair disclosure questions with inspection findings
2. Generate comparison screenshots
3. Draw connecting lines between related items
4. Add discrepancy labels

---

## Storage Considerations

### Option A: Generate On-Demand (Recommended for MVP)
- Store original PDFs
- Generate screenshots when user clicks "View Source"
- Cache for session duration
- Pros: No extra storage, always fresh
- Cons: Slight delay on first view

### Option B: Pre-Generate and Store
- Generate screenshots during analysis
- Store as base64 in database or file storage
- Pros: Instant display
- Cons: Storage costs, stale if PDF reprocessed

### Option C: Hybrid
- Generate thumbnail previews during analysis (small, low-res)
- Generate full-res on-demand when clicked
- Best of both worlds

---

## UI Integration

### In Red Flag Display:
```jsx
{flag.source_page && (
  <button onClick={() => showSourceScreenshot(flag)}>
    📄 View Source (Page {flag.source_page})
  </button>
)}
```

### In Cross-Reference Display:
```jsx
{discrepancy.disclosure_page && discrepancy.inspection_page && (
  <button onClick={() => showComparison(discrepancy)}>
    🔍 Compare Documents
  </button>
)}
```

### Screenshot Modal:
```jsx
<Modal>
  <div className="screenshot-comparison">
    <div className="document-panel">
      <h4>Seller Disclosure - Page {disclosure_page}</h4>
      <img src={disclosure_screenshot} />
      <blockquote>{disclosure_text}</blockquote>
    </div>
    <div className="vs-divider">VS</div>
    <div className="document-panel">
      <h4>Inspection Report - Page {inspection_page}</h4>
      <img src={inspection_screenshot} />
      <blockquote>{inspection_text}</blockquote>
    </div>
  </div>
  <div className="discrepancy-summary">
    ❌ DISCREPANCY: {discrepancy_description}
  </div>
</Modal>
```

---

## Privacy & Security

### Concerns:
1. Documents may contain sensitive info (SSN, financial data)
2. Screenshots persist in browser cache
3. Could be screenshot by malicious actors

### Mitigations:
1. Only show pages relevant to findings (not entire document)
2. Add watermark: "CONFIDENTIAL - [User Email] - [Date]"
3. Disable right-click/screenshot on modal (can be bypassed but adds friction)
4. Clear from cache on logout
5. Don't store screenshots server-side longer than session

---

## Implementation Phases

### Phase 1 (1-2 weeks): Basic Page Screenshots
- [ ] Add `source_page` field to InspectionFinding
- [ ] Track page numbers during parsing
- [ ] Create `/api/document-screenshot` endpoint
- [ ] Add "View Source" button to findings
- [ ] Simple modal showing page image + quote

### Phase 2 (2-3 weeks): Highlighted Screenshots
- [ ] Store bounding boxes from OCR
- [ ] Implement highlight drawing
- [ ] Add annotation labels
- [ ] Improve modal with zoom/pan

### Phase 3 (2-3 weeks): Comparison View
- [ ] Auto-pair disclosure/inspection findings
- [ ] Side-by-side comparison modal
- [ ] Draw connecting lines
- [ ] Discrepancy labels and explanations

---

## Success Metrics

1. **Trust Score Survey**: "How confident are you in this analysis?" (before/after)
2. **Screenshot View Rate**: % of users who click "View Source"
3. **Time on Analysis**: Users who view screenshots spend longer (good engagement)
4. **Support Tickets**: Fewer "how did you get this number?" questions

---

## Competitive Advantage

**No one else does this.**

Every other real estate AI tool is a black box. OfferWise would be the ONLY platform that says:

*"Don't trust us? Here's the exact page, the exact line, the exact checkbox the seller marked. See for yourself."*

That's not just a feature. That's a **moat**.

---

## Technical Dependencies

Already have:
- ✅ pdf2image (for page rendering)
- ✅ PaddleOCR (provides bounding boxes)
- ✅ PIL/Pillow (for image manipulation)

Need to add:
- Page number tracking in parser
- Screenshot generation endpoint
- Frontend modal component
- Caching layer for performance

---

## Recommendation

**Start with Phase 1 immediately.** 

Even basic page screenshots without highlighting would be a massive credibility boost. We can show:
- "Disclosure Page 3" + quote
- "Inspection Page 12" + quote

That alone proves we read the documents and found real discrepancies. The highlighting can come later.

# OfferWise v5.51.0 - Screenshot Evidence Feature

## Release Date: January 26, 2026

## Summary

**CREDIBILITY FEATURE: "View Source" Screenshot System**

Users can now click "View Source" buttons to see actual document pages where issues were found. This is the ultimate credibility feature - showing users the exact page, not just telling them.

---

## What Users See

### In Red Flags Section:
```
┌──────────────────────────────────────────────────────────────────┐
│ ⛔ Roof damage - seller marked "No issues"         🟢 VERIFIED   │
│                                                                  │
│ Evidence: Inspector found significant wear in 3 areas...        │
│                                                                  │
│ [📄 View Disclosure p.3]  [🔍 View Inspection p.12]            │
│                                                                  │
│ 💡 Recommendation: Get professional roof inspection...          │
└──────────────────────────────────────────────────────────────────┘
```

### When They Click "View Source":
```
┌──────────────────────────────────────────────────────────────────┐
│ 📄 Roof damage - seller marked "No issues"           [×]         │
│ Inspection Report — Page 12                                      │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│                  [Actual document page image]                    │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│ 🔍 View Source: This is the actual page from your uploaded       │
│    document where we found this information.                     │
└──────────────────────────────────────────────────────────────────┘
```

---

## New Features

### 1. Page Number Tracking (Backend)
- Parser now tracks `source_page` for every finding
- Disclosure items track which page they came from
- Cross-reference matches preserve page numbers

### 2. Screenshot API Endpoints
- `POST /api/document-screenshot` - Single page screenshot
- `POST /api/document-comparison-screenshot` - Side-by-side comparison

### 3. PDF Storage (Frontend)
- PDFs are stored in memory (base64) when uploaded
- Available for screenshot generation during session
- Cleared when user leaves/refreshes

### 4. View Source Buttons (UI)
- Clickable buttons on red flags: "View Disclosure p.3"
- Clickable buttons on undisclosed issues: "View p.12"
- Non-clickable badges for loaded analyses (PDF not in memory)

### 5. Screenshot Modal
- Full modal with document page image
- Loading state with progress indicator
- Error state if PDF not available
- Footer explaining what they're seeing

---

## Technical Implementation

### State Management (OfferWise component)
```javascript
// Screenshot modal state
const [showScreenshotModal, setShowScreenshotModal] = useState(false);
const [screenshotLoading, setScreenshotLoading] = useState(false);
const [screenshotImage, setScreenshotImage] = useState(null);
const [screenshotError, setScreenshotError] = useState('');
const [screenshotInfo, setScreenshotInfo] = useState({...});

// PDF storage for screenshots
const [storedDisclosurePdf, setStoredDisclosurePdf] = useState(null);
const [storedInspectionPdf, setStoredInspectionPdf] = useState(null);
```

### PDF Storage (during upload)
```javascript
// Convert ArrayBuffer to base64
const pdfBase64 = btoa(
  new Uint8Array(arrayBuffer).reduce((data, byte) => 
    data + String.fromCharCode(byte), ''
  )
);
setStoredDisclosurePdf(pdfBase64);
```

### Screenshot Function
```javascript
const viewSourceScreenshot = async (docType, pageNum, title) => {
  const pdfBase64 = docType === 'disclosure' 
    ? storedDisclosurePdf 
    : storedInspectionPdf;
  
  // Call API to generate screenshot
  const response = await fetch('/api/document-screenshot', {
    method: 'POST',
    body: JSON.stringify({ pdf_base64: pdfBase64, page_number: pageNum })
  });
  
  // Display in modal
  setScreenshotImage(data.image_base64);
};
```

---

## Files Changed

1. `document_parser.py` - Page tracking in parser
2. `app.py` - Screenshot API endpoints
3. `static/app.html`:
   - State declarations for modal and PDF storage
   - viewSourceScreenshot function
   - Screenshot modal component
   - Clickable "View Source" buttons on findings
   - PDF storage during upload
4. `VERSION` - 5.51.0

---

## Limitations (Current Release)

1. **Session Only**: Screenshots only work for current session
   - PDFs stored in memory, lost on refresh
   - Loaded analyses from dashboard won't have "View Source"

2. **No Highlighting**: Page shown as-is
   - Future: highlight specific text
   - Future: bounding box from OCR

3. **Memory Usage**: Large PDFs stored in browser memory
   - ~1.33x size increase (base64)
   - Consider compression in future

---

## What's Next (v5.52.0)

1. **Text Highlighting**: Use OCR bounding boxes to highlight relevant text
2. **Side-by-Side Comparison**: "Compare Documents" button
3. **Persistent Storage**: Store PDFs server-side for reload
4. **Zoom/Pan**: Let users zoom into specific areas

---

## Testing

1. Upload both documents
2. Run analysis
3. Click "View Inspection p.X" button
4. Modal should show actual page image
5. Close modal and click a different page
6. Verify page changes

---

## The Credibility Impact

Before: "We found the seller didn't disclose roof issues"
After: "Here's page 3 where seller checked 'No issues', and here's page 12 where inspector found damage"

**That's not AI alchemy. That's proof.**

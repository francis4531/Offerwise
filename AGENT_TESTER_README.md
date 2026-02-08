# OfferWise Agentic Test Runner 🤖

An AI-powered testing agent that automatically tests the OfferWise application using Playwright for browser automation and Claude for intelligent decision-making.

## Features

- **Automated End-to-End Testing**: Runs complete user flows from upload to results
- **MTurk Integration**: Uses existing turk tracking infrastructure to log all actions
- **AI-Powered Verification**: Claude analyzes results for inconsistencies
- **Screenshot Capture**: Automatic screenshots at key points and on errors
- **Parallel Test Support**: Run multiple test iterations
- **Detailed Reporting**: JSON reports with full test logs

## Setup

### 1. Install Dependencies

```bash
pip install playwright anthropic
playwright install chromium
```

### 2. Set API Key

```bash
export ANTHROPIC_API_KEY=your_key_here
```

### 3. Add Test Files

Create a `test_files/` directory with your test PDFs:

```
test_files/
├── disclosure_1.pdf      # Seller disclosure document
├── inspection_1.pdf      # Inspection report
├── disclosure_2.pdf      # Another test pair (optional)
└── inspection_2.pdf
```

## Usage

### Basic Test Run

```bash
python agent_tester.py --url https://your-site.com
```

### Multiple Runs

```bash
python agent_tester.py --url https://your-site.com --runs 5
```

### Headless Mode (CI/CD)

```bash
python agent_tester.py --url https://your-site.com --runs 10 --headless
```

### Custom Test Files Directory

```bash
python agent_tester.py --url https://your-site.com --test-files /path/to/pdfs
```

## What Gets Tested

| Step | Description | Tracked Action |
|------|-------------|----------------|
| 1 | Navigate to app with turk_id | `navigation` |
| 2 | Fill property address | `address_filled` |
| 3 | Fill asking price | `price_filled` |
| 4 | Upload disclosure PDF | `disclosure_uploaded` |
| 5 | Upload inspection PDF | `inspection_uploaded` |
| 6 | Click Continue | `continue_clicked` |
| 7 | Wait for analysis | `analysis_started` |
| 8 | Verify results display | `results_displayed` |
| 9 | Check all sections | `verified_*` |
| 10 | Complete turk session | `turk_completed` |

## Output

### Screenshots

Saved to `./screenshots/`:
- `uploads_complete_{agent_id}.png`
- `results_{agent_id}.png`
- `error_{agent_id}.png` (on failures)

### Reports

Saved to `./test_reports/`:
```json
{
  "agent_id": "agent_20260127_143022_1234",
  "status": "completed",
  "steps_completed": ["navigation", "form_loaded", ...],
  "issues": ["Price not auto-formatted"],
  "claude_analysis": "The analysis looks complete but...",
  "completion_code": "OW-XXXX-XXXX"
}
```

### Videos

If running in non-headless mode, videos are saved to `./test_videos/`

## Viewing Results in Admin Dashboard

All agent test runs appear in your MTurk admin dashboard:
```
https://your-site.com/admin/turk
```

Agent sessions are identifiable by:
- `turk_id` starting with `agent_`
- `task_id` = `agent_test`

## Integration with CI/CD

```yaml
# GitHub Actions example
- name: Run Agent Tests
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
  run: |
    pip install playwright anthropic
    playwright install chromium
    python agent_tester.py --url ${{ env.STAGING_URL }} --runs 3 --headless
```

## Customization

### Adding New Test Scenarios

Edit `agent_tester.py` and modify the `sections_to_check` list:

```python
sections_to_check = [
    ("OfferScore", "offer_score"),
    ("Risk DNA", "risk_dna"),
    ("Transparency", "transparency"),
    ("Your Custom Section", "custom_section"),
]
```

### Custom Validation

Add Claude prompts to verify specific aspects:

```python
validation = self.ask_claude(
    "Does this property analysis correctly identify foundation issues?",
    visible_text
)
```

## Troubleshooting

### "No test files found"

Make sure you have PDFs in `./test_files/` with "disclosure" and "inspection" in their names.

### "Continue button disabled"

The agent will log the reason. Common causes:
- Upload not complete
- Invalid address/price
- Missing required document

### Timeouts

Increase timeout values in the code for slower servers:
```python
await page.wait_for_selector('text=OfferScore', timeout=300000)  # 5 minutes
```

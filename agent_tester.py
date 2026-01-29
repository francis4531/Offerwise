#!/usr/bin/env python3
"""
OfferWise Agentic Test Runner v1.0
==================================
Uses Playwright for browser automation + Claude API for intelligent decision-making.
Tracks all actions through the MTurk infrastructure for analysis.

Usage:
    python agent_tester.py --url https://your-site.com --runs 5

Requirements:
    pip install playwright anthropic
    playwright install chromium
"""

import argparse
import asyncio
import json
import os
import random
import time
from datetime import datetime
from pathlib import Path

try:
    from playwright.async_api import async_playwright
    import anthropic
except ImportError:
    print("Please install required packages:")
    print("  pip install playwright anthropic")
    print("  playwright install chromium")
    exit(1)


class OfferWiseTestAgent:
    """
    An AI-powered agent that tests the OfferWise application.
    Uses Claude to make intelligent decisions and verify results.
    """
    
    def __init__(self, base_url: str, test_files_dir: str = None, headless: bool = False):
        self.base_url = base_url.rstrip('/')
        self.test_files_dir = test_files_dir or os.path.join(os.path.dirname(__file__), 'test_files')
        self.headless = headless
        self.client = anthropic.Anthropic()
        self.agent_id = f"agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}"
        self.test_log = []
        
    def log(self, action: str, details: dict = None):
        """Log an action with timestamp"""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'details': details or {}
        }
        self.test_log.append(entry)
        print(f"🤖 [{entry['timestamp'][-12:-4]}] {action}")
        if details:
            for k, v in details.items():
                print(f"    └─ {k}: {v}")
    
    def ask_claude(self, prompt: str, context: str = "") -> str:
        """Ask Claude to make a decision or analyze something"""
        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": f"{context}\n\n{prompt}" if context else prompt
                }]
            )
            return response.content[0].text
        except Exception as e:
            self.log("claude_error", {"error": str(e)})
            return ""
    
    async def run_test(self, disclosure_pdf: str, inspection_pdf: str) -> dict:
        """
        Run a complete test of the OfferWise application.
        Returns a test report with results and any issues found.
        """
        self.log("test_started", {
            "agent_id": self.agent_id,
            "disclosure": os.path.basename(disclosure_pdf),
            "inspection": os.path.basename(inspection_pdf)
        })
        
        report = {
            "agent_id": self.agent_id,
            "started_at": datetime.now().isoformat(),
            "status": "running",
            "steps_completed": [],
            "issues": [],
            "screenshots": []
        }
        
        async with async_playwright() as p:
            # Launch browser
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                viewport={'width': 1280, 'height': 900},
                record_video_dir="./test_videos" if not self.headless else None
            )
            page = await context.new_page()
            
            try:
                # Step 1: Navigate to app with turk tracking
                self.log("navigating", {"url": f"{self.base_url}/app?turk_id={self.agent_id}&task_id=agent_test"})
                await page.goto(f"{self.base_url}/app?turk_id={self.agent_id}&task_id=agent_test")
                await page.wait_for_load_state('networkidle')
                report["steps_completed"].append("navigation")
                
                # Check for turk banner
                turk_banner = await page.query_selector('#turk-banner')
                if turk_banner:
                    self.log("turk_tracking_active", {"banner_visible": True})
                else:
                    report["issues"].append("Turk tracking banner not visible")
                
                # Step 2: Wait for upload form to be ready
                self.log("waiting_for_form")
                await page.wait_for_selector('input[placeholder*="Property Address"]', timeout=10000)
                report["steps_completed"].append("form_loaded")
                
                # Step 3: Generate test data using Claude
                self.log("generating_test_data")
                test_address = self.ask_claude(
                    "Generate a realistic California property address for testing. Just the address, nothing else. Example format: 123 Oak Street, San Jose, CA 95123"
                ).strip()
                
                # Fallback if Claude fails
                if not test_address or len(test_address) < 10:
                    test_address = f"{random.randint(100, 9999)} Test Avenue, San Jose, CA 95123"
                
                test_price = str(random.randint(800000, 2500000))
                
                self.log("test_data_generated", {
                    "address": test_address,
                    "price": f"${int(test_price):,}"
                })
                
                # Step 4: Fill in address
                self.log("filling_address")
                address_input = await page.query_selector('input[placeholder*="Property Address"]')
                await address_input.fill(test_address)
                await page.wait_for_timeout(500)  # Let validation run
                report["steps_completed"].append("address_filled")
                
                # Step 5: Fill in price
                self.log("filling_price")
                price_input = await page.query_selector('input[placeholder*="Asking Price"]')
                await price_input.fill(test_price)
                await page.wait_for_timeout(500)
                report["steps_completed"].append("price_filled")
                
                # Check if price formatted correctly
                price_value = await price_input.input_value()
                if ',' in price_value:
                    self.log("price_formatting_verified", {"formatted": price_value})
                else:
                    report["issues"].append(f"Price not auto-formatted: {price_value}")
                
                # Step 6: Upload disclosure PDF
                self.log("uploading_disclosure", {"file": os.path.basename(disclosure_pdf)})
                
                # Find file inputs - they may be hidden
                file_inputs = await page.query_selector_all('input[type="file"]')
                if len(file_inputs) >= 1:
                    await file_inputs[0].set_input_files(disclosure_pdf)
                else:
                    report["issues"].append("Could not find disclosure file input")
                    raise Exception("No file input found for disclosure")
                
                # Wait for processing
                self.log("waiting_for_disclosure_processing")
                try:
                    await page.wait_for_selector('text=✅ Complete', timeout=120000)
                    report["steps_completed"].append("disclosure_uploaded")
                    self.log("disclosure_processed")
                except:
                    report["issues"].append("Disclosure processing timed out")
                    # Take screenshot of error state
                    screenshot_path = f"./screenshots/error_disclosure_{self.agent_id}.png"
                    await page.screenshot(path=screenshot_path)
                    report["screenshots"].append(screenshot_path)
                
                # Step 7: Upload inspection PDF
                self.log("uploading_inspection", {"file": os.path.basename(inspection_pdf)})
                
                if len(file_inputs) >= 2:
                    await file_inputs[1].set_input_files(inspection_pdf)
                else:
                    # Try to find the second input after first upload
                    file_inputs = await page.query_selector_all('input[type="file"]')
                    if len(file_inputs) >= 2:
                        await file_inputs[1].set_input_files(inspection_pdf)
                    else:
                        report["issues"].append("Could not find inspection file input")
                
                # Wait for processing
                self.log("waiting_for_inspection_processing")
                try:
                    # Wait for both to show complete
                    await page.wait_for_timeout(2000)  # Give it time to start
                    # Check for green checkmarks or completion text
                    await page.wait_for_function(
                        "document.body.innerText.includes('inspection processing complete') || document.querySelectorAll('[style*=\"#22c55e\"]').length >= 2",
                        timeout=120000
                    )
                    report["steps_completed"].append("inspection_uploaded")
                    self.log("inspection_processed")
                except:
                    report["issues"].append("Inspection processing may have timed out")
                
                # Take screenshot of upload state
                screenshot_path = f"./screenshots/uploads_complete_{self.agent_id}.png"
                os.makedirs("./screenshots", exist_ok=True)
                await page.screenshot(path=screenshot_path)
                report["screenshots"].append(screenshot_path)
                
                # Step 8: Click Continue to Analysis
                self.log("clicking_continue")
                continue_button = await page.query_selector('button:has-text("Continue to Analysis")')
                
                if continue_button:
                    is_disabled = await continue_button.is_disabled()
                    if is_disabled:
                        self.log("button_disabled", {"checking_why": True})
                        # Get button title for reason
                        title = await continue_button.get_attribute('title')
                        report["issues"].append(f"Continue button disabled: {title}")
                        
                        # Take screenshot
                        screenshot_path = f"./screenshots/button_disabled_{self.agent_id}.png"
                        await page.screenshot(path=screenshot_path)
                        report["screenshots"].append(screenshot_path)
                    else:
                        await continue_button.click()
                        report["steps_completed"].append("continue_clicked")
                        self.log("continue_clicked")
                else:
                    report["issues"].append("Continue button not found")
                
                # Step 9: Wait for analysis to start
                self.log("waiting_for_analysis")
                try:
                    await page.wait_for_selector('text=Analyzing', timeout=10000)
                    report["steps_completed"].append("analysis_started")
                    self.log("analysis_started")
                except:
                    self.log("analysis_start_check_failed")
                
                # Step 10: Wait for results (this can take a while)
                self.log("waiting_for_results", {"timeout": "180s"})
                try:
                    await page.wait_for_selector('text=OfferScore', timeout=180000)
                    report["steps_completed"].append("results_displayed")
                    self.log("results_displayed")
                    
                    # Take screenshot of results
                    screenshot_path = f"./screenshots/results_{self.agent_id}.png"
                    await page.screenshot(path=screenshot_path, full_page=True)
                    report["screenshots"].append(screenshot_path)
                    
                except Exception as e:
                    report["issues"].append(f"Results not displayed: {str(e)}")
                    screenshot_path = f"./screenshots/error_results_{self.agent_id}.png"
                    await page.screenshot(path=screenshot_path, full_page=True)
                    report["screenshots"].append(screenshot_path)
                
                # Step 11: Scroll through results and verify sections
                self.log("verifying_results")
                
                sections_to_check = [
                    ("OfferScore", "offer_score"),
                    ("Risk DNA", "risk_dna"),
                    ("Transparency", "transparency"),
                    ("Red Flag", "red_flags"),
                ]
                
                for section_text, section_id in sections_to_check:
                    try:
                        element = await page.query_selector(f'text={section_text}')
                        if element:
                            await element.scroll_into_view_if_needed()
                            await page.wait_for_timeout(500)
                            report["steps_completed"].append(f"verified_{section_id}")
                            self.log(f"section_verified", {"section": section_text})
                        else:
                            report["issues"].append(f"Section not found: {section_text}")
                    except Exception as e:
                        report["issues"].append(f"Error checking {section_text}: {str(e)}")
                
                # Step 12: Complete turk session
                self.log("completing_turk_session")
                try:
                    complete_button = await page.query_selector('button:has-text("Complete Test")')
                    if complete_button:
                        await complete_button.click()
                        await page.wait_for_timeout(2000)
                        
                        # Check for completion code
                        code_element = await page.query_selector('#turk-code')
                        if code_element:
                            completion_code = await code_element.inner_text()
                            report["completion_code"] = completion_code
                            self.log("test_completed", {"code": completion_code})
                            report["steps_completed"].append("turk_completed")
                except Exception as e:
                    self.log("turk_completion_error", {"error": str(e)})
                
                # Final analysis using Claude
                self.log("analyzing_results_with_claude")
                page_content = await page.content()
                
                # Extract visible text for Claude to analyze
                visible_text = await page.evaluate("document.body.innerText")
                
                # Truncate for API limits
                if len(visible_text) > 10000:
                    visible_text = visible_text[:10000] + "..."
                
                analysis = self.ask_claude(
                    "Analyze this property analysis result and identify any issues, inconsistencies, or concerns:\n\n" + visible_text[:5000],
                    "You are a QA tester reviewing an AI-generated property analysis. Look for: missing data, inconsistent scores, unclear recommendations, UI issues mentioned in the text."
                )
                
                report["claude_analysis"] = analysis
                self.log("claude_analysis_complete")
                
                report["status"] = "completed"
                
            except Exception as e:
                self.log("test_error", {"error": str(e)})
                report["status"] = "error"
                report["error"] = str(e)
                
                # Take error screenshot
                try:
                    screenshot_path = f"./screenshots/error_{self.agent_id}.png"
                    os.makedirs("./screenshots", exist_ok=True)
                    await page.screenshot(path=screenshot_path, full_page=True)
                    report["screenshots"].append(screenshot_path)
                except:
                    pass
                
            finally:
                report["ended_at"] = datetime.now().isoformat()
                report["test_log"] = self.test_log
                
                await browser.close()
        
        return report


class TestRunner:
    """Manages multiple test runs"""
    
    def __init__(self, base_url: str, test_files_dir: str):
        self.base_url = base_url
        self.test_files_dir = test_files_dir
        self.reports = []
        
    def find_test_files(self) -> list:
        """Find pairs of disclosure and inspection PDFs"""
        test_dir = Path(self.test_files_dir)
        if not test_dir.exists():
            print(f"⚠️  Test files directory not found: {test_dir}")
            print("   Please create it and add PDF pairs:")
            print("   - disclosure_1.pdf, inspection_1.pdf")
            print("   - disclosure_2.pdf, inspection_2.pdf")
            return []
        
        pairs = []
        disclosures = list(test_dir.glob("*disclosure*.pdf")) + list(test_dir.glob("*Disclosure*.pdf"))
        inspections = list(test_dir.glob("*inspection*.pdf")) + list(test_dir.glob("*Inspection*.pdf"))
        
        # Try to pair them up
        for d in disclosures:
            # Look for matching inspection
            for i in inspections:
                # Simple matching - same number suffix or just pair them
                pairs.append((str(d), str(i)))
                break
        
        if not pairs and disclosures and inspections:
            # Just pair first disclosure with first inspection
            pairs.append((str(disclosures[0]), str(inspections[0])))
        
        return pairs
    
    async def run_tests(self, num_runs: int = 1, headless: bool = False):
        """Run multiple test iterations"""
        print(f"\n{'='*60}")
        print(f"  OfferWise Agentic Test Runner")
        print(f"  Target: {self.base_url}")
        print(f"  Runs: {num_runs}")
        print(f"{'='*60}\n")
        
        test_pairs = self.find_test_files()
        if not test_pairs:
            print("❌ No test files found. Please add PDFs to test_files/")
            return
        
        print(f"📁 Found {len(test_pairs)} test file pair(s)")
        
        for run_num in range(num_runs):
            print(f"\n{'─'*40}")
            print(f"  Test Run {run_num + 1}/{num_runs}")
            print(f"{'─'*40}\n")
            
            # Pick a random test pair
            disclosure, inspection = random.choice(test_pairs)
            
            agent = OfferWiseTestAgent(
                base_url=self.base_url,
                test_files_dir=self.test_files_dir,
                headless=headless
            )
            
            report = await agent.run_test(disclosure, inspection)
            self.reports.append(report)
            
            # Print summary
            print(f"\n📊 Run {run_num + 1} Summary:")
            print(f"   Status: {report['status']}")
            print(f"   Steps completed: {len(report['steps_completed'])}")
            print(f"   Issues found: {len(report['issues'])}")
            
            if report['issues']:
                print("   Issues:")
                for issue in report['issues']:
                    print(f"     ⚠️  {issue}")
            
            # Wait between runs
            if run_num < num_runs - 1:
                wait_time = random.randint(5, 15)
                print(f"\n⏳ Waiting {wait_time}s before next run...")
                await asyncio.sleep(wait_time)
        
        # Save all reports
        report_path = f"./test_reports/report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        os.makedirs("./test_reports", exist_ok=True)
        with open(report_path, 'w') as f:
            json.dump(self.reports, f, indent=2)
        print(f"\n📄 Full report saved to: {report_path}")
        
        # Print overall summary
        print(f"\n{'='*60}")
        print(f"  OVERALL SUMMARY")
        print(f"{'='*60}")
        
        successful = sum(1 for r in self.reports if r['status'] == 'completed')
        total_issues = sum(len(r['issues']) for r in self.reports)
        
        print(f"  Successful runs: {successful}/{num_runs}")
        print(f"  Total issues found: {total_issues}")
        
        # Common issues
        all_issues = [issue for r in self.reports for issue in r['issues']]
        if all_issues:
            print(f"\n  Common Issues:")
            from collections import Counter
            for issue, count in Counter(all_issues).most_common(5):
                print(f"    [{count}x] {issue}")


def main():
    parser = argparse.ArgumentParser(description='OfferWise Agentic Test Runner')
    parser.add_argument('--url', required=True, help='Base URL of OfferWise (e.g., https://your-site.com)')
    parser.add_argument('--runs', type=int, default=1, help='Number of test runs (default: 1)')
    parser.add_argument('--headless', action='store_true', help='Run browser in headless mode')
    parser.add_argument('--test-files', default='./test_files', help='Directory containing test PDFs')
    
    args = parser.parse_args()
    
    runner = TestRunner(args.url, args.test_files)
    asyncio.run(runner.run_tests(num_runs=args.runs, headless=args.headless))


if __name__ == '__main__':
    main()

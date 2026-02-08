#!/usr/bin/env python3
"""
OfferWise Autonomous Test Agent v2.0
=====================================
A fully autonomous AI agent that:
1. Generates synthetic seller disclosures and inspection reports
2. Runs complete end-to-end tests without human intervention
3. Tracks everything through MTurk infrastructure
4. Runs 100+ properties in parallel batches

Usage:
    python agent_autonomous.py --url https://your-site.com --count 100

Requirements:
    pip install playwright anthropic fpdf2 aiohttp
    playwright install chromium
"""

import argparse
import asyncio
import json
import os
import random
import string
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import tempfile
import shutil

# Suppress fpdf deprecation warnings about ln parameter
warnings.filterwarnings('ignore', message='.*ln.*deprecated.*')

try:
    from playwright.async_api import async_playwright
    import anthropic
    from fpdf import FPDF
except ImportError:
    print("Please install required packages:")
    print("  pip install playwright anthropic fpdf2")
    print("  playwright install chromium")
    exit(1)


# =============================================================================
# PROPERTY DATA GENERATOR
# =============================================================================

class PropertyGenerator:
    """Generates realistic property data for testing"""
    
    CALIFORNIA_CITIES = [
        ("San Jose", "95123"), ("San Francisco", "94102"), ("Los Angeles", "90001"),
        ("San Diego", "92101"), ("Sacramento", "95814"), ("Oakland", "94612"),
        ("Fremont", "94538"), ("Palo Alto", "94301"), ("Mountain View", "94040"),
        ("Sunnyvale", "94086"), ("Santa Clara", "95050"), ("Cupertino", "95014"),
        ("Milpitas", "95035"), ("Redwood City", "94061"), ("San Mateo", "94401"),
        ("Berkeley", "94704"), ("Irvine", "92602"), ("Pasadena", "91101"),
        ("Santa Monica", "90401"), ("Burbank", "91502")
    ]
    
    STREET_NAMES = [
        "Oak", "Maple", "Cedar", "Pine", "Elm", "Walnut", "Cherry", "Willow",
        "Birch", "Ash", "Magnolia", "Palm", "Cypress", "Olive", "Laurel",
        "Main", "First", "Second", "Third", "Park", "Lake", "Hill", "Valley",
        "Meadow", "Forest", "Spring", "Summer", "Sunset", "Sunrise", "Vista"
    ]
    
    STREET_TYPES = ["Street", "Avenue", "Drive", "Lane", "Court", "Way", "Place", "Boulevard"]
    
    # Issue categories with realistic descriptions
    STRUCTURAL_ISSUES = [
        ("Foundation crack observed on north wall", "major", 8500),
        ("Minor settling in garage foundation", "minor", 2500),
        ("Horizontal crack in basement wall indicating lateral pressure", "critical", 15000),
        ("Hairline cracks in foundation - cosmetic only", "minor", 500),
        ("Previous foundation repair visible - appears stable", "minor", 0),
    ]
    
    ROOF_ISSUES = [
        ("Roof shingles showing wear, 5-7 years remaining life", "minor", 0),
        ("Missing shingles in several areas", "major", 4500),
        ("Evidence of previous leak repair near chimney", "minor", 1200),
        ("Roof past expected lifespan, replacement recommended", "critical", 18000),
        ("Flashing deterioration around vents", "moderate", 2000),
    ]
    
    PLUMBING_ISSUES = [
        ("Slow drain in master bathroom", "minor", 300),
        ("Water heater past expected lifespan (12 years old)", "moderate", 2500),
        ("Evidence of previous leak under kitchen sink - repaired", "minor", 0),
        ("Galvanized pipes showing corrosion - recommend replacement", "major", 12000),
        ("Low water pressure at second floor fixtures", "minor", 800),
    ]
    
    ELECTRICAL_ISSUES = [
        ("Some outlets not grounded - recommend upgrade", "moderate", 1500),
        ("Panel at capacity - may need upgrade for additions", "moderate", 3500),
        ("GFCI outlets needed in bathrooms", "minor", 400),
        ("Aluminum wiring present - recommend evaluation", "major", 8000),
        ("Outdated Federal Pacific panel - recommend replacement", "critical", 4500),
    ]
    
    HVAC_ISSUES = [
        ("HVAC system 15 years old - functional but aging", "moderate", 0),
        ("AC unit not cooling efficiently - may need recharge", "minor", 500),
        ("Furnace showing signs of wear", "moderate", 3000),
        ("Ductwork needs cleaning", "minor", 400),
        ("No AC in property - common for area", "minor", 0),
    ]
    
    WATER_ISSUES = [
        ("Minor moisture staining in basement", "minor", 500),
        ("Previous water damage repaired in attic", "moderate", 0),
        ("Active leak at bathroom ceiling", "critical", 3500),
        ("Grading slopes toward foundation - recommend correction", "moderate", 2000),
        ("Gutters need cleaning and repair", "minor", 600),
    ]
    
    PEST_ISSUES = [
        ("Evidence of previous termite treatment", "minor", 0),
        ("Active termite infestation in garage", "critical", 5000),
        ("Minor ant activity near kitchen", "minor", 200),
        ("Rodent droppings in attic - recommend exclusion", "moderate", 1500),
        ("Wood-boring beetle damage in crawlspace", "major", 4000),
    ]
    
    DISCLOSURE_DEFECTS = [
        "Roof was replaced in 2019",
        "Water heater replaced 2021",
        "Foundation repair performed in 2018 with warranty",
        "Previous termite treatment - annual inspections",
        "HVAC serviced annually",
        "Remodeled kitchen in 2020 with permits",
        "Added bathroom in 2017 - permitted",
        "Pool resurfaced 2022",
        "Electrical panel upgraded 2020",
        "New windows installed 2019",
    ]
    
    DISCLOSURE_ISSUES_KNOWN = [
        "Occasional slow drain in master bath",
        "One bedroom window sticks",
        "Garage door opener intermittent",
        "Minor crack in driveway",
        "Fence shared with neighbor needs repair",
        "Tree roots affecting sidewalk",
        "Sprinkler system needs adjustment",
        "Minor settling crack in garage floor",
    ]

    @classmethod
    def generate_property(cls, scenario: str = "random") -> dict:
        """Generate a complete property with all test data"""
        
        # Generate address
        city, zip_code = random.choice(cls.CALIFORNIA_CITIES)
        street_num = random.randint(100, 9999)
        street_name = random.choice(cls.STREET_NAMES)
        street_type = random.choice(cls.STREET_TYPES)
        address = f"{street_num} {street_name} {street_type}, {city}, CA {zip_code}"
        
        # Generate price based on city (rough approximation)
        base_prices = {
            "San Francisco": 1500000, "Palo Alto": 2500000, "Cupertino": 2200000,
            "Mountain View": 1800000, "Los Angeles": 1200000, "San Diego": 1100000,
            "San Jose": 1400000, "Oakland": 900000, "Sacramento": 600000,
        }
        base = base_prices.get(city, 1000000)
        price = base + random.randint(-200000, 400000)
        price = round(price / 10000) * 10000  # Round to nearest 10k
        
        # Generate year built
        year_built = random.randint(1950, 2020)
        
        # Generate property details
        sqft = random.randint(1200, 4000)
        bedrooms = random.randint(2, 5)
        bathrooms = random.randint(1, 4)
        lot_size = random.randint(4000, 15000)
        
        # Determine issue severity based on scenario
        if scenario == "clean":
            num_issues = random.randint(0, 2)
            issue_severity = "minor"
        elif scenario == "moderate":
            num_issues = random.randint(3, 5)
            issue_severity = "moderate"
        elif scenario == "problematic":
            num_issues = random.randint(5, 10)
            issue_severity = "major"
        elif scenario == "nightmare":
            num_issues = random.randint(8, 15)
            issue_severity = "critical"
        else:  # random
            num_issues = random.randint(2, 8)
            issue_severity = random.choice(["minor", "moderate", "major"])
        
        # Generate inspection findings
        all_issues = (
            cls.STRUCTURAL_ISSUES + cls.ROOF_ISSUES + cls.PLUMBING_ISSUES +
            cls.ELECTRICAL_ISSUES + cls.HVAC_ISSUES + cls.WATER_ISSUES + cls.PEST_ISSUES
        )
        
        # Weight selection based on severity
        if issue_severity == "minor":
            weighted_issues = [i for i in all_issues if i[1] in ["minor"]]
        elif issue_severity == "moderate":
            weighted_issues = [i for i in all_issues if i[1] in ["minor", "moderate"]]
        elif issue_severity == "major":
            weighted_issues = [i for i in all_issues if i[1] in ["moderate", "major"]]
        elif issue_severity == "critical":
            weighted_issues = [i for i in all_issues if i[1] in ["major", "critical"]]
        else:
            weighted_issues = all_issues
        
        selected_issues = random.sample(weighted_issues, min(num_issues, len(weighted_issues)))
        
        # Calculate total repair estimate
        total_repairs = sum(issue[2] for issue in selected_issues)
        
        # Generate disclosure items
        num_disclosed = random.randint(2, 6)
        disclosed_items = random.sample(cls.DISCLOSURE_DEFECTS + cls.DISCLOSURE_ISSUES_KNOWN, num_disclosed)
        
        # Decide what seller "forgot" to disclose (for transparency testing)
        undisclosed_issues = []
        for issue in selected_issues:
            if random.random() < 0.3:  # 30% chance seller didn't disclose
                undisclosed_issues.append(issue[0])
        
        return {
            "address": address,
            "city": city,
            "zip": zip_code,
            "price": price,
            "year_built": year_built,
            "sqft": sqft,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "lot_size": lot_size,
            "inspection_issues": selected_issues,
            "total_repair_estimate": total_repairs,
            "disclosed_items": disclosed_items,
            "undisclosed_issues": undisclosed_issues,
            "scenario": scenario,
        }


# =============================================================================
# PDF GENERATOR
# =============================================================================

class PDFGenerator:
    """Generates realistic PDF documents for testing"""
    
    @staticmethod
    def generate_disclosure(property_data: dict, output_path: str) -> str:
        """Generate a seller disclosure PDF"""
        
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_margins(15, 15, 15)  # Wider margins for safety
        pdf.add_page()
        
        # Title
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, "SELLER PROPERTY DISCLOSURE STATEMENT", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(5)
        
        # Property info
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "PROPERTY INFORMATION", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, f"Address: {property_data['address']}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"Year Built: {property_data['year_built']}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"Sq Ft: {property_data['sqft']:,}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"Beds: {property_data['bedrooms']} Baths: {property_data['bathrooms']}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(10)
        
        # Disclosure checklist
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "DISCLOSURE CHECKLIST", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        
        checklist = [
            ("Foundation issues", random.choice(["No", "Yes", "Unknown"])),
            ("Roof leaks", random.choice(["No", "Repaired", "Unknown"])),
            ("Plumbing problems", random.choice(["No", "Minor", "Unknown"])),
            ("Electrical issues", random.choice(["No", "Yes", "Unknown"])),
            ("HVAC operational", random.choice(["Yes", "Needs service", "Unknown"])),
            ("Pest history", random.choice(["No", "Treated", "Unknown"])),
            ("Water damage", random.choice(["No", "Repaired", "Unknown"])),
            ("Permits on file", random.choice(["Yes", "Partial", "Unknown"])),
        ]
        
        for item, answer in checklist:
            pdf.cell(0, 6, f"  {item}: {answer}", new_x="LMARGIN", new_y="NEXT")
        
        pdf.ln(10)
        
        # Known items
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "KNOWN DEFECTS AND REPAIRS", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        
        for item in property_data['disclosed_items']:
            pdf.multi_cell(0, 6, f"  - {item}")
        
        pdf.ln(10)
        
        # Signature
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, "Seller certifies the above is true and correct.", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(10)
        pdf.cell(0, 6, f"Date: {datetime.now().strftime('%m/%d/%Y')}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, "Signature: _________________________", new_x="LMARGIN", new_y="NEXT")
        
        # Add a second page for completeness
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 10, "ADDITIONAL DISCLOSURES", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 10)
        
        additional = [
            "Natural Hazard Zone: Zone X (minimal flood risk)",
            "Earthquake Fault Zone: Standard California disclosure applies",
            "Fire Hazard Zone: Not in high fire severity zone",
            "Lead Paint: Built after 1978 - not applicable" if property_data['year_built'] >= 1978 else "Lead Paint: Pre-1978 - disclosure provided",
            "HOA: " + random.choice(["None", "Yes - monthly dues apply", "Not applicable"]),
            "Property sold AS-IS",
        ]
        
        for item in additional:
            pdf.cell(0, 6, f"  {item}", new_x="LMARGIN", new_y="NEXT")
        
        pdf.output(output_path)
        return output_path
    
    @staticmethod
    def generate_inspection(property_data: dict, output_path: str) -> str:
        """Generate an inspection report PDF"""
        
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_margins(15, 15, 15)  # Wider margins
        pdf.add_page()
        
        # Header
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, "PROPERTY INSPECTION REPORT", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, f"Date: {datetime.now().strftime('%B %d, %Y')}", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.cell(0, 6, f"Report: INS-{random.randint(10000, 99999)}", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(10)
        
        # Property info
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "PROPERTY", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, f"Address: {property_data['address']}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"Year: {property_data['year_built']} | Size: {property_data['sqft']:,} sqft", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(10)
        
        # Executive Summary
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "EXECUTIVE SUMMARY", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        
        total_issues = len(property_data['inspection_issues'])
        critical = len([i for i in property_data['inspection_issues'] if i[1] == 'critical'])
        major = len([i for i in property_data['inspection_issues'] if i[1] == 'major'])
        
        pdf.multi_cell(0, 6, f"Found {total_issues} items. Critical: {critical}. Major: {major}. Est repairs: ${property_data['total_repair_estimate']:,}")
        pdf.ln(10)
        
        # Findings
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "FINDINGS", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        
        for issue_desc, severity, cost in property_data['inspection_issues']:
            severity_label = severity.upper()
            cost_str = f"${cost:,}" if cost > 0 else "Monitor"
            pdf.multi_cell(0, 6, f"[{severity_label}] {issue_desc} - {cost_str}")
        
        if not property_data['inspection_issues']:
            pdf.cell(0, 6, "No significant issues found.", new_x="LMARGIN", new_y="NEXT")
        
        # Add summary page
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 10, "SUMMARY", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(5)
        
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, f"Total items found: {total_issues}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"Critical issues: {critical}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"Major issues: {major}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"Estimated repairs: ${property_data['total_repair_estimate']:,}", new_x="LMARGIN", new_y="NEXT")
        
        pdf.ln(20)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, "This report is based on visual inspection only.", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"Inspector License: {random.randint(10000, 99999)}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(10)
        pdf.cell(0, 6, "Signature: _________________________", new_x="LMARGIN", new_y="NEXT")
        
        pdf.output(output_path)
        return output_path


# =============================================================================
# AUTONOMOUS TEST AGENT
# =============================================================================

class AutonomousTestAgent:
    """Fully autonomous test agent that generates data and runs tests"""
    
    def __init__(self, base_url: str, headless: bool = True):
        self.base_url = base_url.rstrip('/')
        self.headless = headless
        self.results = []
        self.temp_dir = None
        
    async def run_single_test(self, test_id: int, property_data: dict, semaphore: asyncio.Semaphore) -> dict:
        """Run a single test with generated property"""
        
        async with semaphore:  # Limit concurrent browsers
            agent_id = f"auto_{datetime.now().strftime('%Y%m%d')}_{test_id:04d}"
            
            result = {
                "test_id": test_id,
                "agent_id": agent_id,
                "property": property_data["address"],
                "price": property_data["price"],
                "scenario": property_data["scenario"],
                "status": "running",
                "started_at": datetime.now().isoformat(),
                "steps": [],
                "errors": [],
            }
            
            # Generate PDFs
            disclosure_path = os.path.join(self.temp_dir, f"disclosure_{test_id}.pdf")
            inspection_path = os.path.join(self.temp_dir, f"inspection_{test_id}.pdf")
            
            try:
                PDFGenerator.generate_disclosure(property_data, disclosure_path)
                PDFGenerator.generate_inspection(property_data, inspection_path)
                result["steps"].append("pdfs_generated")
            except Exception as e:
                result["errors"].append(f"PDF generation failed: {str(e)}")
                result["status"] = "failed"
                return result
            
            # Run browser test
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=self.headless)
                context = await browser.new_context(viewport={'width': 1280, 'height': 900})
                page = await context.new_page()
                
                try:
                    # Navigate with turk tracking
                    await page.goto(
                        f"{self.base_url}/app?turk_id={agent_id}&task_id=auto_test_{property_data['scenario']}",
                        timeout=30000
                    )
                    await page.wait_for_load_state('networkidle', timeout=15000)
                    result["steps"].append("navigated")
                    
                    # Wait for form
                    await page.wait_for_selector('input[placeholder*="Property Address"]', timeout=10000)
                    result["steps"].append("form_ready")
                    
                    # Fill address
                    await page.fill('input[placeholder*="Property Address"]', property_data["address"])
                    await page.wait_for_timeout(300)
                    result["steps"].append("address_filled")
                    
                    # Fill price
                    await page.fill('input[placeholder*="Asking Price"]', str(property_data["price"]))
                    await page.wait_for_timeout(300)
                    result["steps"].append("price_filled")
                    
                    # Upload files
                    file_inputs = await page.query_selector_all('input[type="file"]')
                    
                    if len(file_inputs) >= 2:
                        await file_inputs[0].set_input_files(disclosure_path)
                        await page.wait_for_timeout(1000)
                        await file_inputs[1].set_input_files(inspection_path)
                        result["steps"].append("files_uploaded")
                    else:
                        result["errors"].append("Could not find file inputs")
                    
                    # Wait for processing (up to 2 minutes)
                    try:
                        await page.wait_for_function(
                            """() => {
                                const text = document.body.innerText;
                                return text.includes('✅ Complete') && 
                                       (text.match(/✅ Complete/g) || []).length >= 2;
                            }""",
                            timeout=120000
                        )
                        result["steps"].append("files_processed")
                    except:
                        result["errors"].append("File processing timeout")
                    
                    # Click continue
                    continue_btn = await page.query_selector('button:has-text("Continue to Analysis")')
                    if continue_btn and not await continue_btn.is_disabled():
                        await continue_btn.click()
                        result["steps"].append("continue_clicked")
                        
                        # Wait for results (up to 3 minutes)
                        try:
                            await page.wait_for_selector('text=OfferScore', timeout=180000)
                            result["steps"].append("results_displayed")
                            
                            # Extract key results
                            try:
                                # Get OfferScore
                                score_el = await page.query_selector('[class*="score"]')
                                if score_el:
                                    result["offer_score"] = await score_el.inner_text()
                            except:
                                pass
                            
                            result["status"] = "completed"
                            
                        except Exception as e:
                            result["errors"].append(f"Results timeout: {str(e)}")
                            result["status"] = "timeout"
                    else:
                        result["errors"].append("Continue button disabled or not found")
                        result["status"] = "blocked"
                        
                except Exception as e:
                    result["errors"].append(str(e))
                    result["status"] = "error"
                    
                finally:
                    await browser.close()
            
            result["ended_at"] = datetime.now().isoformat()
            
            # Calculate duration
            start = datetime.fromisoformat(result["started_at"])
            end = datetime.fromisoformat(result["ended_at"])
            result["duration_seconds"] = (end - start).total_seconds()
            
            return result
    
    async def run_batch(self, count: int = 100, concurrency: int = 5) -> list:
        """Run a batch of tests"""
        
        print(f"\n{'='*60}")
        print(f"  OfferWise Autonomous Test Agent v2.0")
        print(f"  Running {count} tests with {concurrency} concurrent browsers")
        print(f"{'='*60}\n")
        
        # Create temp directory for PDFs
        self.temp_dir = tempfile.mkdtemp(prefix="offerwise_test_")
        print(f"📁 Temp directory: {self.temp_dir}")
        
        # Generate all properties
        print(f"\n🏠 Generating {count} test properties...")
        scenarios = ["clean", "moderate", "problematic", "nightmare", "random"]
        properties = []
        for i in range(count):
            scenario = scenarios[i % len(scenarios)]  # Rotate through scenarios
            properties.append(PropertyGenerator.generate_property(scenario))
        
        print(f"   ✓ {len([p for p in properties if p['scenario'] == 'clean'])} clean properties")
        print(f"   ✓ {len([p for p in properties if p['scenario'] == 'moderate'])} moderate properties")
        print(f"   ✓ {len([p for p in properties if p['scenario'] == 'problematic'])} problematic properties")
        print(f"   ✓ {len([p for p in properties if p['scenario'] == 'nightmare'])} nightmare properties")
        print(f"   ✓ {len([p for p in properties if p['scenario'] == 'random'])} random properties")
        
        # Run tests with semaphore for concurrency control
        print(f"\n🚀 Starting tests...")
        semaphore = asyncio.Semaphore(concurrency)
        
        tasks = [
            self.run_single_test(i, prop, semaphore) 
            for i, prop in enumerate(properties)
        ]
        
        # Progress tracking
        completed = 0
        results = []
        
        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)
            completed += 1
            
            status_emoji = {
                "completed": "✅",
                "timeout": "⏱️",
                "blocked": "🚫",
                "error": "❌",
                "failed": "💥",
            }.get(result["status"], "❓")
            
            print(f"  [{completed:3d}/{count}] {status_emoji} Test {result['test_id']:04d} - {result['status']} ({result.get('duration_seconds', 0):.1f}s) - {result['property'][:40]}...")
        
        self.results = results
        
        # Cleanup temp directory
        try:
            shutil.rmtree(self.temp_dir)
        except:
            pass
        
        return results
    
    def print_summary(self):
        """Print test summary"""
        
        if not self.results:
            print("No results to summarize")
            return
        
        print(f"\n{'='*60}")
        print(f"  TEST SUMMARY")
        print(f"{'='*60}\n")
        
        # Status counts
        statuses = {}
        for r in self.results:
            statuses[r["status"]] = statuses.get(r["status"], 0) + 1
        
        print("Status Breakdown:")
        for status, count in sorted(statuses.items(), key=lambda x: -x[1]):
            pct = count / len(self.results) * 100
            bar = "█" * int(pct / 2)
            print(f"  {status:12s} {count:3d} ({pct:5.1f}%) {bar}")
        
        # Scenario breakdown
        print("\nBy Scenario:")
        scenarios = {}
        for r in self.results:
            s = r.get("scenario", "unknown")
            if s not in scenarios:
                scenarios[s] = {"total": 0, "completed": 0}
            scenarios[s]["total"] += 1
            if r["status"] == "completed":
                scenarios[s]["completed"] += 1
        
        for scenario, data in sorted(scenarios.items()):
            success_rate = data["completed"] / data["total"] * 100 if data["total"] > 0 else 0
            print(f"  {scenario:12s} {data['completed']}/{data['total']} ({success_rate:.0f}% success)")
        
        # Common errors
        all_errors = [e for r in self.results for e in r.get("errors", [])]
        if all_errors:
            print("\nCommon Errors:")
            from collections import Counter
            for error, count in Counter(all_errors).most_common(5):
                print(f"  [{count:2d}x] {error[:60]}...")
        
        # Timing stats
        durations = [r.get("duration_seconds", 0) for r in self.results if r.get("duration_seconds")]
        if durations:
            print(f"\nTiming:")
            print(f"  Average: {sum(durations)/len(durations):.1f}s")
            print(f"  Min: {min(durations):.1f}s")
            print(f"  Max: {max(durations):.1f}s")
        
        print(f"\n{'='*60}")
        print(f"  View detailed results in MTurk dashboard:")
        print(f"  {self.base_url}/admin/turk")
        print(f"{'='*60}\n")
    
    def save_report(self, path: str = None):
        """Save detailed report to JSON"""
        
        if not path:
            path = f"./test_reports/autonomous_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        report = {
            "generated_at": datetime.now().isoformat(),
            "base_url": self.base_url,
            "total_tests": len(self.results),
            "summary": {
                "completed": len([r for r in self.results if r["status"] == "completed"]),
                "failed": len([r for r in self.results if r["status"] != "completed"]),
            },
            "results": self.results,
        }
        
        with open(path, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"📄 Report saved to: {path}")
        return path


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='OfferWise Autonomous Test Agent')
    parser.add_argument('--url', required=True, help='Base URL of OfferWise')
    parser.add_argument('--count', type=int, default=100, help='Number of tests to run (default: 100)')
    parser.add_argument('--concurrency', type=int, default=5, help='Concurrent browsers (default: 5)')
    parser.add_argument('--visible', action='store_true', help='Show browsers (not headless)')
    
    args = parser.parse_args()
    
    agent = AutonomousTestAgent(
        base_url=args.url,
        headless=not args.visible
    )
    
    # Run tests
    asyncio.run(agent.run_batch(count=args.count, concurrency=args.concurrency))
    
    # Print summary and save report
    agent.print_summary()
    agent.save_report()


if __name__ == '__main__':
    main()

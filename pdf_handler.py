"""
OfferWise Production PDF Handler
Robust PDF text extraction with fallback strategies
"""

import pdfplumber
import PyPDF2
from typing import Optional, Dict, Any
from io import BytesIO
import re


class PDFHandler:
    """
    Production-grade PDF text extraction.
    Handles various PDF formats with fallback strategies.
    """
    
    def __init__(self):
        self.extraction_stats = {}
    
    def extract_text_from_file(self, pdf_path: str) -> Dict[str, Any]:
        """
        Extract text from PDF file path.
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Dict with extracted text and metadata
        """
        with open(pdf_path, 'rb') as file:
            return self.extract_text_from_bytes(file.read())
    
    def extract_text_from_bytes(self, pdf_bytes: bytes) -> Dict[str, Any]:
        """
        Extract text from PDF bytes (for uploaded files).
        
        Args:
            pdf_bytes: PDF file as bytes
            
        Returns:
            Dict containing:
                - text: Extracted text
                - page_count: Number of pages
                - method: Extraction method used
                - tables: Extracted tables (if any)
        """
        
        # Try pdfplumber first (best for structure)
        try:
            result = self._extract_with_pdfplumber(pdf_bytes)
            if result and len(result['text']) > 100:
                return result
        except Exception as e:
            print(f"pdfplumber failed: {e}")
        
        # Fallback to PyPDF2
        try:
            result = self._extract_with_pypdf2(pdf_bytes)
            if result and len(result['text']) > 100:
                return result
        except Exception as e:
            print(f"PyPDF2 failed: {e}")
        
        # If all else fails, return empty
        return {
            'text': '',
            'page_count': 0,
            'method': 'failed',
            'tables': [],
            'error': 'Could not extract text from PDF'
        }
    
    def _extract_with_pdfplumber(self, pdf_bytes: bytes) -> Dict[str, Any]:
        """Extract using pdfplumber (best for structure and tables)"""
        
        text_parts = []
        tables_data = []
        page_count = 0
        
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            page_count = len(pdf.pages)
            
            for page_num, page in enumerate(pdf.pages, 1):
                # Extract text
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"\n--- Page {page_num} ---\n")
                    text_parts.append(page_text)
                
                # Extract tables
                tables = page.extract_tables()
                for table in tables:
                    if table:
                        tables_data.append({
                            'page': page_num,
                            'data': table
                        })
        
        return {
            'text': '\n'.join(text_parts),
            'page_count': page_count,
            'method': 'pdfplumber',
            'tables': tables_data
        }
    
    def _extract_with_pypdf2(self, pdf_bytes: bytes) -> Dict[str, Any]:
        """Extract using PyPDF2 (fallback method)"""
        
        text_parts = []
        page_count = 0
        
        pdf_reader = PyPDF2.PdfReader(BytesIO(pdf_bytes))
        page_count = len(pdf_reader.pages)
        
        for page_num, page in enumerate(pdf_reader.pages, 1):
            page_text = page.extract_text()
            if page_text:
                text_parts.append(f"\n--- Page {page_num} ---\n")
                text_parts.append(page_text)
        
        return {
            'text': '\n'.join(text_parts),
            'page_count': page_count,
            'method': 'pypdf2',
            'tables': []
        }
    
    def detect_document_type(self, pdf_text: str) -> str:
        """
        Detect if document is seller disclosure, inspection report, or other.
        
        Returns:
            'seller_disclosure', 'inspection_report', 'hoa_docs', or 'unknown'
        """
        
        text_lower = pdf_text.lower()
        
        # Seller disclosure indicators
        disclosure_keywords = [
            'seller disclosure',
            'transfer disclosure',
            'real property disclosure',
            'spds',
            'disclosure statement'
        ]
        if any(kw in text_lower for kw in disclosure_keywords):
            return 'seller_disclosure'
        
        # Inspection report indicators
        inspection_keywords = [
            'inspection report',
            'home inspection',
            'property inspection',
            'inspector',
            'internachi',
            'ashi'
        ]
        if any(kw in text_lower for kw in inspection_keywords):
            return 'inspection_report'
        
        # HOA documents
        hoa_keywords = [
            'homeowners association',
            'hoa',
            'cc&r',
            'covenants',
            'hoa dues',
            'association fees'
        ]
        if any(kw in text_lower for kw in hoa_keywords):
            return 'hoa_docs'
        
        return 'unknown'

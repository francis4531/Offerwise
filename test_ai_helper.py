#!/usr/bin/env python3
"""
Test script to verify AI helper initialization
Run this to see if API key is loaded
"""

import os
import sys

# Add app directory to path
sys.path.insert(0, '/home/claude/offerwise_render')

print("=" * 80)
print("🔍 TESTING AI HELPER INITIALIZATION")
print("=" * 80)

# Check environment
api_key = os.environ.get('ANTHROPIC_API_KEY')
print(f"\n1. Environment Check:")
print(f"   ANTHROPIC_API_KEY exists: {api_key is not None}")
if api_key:
    print(f"   ANTHROPIC_API_KEY length: {len(api_key)}")
    print(f"   ANTHROPIC_API_KEY starts with: {api_key[:15]}...")
else:
    print(f"   ANTHROPIC_API_KEY value: None")

# Try to import and initialize
print(f"\n2. Importing AI Helper:")
try:
    from analysis_ai_helper import AnalysisAIHelper
    print(f"   ✅ Import successful")
    
    print(f"\n3. Initializing AI Helper:")
    helper = AnalysisAIHelper()
    
    print(f"\n4. AI Helper Status:")
    print(f"   enabled: {helper.enabled}")
    print(f"   client: {helper.client is not None}")
    
    if helper.enabled:
        print(f"\n✅ SUCCESS: AI features are ENABLED!")
        print(f"   LLM calls will work!")
    else:
        print(f"\n❌ FAILED: AI features are DISABLED!")
        print(f"   LLM calls will NOT work!")
        print(f"   Reason: API key not set or initialization failed")
        
except Exception as e:
    print(f"   ❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)

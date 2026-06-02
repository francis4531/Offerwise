#!/bin/bash
# Pre-Deployment Verification Script
# Run this AFTER copying files but BEFORE committing to git

echo "🔍 PRE-DEPLOYMENT VERIFICATION"
echo "======================================"
echo ""

# Check if we're in the right directory
if [ ! -f "app_with_auth.py" ]; then
    echo "❌ ERROR: Not in offerwise_render directory!"
    echo "   Run this script from your repo root"
    exit 1
fi

echo "✅ In correct directory"
echo ""

# Backend files
echo "📦 Checking Backend Files..."
BACKEND_OK=true

files=("app_with_auth.py" "models.py" "migrate_preferences.py" "optimized_hybrid_cross_reference.py" "offerwise_intelligence.py")
for file in "${files[@]}"; do
    if [ -f "$file" ]; then
        echo "   ✅ $file"
    else
        echo "   ❌ $file MISSING!"
        BACKEND_OK=false
    fi
done

echo ""

# Frontend files
echo "📦 Checking Frontend Files..."
FRONTEND_OK=true

files=("static/settings.html" "static/onboarding.html" "static/app.html" "static/dashboard.html" "static/login.html")
for file in "${files[@]}"; do
    if [ -f "$file" ]; then
        echo "   ✅ $file"
    else
        echo "   ❌ $file MISSING!"
        FRONTEND_OK=false
    fi
done

echo ""

# Check JavaScript syntax in settings.html
echo "🔍 Checking JavaScript Syntax..."
if [ -f "static/settings.html" ]; then
    OPEN_BRACES=$(grep -o '{' static/settings.html | wc -l)
    CLOSE_BRACES=$(grep -o '}' static/settings.html | wc -l)
    
    if [ "$OPEN_BRACES" -eq "$CLOSE_BRACES" ]; then
        echo "   ✅ settings.html: $OPEN_BRACES open, $CLOSE_BRACES close (balanced)"
    else
        echo "   ❌ settings.html: $OPEN_BRACES open, $CLOSE_BRACES close (NOT balanced!)"
        FRONTEND_OK=false
    fi
fi

if [ -f "static/onboarding.html" ]; then
    OPEN_BRACES=$(grep -o '{' static/onboarding.html | wc -l)
    CLOSE_BRACES=$(grep -o '}' static/onboarding.html | wc -l)
    
    if [ "$OPEN_BRACES" -eq "$CLOSE_BRACES" ]; then
        echo "   ✅ onboarding.html: $OPEN_BRACES open, $CLOSE_BRACES close (balanced)"
    else
        echo "   ❌ onboarding.html: $OPEN_BRACES open, $CLOSE_BRACES close (NOT balanced!)"
        FRONTEND_OK=false
    fi
fi

echo ""

# Check backend routes
echo "🔍 Checking Backend Routes..."
ROUTES_OK=true

routes=("/api/user" "/api/user/preferences" "/api/consent/status" "/settings" "/onboarding")
for route in "${routes[@]}"; do
    if grep -q "@app.route('$route" app_with_auth.py; then
        echo "   ✅ $route"
    else
        echo "   ❌ $route MISSING!"
        ROUTES_OK=false
    fi
done

echo ""

# Final summary
echo "======================================"
echo "📊 VERIFICATION SUMMARY"
echo "======================================"
echo ""

if [ "$BACKEND_OK" = true ] && [ "$FRONTEND_OK" = true ] && [ "$ROUTES_OK" = true ]; then
    echo "✅ ALL CHECKS PASSED!"
    echo ""
    echo "Ready to deploy! Run:"
    echo "  git add -A"
    echo "  git commit -m 'v4.30: Complete deployment'"
    echo "  git push origin main"
    echo ""
    exit 0
else
    echo "❌ SOME CHECKS FAILED!"
    echo ""
    if [ "$BACKEND_OK" = false ]; then
        echo "   ❌ Backend files missing or incorrect"
    fi
    if [ "$FRONTEND_OK" = false ]; then
        echo "   ❌ Frontend files missing or syntax errors"
    fi
    if [ "$ROUTES_OK" = false ]; then
        echo "   ❌ Backend routes missing"
    fi
    echo ""
    echo "DO NOT DEPLOY YET! Fix issues above first."
    echo ""
    exit 1
fi

# OfferWise - Render.com Deployment

AI-powered real estate offer analysis platform.

## Quick Deploy

### Option 1: GitHub (Recommended)

```bash
# Push to GitHub
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR-USERNAME/offerwise.git
git push -u origin main

# Then in Render:
# 1. New + → Web Service
# 2. Connect repository
# 3. Deploy (auto-detected from render.yaml)
```

### Option 2: Manual

1. Go to https://render.com
2. New + → Web Service
3. Upload this folder
4. Configure:
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn app:app`
5. Deploy

---

## What's Inside

- **Flask API** - 3 endpoints (health, upload-pdf, analyze)
- **React App** - Complete user flow
- **Intelligence Core** - 100% tested property analysis
- **Static Files** - Landing page + app

---

## Features

✅ PDF upload and text extraction  
✅ Risk scoring (0-100)  
✅ Seller transparency analysis  
✅ Deal-breaker detection  
✅ Specific offer recommendations  
✅ Cost estimation

---

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
python app.py

# Visit http://localhost:10000
```

---

## API Endpoints

- `GET /` - Landing page
- `GET /app` - React application  
- `GET /api/health` - Health check
- `POST /api/upload-pdf` - Upload PDF, extract text
- `POST /api/analyze` - Generate property analysis

---

## Stack

- **Backend:** Flask + Python 3.11
- **Frontend:** React (vanilla, no build step)
- **PDF:** pdfplumber + PyPDF2
- **Deployment:** Render.com
- **Server:** Gunicorn

---

## Documentation

See `DEPLOY.md` for complete deployment instructions.

---

## Support

Issues? Check:
1. Render logs (dashboard → Logs tab)
2. Build output (look for errors)
3. Start command (should be `gunicorn app:app`)

---

## License

Proprietary - All rights reserved

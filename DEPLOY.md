# 🚀 DEPLOY OFFERWISE TO RENDER.COM

## Why Render?

✅ Python works out of the box (no account issues)  
✅ Simple deployment (no serverless complexity)  
✅ Free tier available  
✅ Automatic HTTPS  
✅ Easy logs and monitoring

---

## Step 1: Create Render Account

1. Go to https://render.com
2. Click "Get Started"
3. Sign up with GitHub, GitLab, or email
4. **No credit card required for free tier**

---

## Step 2: Deploy from GitHub (Recommended)

### A. Push to GitHub

```bash
cd offerwise_render

# Initialize git
git init
git add .
git commit -m "Initial commit"

# Create repo on GitHub, then:
git remote add origin https://github.com/YOUR-USERNAME/offerwise.git
git push -u origin main
```

### B. Deploy on Render

1. In Render dashboard, click **"New +"**
2. Select **"Web Service"**
3. Click **"Connect a repository"**
4. Select your `offerwise` repository
5. Render will auto-detect everything from `render.yaml`
6. Click **"Create Web Service"**

**Done!** Render will:
- Install dependencies
- Start the server
- Give you a URL like `https://offerwise.onrender.com`

---

## Step 3: Deploy Manually (Alternative)

If you don't want to use GitHub:

1. In Render dashboard, click **"New +"**
2. Select **"Web Service"**
3. Choose **"Deploy from Git"** → **"Public Git repository"**
4. Or upload files directly

Then configure:
- **Name:** offerwise
- **Runtime:** Python 3
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `gunicorn app:app`
- **Plan:** Free

Click **"Create Web Service"**

---

## Step 4: Test

After deployment (takes 2-5 minutes):

```bash
# Health check
curl https://YOUR-APP.onrender.com/api/health

# Should return:
# {"status": "healthy", "service": "offerwise-api", "version": "1.0.0"}
```

Open in browser:
```
https://YOUR-APP.onrender.com
```

---

## What You Get

### Automatic Features

✅ **HTTPS** - Automatic SSL certificate  
✅ **Logs** - View in Render dashboard  
✅ **Monitoring** - Built-in uptime monitoring  
✅ **Auto-deploy** - Push to GitHub = auto deploy  
✅ **Custom domain** - Add your own domain free

### Free Tier Limits

- ✅ 750 hours/month (enough for 24/7)
- ✅ Spins down after 15 min inactivity
- ✅ First request takes ~30 seconds (cold start)
- ✅ 512MB RAM
- ✅ Shared CPU

**Upgrade to Starter ($7/month) for:**
- No spin down
- More RAM
- Faster performance

---

## File Structure

```
offerwise_render/
├── app.py                    # Flask server
├── requirements.txt          # Python dependencies
├── render.yaml              # Render configuration
├── static/                  # Frontend files
│   ├── index.html          # Landing page
│   └── app.html            # React app
├── document_parser.py       # Intelligence
├── cross_reference_engine.py
├── risk_scoring_model.py
├── offerwise_intelligence.py
└── pdf_handler.py
```

---

## API Endpoints

All working at `https://YOUR-APP.onrender.com`:

- `GET /` - Landing page
- `GET /app` - React application
- `GET /api/health` - Health check
- `POST /api/upload-pdf` - PDF upload
- `POST /api/analyze` - Property analysis

---

## Troubleshooting

### Build Fails

Check logs in Render dashboard:
- Look for dependency install errors
- Check Python version compatibility

### App Won't Start

Check start command:
- Should be: `gunicorn app:app`
- Port is auto-assigned by Render

### 502 Bad Gateway

- App is starting up (wait 30 seconds)
- Or check logs for Python errors

---

## View Logs

In Render dashboard:
1. Click your service
2. Click "Logs" tab
3. See real-time logs

Or via CLI:
```bash
# Install Render CLI
npm install -g render-cli

# View logs
render logs
```

---

## Update Your App

### If using GitHub:
```bash
git add .
git commit -m "Update"
git push
```

Render auto-deploys!

### If manual:
1. Make changes
2. In Render dashboard, click "Manual Deploy"
3. Select branch
4. Deploy

---

## Custom Domain

Free tier includes custom domain:

1. In Render dashboard → Your service
2. Click "Settings"
3. Scroll to "Custom Domain"
4. Add your domain
5. Update DNS records (Render shows you how)

---

## Monitoring

Render dashboard shows:
- ✅ Uptime
- ✅ Request count
- ✅ Response times
- ✅ CPU/Memory usage
- ✅ Error rates

---

## Cost

**Free tier:**
- Perfect for testing and low traffic
- Spins down after 15 min inactivity
- First request takes ~30s (cold start)

**Starter ($7/month):**
- Always on (no spin down)
- Faster response times
- More RAM

**Standard ($25/month):**
- Better performance
- More resources
- Priority support

---

## Next Steps

1. ✅ Deploy to Render
2. ✅ Test all endpoints
3. ✅ Upload real PDFs
4. ✅ Generate analysis
5. ✅ Add custom domain (optional)
6. ✅ Launch! 🚀

---

## Support

- Docs: https://render.com/docs
- Status: https://status.render.com
- Community: https://community.render.com

---

**Much simpler than Vercel. Just works!** ✅

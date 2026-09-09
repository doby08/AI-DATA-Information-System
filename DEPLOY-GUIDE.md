# A.I.S.I.A.P. — Online Deployment Guide

Pwede mo i-deploy ang system online! Heto ang mga steps.

## Option A — Render.com (Recommended, FREE)

1. **I-upload ang project sa GitHub**
   - Create ka ng repository sa https://github.com (free)
   - I-upload ang files (except `venv/`, `data.db`, `app.log` — may .gitignore na)
2. **Create Web Service sa Render**
   - Pumunta ka sa https://render.com → **New** → **Web Service**
   - I-connect ang GitHub repo mo
3. **Render naga-detect ng automatic:**
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120` (galing sa Procfile)
4. **Environment Variables (i-add sa Render dashboard:**
   - `SECRET_KEY` = (make ka ng random long string)
   - `FLASK_DEBUG` = `0`
5. Click **Create Web Service** — pagkatapos ng 1-2 minutes, may URL na ang system!

## Option B — PythonAnywhere.com (FREE, simple)

1. Create free account → **Web** tab → **Add a new web app** → **Manual configuration** → Python 3.13
2. Sa **Code** section, i-upload ang files via **Files** tab
3. Sa **Virtualenv** → i-run: `pip install -r requirements.txt`
4. Sa **WSGI configuration file**, papalitan ang content ng:
   ```python
   from app import app as application
   init_db()  # optional: i-add import para i-run once
   ```
   *(kinakailangan i-add `from app import init_db` sa itaas)*
5. I-add ang env vars (SECRET_KEY, FLASK_DEBUG=0) sa **Web → Environment variables**
6. Click **Reload** — may URL na: `https://username.pythonanywhere.com`

## Option C — VPS / DigitalOcean ($4-6/month)

1. DigitalOcean Droplet (Ubuntu) → may full control
2. Steps: install Python, clone repo, `pip install -r requirements.txt`
3. I-run: `gunicorn app:app --bind 0.0.0.0:8000` (or use nginx + gunicorn + supervisor)

## IMPORTANT — Mgandang malaman

| Item | Bakit mahalaga |
|------|----------------|
| **`data.db` ang database** | Sa free platforms (Render free tier), nade-delete ang files kapag mag-restart — **i-download ang DB backup** (may button sa Settings page) lagi bago mag-restart, at i-auto-backup regularly |
| **`static/uploads/` avatars** | Parehong rin — temporary sa free tier. Backup mo. |
| **Gemini API key** | Nasubmit sa Settings page pagkatapos i-deploy. Ang app tumatawag sa Google Gemini — **kinakailangan ang internet at API key** |
| **Google Fonts CDN** | Gumagana sa online (may internet naman) |
| **SECRET_KEY** | I-bago mo from 'your_secret_key_here' — i-set ang env var |
| **`FLASK_DEBUG=0`** | I-set sa production — para hindi lumalabas ang debugger sa iba users |

## Local pa rin gumagana

Hindi nagbago ang local workflow:
```
python app.py        # runs sa http://127.0.0.1:5000 (debug ON siya pa rin)
```
Ang app naga-tanggap ng `PORT` environment variable (gagagamit ng 5000 kung wala) at `FLASK_DEBUG` (default 1).

## Good-to-know

- Ang whole system ay **walang paid dependencies** — Flask + standard library lang (ang Gemini call ay direct via urllib)
- Para may custom domain: i-buy ka ng domain at i-configure sa Render/PythonAnywhere settings
- HTTPS ay automatic na sa Render at PythonAnywhere (free SSL)
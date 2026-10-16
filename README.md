# Portfolio AI Chatbot 🤖

Lightweight AI chatbot for imran-akanda.netlify.app — answers visitor questions
about Imran's services/prices (Bengali + English) and sends lead alerts to Telegram.

Runs on ~10MB RAM (replaces the n8n workflow that kept OOM-crashing the 512MB free server).

## Run locally

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=your_key
export TELEGRAM_BOT_TOKEN=your_bot_token   # optional (lead alerts)
uvicorn app:app --port 8000
```

Open http://localhost:8000

## Env vars

| Var | Required | Default |
|---|---|---|
| `GEMINI_API_KEY` | yes | — |
| `TELEGRAM_BOT_TOKEN` | for lead alerts | — |
| `TELEGRAM_CHAT_ID` | no | `6249557228` |
| `MODEL_NAME` | no | `gemini-2.5-flash` |

## Deploy on Render (free)

- New → Web Service → from this repo
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app:app --host 0.0.0.0 --port $PORT`
- Add the env vars above. Health check path: `/healthz`

## Embed on the portfolio site

```html
<iframe src="https://YOUR-SERVICE.onrender.com" style="width:100%;height:600px;border:none;border-radius:12px;"></iframe>
```

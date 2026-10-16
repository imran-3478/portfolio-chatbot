"""Imran's portfolio AI chatbot — lightweight FastAPI service.

Replaces the n8n "Portfolio AI Chatbot" workflow (which kept OOM-crashing the
512MB Render free instance). Uses <10MB RAM vs n8n's 300MB+.

Flow per message:
  1. Keyword retrieval over the local knowledge base (zero API cost)
  2. Gemini generates the reply with KB context + short session history
  3. [LEAD] marker parsed -> Telegram alert to Imran (fire-and-forget)

Env vars:
  GEMINI_API_KEY      - Google AI Studio key (required)
  TELEGRAM_BOT_TOKEN  - Imran's Telegram bot token (required for lead alerts)
  TELEGRAM_CHAT_ID    - defaults to 6249557228 (Imran)
  MODEL_NAME          - defaults to gemini-2.5-flash
"""
import os
import re

import httpx
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from knowledge import KB_SECTIONS, SYSTEM_PROMPT

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6249557228").strip()
MODEL_NAME = os.environ.get("MODEL_NAME", "gemini-3.5-flash").strip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="Portfolio AI Chatbot")

# session_id -> [{"role": "user"|"model", "text": ...}]  (in-memory, free-tier OK)
sessions: dict[str, list[dict]] = {}


class ChatIn(BaseModel):
    message: str
    session_id: str = "default"


# ---------------- retrieval (pure python, no API cost) ----------------
def tokenize(s: str) -> list[str]:
    return re.findall(r"[\u0980-\u09FF\w]+", s.lower())


def retrieve(query: str, k: int = 3) -> list[str]:
    qt = tokenize(query)
    if not qt:
        return []
    scored = []
    for sec in KB_SECTIONS:
        st = tokenize(sec)
        if not st:
            continue
        overlap = sum(1 for t in qt if t in st)
        if overlap:
            scored.append((overlap / (1 + 0.05 * len(st)), sec))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:k]]


# ---------------- lead parsing (ported from n8n Extract Lead) ----------------
def extract_lead(text: str):
    m = re.search(r"\[LEAD\](.*?)\[/LEAD\]", text, re.S)
    if not m:
        return text, None
    reply = (text[: m.start()] + text[m.end():]).strip()
    parts = [(s or "").strip() for s in m.group(1).split("|")]
    parts += [""] * (5 - len(parts))
    clean = lambda s: "" if s == "-" else s
    name, phone, service, budget, notes = [clean(p) for p in parts[:5]]
    if not phone:  # same gate as the n8n "IF Lead Captured?" node
        return reply, None
    return reply, {
        "name": name, "phone": phone, "service": service,
        "budget": budget, "notes": notes,
    }


async def send_telegram_lead(lead: dict):
    if not TELEGRAM_BOT_TOKEN:
        return
    lines = ["🔥 <b>New Portfolio Lead!</b>", ""]
    if lead["name"]:
        lines.append(f"👤 <b>{lead['name']}</b>")
    lines.append(f"📞 <code>{lead['phone']}</code>")
    if lead["service"]:
        lines.append(f"🛠 Service: {lead['service']}")
    if lead["budget"]:
        lines.append(f"💰 Budget: {lead['budget']}")
    if lead["notes"]:
        lines.append(f"📝 {lead['notes']}")
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID,
                      "text": "\n".join(lines), "parse_mode": "HTML"},
            )
    except Exception:
        pass  # lead alert must never break the chat reply


# ---------------- gemini ----------------
async def ask_gemini(history: list[dict], context: str, user_msg: str) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{MODEL_NAME}:generateContent")
    contents = []
    for h in history[-8:]:
        contents.append({
            "role": "user" if h["role"] == "user" else "model",
            "parts": [{"text": h["text"]}],
        })
    prompt = ("Context from knowledge base:\n" + context +
              f"\n\nVisitor: {user_msg}")
    contents.append({"role": "user", "parts": [{"text": prompt}]})
    body = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        # thinkingBudget 0: hidden reasoning eats the token budget and truncates
        # replies (finishReason MAX_TOKENS with ~380 thought tokens). Not needed
        # for FAQ answers — disable for complete, faster, cheaper replies.
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1024,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(url, params={"key": GEMINI_API_KEY}, json=body)
        r.raise_for_status()
        data = r.json()
    # join ALL response parts — taking only parts[0] truncates mid-sentence
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts).strip()


FALLBACK = ("দুঃখিত, এই মুহূর্তে উত্তর দিতে পারছি না। "
            "সরাসরি WhatsApp-এ মেসেজ করো: +8801767462347")


# ---------------- routes ----------------
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(body: ChatIn, bg: BackgroundTasks):
    msg = (body.message or "").strip()
    if not msg:
        return JSONResponse({"reply": "কিছু লিখো, আমি সাহায্য করছি! 😊"})
    sid = (body.session_id or "default").strip()[:64]
    hist = sessions.setdefault(sid, [])

    if not GEMINI_API_KEY:
        return JSONResponse({"reply": FALLBACK})

    context = "\n".join(retrieve(msg)) or "No directly matching knowledge base entry."
    try:
        raw = await ask_gemini(hist, context, msg)
    except Exception:
        return JSONResponse({"reply": FALLBACK})

    reply, lead = extract_lead(raw)
    hist.append({"role": "user", "text": msg})
    hist.append({"role": "model", "text": reply})
    sessions[sid] = hist[-10:]

    if lead:
        bg.add_task(send_telegram_lead, lead)
    return {"reply": reply or FALLBACK}

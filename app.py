"""Imran's portfolio AI chatbot — lightweight FastAPI service.

Replaces the n8n "Portfolio AI Chatbot" workflow (which kept OOM-crashing the
512MB Render free instance). Uses <10MB RAM vs n8n's 300MB+.

Flow per message:
  1. Keyword retrieval over the local knowledge base (zero API cost)
  2. Gemini generates the reply with KB context + short session history,
     and may call TOOLS (agentic loop): generate_quote, search_projects,
     check_availability, request_meeting
  3. [LEAD] marker parsed -> Telegram alert to Imran (fire-and-forget)
  4. Meeting request -> Telegram alert to Imran (fire-and-forget)

Env vars:
  GEMINI_API_KEY      - Google AI Studio key (required)
  TELEGRAM_BOT_TOKEN  - Imran's Telegram bot token (required for lead alerts)
  TELEGRAM_CHAT_ID    - defaults to 6249557228 (Imran)
  MODEL_NAME          - defaults to gemini-2.5-flash
"""
import asyncio
import os
import re
import time as _time

import httpx
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from knowledge import KB_SECTIONS, SYSTEM_PROMPT, PRICING, PROJECTS, AVAILABILITY

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6249557228").strip()
MODEL_NAME = os.environ.get("MODEL_NAME", "gemini-3.5-flash").strip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="Portfolio AI Chatbot")

# session_id -> [{"role": "user"|"model", "text": ...}]  (in-memory, free-tier OK)
sessions: dict[str, list[dict]] = {}

# ---------------- reply cache ----------------
# FAQ bot: visitors ask the same questions repeatedly. Cache the raw Gemini
# response per normalized message (1h TTL) so repeats answer instantly AND
# don't burn the ~20 req/day free quota. Only used for fresh conversations
# (<=2 prior exchanges) where the reply doesn't depend on long history.
# extract_lead still runs on the cached raw text, so Telegram lead alerts
# keep working on cache hits.
_CACHE_TTL = 3600
_reply_cache: dict[str, tuple[float, str]] = {}


def _cache_key(msg: str) -> str:
    return " ".join(msg.lower().split())[:200]


def cache_get(msg: str) -> str | None:
    hit = _reply_cache.get(_cache_key(msg))
    if hit and _time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    return None


def cache_put(msg: str, raw: str) -> None:
    if len(_reply_cache) > 300:
        _reply_cache.clear()
    _reply_cache[_cache_key(msg)] = (_time.time(), raw)


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
        # trust_env=False: poisoned proxy env vars must never break alerts
        async with httpx.AsyncClient(timeout=15, trust_env=False) as c:
            await c.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID,
                      "text": "\n".join(lines), "parse_mode": "HTML"},
            )
    except Exception as e:
        print(f"[chatbot] telegram alert failed: {type(e).__name__}: {e}",
              flush=True)


async def send_telegram_meeting(meeting: dict):
    if not TELEGRAM_BOT_TOKEN:
        return
    lines = ["📅 <b>New Meeting Request!</b>", ""]
    if meeting.get("name"):
        lines.append(f"👤 <b>{meeting['name']}</b>")
    lines.append(f"📞 <code>{meeting.get('phone', '')}</code>")
    lines.append(f"🕗 Preferred: {meeting.get('preferred_time', '')}")
    if meeting.get("topic"):
        lines.append(f"💬 Topic: {meeting['topic']}")
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as c:
            await c.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID,
                      "text": "\n".join(lines), "parse_mode": "HTML"},
            )
    except Exception as e:
        print(f"[chatbot] telegram meeting alert failed: {type(e).__name__}: {e}",
              flush=True)


# ---------------- agentic tools (Gemini function calling) ----------------
# The model decides WHEN to call these — that is what makes the bot agentic
# rather than a hardcoded script. All data is deterministic (from knowledge.py)
# so the bot can never hallucinate prices, projects or availability.

FUNCTION_DECLARATIONS = [
    {
        "name": "generate_quote",
        "description": ("Get Imran's official price range, timeline and what's included "
                        "for a service. ALWAYS use this when the visitor asks about "
                        "price/cost — never quote from memory."),
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": ("One of: meta_pixel, flutter_app, ai_chatbot, "
                                    "ai_video_ad, website, brand_design, content_seo"),
                },
                "requirements": {
                    "type": "string",
                    "description": "Short summary of what the visitor needs",
                },
            },
            "required": ["service"],
        },
    },
    {
        "name": "search_projects",
        "description": ("Search Imran's past projects. Use when the visitor asks about "
                        "past work, portfolio or examples."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What kind of project the visitor is asking about",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "check_availability",
        "description": ("Get Imran's free hours for calls/meetings. Call this FIRST when "
                        "the visitor wants to talk, meet or call Imran."),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "request_meeting",
        "description": ("Send Imran a meeting request (he gets a Telegram alert and "
                        "personally confirms). Call ONLY when you have the visitor's "
                        "name, phone number AND preferred time — ask first if missing."),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Visitor's name"},
                "phone": {"type": "string", "description": "Visitor's phone/WhatsApp number"},
                "preferred_time": {"type": "string", "description": "When they want to talk, e.g. 'Friday 4pm'"},
                "topic": {"type": "string", "description": "What they want to discuss"},
            },
            "required": ["name", "phone", "preferred_time"],
        },
    },
]


def handle_generate_quote(args: dict) -> dict:
    service = (args.get("service") or "").strip()
    req = (args.get("requirements") or "").strip()
    info = PRICING.get(service)
    if not info:
        return {"ok": False,
                "error": f"Unknown service '{service}'. Valid: {', '.join(sorted(PRICING))}"}
    return {"ok": True, "service": info["label"], "price": info["price"],
            "timeline": info["timeline"], "includes": info["includes"],
            "requirements": req, "payment": "50% advance to start"}


def handle_search_projects(args: dict) -> dict:
    q = (args.get("query") or "").strip()
    qt = set(tokenize(q))
    scored = []
    for p in PROJECTS:
        hay = f"{p['name']} {p['desc']} {p['tech']} {p['highlights']}".lower()
        score = sum(1 for t in qt if t in hay)
        scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    top = [p for s, p in scored[:3] if s > 0] or PROJECTS[:3]
    return {"ok": True, "projects": top}


def handle_check_availability(args: dict) -> dict:
    return {"ok": True, "availability": AVAILABILITY["summary"],
            "note": AVAILABILITY["note"]}


def handle_request_meeting(args: dict) -> dict:
    name = (args.get("name") or "").strip()
    phone = (args.get("phone") or "").strip()
    when = (args.get("preferred_time") or "").strip()
    topic = (args.get("topic") or "").strip()
    missing = [k for k, v in (("name", name), ("phone", phone),
                              ("preferred_time", when)) if not v]
    if missing:
        return {"ok": False,
                "error": f"Missing: {', '.join(missing)}. Ask the visitor first."}
    return {"ok": True,
            "meeting": {"name": name, "phone": phone,
                        "preferred_time": when, "topic": topic},
            "message": "Meeting request sent to Imran. He will personally confirm the slot."}


TOOL_HANDLERS = {
    "generate_quote": handle_generate_quote,
    "search_projects": handle_search_projects,
    "check_availability": handle_check_availability,
    "request_meeting": handle_request_meeting,
}


# ---------------- gemini (agentic loop) ----------------
async def _gemini_post(c: httpx.AsyncClient, url: str, body: dict) -> dict:
    # trust_env=False: host proxy env vars (e.g. unparsable NO_PROXY entries)
    # must never break API calls. Patient retries: the free tier allows only a
    # few requests per minute, so back off respectfully on 429/5xx.
    backoffs = [5, 15, 30]
    last_err: Exception | None = None
    for attempt in range(1 + len(backoffs)):
        try:
            r = await c.post(url, params={"key": GEMINI_API_KEY}, json=body)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            last_err = e
            code = e.response.status_code
            if code in (429, 500, 502, 503) and attempt < len(backoffs):
                wait = backoffs[attempt]
                try:
                    wait = max(wait, int(e.response.headers.get("retry-after", 0)))
                except (TypeError, ValueError):
                    pass
                print(f"[chatbot] Gemini {code}, retrying in {wait}s "
                      f"(attempt {attempt + 1})", flush=True)
                await asyncio.sleep(wait)
                continue
            raise
    raise last_err  # pragma: no cover


async def ask_gemini(history: list[dict], context: str, user_msg: str):
    """Agentic loop: the model may call tools; results feed back for the
    final reply. Returns (reply_text, tools_used, meeting|None)."""
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
        "tools": [{"functionDeclarations": FUNCTION_DECLARATIONS}],
        # thinkingBudget 0: hidden reasoning eats the token budget and truncates
        # replies (finishReason MAX_TOKENS with ~380 thought tokens). Not needed
        # for FAQ answers — disable for complete, faster, cheaper replies.
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 700,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }
    meeting = None
    tools_used = False
    async with httpx.AsyncClient(timeout=90, trust_env=False) as c:
        # trust_env=False: poisoned proxy env vars must never break API calls
        for _round in range(3):  # tool rounds: call -> result -> final answer
            data = await _gemini_post(c, url, body)
            content = data["candidates"][0]["content"]
            parts = content.get("parts", [])
            body["contents"].append(content)  # keep the model turn for next round
            calls = [p.get("functionCall") for p in parts if p.get("functionCall")]
            if not calls:
                text = "".join(p.get("text", "") for p in parts).strip()
                return text, tools_used, meeting
            tools_used = True
            for fc in calls:
                name = fc.get("name", "")
                args = fc.get("args", {}) or {}
                handler = TOOL_HANDLERS.get(name)
                try:
                    result = handler(args) if handler else \
                        {"ok": False, "error": f"Unknown tool: {name}"}
                except Exception as e:
                    result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                print(f"[chatbot] tool call: {name} args={args} ok={result.get('ok')}",
                      flush=True)
                if name == "request_meeting" and result.get("ok"):
                    meeting = result["meeting"]
                # NOTE: the generateContent API only accepts roles "user"/"model"
                # (strictly alternating). Function results go as "user" —
                # "function" role gets an instant 400 rejection.
                body["contents"].append({
                    "role": "user",
                    "parts": [{"functionResponse": {"name": name,
                                                    "response": result}}],
                })
    # 3 rounds exhausted without a final answer — shouldn't happen
    print("[chatbot] agentic loop exhausted rounds", flush=True)
    return FALLBACK, tools_used, meeting


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
    # Fresh conversation? Serve repeat questions from cache (instant, no quota burn).
    # Never cache turns where tools ran (availability/meeting data is live).
    use_cache = len(hist) <= 2
    raw = cache_get(msg) if use_cache else None
    meeting = None
    if raw is None:
        try:
            raw, tools_used, meeting = await ask_gemini(hist, context, msg)
        except Exception as e:
            # logged to Render logs — silent fallback was hiding real bugs
            print(f"[chatbot] ask_gemini failed: {type(e).__name__}: {e}", flush=True)
            return JSONResponse({"reply": FALLBACK})
        if use_cache and not tools_used:
            cache_put(msg, raw)
    else:
        print("[chatbot] cache hit", flush=True)

    reply, lead = extract_lead(raw)
    hist.append({"role": "user", "text": msg})
    hist.append({"role": "model", "text": reply})
    sessions[sid] = hist[-10:]

    if lead:
        bg.add_task(send_telegram_lead, lead)
    if meeting:
        bg.add_task(send_telegram_meeting, meeting)
    return {"reply": reply or FALLBACK}

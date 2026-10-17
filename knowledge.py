"""Knowledge base + behaviour spec for Imran's portfolio chatbot.
Ported from the n8n "Portfolio AI Chatbot" workflow (Load Knowledge Base node + AI Agent system prompt).
"""

SYSTEM_PROMPT = """You are Imran's friendly portfolio assistant on his website imran-akanda.netlify.app. You help visitors learn about Imran's services and get in touch with him.

LANGUAGE: Always reply in the visitor's language. If they write in Bengali, reply in Bengali. If English, reply in English.

BEHAVIOUR: Be polite, warm, friendly and professional — like a well-mannered receptionist. Greet visitors warmly. If you learn their name, use it. Thank them for their interest. Never be rude, sarcastic or pushy, even if the visitor is blunt.

KNOWLEDGE: For ANY factual question about Imran's services, prices, timelines, projects, FAQ or contact info, ALWAYS use the "Context from knowledge base" provided with the visitor's message. Never invent prices, timelines or facts.

HONESTY: If the knowledge base has no answer, say honestly that you are not sure and offer Imran's WhatsApp +8801767462347 for the exact answer. A wrong answer is worse than saying "I don't know". Never guess.

OFF-TOPIC: If the visitor talks about something unrelated to Imran's services (chit-chat, jokes, random questions), do NOT shut them down. Reply briefly and naturally (1-2 sentences) to keep the conversation flowing, then gently steer back, e.g. "By the way, I can help with Imran's services — websites, apps, Meta Pixel setup, AI chatbots and more. Anything I can help you with there?" After two off-topic exchanges, politely redirect to services. Never discuss politics or religion beyond a one-line neutral acknowledgement.

LEAD CAPTURE: If the visitor shares their name AND phone/WhatsApp number, acknowledge warmly, and append EXACTLY one line at the very end of your reply in this format:
[LEAD]name|phone|service|budget|notes[/LEAD]
Use - for any unknown field. Example: [LEAD]Rahim|01700000000|Website|-|needs shop website[/LEAD]

STYLE: Keep answers short — 2 to 4 sentences. Be conversational, not robotic.

TOOLS (agentic abilities — use them, don't just talk about them):
- generate_quote: when the visitor asks the PRICE/COST of a specific service, ALWAYS call this tool instead of quoting from memory. Present the returned price, timeline and included items naturally in the visitor's language.
- search_projects: when the visitor asks about past work, portfolio or examples, call this tool and present the matching projects with their highlights.
- check_availability: when the visitor wants to talk / meet / call Imran, call this FIRST to see his free hours, then propose a slot.
- request_meeting: after check_availability, ask the visitor for their NAME, PHONE number and PREFERRED TIME. Call this tool ONLY when you have all three — never call it with missing info; ask first. After a successful call, tell the visitor Imran will personally confirm.
Never invent prices, project details or availability — always use the tools above. If a tool returns an error, tell the visitor honestly and offer WhatsApp +8801767462347.
"""

# --- Structured data for agentic tools (deterministic — never hallucinated) ---
# Prices in BDT. Imran: edit these if your pricing changes.
PRICING = {
    "meta_pixel": {"label": "Meta Pixel Setup", "price": "৳5,000–15,000",
                   "timeline": "2–3 days",
                   "includes": "Pixel installation, conversion event setup, ad tracking test"},
    "flutter_app": {"label": "Flutter Mobile App", "price": "from ৳30,000",
                    "timeline": "3–6 weeks",
                    "includes": "Android APK, push notifications, backend if needed"},
    "ai_chatbot": {"label": "AI Chatbot / Agent", "price": "from ৳15,000",
                   "timeline": "1–2 weeks",
                   "includes": "Gemini-powered replies, knowledge base, lead alerts"},
    "ai_video_ad": {"label": "AI Video Ad", "price": "৳5,000–10,000",
                    "timeline": "3–5 days",
                    "includes": "AI visuals, Bengali voiceover, ad copy"},
    "website": {"label": "Website", "price": "from ৳10,000",
                "timeline": "1–2 weeks",
                "includes": "Mobile-first design, Bengali/English, live deployment"},
    "brand_design": {"label": "Brand Design", "price": "৳5,000–15,000",
                     "timeline": "3–7 days",
                     "includes": "Logo, business cards, signboard/banner design"},
    "content_seo": {"label": "Content / SEO", "price": "from ৳3,000",
                    "timeline": "2–5 days",
                    "includes": "Bengali/English copy, product descriptions, ad creatives"},
}

PROJECTS = [
    {"name": "CNG Ride",
     "desc": "Ride-hailing app for Tangail",
     "tech": "Flutter, Firebase FCM, Supabase",
     "highlights": "Live driver tracking, push alerts, PIN login, admin panel",
     "link": "https://github.com/imran-3478/CNG-RIDE-"},
    {"name": "GushKhor",
     "desc": "Anti-corruption reporting platform",
     "tech": "Web, Supabase, Telegram bot",
     "highlights": "Private evidence handling, instant Telegram alerts on new reports",
     "link": "https://imran-3478.github.io/Gush.khor/"},
    {"name": "Portfolio Website",
     "desc": "Imran's own portfolio site (this website)",
     "tech": "HTML/CSS/JS, Cloudflare Pages",
     "highlights": "Bilingual, SEO-optimized, with this AI assistant built in",
     "link": "https://imran-akanda.pages.dev"},
    {"name": "Portfolio AI Chatbot",
     "desc": "AI receptionist for the portfolio",
     "tech": "FastAPI, Gemini, RAG + tools",
     "highlights": "Answers visitor questions, generates quotes, books meetings"},
    {"name": "LLM Bangla",
     "desc": "9-step visual guide to how AI thinks, in Bengali",
     "tech": "Single-file website + video",
     "highlights": "Bengali explainer video included"},
    {"name": "Haidar Cosmetics",
     "desc": "Complete brand kit + AI video ad for a cosmetics shop",
     "tech": "Brand design + AI video",
     "highlights": "Logo, cards, signboard, Bengali-voiceover video ad"},
    {"name": "AI Telegram Bot",
     "desc": "Personal AI assistant on Telegram",
     "tech": "Telegram Bot API, Gemini",
     "highlights": "Text + voice messages in Bengali/English"},
]

# Imran: edit your free hours here (Bangladesh time, Asia/Dhaka).
# The bot proposes these slots; every booking is confirmed by you personally
# via the Telegram meeting alert.
AVAILABILITY = {
    "summary": "Every day 8:00 PM – 10:00 PM, and Friday 3:00 PM – 10:00 PM (Bangladesh time).",
    "note": "Slots are indicative — every booking is confirmed by Imran personally.",
}

# Each section is one retrievable chunk.
# Bengali keywords in parentheses make retrieval work for Bengali queries too.
KB_SECTIONS = [
    # --- Services ---
    "Service: Mobile App Development — Flutter Android apps: APK builds, push notifications, live location tracking, offline-friendly design. (মোবাইল অ্যাপ অ্যান্ড্রয়েড)",
    "Service: Meta Pixel Setup — Pixel installation, conversion tracking and event setup so Facebook ads become measurable and profitable. (মেটা পিক্সেল ফেসবুক বিজ্ঞাপন)",
    "Service: AI Chatbots & Agents — Gemini-powered Telegram AI assistants with text and voice replies, for business or personal use. (এআই চ্যাটবট বট)",
    "Service: Brand Design & AI Video Ads — Logos, business cards, signboards, banners, plus AI-generated video ads with Bengali voiceover. (ব্র্যান্ড ডিজাইন লোগো ভিডিও বিজ্ঞাপন)",
    "Service: Website Development — Fast, mobile-first Bengali/English websites and landing pages, deployed live in days. (ওয়েবসাইট)",
    "Service: Content & SEO Writing — Bengali/English content: product descriptions, landing-page copy, ad creatives. (কনটেন্ট এসইও লেখা)",
    # --- Prices (BDT, starting prices — exact quote depends on requirements) ---
    "Price: Meta Pixel Setup costs ৳5,000–15,000. (দাম খরচ মেটা পিক্সেল)",
    "Price: Flutter Mobile App starts from ৳30,000. (দাম খরচ মোবাইল অ্যাপ)",
    "Price: AI Chatbot/Agent starts from ৳15,000. (দাম খরচ এআই চ্যাটবট)",
    "Price: AI Video Ad costs ৳5,000–10,000. (দাম খরচ ভিডিও বিজ্ঞাপন)",
    "Price: Website starts from ৳10,000. (দাম খরচ ওয়েবসাইট)",
    "Price: Brand Design costs ৳5,000–15,000. (দাম খরচ ব্র্যান্ড ডিজাইন)",
    "Price: Content/SEO starts from ৳3,000. (দাম খরচ কনটেন্ট এসইও)",
    # --- FAQ ---
    "How to start / contact: message on WhatsApp +8801767462347 (fastest reply). Email: emrankhan172@gmail.com. GitHub: @imran-3478. (যোগাযোগ হোয়াটসঅ্যাপ ফোন নম্বর ইমেইল)",
    "Imran works remotely with clients worldwide. Communicates in Bengali and English. (রিমোট দেশি বিদেশি ক্লায়েন্ট)",
    "Payment terms: 50% advance to start a project. (পেমেন্ট অগ্রিম টাকা)",
    "Timelines: Pixel setup 2–3 days; AI video ad 3–5 days; website 1–2 weeks; mobile app 3–6 weeks (depends on scope). (সময় কতদিন ডেলিভারি)",
    "Who is Imran: solo developer from Tangail, Bangladesh. Takes ideas from zero to a live product — app design, backend, push notifications, ad tracking, launch — all from his phone. (ইমরান কে পরিচয় টাঙ্গাইল)",
    # --- Projects ---
    "Project: CNG Ride — ride-hailing app for Tangail (Flutter, live driver tracking, push alerts). (সিএনজি রাইড প্রজেক্ট)",
    "Project: GushKhor — anti-corruption reporting platform with private evidence handling. (ঘুষখোর প্রজেক্ট)",
    "Project: LLM Bangla — 9-step visual guide to how AI thinks, in Bengali, with explainer video. (এলএলএম বাংলা প্রজেক্ট)",
    "Project: Haidar Cosmetics — complete brand kit (logo, cards, signboard) + AI video ad. (হায়দার কসমেটিক্স প্রজেক্ট)",
    "Project: Google Ads Full Course in Bengali (6 videos). (গুগল অ্যাডস কোর্স)",
    "Project: Data Analysis with Pandas — sales data analysis with Bengali explainer. (ডেটা অ্যানালাইসিস প্রজেক্ট)",
]

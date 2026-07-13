# Polaris AI

Polaris AI is an online, AI-powered property and real estate portal for buying and selling property. It solves the problem of slow, passive real estate marketplaces where sellers wait to be found and buyers sift through listings alone: on Polaris, sellers can actively discover and reach the buyers most likely to purchase their property, buyers get matched to listings that fit their preferences, and **Polaris AI** — an AI real estate agent and copilot — sits between both sides to help throughout the deal.

Polaris AI works as both an **agent** that can do the work for either side and a **copilot** that assists in real time, helping with:

- **Answering questions** for buyers and sellers
- **Buyer matching** — ranking the buyers most likely to purchase a property
- **Finding property to buy** for buyers
- **Outreach campaigns** — personalized seller-to-buyer outreach
- **Context-aware deal coaching** inside buyer–seller chat conversations

## Tech Stack

- **Frontend:** Next.js
- **Backend:** Python · Django REST Framework (DRF)
- **Database:** PostgreSQL + PostGIS
- **AI agents:** LangGraph, with models served via OpenRouter (Claude Sonnet / Opus / Haiku)
- **Observability & prompts:** Langfuse Cloud — LLM tracing + versioned system prompts (optional; set the `LANGFUSE_*` keys from `.env.example`, everything degrades to built-in prompts without them)
- **Async orchestration:** Inngest
- **Real-time messaging:** WebSockets
- _Auth, media storage, search, hosting, and payments: to be decided._

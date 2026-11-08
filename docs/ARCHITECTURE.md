# Architecture

SoukSaaS is a small team of cooperating agents behind a layered security gateway. All state lives in SQLite, and every decision is written to a hash-chained audit log. Everything financial is simulated.

## 1. System overview

```mermaid
flowchart TD
    Owner([Owner]) -->|dashboard intake + optional PDF| API[Owner API]
    API --> Parser[Text-only PDF parser]
    API --> PJ[(Product drafts and published snapshots)]
    PJ --> Page[Generated page + chat + feedback<br/>Jinja2 + vanilla JS]
    PJ --> QAGen[Q&A generation: draft]
    QAGen -->|owner validates| QA[(Approved Q&A base)]

    Prospect([Prospect]) --> Page
    Page -->|/api/chat, /api/pay, /api/rating| GWI{{Security Gateway<br/>check_input}}
    Page -->|/api/feedback/{slug}| FB[Feedback handler and deterministic scoring]
    GWI --> ORCH[Orchestrator<br/>envelopes, permissions, timeout, retries]

    ORCH --> Sales[Sales Agent<br/>Q&A first, LLM fallback]
    ORCH --> Pay[Payment Agent<br/>sandbox, approval gate]
    ORCH --> Ins[Insights and Strategy Agent<br/>analyze / opportunities]

    Sales --> QA
    Sales -->|payment_request| Pay
    Pay --> SB[Payments sandbox<br/>simulated]

    Sales --> GWO{{Gateway<br/>check_output / check_tool_call}}
    Pay --> GWO
    Ins --> GWO

    GWO --> DB[(SQLite shared state)]
    GWO --> AUD[(Hash-chained audit log)]
    DB --> AN[Analytics service<br/>deterministic KPIs, no LLM]
    FB --> DB
    DB --> IS[Evidence-backed product suggestions]
    IS --> OUT[Owner inbox + Markdown outbox]
    AUD --> API
    AN --> API[FastAPI owner API<br/>X-Owner-Token]
    API --> Dash[Streamlit dashboard<br/>Products, Inbox, Suggestions, KPIs, approvals, audit, evals]
    Dash --> Owner
```

## 2. Gateway layers

```mermaid
flowchart LR
    T[Text or tool call] --> L1[1. Deterministic filters<br/>length, normalization, patterns, Luhn]
    L1 -->|uncertain 0.3-0.7| L2[2. LLM classifier<br/>output validated, else escalate]
    L1 --> L3
    L2 --> L3[3. Permission matrix<br/>enforced by orchestrator]
    L3 --> L4[4. Output and tool-arg screening]
    L4 --> L6[6. Human in the loop<br/>held payments, escalations, drafts]
    L4 --> D[Decision: allow / sanitize / block / escalate]
```

## 3. Payment flow

```mermaid
sequenceDiagram
    actor P as Prospect
    participant W as Chat widget
    participant G as Gateway
    participant O as Orchestrator
    participant PA as Payment Agent
    participant S as Sandbox
    actor Ow as Owner (dashboard)
    participant A as Audit log

    P->>W: click "Pay (test ...)"
    W->>G: POST /api/pay {session_id, plan_id, test_token}
    G->>A: log input decision
    G->>O: payment_request
    O->>PA: handle (permission checked)
    PA->>PA: verify plan and amount vs product.json
    PA->>G: check_tool_call(payment.create) with fraud score
    alt risk >= medium or amount >= threshold
        PA->>A: payment_held
        PA-->>W: status held_for_approval
        Ow->>PA: approve or reject
        PA->>A: payment_approved / payment_rejected
        opt approved
            PA->>S: create(test_token)
        end
    else low risk
        PA->>S: create(test_token)
    end
    S-->>PA: succeeded / declined / pending
    PA->>A: payment_created
    PA-->>W: status and message
    PA->>O: payment_result to Sales and Analytics
```

## 4. Cahier agents to modules

| Cahier agent | Module |
|---|---|
| 1. Voice Sales Agent | `app/agents/sales.py` (text chat, voice simulated, optional browser speech) |
| 2. Customer Intelligence + 5. Business Strategy | `app/agents/insights.py` (modes analyze, opportunities) |
| 3. Payment Agent | `app/agents/payment.py` + `app/services/payments_sandbox.py` |
| 4. Financial Data Analyst | `app/services/analytics.py` (plain code, LLM only for optional narrative) |
| 6. Security and Fraud Detection | `app/gateway/` (filters, classifier, fraud) + `app/permissions.py` + `app/audit.py` |

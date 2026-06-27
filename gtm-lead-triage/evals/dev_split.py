"""Development split — for calibration and iteration ONLY.

CONSTRUCTION (same rules as holdout_v2):
  - Written BLIND to scoring rules (score_lead.py not referenced).
  - Company names do NOT contain industry/size keywords.
  - Real company domains, fictional contact names/emails.
  - Labels from senior-SDR-manager judgment.
  - ALL tuning and development measurement happens here.
  - holdout_v2 is measured exactly ONCE, at the end.

EXPECTED_SIGNALS (Phase E.2):
  Each lead carries an `expected_signals` list — the atomic signals a
  senior SDR would extract from the message. Each signal has:
    type:          intent | seniority | timeline | deal_size | fit | objection | ...
    value:         typed value (e.g., "high", "vp", "Q3", "200 seats")
    subject:       sender | third_party | company
    evidence_span: verbatim span from the message that justifies the signal

  This is ground truth for the EXTRACTION eval (separate from the tier label):
  - Is the subject attribution correct?
  - Is the evidence span a real substring of the message?

30 leads covering: clear tiers, adversarial cases, non-English,
good-company-but-wrong-intent, third-party attribution, thin input,
conflicting signals, draft-grounding traps.
"""

DEV_LEADS = [
    # ── HOT (4) ────────────────────────────────────────────────────────
    {
        "lead": {
            "email": "j.kim@palantir.com",
            "name": "Jason Kim",
            "company": "Palantir",
            "message": "I run our procurement team. We've shortlisted your platform and need to move to contract by end of Q3. 200 seats.",
            "source": "inbound_form",
        },
        "expected_tier": "hot",
        "expected_route": "ae_immediate",
        "rationale": "Procurement lead, explicit shortlist+contract+timeline+seat count. Business email at major defense-tech company.",
        "review": False,
        "expected_signals": [
            {"type": "seniority", "value": "manager", "subject": "sender", "evidence_span": "I run our procurement team"},
            {"type": "intent", "value": "high", "subject": "sender", "evidence_span": "need to move to contract"},
            {"type": "timeline", "value": "Q3", "subject": "sender", "evidence_span": "by end of Q3"},
            {"type": "deal_size", "value": "200 seats", "subject": "sender", "evidence_span": "200 seats"},
        ],
    },
    {
        "lead": {
            "email": "s.berg@netflix.com",
            "name": "Sandra Berg",
            "company": "Netflix",
            "message": "Budget approved, we need 80 licenses deployed before our next sprint cycle. Can we get a kickoff call tomorrow?",
            "source": "inbound_form",
        },
        "expected_tier": "hot",
        "expected_route": "ae_immediate",
        "rationale": "Budget approved + specific license count + immediate timeline. Business email at enterprise.",
        "review": False,
        "expected_signals": [
            {"type": "intent", "value": "high", "subject": "sender", "evidence_span": "Budget approved"},
            {"type": "deal_size", "value": "80 licenses", "subject": "sender", "evidence_span": "80 licenses"},
            {"type": "timeline", "value": "tomorrow", "subject": "sender", "evidence_span": "kickoff call tomorrow"},
        ],
    },
    {
        "lead": {
            "email": "c.mueller@basf.com",
            "name": "Christian Mueller",
            "company": "BASF",
            "message": "Wir haben das Budget freigegeben. Bitte senden Sie den Vertragsentwurf an unsere Rechtsabteilung.",
            "source": "inbound_form",
        },
        "expected_tier": "hot",
        "expected_route": "ae_immediate",
        "rationale": "German: 'Budget approved. Please send draft contract to our legal department.' Late-funnel buying signal, business email at major chemical company.",
        "review": True,
        "expected_signals": [
            {"type": "intent", "value": "high", "subject": "sender", "evidence_span": "Budget freigegeben"},
            {"type": "intent", "value": "high", "subject": "sender", "evidence_span": "Vertragsentwurf"},
        ],
    },
    {
        "lead": {
            "email": "a.park@samsung.com",
            "name": "Alice Park",
            "company": "Samsung",
            "message": "As VP of Digital Transformation, I'd like to schedule a technical review with your engineering team this week.",
            "source": "inbound_form",
        },
        "expected_tier": "hot",
        "expected_route": "ae_immediate",
        "rationale": "VP-level, explicit first-person role claim, immediate timeline, business email at major enterprise.",
        "review": False,
        "expected_signals": [
            {"type": "seniority", "value": "vp", "subject": "sender", "evidence_span": "As VP of Digital Transformation"},
            {"type": "intent", "value": "high", "subject": "sender", "evidence_span": "schedule a technical review"},
            {"type": "timeline", "value": "this week", "subject": "sender", "evidence_span": "this week"},
        ],
    },

    # ── WARM (6) ───────────────────────────────────────────────────────
    {
        "lead": {
            "email": "t.nguyen@shopify.com",
            "name": "Tina Nguyen",
            "company": "Shopify",
            "message": "We're evaluating tools for our merchant support team. Can you share a product overview?",
            "source": "inbound_form",
        },
        "expected_tier": "warm",
        "expected_route": "sdr_nurture",
        "rationale": "Business email at major SaaS, medium intent (evaluating), no urgency or budget signal.",
        "review": False,
        "expected_signals": [
            {"type": "intent", "value": "medium", "subject": "sender", "evidence_span": "evaluating tools"},
        ],
    },
    {
        "lead": {
            "email": "r.costa@itau.com.br",
            "name": "Roberto Costa",
            "company": "Itau Unibanco",
            "message": "Gostaríamos de agendar uma demonstração para o nosso time de compliance.",
            "source": "inbound_form",
        },
        "expected_tier": "warm",
        "expected_route": "sdr_nurture",
        "rationale": "Portuguese: 'We'd like to schedule a demo for our compliance team.' Business email at major LatAm bank, medium intent.",
        "review": True,
        "expected_signals": [
            {"type": "intent", "value": "medium", "subject": "sender", "evidence_span": "agendar uma demonstração"},
        ],
    },
    {
        "lead": {
            "email": "m.jones@lyft.com",
            "name": "Marcus Jones",
            "company": "Lyft",
            "message": "Saw your product mentioned in a Gartner report. Curious how it handles real-time data at our scale.",
            "source": "inbound_form",
        },
        "expected_tier": "warm",
        "expected_route": "sdr_nurture",
        "rationale": "Business email at tech company, Gartner-referral interest, scale question implies genuine evaluation. SDR follow-up.",
        "review": False,
        "expected_signals": [
            {"type": "intent", "value": "medium", "subject": "sender", "evidence_span": "Curious how it handles"},
            {"type": "fit", "value": "scale_question", "subject": "company", "evidence_span": "real-time data at our scale"},
        ],
    },
    {
        "lead": {
            "email": "k.tanaka@yahoo.com",
            "name": "Ken Tanaka",
            "company": "Rakuten",
            "message": "I'm the Director of Engineering at Rakuten. Looking at options for our CI/CD pipeline.",
            "source": "inbound_form",
        },
        "expected_tier": "warm",
        "expected_route": "sdr_nurture",
        "rationale": "Director at major company, medium intent, BUT free email. SDR should verify via business email.",
        "review": True,
        "expected_signals": [
            {"type": "seniority", "value": "director", "subject": "sender", "evidence_span": "I'm the Director of Engineering"},
            {"type": "intent", "value": "medium", "subject": "sender", "evidence_span": "Looking at options"},
        ],
    },
    {
        "lead": {
            "email": "h.schmidt@bmw.de",
            "name": "Hans Schmidt",
            "company": "BMW",
            "message": "Können Sie uns Referenzkunden aus der Automobilindustrie nennen?",
            "source": "inbound_form",
        },
        "expected_tier": "warm",
        "expected_route": "sdr_nurture",
        "rationale": "German: 'Can you name reference customers from the automotive industry?' Business email at BMW, medium intent (reference check = evaluation stage).",
        "review": True,
        "expected_signals": [
            {"type": "intent", "value": "medium", "subject": "sender", "evidence_span": "Referenzkunden"},
        ],
    },
    {
        "lead": {
            "email": "d.okonkwo@stripe.com",
            "name": "Diana Okonkwo",
            "company": "Stripe",
            "message": "Hi there.",
            "source": "inbound_form",
        },
        "expected_tier": "warm",
        "expected_route": "sdr_nurture",
        "rationale": "'Hi there' is zero intent, but business email at Stripe deserves an SDR follow-up to qualify.",
        "review": True,
        "expected_signals": [],  # No extractable signals — thin input
    },

    # ── COLD (7) ───────────────────────────────────────────────────────
    {
        "lead": {
            "email": "research@nyu.edu",
            "name": "Dr. Li Wei",
            "company": "NYU",
            "message": "I'm a professor studying enterprise software adoption patterns. Could I interview your product team for my research?",
            "source": "inbound_form",
        },
        "expected_tier": "cold",
        "expected_route": "marketing_nurture",
        "rationale": "Academic research, .edu domain. Not a buying motion.",
        "review": False,
        "expected_signals": [
            {"type": "seniority", "value": "ic", "subject": "sender", "evidence_span": "I'm a professor"},
            {"type": "intent", "value": "low", "subject": "sender", "evidence_span": "for my research"},
        ],
    },
    {
        "lead": {
            "email": "hello@tinyshop.io",
            "name": "",
            "company": "Tiny Shop",
            "message": "How much does it cost?",
            "source": "inbound_form",
        },
        "expected_tier": "cold",
        "expected_route": "marketing_nurture",
        "rationale": "Unknown company, no name, low-effort pricing question. Marketing drip.",
        "review": False,
        "expected_signals": [
            {"type": "intent", "value": "low", "subject": "sender", "evidence_span": "How much does it cost"},
        ],
    },
    {
        "lead": {
            "email": "admin@coinbase.com",
            "name": "Engineering Blog",
            "company": "Coinbase",
            "message": "We're publishing a technical blog post comparing data platforms. Can we include a quote from your team?",
            "source": "inbound_form",
        },
        "expected_tier": "cold",
        "expected_route": "marketing_nurture",
        "rationale": "Content request, not a buying motion. Good company but wrong intent direction (PR, not procurement).",
        "review": False,
        "expected_signals": [
            {"type": "intent", "value": "low", "subject": "sender", "evidence_span": "include a quote from your team"},
        ],
    },
    {
        "lead": {
            "email": "intern.2025@salesforce.com",
            "name": "Priya Sharma",
            "company": "Salesforce",
            "message": "Hi, I'm an intern and my manager asked me to look into tools like yours. Can you send documentation?",
            "source": "inbound_form",
        },
        "expected_tier": "cold",
        "expected_route": "marketing_nurture",
        "rationale": "Intern, no buying authority. Manager-delegated research. Send materials, don't spend AE/SDR time.",
        "review": False,
        "expected_signals": [
            {"type": "seniority", "value": "ic", "subject": "sender", "evidence_span": "I'm an intern"},
            {"type": "seniority", "value": "manager", "subject": "third_party", "evidence_span": "my manager asked me"},
            {"type": "intent", "value": "low", "subject": "sender", "evidence_span": "send documentation"},
        ],
    },
    {
        "lead": {
            "email": "procurement@va.gov",
            "name": "Federal Acquisition Office",
            "company": "Department of Veterans Affairs",
            "message": "This is a request for information (RFI) regarding data analytics platforms for potential federal procurement.",
            "source": "inbound_form",
        },
        "expected_tier": "cold",
        "expected_route": "marketing_nurture",
        "rationale": "Federal RFI = extremely long cycle, high friction. Respond to maintain presence but don't allocate AE time.",
        "review": True,
        "expected_signals": [
            {"type": "intent", "value": "low", "subject": "company", "evidence_span": "request for information (RFI)"},
        ],
    },
    {
        "lead": {
            "email": "dev42@protonmail.com",
            "name": "Anonymous Dev",
            "company": "",
            "message": "Does your API support GraphQL?",
            "source": "inbound_form",
        },
        "expected_tier": "cold",
        "expected_route": "marketing_nurture",
        "rationale": "Privacy email, no company, technical question = developer browsing. Marketing nurture.",
        "review": False,
        "expected_signals": [
            {"type": "intent", "value": "low", "subject": "sender", "evidence_span": "Does your API support GraphQL"},
        ],
    },
    {
        "lead": {
            "email": "t.wright@oracle.com",
            "name": "Tom Wright",
            "company": "Oracle",
            "message": "My team just signed a 3-year deal with a competitor. Keeping you on file for when it expires.",
            "source": "inbound_form",
        },
        "expected_tier": "cold",
        "expected_route": "marketing_nurture",
        "rationale": "Locked into competitor for 3 years. No near-term deal despite good company. Long-term nurture.",
        "review": False,
        "expected_signals": [
            {"type": "objection", "value": "competitor_locked", "subject": "company", "evidence_span": "signed a 3-year deal with a competitor"},
            {"type": "intent", "value": "low", "subject": "sender", "evidence_span": "Keeping you on file"},
        ],
    },

    # ── DISQUALIFIED (5) ──────────────────────────────────────────────
    {
        "lead": {
            "email": "bounce@10minutemail.com",
            "name": "",
            "company": "",
            "message": "test 123",
            "source": "inbound_form",
        },
        "expected_tier": "disqualified",
        "expected_route": "drop",
        "rationale": "Disposable email, empty fields, test message.",
        "review": False,
        "expected_signals": [],  # Nothing to extract from "test 123"
    },
    {
        "lead": {
            "email": "legal@zoom.us",
            "name": "Zoom Legal Team",
            "company": "Zoom",
            "message": "Pursuant to CCPA Section 1798.100, we request disclosure of all personal information collected about Zoom employees.",
            "source": "inbound_form",
        },
        "expected_tier": "disqualified",
        "expected_route": "drop",
        "rationale": "CCPA legal request, not a lead. Route to legal, not sales.",
        "review": False,
        "expected_signals": [
            {"type": "intent", "value": "legal_or_compliance", "subject": "company", "evidence_span": "CCPA Section 1798.100"},
        ],
    },
    {
        "lead": {
            "email": "spam@quickbucks.biz",
            "name": "Revenue Team",
            "company": "QuickBucks",
            "message": "Double your revenue with our proven AI system! Visit our website for a free consultation. Limited time offer, act now!",
            "source": "inbound_form",
        },
        "expected_tier": "disqualified",
        "expected_route": "drop",
        "rationale": "Outbound spam, selling to us.",
        "review": False,
        "expected_signals": [
            {"type": "intent", "value": "spam", "subject": "sender", "evidence_span": "Visit our website for a free consultation"},
        ],
    },
    {
        "lead": {
            "email": "unsubscribe@airbnb.com",
            "name": "Sarah Chen",
            "company": "Airbnb",
            "message": "I do not wish to receive any further emails from your company. Please remove my address from all lists.",
            "source": "inbound_form",
        },
        "expected_tier": "disqualified",
        "expected_route": "drop",
        "rationale": "Explicit opt-out. Business email at a valuable company is irrelevant.",
        "review": False,
        "expected_signals": [
            {"type": "intent", "value": "opt_out", "subject": "sender", "evidence_span": "do not wish to receive any further emails"},
        ],
    },
    {
        "lead": {
            "email": "security@apple.com",
            "name": "Apple Security",
            "company": "Apple",
            "message": "Our security team identified your domain in a phishing simulation. This is not a sales inquiry. Please confirm domain ownership.",
            "source": "inbound_form",
        },
        "expected_tier": "disqualified",
        "expected_route": "drop",
        "rationale": "Security/operational inquiry, explicitly not a sales inquiry. Route to security team, not sales.",
        "review": False,
        "expected_signals": [
            {"type": "intent", "value": "legal_or_compliance", "subject": "company", "evidence_span": "not a sales inquiry"},
        ],
    },

    # ── ADVERSARIAL: good company, wrong intent (calibration targets) ─
    {
        "lead": {
            "email": "events@atlassian.com",
            "name": "Atlassian Events",
            "company": "Atlassian",
            "message": "Would your company like to sponsor a booth at our annual Team conference? Early bird pricing available.",
            "source": "inbound_form",
        },
        "expected_tier": "cold",
        "expected_route": "marketing_nurture",
        "rationale": "Reverse intent: they're selling sponsorship TO us. Enterprise company but wrong direction. Pass to marketing/partnerships.",
        "review": False,
        "expected_signals": [
            {"type": "intent", "value": "reverse", "subject": "sender", "evidence_span": "sponsor a booth"},
        ],
    },
    {
        "lead": {
            "email": "press@dropbox.com",
            "name": "Dropbox Communications",
            "company": "Dropbox",
            "message": "We're issuing a press release about our new enterprise storage product. Would you like to be quoted as an industry peer?",
            "source": "inbound_form",
        },
        "expected_tier": "cold",
        "expected_route": "marketing_nurture",
        "rationale": "PR/media request, not a buying motion. Good company, wrong intent.",
        "review": False,
        "expected_signals": [
            {"type": "intent", "value": "reverse", "subject": "sender", "evidence_span": "quoted as an industry peer"},
        ],
    },
    {
        "lead": {
            "email": "campus@google.com",
            "name": "Google University Programs",
            "company": "Google",
            "message": "We're looking for tools to demo at our intern hackathon. Is there a free tier available?",
            "source": "inbound_form",
        },
        "expected_tier": "cold",
        "expected_route": "marketing_nurture",
        "rationale": "Enterprise company but intern/education program asking for free tier. No commercial value.",
        "review": False,
        "expected_signals": [
            {"type": "intent", "value": "low", "subject": "company", "evidence_span": "intern hackathon"},
            {"type": "fit", "value": "free_tier_request", "subject": "sender", "evidence_span": "free tier available"},
        ],
    },

    # ── E.2 STRESS: third-party attribution ───────────────────────────
    {
        "lead": {
            "email": "l.mora@dell.com",
            "name": "Lucia Mora",
            "company": "Dell",
            "message": "Our CTO mentioned your platform in a leadership meeting. I'm on the analytics team and wanted to learn more.",
            "source": "inbound_form",
        },
        "expected_tier": "warm",
        "expected_route": "sdr_nurture",
        "rationale": "CTO mention is a referral signal but the SENDER is IC on the analytics team. Business email at enterprise. SDR follow-up, but seniority is IC, not CTO.",
        "review": False,
        "expected_signals": [
            {"type": "seniority", "value": "c_level", "subject": "third_party", "evidence_span": "Our CTO mentioned"},
            {"type": "seniority", "value": "ic", "subject": "sender", "evidence_span": "I'm on the analytics team"},
            {"type": "intent", "value": "medium", "subject": "sender", "evidence_span": "wanted to learn more"},
        ],
    },
    {
        "lead": {
            "email": "n.rowe@cisco.com",
            "name": "Nadia Rowe",
            "company": "Cisco",
            "message": "My VP asked me to reach out about your data pipeline product. She wants a demo for our Q4 planning. I'm a program coordinator.",
            "source": "inbound_form",
        },
        "expected_tier": "warm",
        "expected_route": "sdr_nurture",
        "rationale": "VP-delegated outreach. Sender is a coordinator (IC), not the VP. Demo request is real but the buyer isn't in the room. SDR should loop in the VP.",
        "review": False,
        "expected_signals": [
            {"type": "seniority", "value": "vp", "subject": "third_party", "evidence_span": "My VP asked me"},
            {"type": "seniority", "value": "ic", "subject": "sender", "evidence_span": "I'm a program coordinator"},
            {"type": "intent", "value": "high", "subject": "third_party", "evidence_span": "She wants a demo"},
            {"type": "timeline", "value": "Q4", "subject": "third_party", "evidence_span": "Q4 planning"},
        ],
    },

    # ── E.2 STRESS: thin input ────────────────────────────────────────
    {
        "lead": {
            "email": "a.novak@plaid.com",
            "name": "",
            "company": "",
            "message": "",
            "source": "inbound_form",
        },
        "expected_tier": "cold",
        "expected_route": "marketing_nurture",
        "rationale": "Email-only submission. Business email at Plaid, but zero fields and zero message. Nothing to act on. Auto-reply asking for context.",
        "review": True,
        "expected_signals": [],  # Must NOT over-extract from an empty message
    },

    # ── E.2 STRESS: conflicting / multi-intent ───────────────────────
    {
        "lead": {
            "email": "p.hayes@workday.com",
            "name": "Patrick Hayes",
            "company": "Workday",
            "message": "We're very interested in your platform, but our legal team flagged a GDPR concern with your data processing addendum. Can you send an updated DPA before we proceed?",
            "source": "inbound_form",
        },
        "expected_tier": "warm",
        "expected_route": "sdr_nurture",
        "rationale": "Multi-intent: genuine buying interest + compliance blocker. The lead wants to buy but can't proceed until legal is satisfied. SDR should engage and route the DPA request to legal.",
        "review": True,
        "expected_signals": [
            {"type": "intent", "value": "high", "subject": "sender", "evidence_span": "very interested in your platform"},
            {"type": "objection", "value": "compliance_blocker", "subject": "company", "evidence_span": "legal team flagged a GDPR concern"},
            {"type": "intent", "value": "high", "subject": "sender", "evidence_span": "before we proceed"},
        ],
    },

    # ── E.2 STRESS: draft-grounding traps ─────────────────────────────
    # Leads where enrichment will be sparse or low-confidence.
    # A tempting personalization ("I saw you're in <industry>") would be
    # UNGROUNDED if the industry came from a guess, not a real source.
    # Correct behavior: generic draft, no fabricated facts.
    {
        "lead": {
            "email": "contact@novafirm.xyz",
            "name": "Alex Reyes",
            "company": "Nova Firm",
            "message": "We'd like to explore your platform for our operations team. Can we set up a call?",
            "source": "inbound_form",
        },
        "expected_tier": "warm",
        "expected_route": "sdr_nurture",
        "rationale": "Unknown company (no PDL data, no industry signal). Medium intent. SDR should follow up but draft must NOT fabricate company details.",
        "review": False,
        "expected_signals": [
            {"type": "intent", "value": "medium", "subject": "sender", "evidence_span": "explore your platform"},
        ],
        "grounding_trap": True,  # Flag: draft must not reference industry/size since enrichment is empty
    },
    {
        "lead": {
            "email": "info@steelbridge.co",
            "name": "",
            "company": "Steelbridge",
            "message": "Interested in a demo.",
            "source": "inbound_form",
        },
        "expected_tier": "warm",
        "expected_route": "sdr_nurture",
        "rationale": "Unknown company, no name, brief message. Medium intent. Draft must be generic — we know nothing about this company except the domain.",
        "review": True,
        "expected_signals": [
            {"type": "intent", "value": "medium", "subject": "sender", "evidence_span": "Interested in a demo"},
        ],
        "grounding_trap": True,
    },
]

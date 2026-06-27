"""Development split — for calibration and iteration ONLY.

CONSTRUCTION (same rules as holdout_v2):
  - Written BLIND to scoring rules (score_lead.py not referenced).
  - Company names do NOT contain industry/size keywords.
  - Real company domains, fictional contact names/emails.
  - Labels from senior-SDR-manager judgment.
  - ALL tuning and development measurement happens here.
  - holdout_v2 is measured exactly ONCE, at the end.

25 leads covering: clear tiers, adversarial cases, non-English,
good-company-but-wrong-intent (the calibration target).
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
    },

    # ── DISQUALIFIED (4) ──────────────────────────────────────────────
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
    },
]

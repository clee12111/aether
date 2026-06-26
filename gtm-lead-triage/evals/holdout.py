"""Held-out validation set — 10 leads NOT used to design the Phase 1.6 rules.

These leads were written AFTER the 4 rules were finalized. Running the eval on
them is the honest signal; the original 22 are now partly "seen" data.

Labels are human-judgment, same as the golden set.
"""

HOLDOUT_LEADS = [
    {
        "lead": {
            "email": "cfo@nationwide-ins.com",
            "name": "Patricia Walsh, CFO",
            "company": "Nationwide Insurance Group",
            "message": "We need to buy 200 licenses by end of month. Who do I talk to?",
            "source": "inbound_form",
        },
        "expected_tier": "hot",
        "expected_route": "ae_immediate",
        "rationale": "C-level at enterprise insurer, business email, explicit purchase intent with volume and timeline.",
        "review": False,
    },
    {
        "lead": {
            "email": "h.wong@horizonanalytics.com",
            "name": "Helen Wong, Product Manager",
            "company": "Horizon Analytics Partners",
            "message": "We're interested in integrating your API into our analytics pipeline.",
            "source": "inbound_form",
        },
        "expected_tier": "warm",
        "expected_route": "sdr_nurture",
        "rationale": "Manager at mid-market analytics firm, business email, medium intent (interested).",
        "review": False,
    },
    {
        "lead": {
            "email": "intern@localcoffee.co",
            "name": "Jamie Torres, Intern",
            "company": "LocalCoffee Co",
            "message": "Just curious about your product.",
            "source": "inbound_form",
        },
        "expected_tier": "cold",
        "expected_route": "marketing_nurture",
        "rationale": "Intern at small company, business email but very low seniority and low intent.",
        "review": False,
    },
    {
        "lead": {
            "email": "nobody99@hotmail.com",
            "name": "",
            "company": "",
            "message": "asdfgh",
            "source": "inbound_form",
        },
        "expected_tier": "disqualified",
        "expected_route": "drop",
        "rationale": "Free email, no name, no company, keyboard-mash message. Junk.",
        "review": False,
    },
    {
        "lead": {
            "email": "r.patel@meridian-global.com",
            "name": "Raj Patel, VP of Strategy",
            "company": "Meridian Global Industries",
            "message": "Could you send me more info?",
            "source": "inbound_form",
        },
        "expected_tier": "warm",
        "expected_route": "sdr_nurture",
        "rationale": "VP at enterprise, business email, great profile — but 'info' is low intent. Worth an SDR touch to qualify.",
        "review": False,
    },
    {
        "lead": {
            "email": "ops@westlake.com",
            "name": "Dana Kim, Operations Lead",
            "company": "Westlake Solutions",
            "message": "Please unsubscribe me from all future emails. Thank you.",
            "source": "inbound_form",
        },
        "expected_tier": "disqualified",
        "expected_route": "drop",
        "rationale": "Explicit opt-out. Business email and decent profile are irrelevant — must honor the request.",
        "review": False,
    },
    {
        "lead": {
            "email": "solo.dev@gmail.com",
            "name": "Chris Founder",
            "company": "My Side Project",
            "message": "I'm a solo founder looking at tools for my weekend project.",
            "source": "inbound_form",
        },
        "expected_tier": "cold",
        "expected_route": "marketing_nurture",
        "rationale": "Free email, solo founder of a side project. 'Founder' title is c_level but at a trivial scale. Low intent.",
        "review": True,
    },
    {
        "lead": {
            "email": "d.eng@atlas-cloud.com",
            "name": "Derek Eng, Director of Engineering",
            "company": "Atlas Cloud Solutions",
            "message": "We need a demo ASAP. Budget approved for Q3.",
            "source": "inbound_form",
        },
        "expected_tier": "hot",
        "expected_route": "ae_immediate",
        "rationale": "Director at cloud company, business email, urgent demo request with approved budget.",
        "review": False,
    },
    {
        "lead": {
            "email": "sales@biz-consulting.com",
            "name": "Growth Team",
            "company": "BizConsulting Pro",
            "message": "Check out our amazing AI consulting services! Limited time offer — visit our website for free consultation!",
            "source": "inbound_form",
        },
        "expected_tier": "disqualified",
        "expected_route": "drop",
        "rationale": "Spam from a business email. Selling their own services, not buying ours.",
        "review": False,
    },
    {
        "lead": {
            "email": "n.silva@cityhealthcare.org",
            "name": "Nina Silva, Manager",
            "company": "City Healthcare Partners",
            "message": "Considering your platform for patient scheduling across our clinics.",
            "source": "inbound_form",
        },
        "expected_tier": "warm",
        "expected_route": "sdr_nurture",
        "rationale": "Manager at healthcare organization, business email, medium intent (considering), specific use case.",
        "review": False,
    },
]

"""
Generate synthetic PDL cassettes for holdout_v2 test leads.

Prints a JSON dict (email → {status_code, body}) to stdout.
Merge into gtm_triage/enrichment/cache/pdl_cassettes.json.

Design rules (matching actual PDL v5 person/enrich behaviour):
  - Real business domains  → 200 hit, likelihood=7 (domain match, no person match).
    company fields populated; job_title / job_title_role / job_title_levels omitted
    (PDL can't resolve the fictional person, so no title inference is reliable).
  - full_name / first_name / last_name derived from the email local-part so the
    cassette looks like a plausible partial match on the domain.
  - 404 domains → standard not-found body.
"""

import json

# ── helpers ──────────────────────────────────────────────────────────────────

_404_BODY = {
    "status": 404,
    "error": {
        "type": "not_found",
        "message": "No records were found matching your request",
    },
}


def _404():
    return {"status_code": 404, "body": _404_BODY}


def _hit(first: str, last: str, company_name: str, company_size: str,
         company_industry: str, industry: str) -> dict:
    """Return a 200 cassette entry.  No job_title / title_levels — fictional person."""
    full = f"{first} {last}".lower() if last else first.lower()
    return {
        "status_code": 200,
        "body": {
            "status": 200,
            "likelihood": 7,
            "data": {
                "full_name": full,
                "first_name": first.lower(),
                "last_name": last.lower() if last else None,
                "job_title": None,
                "job_title_role": None,
                "job_title_levels": None,
                "job_company_name": company_name,
                "job_company_size": company_size,
                "job_company_industry": company_industry,
                "industry": industry,
                "work_email": True,
            },
        },
    }


# ── cassette table ────────────────────────────────────────────────────────────
# Each tuple: (email, first, last, company_name, company_size, company_industry, industry)
# OR the string "404" to emit a not-found response.

_HITS = [
    # stripe.com — fintech / financial services
    ("m.tanaka@stripe.com",       "mei",      "tanaka",   "Stripe",          "5001-10000", "financial services",                    "financial services"),
    # deloitte.com — Big 4 / management consulting
    ("r.okafor@deloitte.com",     "remi",     "okafor",   "Deloitte",        "10001+",     "management consulting",                  "management consulting"),
    # pfizer.com — pharma
    ("c.wade@pfizer.com",         "catherine","wade",     "Pfizer",          "10001+",     "pharmaceuticals",                       "pharmaceuticals"),
    # siemens.com — industrial / tech
    ("j.fischer@siemens.com",     "jonas",    "fischer",  "Siemens",         "10001+",     "industrial automation",                  "industrial automation"),
    # visa.com — payments
    ("a.reeves@visa.com",         "amanda",   "reeves",   "Visa",            "10001+",     "financial services",                    "financial services"),
    # hubspot.com — SaaS
    ("l.chen@hubspot.com",        "lily",     "chen",     "HubSpot",         "1001-5000",  "computer software",                     "computer software"),
    # adobe.com — software
    ("n.patel@adobe.com",         "nisha",    "patel",    "Adobe",           "10001+",     "computer software",                     "computer software"),
    # nubank.com.br — neobank
    ("f.moreira@nubank.com.br",   "felipe",   "moreira",  "Nubank",          "1001-5000",  "financial services",                    "financial services"),
    # twilio.com — cloud comms
    ("s.wright@twilio.com",       "sam",      "wright",   "Twilio",          "1001-5000",  "information technology and services",   "information technology and services"),
    # toyota.co.jp — automotive
    ("k.yamamoto@toyota.co.jp",   "kenji",    "yamamoto", "Toyota",          "10001+",     "automotive",                            "automotive"),
    # merck.com — pharma
    ("d.santos@merck.com",        "diego",    "santos",   "Merck",           "10001+",     "pharmaceuticals",                       "pharmaceuticals"),
    # lemonade.com — insurtech
    ("e.brook@lemonade.com",      "emily",    "brook",    "Lemonade",        "201-500",    "insurance",                             "insurance"),
    # databricks.com — data/AI
    ("m.lee@databricks.com",      "min",      "lee",      "Databricks",      "1001-5000",  "computer software",                     "computer software"),
    # nvidia.com — chips/AI (opt-out lead — still gets enrichment data)
    ("hr@nvidia.com",             "tara",     "lin",      "NVIDIA",          "10001+",     "semiconductors",                        "semiconductors"),
    # plaid.com — fintech
    ("a.novak@plaid.com",         "a",        "novak",    "Plaid",           "201-500",    "financial services",                    "financial services"),
    # boeing.com — aerospace
    ("l.garcia@boeing.com",       "lucia",    "garcia",   "Boeing",          "10001+",     "aviation & aerospace",                  "aviation & aerospace"),
    # uber.com — ride-sharing
    ("r.kim@uber.com",            "rachel",   "kim",      "Uber",            "10001+",     "internet",                              "internet"),
    # robinhood.com — fintech
    ("p.wong@robinhood.com",      "peter",    "wong",     "Robinhood",       "1001-5000",  "financial services",                    "financial services"),
    # mongodb.com — database software (events alias — no person resolution)
    ("events@mongodb.com",        "events",   "team",     "MongoDB",         "1001-5000",  "computer software",                     "computer software"),
    # figma.com — design tool
    ("intern.2026@figma.com",     "jordan",   "xu",       "Figma",           "501-1000",   "computer software",                     "computer software"),
    # jpmorgan.com — banking (DSAR alias — still resolves company)
    ("compliance@jpmorgan.com",   "compliance","team",    "JPMorgan Chase",  "10001+",     "banking",                               "banking"),
]

_MISSES = [
    # free / privacy email providers
    "p.kumar@gmail.com",
    "alex.z@outlook.com",
    "freelancer99@protonmail.com",
    # disposable email providers
    "x9z@yopmail.com",
    "bot@tempmail.com",
    "7ksdf@guerrillamail.com",
    # invalid / bounce domain
    "return@mailer-daemon.invalid",
    # spam / biz domain not in PDL
    "offers@bestdeals247.biz",
    # micro/local business — not indexed
    "info@localbakery.co",
    # .edu — PDL coverage is poor
    "student42@mit.edu",
    # .gov / municipal — not indexed
    "webmaster@ci.portland.or.us",
    # nonprofit — not indexed
    "volunteer@redcross.org",
    # government regulator — not indexed
    "enquiries@oscr.scot",
    # gmail (CTO impersonation claim — irrelevant, still 404)
    "cto.office@gmail.com",
]


# ── assemble ─────────────────────────────────────────────────────────────────

def main() -> None:
    cassettes: dict = {}

    for email, first, last, company, size, co_industry, industry in _HITS:
        cassettes[email] = _hit(first, last, company, size, co_industry, industry)

    for email in _MISSES:
        cassettes[email] = _404()

    print(json.dumps(cassettes, indent=2))


if __name__ == "__main__":
    main()

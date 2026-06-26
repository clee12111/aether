from __future__ import annotations

from gtm_triage.tools.base import BaseTool


_TEMPLATES = {
    "hot": {
        "subject": "Let's schedule a demo — {company}",
        "body": (
            "Hi {name},\n\n"
            "Thanks for reaching out. Based on your interest, I'd love to set up a "
            "personalized demo for {company}.\n\n"
            "Would any time this week work for a quick 30-minute call?\n\n"
            "Best,\nThe Team"
        ),
    },
    "warm": {
        "subject": "Resources for {company} — next steps",
        "body": (
            "Hi {name},\n\n"
            "Thanks for your interest. I've put together some resources that might "
            "be helpful for {company} in the {industry} space.\n\n"
            "Happy to answer any questions when you're ready to dive deeper.\n\n"
            "Best,\nThe Team"
        ),
    },
    "cold": {
        "subject": "Welcome, {name} — here's what we do",
        "body": (
            "Hi {name},\n\n"
            "Thanks for reaching out. Here's a quick overview of how we help "
            "companies like {company}.\n\n"
            "Feel free to explore our resources, and let us know if you have questions.\n\n"
            "Best,\nThe Team"
        ),
    },
    "disqualified": {
        "subject": "",
        "body": "",
    },
}


class DraftOutreachTool(BaseTool):
    @property
    def name(self) -> str:
        return "draft_outreach"

    def run(self, args: dict) -> dict:
        email = args.get("email", "")
        name = args.get("name", "there")
        company = args.get("company", "your company")
        enrichment = args.get("enrichment", {})
        tier = args.get("tier", "cold")

        industry = enrichment.get("industry", "your industry")

        template = _TEMPLATES.get(tier, _TEMPLATES["cold"])
        subject = template["subject"].format(name=name, company=company, industry=industry)
        body = template["body"].format(name=name, company=company, industry=industry)

        return {
            "email": email,
            "name": name,
            "subject": subject,
            "body": body,
            "status": "draft",
        }

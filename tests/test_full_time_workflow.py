import unittest

from lead import Lead
from lead_scoring import disqualifying_reason, score_lead
from config import load_settings
from email_template import render_email, validate_full_time_email


class FullTimeWorkflowTests(unittest.TestCase):
    def test_internship_lead_is_disqualified(self):
        lead = Lead(
            title="University Recruiter",
            role_title="Data Analyst Intern",
            company_name="Example Co",
            company_industry="software analytics",
            country="United States",
        )

        self.assertIn("Internship", disqualifying_reason(lead))

    def test_full_time_email_has_no_internship_wording(self):
        settings = load_settings()
        lead = Lead(
            first_name="Alex",
            full_name="Alex Rivera",
            title="Technical Recruiter",
            role_title="Data Engineer",
            company_name="Example Data",
            company_industry="software analytics",
            country="United States",
            source_tier="tier_3_remote_us_full_time",
            email="alex@example.com",
            email_status="verified",
            reason_for_outreach="Example Data looked relevant to full-time data engineering work.",
        )

        subject, body = render_email(lead, settings)

        self.assertEqual(validate_full_time_email(subject, body), [])
        self.assertNotRegex(f"{subject}\n{body}".lower(), r"\bintern(ship)?s?\b")

    def test_full_time_score_can_reach_send_threshold(self):
        settings = load_settings()
        lead = Lead(
            title="Technical Recruiter",
            role_title="Data Engineer",
            company_name="Example AI Analytics",
            company_industry="artificial intelligence software analytics",
            company_size="201",
            country="United States",
            source_tier="tier_3_remote_us_full_time",
            email="alex@example.com",
            email_status="verified",
            reason_for_outreach="Example AI Analytics is hiring full-time data engineers for Python, SQL, cloud, and AI work.",
        )

        score, _ = score_lead(lead, settings)

        self.assertGreaterEqual(score, settings.min_score_to_send)


if __name__ == "__main__":
    unittest.main()

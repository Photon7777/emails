"""Regression tests for UMD TA/RA draft personalization safety."""

from types import SimpleNamespace
import unittest

from umd_ta_ra_workflow import (
    UmdContact,
    clean_personalization_text,
    render_umd_email,
    select_best_personalization,
    validate_umd_draft,
)


BAD_CARD_TEXT = (
    "Tyser Professor of Management Science Decision, Operations and Information "
    "Technologies Social Impact Contact 301-405-2302 ganand@umd.edu "
    "4303 Van Munching Hall T."
)


def make_settings():
    return SimpleNamespace(
        sender_name="Sai Praneeth Kathi Moksha",
        sender_email="lakhrav@terpmail.umd.edu",
        sender_linkedin="https://www.linkedin.com/in/sai-praneeth-kmg",
        sender_portfolio="https://sai-praneeth-portfolio.netlify.app/",
    )


def make_contact(**overrides):
    base = dict(
        name="Tania Babina",
        email="professor@umd.edu",
        title="Professor",
        department="Decision, Operations and Information Technologies",
        phone="301-405-2302",
        office="4303 Van Munching Hall",
        research_interests="",
        courses_taught="",
        lab_name="",
        profile_url="https://www.rhsmith.umd.edu/directory",
        source_url="https://www.rhsmith.umd.edu/directory",
        research_or_course_area=BAD_CARD_TEXT,
        opportunity_type="General",
        semester="General",
        fit_score=85,
        fit_reason="Relevant department",
        personalization_notes="",
        personalization_context="",
        personalization_source="Fallback",
        personalization_confidence="Low",
        raw_text=BAD_CARD_TEXT,
    )
    base.update(overrides)
    return UmdContact(**base)


class UmdPersonalizationTests(unittest.TestCase):
    def test_bad_faculty_card_text_is_rejected(self):
        self.assertIsNone(clean_personalization_text(BAD_CARD_TEXT))

    def test_department_fallback_ignores_raw_card_text(self):
        contact = make_contact()
        text, source, confidence = select_best_personalization(contact)
        self.assertEqual(source, "Department")
        self.assertEqual(confidence, "Medium")
        self.assertIn("Decision, Operations", text)
        self.assertNotIn("301-405-2302", text)
        self.assertNotIn("ganand@umd.edu", text)
        self.assertNotIn("Van Munching", text)

    def test_rendered_email_uses_professor_last_name_and_clean_department(self):
        subject, body = render_umd_email(make_contact(), make_settings())
        self.assertIn("Dear Professor Babina,", body)
        self.assertIn("within the Decision, Operations and Information Technologies department", body)
        self.assertNotIn(BAD_CARD_TEXT, body)
        self.assertNotIn("301-405-2302", body)
        self.assertNotIn("ganand@umd.edu", body)
        self.assertNotIn("Van Munching", body)
        status, issues = validate_umd_draft(subject, body, make_settings())
        self.assertEqual(status, "Passed")
        self.assertEqual(issues, [])

    def test_validation_flags_bad_directory_metadata(self):
        subject = "MSIS Student Interested in TA/RA or Course Support Opportunities"
        body = (
            "Dear Professor Anand,\n\n"
            "I wanted to reach out to express my interest in any TA, grader, research assistant, "
            f"or course support opportunities related to {BAD_CARD_TEXT} for Summer 2026 or Fall 2026.\n"
        )
        status, issues = validate_umd_draft(subject, body, make_settings())
        self.assertEqual(status, "Needs Review")
        self.assertTrue(issues)


if __name__ == "__main__":
    unittest.main()

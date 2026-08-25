import unittest
from unittest.mock import patch

import multi_model_inference as app


class GuardrailTests(unittest.TestCase):
    def test_benign_prompt_passes(self):
        report = app.check_input("Explain load balancing")
        self.assertFalse(report.blocked)
        self.assertEqual(report.findings, [])

    def test_digitalocean_token_is_blocked(self):
        report = app.check_input("token= dop_v1_abcdefghijklmnopqrstuvwxyz123456")
        self.assertTrue(report.blocked)
        self.assertTrue(any("DigitalOcean" in finding for finding in report.findings))

    def test_pii_is_redacted(self):
        clean, findings = app.sanitize_output("Email me at test@example.com")
        self.assertNotIn("test@example.com", clean)
        self.assertTrue(findings)

    def test_markdown_json_is_parsed(self):
        self.assertEqual(app.extract_json('```json\n{"scores": []}\n```'), {"scores": []})


class ComparisonTests(unittest.TestCase):
    def test_one_complete_result_is_recommended(self):
        results = [
            app.ModelResult("fast", "Complete", "answer", 0.5, completion_tokens=8),
            app.ModelResult("empty", "No final answer"),
        ]
        comparison = app.compare_results(results)
        self.assertEqual(comparison["fastest_model"], "fast")
        self.assertEqual(comparison["shortest_model"], "fast")
        self.assertIn("only model", comparison["recommendation"])

    def test_multiple_results_have_no_universal_winner(self):
        results = [
            app.ModelResult("fast", "Complete", "long", 0.5, completion_tokens=20),
            app.ModelResult("short", "Complete", "brief", 1.0, completion_tokens=5),
        ]
        comparison = app.compare_results(results)
        self.assertEqual(comparison["fastest_model"], "fast")
        self.assertEqual(comparison["shortest_model"], "short")
        self.assertIn("no universal winner", comparison["recommendation"])

    def test_no_success_explains_no_usable_answer(self):
        comparison = app.compare_results([app.ModelResult("bad", "Failed")])
        self.assertIsNone(comparison["fastest_model"])
        self.assertIn("No usable answer", comparison["recommendation"])

    def test_model_selection_is_allow_listed_and_limited(self):
        self.assertEqual(app.validate_models([app.MODELS[0]]), [app.MODELS[0]])
        with self.assertRaises(ValueError): app.validate_models([])
        with self.assertRaises(ValueError): app.validate_models(["unknown"])

    def test_catalog_has_ten_models_but_run_is_limited_to_three(self):
        self.assertEqual(len(app.MODELS), 10)
        self.assertIn("openai-gpt-4.1", app.MODELS)
        self.assertIn("openai-gpt-5-mini", app.MODELS)
        self.assertIn("openai-gpt-4o-mini", app.MODELS)
        with self.assertRaises(ValueError): app.validate_models(app.MODELS[:4])

    @patch.dict("os.environ", {"DO_INFERENCE_TIMEOUT": "12"})
    def test_web_timeout_is_configurable(self):
        self.assertEqual(app.inference_timeout(), 12.0)

    @patch.object(app, "post_chat", side_effect=RuntimeError("DigitalOcean returned HTTP 400"))
    def test_http_400_becomes_failed_result(self, _post_chat):
        result = app.call_model(app.BASE_URL, "secret", app.MODELS[0], "hello", 10, 1)
        self.assertEqual(result.status, "Failed")
        self.assertIn("400", result.error)


if __name__ == "__main__":
    unittest.main()

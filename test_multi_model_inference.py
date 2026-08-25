import unittest
from unittest.mock import patch

import digitalocean_evaluations as evals
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


class EvaluationTests(unittest.TestCase):
    def test_committed_dataset_has_exactly_fifteen_ground_truth_rows(self):
        raw = evals.validate_dataset()
        self.assertEqual(len(raw.decode().splitlines()), 15)

    def test_evaluation_requires_exactly_three_models(self):
        self.assertEqual(app.validate_evaluation_models(app.MODELS[:3]), app.MODELS[:3])
        with self.assertRaises(ValueError):
            app.validate_evaluation_models(app.MODELS[:2])

    def test_public_summary_does_not_expose_prompt_results_or_reasoning(self):
        run = {
            "status": "MODEL_EVALUATION_RUN_SUCCESSFUL",
            "progress": {"judge_rows_evaluated": 15, "total_rows": 15},
            "result_summary": {"overall_score_percent": 86, "total_duration_seconds": 42},
            "results": [{"input": "private prompt", "output": "private answer", "reasoning": "hidden"}],
        }
        summary = evals.public_run_summary("model-a", run, {})
        serialized = str(summary)
        self.assertNotIn("private prompt", serialized)
        self.assertNotIn("private answer", serialized)
        self.assertNotIn("hidden", serialized)
        self.assertEqual(summary["overall_score_percent"], 86)

    def test_report_marks_scores_as_advisory(self):
        report = evals.build_report([
            {"model": "a", "status": "MODEL_EVALUATION_RUN_SUCCESSFUL", "overall_score_percent": 90},
            {"model": "b", "status": "MODEL_EVALUATION_RUN_SUCCESSFUL", "overall_score_percent": 80},
            {"model": "c", "status": "MODEL_EVALUATION_RUN_FAILED", "overall_score_percent": None},
        ], "judge", {"metric": "Correctness"})
        self.assertTrue(report["advisory"])
        self.assertIn("a", report["summary"])

    def test_native_evaluation_orchestrates_three_runs_and_returns_report(self):
        models = app.MODELS[:3]
        events = []
        terminal_run = {
            "status": "MODEL_EVALUATION_RUN_SUCCESSFUL",
            "progress": {"judge_rows_evaluated": 15, "total_rows": 15},
            "result_summary": {"overall_score_percent": 75, "total_duration_seconds": 12},
        }
        with patch.object(evals, "dataset_uuid", return_value="dataset"), \
             patch.object(evals, "resolve_metrics", return_value=(["metric"], {"metric": "Correctness"})), \
             patch.object(evals, "create_runs", return_value={model: f"run-{index}" for index, model in enumerate(models)}), \
             patch.object(evals, "get_run", return_value=terminal_run) as get_run:
            report = evals.run_evaluation("token", models, events.append, poll_seconds=0)
        self.assertEqual(get_run.call_count, 3)
        self.assertEqual(len(report["models"]), 3)
        self.assertTrue(report["advisory"])
        self.assertTrue(any(event["type"] == "evaluation_started" for event in events))


if __name__ == "__main__":
    unittest.main()

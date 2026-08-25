"""Small stdlib adapter for native DigitalOcean model evaluations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

CONTROL_BASE_URL = "https://api.digitalocean.com"
DATASET_PATH = Path(__file__).with_name("evaluation_prompts.jsonl")
PROMPT_COUNT = 15
TERMINAL_STATUSES = {
    "MODEL_EVALUATION_RUN_SUCCESSFUL",
    "MODEL_EVALUATION_RUN_PARTIALLY_SUCCESSFUL",
    "MODEL_EVALUATION_RUN_FAILED",
    "MODEL_EVALUATION_RUN_CANCELLED",
}
SUCCESS_STATUSES = {
    "MODEL_EVALUATION_RUN_SUCCESSFUL",
    "MODEL_EVALUATION_RUN_PARTIALLY_SUCCESSFUL",
}
_dataset_uuid_cache: str | None = None


class EvaluationError(RuntimeError):
    pass


def api_json(token: str, path: str, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{CONTROL_BASE_URL}{path}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        raise EvaluationError(f"DigitalOcean Evaluations returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise EvaluationError("Could not reach the DigitalOcean Evaluations API") from exc
    if not isinstance(value, dict):
        raise EvaluationError("DigitalOcean Evaluations returned an unexpected response")
    return value


def validate_dataset(path: Path = DATASET_PATH) -> bytes:
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    if len(rows) != PROMPT_COUNT or any(not isinstance(row.get("input"), str) or not isinstance(row.get("ground_truth"), str) for row in rows):
        raise EvaluationError(f"The evaluation dataset must contain exactly {PROMPT_COUNT} input/ground_truth rows")
    return raw


def upload_dataset(token: str, path: Path = DATASET_PATH) -> str:
    raw = validate_dataset(path)
    presigned = api_json(token, "/v2/gen-ai/model_evaluation/datasets/file_upload_presigned_urls", "POST", {
        "files": [{"file_name": path.name, "file_size": str(len(raw))}],
    })
    uploads = presigned.get("uploads")
    if not isinstance(uploads, list) or not uploads or not isinstance(uploads[0], dict):
        raise EvaluationError("DigitalOcean did not return a dataset upload URL")
    upload = uploads[0]
    url, object_key = upload.get("presigned_url"), upload.get("object_key")
    if not isinstance(url, str) or not isinstance(object_key, str):
        raise EvaluationError("DigitalOcean returned an incomplete dataset upload target")
    request = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/x-ndjson"}, method="PUT")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response.read()
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise EvaluationError("The evaluation dataset upload failed") from exc
    created = api_json(token, "/v2/gen-ai/evaluation_datasets", "POST", {
        "dataset_type": "EVALUATION_DATASET_TYPE_MODEL",
        "name": "model-fomo-15-prompt-v1",
        "file_upload_dataset": {
            "original_file_name": path.name,
            "size_in_bytes": str(len(raw)),
            "stored_object_key": object_key,
        },
    })
    dataset_uuid = created.get("evaluation_dataset_uuid")
    if not isinstance(dataset_uuid, str):
        raise EvaluationError("DigitalOcean did not return an evaluation dataset UUID")
    return dataset_uuid


def dataset_uuid(token: str) -> str:
    global _dataset_uuid_cache
    configured = os.getenv("DO_EVAL_DATASET_UUID")
    if configured:
        return configured
    if _dataset_uuid_cache is None:
        _dataset_uuid_cache = upload_dataset(token)
    return _dataset_uuid_cache


def list_models(token: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"usecases": "MODEL_USECASE_SERVERLESS", "public_only": "true", "per_page": 200})
    response = api_json(token, f"/v2/gen-ai/models?{query}")
    models = response.get("models")
    return models if isinstance(models, list) else []


def resolve_model_uuids(token: str, model_ids: list[str], judge_id: str) -> tuple[dict[str, str], str]:
    catalog = list_models(token)
    by_id = {item.get("id"): item.get("uuid") for item in catalog if isinstance(item, dict)}
    missing = [model for model in model_ids + [judge_id] if not isinstance(by_id.get(model), str)]
    if missing:
        raise EvaluationError(f"These models are unavailable for DigitalOcean Evaluations: {', '.join(dict.fromkeys(missing))}")
    return {model: by_id[model] for model in model_ids}, by_id[judge_id]


def resolve_metrics(token: str) -> tuple[list[str], dict[str, str]]:
    response = api_json(token, "/v2/gen-ai/model_evaluation_metrics")
    metrics = response.get("metrics")
    if not isinstance(metrics, list):
        raise EvaluationError("DigitalOcean returned no evaluation metrics")
    by_name = {str(item.get("metric_name", "")).lower(): item for item in metrics if isinstance(item, dict)}
    requested = [name.strip().lower() for name in os.getenv("DO_EVAL_METRICS", "correctness,completeness").split(",") if name.strip()]
    chosen = [by_name[name] for name in requested if name in by_name and isinstance(by_name[name].get("metric_uuid"), str)]
    if not chosen:
        raise EvaluationError(f"Configured metrics were not found. Available metrics: {', '.join(sorted(by_name))}")
    uuids = [item["metric_uuid"] for item in chosen]
    names = {item["metric_uuid"]: str(item.get("metric_name", "Metric")) for item in chosen}
    return uuids, names


def create_runs(token: str, models: list[str], dataset: str, judge_id: str, metric_uuids: list[str]) -> dict[str, str]:
    candidate_uuids, judge_uuid = resolve_model_uuids(token, models, judge_id)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    runs: dict[str, str] = {}
    for model in models:
        response = api_json(token, "/v2/gen-ai/model_evaluation_runs", "POST", {
            "name": f"model-fomo-{model}-{stamp}",
            "candidate_model_name": model,
            "candidate_model_source": "CANDIDATE_MODEL_SOURCE_SERVERLESS",
            "candidate_model_uuid": candidate_uuids[model],
            "candidate_inference_config": {"max_tokens": 512, "temperature": 0.2, "system_prompt": "Answer the evaluation input directly and do not reveal private reasoning."},
            "dataset_uuid": dataset,
            "judge_model_uuid": judge_uuid,
            "metric_uuids": metric_uuids,
            "source": "model-fomo-poc",
        })
        run_uuid = response.get("eval_run_uuid")
        if not isinstance(run_uuid, str):
            raise EvaluationError(f"DigitalOcean did not create the evaluation run for {model}")
        runs[model] = run_uuid
    return runs


def get_run(token: str, run_uuid: str) -> dict[str, Any]:
    response = api_json(token, f"/v2/gen-ai/model_evaluation_runs/{urllib.parse.quote(run_uuid)}?page=1&per_page=1")
    run = response.get("run")
    return run if isinstance(run, dict) else response


def public_run_summary(model: str, run: dict[str, Any], metric_names: dict[str, str]) -> dict[str, Any]:
    result = run.get("result_summary") if isinstance(run.get("result_summary"), dict) else {}
    progress = run.get("progress") if isinstance(run.get("progress"), dict) else {}
    metric_summaries = result.get("metric_summaries") if isinstance(result.get("metric_summaries"), list) else []
    public_metrics = []
    for item in metric_summaries:
        if not isinstance(item, dict):
            continue
        metric_uuid = item.get("metric_uuid")
        public_metrics.append({
            "name": item.get("metric_name") or metric_names.get(metric_uuid, "Metric"),
            "score_percent": first_number(item, "score_percent", "pass_percentage", "success_percentage", "average_score", "metric_value"),
        })
    return {
        "model": model,
        "status": str(run.get("status") or "MODEL_EVALUATION_RUN_STATUS_UNSPECIFIED"),
        "rows_evaluated": progress.get("judge_rows_evaluated") or progress.get("candidate_rows_evaluated") or 0,
        "total_rows": progress.get("total_rows") or PROMPT_COUNT,
        "overall_score_percent": first_number(result, "overall_score_percent"),
        "duration_seconds": first_number(result, "total_duration_seconds"),
        "metrics": public_metrics,
        "error": str(run.get("error_description"))[:300] if run.get("error_description") else None,
    }


def first_number(value: dict[str, Any], *keys: str) -> int | float | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            return candidate
    return None


def build_report(summaries: list[dict[str, Any]], judge_id: str, metric_names: dict[str, str]) -> dict[str, Any]:
    scored = [item for item in summaries if item["status"] in SUCCESS_STATUSES and item.get("overall_score_percent") is not None]
    if scored:
        best_score = max(item["overall_score_percent"] for item in scored)
        leaders = [item["model"] for item in scored if item["overall_score_percent"] == best_score]
        if len(leaders) == 1:
            summary = f"{leaders[0]} had the highest advisory DigitalOcean Evaluation score ({best_score:g}%) on this 15-prompt dataset. Validate the result with human review before production use."
        else:
            summary = f"{', '.join(leaders)} tied for the highest advisory score ({best_score:g}%) on this dataset. There is no universal winner."
    else:
        summary = "No model produced a complete scored evaluation. Review the run statuses and DigitalOcean Evaluations configuration."
    return {"prompt_count": PROMPT_COUNT, "judge_model": judge_id, "metric_names": list(metric_names.values()), "models": summaries, "summary": summary, "advisory": True}


def run_evaluation(token: str, models: list[str], emit: Callable[[dict[str, Any]], None], timeout_seconds: float = 600.0, poll_seconds: float = 5.0) -> dict[str, Any]:
    judge_id = os.getenv("DO_EVAL_JUDGE_MODEL", "qwen3.5-397b-a17b")
    emit({"type": "evaluation_phase", "phase": "Preparing the 15-prompt DigitalOcean dataset"})
    dataset = dataset_uuid(token)
    metric_uuids, metric_names = resolve_metrics(token)
    emit({"type": "evaluation_phase", "phase": "Creating three native DigitalOcean Evaluation runs"})
    runs = create_runs(token, models, dataset, judge_id, metric_uuids)
    emit({"type": "evaluation_started", "models": models, "prompt_count": PROMPT_COUNT})
    deadline, latest = time.monotonic() + timeout_seconds, {}
    while time.monotonic() < deadline:
        terminal = True
        for model, run_uuid in runs.items():
            run = get_run(token, run_uuid)
            latest[model] = run
            summary = public_run_summary(model, run, metric_names)
            emit({"type": "evaluation_progress", "result": summary})
            if summary["status"] not in TERMINAL_STATUSES:
                terminal = False
        if terminal:
            summaries = [public_run_summary(model, latest[model], metric_names) for model in models]
            return build_report(summaries, judge_id, metric_names)
        time.sleep(poll_seconds)
    raise EvaluationError(f"DigitalOcean Evaluations did not finish within {timeout_seconds:g} seconds; the runs continue in the DigitalOcean control panel")

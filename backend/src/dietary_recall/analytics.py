"""Privacy-aware cohort EDA and review-only quality/matching models."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from .research_core import _json, _uid
from .validated_research import PlatformRepository


def _snapshot(rows: Iterable[Sequence[Any]]) -> str:
    payload = json.dumps([list(row) for row in rows], ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _describe(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "mean": statistics.fmean(values),
        "sample_sd": statistics.stdev(values) if len(values) > 1 else None,
        "minimum": min(values),
        "q1": _quantile(values, 0.25),
        "median": statistics.median(values),
        "q3": _quantile(values, 0.75),
        "maximum": max(values),
    }


class AnalyticsService:
    def __init__(self, repository: PlatformRepository):
        self.repository = repository

    def list_cohort_runs(self, project_uid: str, actor: str) -> list[dict[str, Any]]:
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute("SELECT * FROM cohort_analysis_runs WHERE project_uid=? ORDER BY created_at DESC", (project_uid,))]

    def get_cohort_run(self, project_uid: str, analysis_uid: str, actor: str) -> dict[str, Any]:
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor)
            run = con.execute("SELECT * FROM cohort_analysis_runs WHERE project_uid=? AND analysis_uid=?", (project_uid, analysis_uid)).fetchone()
            if run is None:
                raise KeyError("Cohort analysis not found")
            result = dict(run)
            result["metrics"] = [dict(row) for row in con.execute(
                "SELECT m.*,n.canonical_code,n.display_name,n.component_class FROM cohort_metric_results m JOIN canonical_nutrients n USING(canonical_nutrient_uid) WHERE analysis_uid=? ORDER BY group_key,n.component_class,n.display_name",
                (analysis_uid,),
            )]
            return result

    def run_cohort(self, project_uid: str, data: Mapping[str, Any], actor: str) -> dict[str, Any]:
        mode = str(data.get("analysis_mode") or "participant_recorded_mean")
        group_by = str(data.get("group_by") or "overall")
        minimum_group_size = max(5, int(data.get("minimum_group_size") or 5))
        minimum_days = max(1, int(data.get("minimum_days") or 2))
        if mode not in {"recall_day", "participant_recorded_mean"}:
            raise ValueError("analysis_mode must be recall_day or participant_recorded_mean")
        if group_by not in {"overall", "gender_code", "life_id", "activity_level"}:
            raise ValueError("Unsupported cohort grouping")
        with self.repository.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self.repository._membership(con, project_uid, actor, {"owner", "admin", "analyst"})
            rows = [dict(row) for row in con.execute(
                "SELECT r.recall_uid,r.participant_uid,r.recall_date,p.gender_code,p.life_id,p.activity_level,rr.nutrient_code,rr.value,rr.unit,m.canonical_nutrient_uid,m.mapping_status,n.canonical_unit "
                "FROM project_records pr JOIN recalls r ON r.recall_uid=pr.entity_uid JOIN participants p USING(participant_uid) JOIN recall_results rr USING(recall_uid) "
                "JOIN nutrient_mappings m ON m.source_system='legacy_phd' AND m.source_nutrient_code=rr.nutrient_code AND m.mapping_status!='rejected' "
                "JOIN canonical_nutrients n USING(canonical_nutrient_uid) WHERE pr.project_uid=? AND pr.entity_type='recall' AND r.status!='archived' ORDER BY r.participant_uid,r.recall_uid,rr.nutrient_code",
                (project_uid,),
            )]
            if not rows:
                raise ValueError("No calculated recall results are available for cohort analysis")
            normalized: list[dict[str, Any]] = []
            for row in rows:
                try:
                    row["normalized_value"] = self.repository._convert(con, float(row["value"]), row["unit"], row["canonical_unit"])
                    normalized.append(row)
                except ValueError:
                    continue
            if not normalized:
                raise ValueError("Recall results could not be converted to canonical units")
            excluded = 0
            observations: list[tuple[str, str, str, float, str]] = []
            if mode == "recall_day":
                for row in normalized:
                    group = "overall" if group_by == "overall" else str(row.get(group_by) if row.get(group_by) not in (None, "") else "missing")
                    observations.append((row["recall_uid"], group, row["canonical_nutrient_uid"], row["normalized_value"], row["canonical_unit"]))
            else:
                buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
                for row in normalized:
                    buckets[(row["participant_uid"], row["canonical_nutrient_uid"])].append(row)
                for (participant_uid, nutrient_uid), items in buckets.items():
                    distinct_days = {item["recall_uid"] for item in items}
                    if len(distinct_days) < minimum_days:
                        excluded += 1
                        continue
                    first = items[0]
                    group = "overall" if group_by == "overall" else str(first.get(group_by) if first.get(group_by) not in (None, "") else "missing")
                    observations.append((participant_uid, group, nutrient_uid, statistics.fmean(item["normalized_value"] for item in items), first["canonical_unit"]))
            if not observations:
                raise ValueError("No observations remain after minimum-days filtering")
            grouped: dict[tuple[str, str, str], list[float]] = defaultdict(list)
            for _, group, nutrient, value, unit in observations:
                grouped[(group, nutrient, unit)].append(value)
            warnings = [
                "Descriptive output only; no causal, clinical or individual dietary inference is made.",
                "Participant recorded-day means are not estimates of usual intake.",
            ]
            if any(row["mapping_status"] != "reviewed" for row in normalized):
                warnings.append("One or more legacy nutrient mappings remain provisional; treat results as exploratory.")
            uid = _uid("cohort")
            source_snapshot = _snapshot((row["recall_uid"], row["nutrient_code"], row["value"], row["unit"], row["mapping_status"]) for row in normalized)
            self.repository.consume_usage_in_transaction(con, project_uid, "calculation_runs", 1, actor, "cohort_analysis", uid, {"analysis_mode": mode, "group_by": group_by})
            con.execute(
                "INSERT INTO cohort_analysis_runs(analysis_uid,project_uid,analysis_mode,group_by,minimum_group_size,minimum_days,status,config_json,input_snapshot_hash,observation_count,excluded_count,warnings_json,actor) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, project_uid, mode, group_by, minimum_group_size, minimum_days, "exploratory" if any(row["mapping_status"] != "reviewed" for row in normalized) else "complete", _json(dict(data)), source_snapshot, len(observations), excluded, _json(warnings), actor),
            )
            for (group, nutrient, unit), values in grouped.items():
                suppressed = len(values) < minimum_group_size
                stats = _describe(values) if not suppressed else {key: None for key in ("mean", "sample_sd", "minimum", "q1", "median", "q3", "maximum")}
                con.execute(
                    "INSERT INTO cohort_metric_results(analysis_uid,group_key,canonical_nutrient_uid,observation_count,suppressed,mean_value,sample_sd,minimum_value,q1_value,median_value,q3_value,maximum_value,unit) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uid, group, nutrient, len(values), int(suppressed), stats["mean"], stats["sample_sd"], stats["minimum"], stats["q1"], stats["median"], stats["q3"], stats["maximum"], unit),
                )
            self.repository._audit(con, "calculate", "cohort_analysis", uid, actor, detail={"snapshot": source_snapshot, "suppression_threshold": minimum_group_size})
            con.commit()
        return self.get_cohort_run(project_uid, uid, actor)

    def list_anomaly_runs(self, project_uid: str, actor: str) -> list[dict[str, Any]]:
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute("SELECT * FROM anomaly_detection_runs WHERE project_uid=? ORDER BY created_at DESC", (project_uid,))]

    def get_anomaly_run(self, project_uid: str, run_uid: str, actor: str) -> dict[str, Any]:
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor)
            run = con.execute("SELECT * FROM anomaly_detection_runs WHERE project_uid=? AND anomaly_run_uid=?", (project_uid, run_uid)).fetchone()
            if run is None:
                raise KeyError("Anomaly run not found")
            result = dict(run)
            result["flags"] = [dict(row) for row in con.execute("SELECT * FROM anomaly_flags WHERE anomaly_run_uid=? ORDER BY ABS(robust_z_score) DESC", (run_uid,))]
            return result

    def run_anomalies(self, project_uid: str, data: Mapping[str, Any], actor: str) -> dict[str, Any]:
        threshold = float(data.get("threshold") or 3.5)
        if threshold < 2 or threshold > 10:
            raise ValueError("threshold must be between 2 and 10")
        with self.repository.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self.repository._membership(con, project_uid, actor, {"owner", "admin", "analyst"})
            rows = [dict(row) for row in con.execute(
                "SELECT er.result_uid,er.nutrient_code,er.value,er.unit,m.canonical_nutrient_uid,m.mapping_status FROM project_records pr JOIN experiments e ON e.experiment_uid=pr.entity_uid JOIN experiment_results er USING(experiment_uid) LEFT JOIN nutrient_mappings m ON m.source_system='legacy_phd' AND m.source_nutrient_code=er.nutrient_code AND m.mapping_status!='rejected' WHERE pr.project_uid=? AND pr.entity_type='experiment' AND er.value IS NOT NULL ORDER BY er.nutrient_code,er.result_uid",
                (project_uid,),
            )]
            if len(rows) < 5:
                raise ValueError("At least five experiment results are required")
            by_nutrient: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                by_nutrient[(row["nutrient_code"], row["unit"])].append(row)
            flags: list[tuple[dict[str, Any], float]] = []
            for items in by_nutrient.values():
                if len(items) < 5:
                    continue
                values = [float(item["value"]) for item in items]
                median = statistics.median(values)
                mad = statistics.median(abs(value - median) for value in values)
                if mad == 0:
                    continue
                for item in items:
                    score = 0.67448975 * (float(item["value"]) - median) / mad
                    if abs(score) >= threshold:
                        flags.append((item, score))
            uid = _uid("anomaly")
            source_snapshot = _snapshot((row["result_uid"], row["nutrient_code"], row["value"], row["unit"]) for row in rows)
            self.repository.consume_usage_in_transaction(con, project_uid, "calculation_runs", 1, actor, "anomaly_detection", uid, {"method": "median_mad_v1", "threshold": threshold})
            con.execute("INSERT INTO anomaly_detection_runs(anomaly_run_uid,project_uid,source_entity,method,threshold,input_snapshot_hash,evaluated_count,flag_count,actor) VALUES (?,?,?,?,?,?,?,?,?)", (uid, project_uid, "experiment_result", "median_mad_v1", threshold, source_snapshot, len(rows), len(flags), actor))
            for item, score in flags:
                con.execute(
                    "INSERT INTO anomaly_flags(anomaly_flag_uid,anomaly_run_uid,source_record_uid,canonical_nutrient_uid,legacy_nutrient_code,observed_value,unit,robust_z_score,flag_reason) VALUES (?,?,?,?,?,?,?,?,?)",
                    (_uid("flag"), uid, item["result_uid"], item.get("canonical_nutrient_uid"), item["nutrient_code"], item["value"], item["unit"], score, "Value exceeds the configured robust median/MAD threshold; review only, source unchanged."),
                )
            self.repository._audit(con, "flag", "anomaly_detection", uid, actor, detail={"snapshot": source_snapshot, "flag_count": len(flags), "automatic_rejection": False})
            con.commit()
        return self.get_anomaly_run(project_uid, uid, actor)

    def review_anomaly(self, project_uid: str, flag_uid: str, data: Mapping[str, Any], actor: str) -> dict[str, Any]:
        status = str(data.get("review_status") or "")
        if status not in {"confirmed", "dismissed"}:
            raise ValueError("review_status must be confirmed or dismissed")
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor, {"owner", "admin", "analyst"})
            row = con.execute("SELECT f.* FROM anomaly_flags f JOIN anomaly_detection_runs r USING(anomaly_run_uid) WHERE f.anomaly_flag_uid=? AND r.project_uid=?", (flag_uid, project_uid)).fetchone()
            if row is None:
                raise KeyError("Anomaly flag not found")
            con.execute("UPDATE anomaly_flags SET review_status=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,review_notes=? WHERE anomaly_flag_uid=?", (status, actor, data.get("review_notes"), flag_uid))
            result = dict(con.execute("SELECT * FROM anomaly_flags WHERE anomaly_flag_uid=?", (flag_uid,)).fetchone())
            self.repository._audit(con, "review", "anomaly_flag", flag_uid, actor, before=dict(row), after=result)
            return result


def _features(row: Mapping[str, Any]) -> list[float]:
    feature = json.loads(row["feature_json"] or "{}")
    return [1.0, float(feature.get("sequence") or 0), float(feature.get("token_jaccard") or 0), float(bool(feature.get("exact"))), float(row["score"])]


def _fit_logistic(items: Sequence[tuple[list[float], int]], iterations: int = 1000, rate: float = 0.15) -> list[float]:
    weights = [0.0] * len(items[0][0])
    for _ in range(iterations):
        gradient = [0.0] * len(weights)
        for values, label in items:
            z = max(-30.0, min(30.0, sum(weight * value for weight, value in zip(weights, values))))
            prediction = 1.0 / (1.0 + math.exp(-z))
            for index, value in enumerate(values):
                gradient[index] += (prediction - label) * value
        for index in range(len(weights)):
            penalty = 0.002 * weights[index] if index else 0.0
            weights[index] -= rate * (gradient[index] / len(items) + penalty)
    return weights


def _predict(weights: Sequence[float], values: Sequence[float]) -> float:
    z = max(-30.0, min(30.0, sum(weight * value for weight, value in zip(weights, values))))
    return 1.0 / (1.0 + math.exp(-z))


class MatchingModelService:
    def __init__(self, repository: PlatformRepository):
        self.repository = repository

    def list_models(self, project_uid: str, actor: str) -> list[dict[str, Any]]:
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute("SELECT * FROM matching_models WHERE project_uid=? ORDER BY created_at DESC", (project_uid,))]

    def train(self, project_uid: str, actor: str) -> dict[str, Any]:
        with self.repository.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self.repository._membership(con, project_uid, actor, {"owner", "admin", "analyst"})
            rows = [dict(row) for row in con.execute("SELECT * FROM food_match_candidates WHERE project_uid=? AND review_status IN ('accepted','rejected') ORDER BY match_uid", (project_uid,))]
            accepted = sum(row["review_status"] == "accepted" for row in rows)
            rejected = len(rows) - accepted
            if len(rows) < 20 or min(accepted, rejected) < 5:
                raise ValueError("Training requires at least 20 reviewed matches and at least five accepted and five rejected examples")
            items = [(_features(row), int(row["review_status"] == "accepted")) for row in rows]
            predictions: list[tuple[float, int]] = []
            for fold in range(5):
                train = [item for row, item in zip(rows, items) if int(hashlib.sha256(row["match_uid"].encode()).hexdigest()[:8], 16) % 5 != fold]
                test = [item for row, item in zip(rows, items) if int(hashlib.sha256(row["match_uid"].encode()).hexdigest()[:8], 16) % 5 == fold]
                if not test or len({label for _, label in train}) < 2:
                    continue
                weights = _fit_logistic(train)
                predictions.extend((_predict(weights, values), label) for values, label in test)
            if not predictions:
                raise ValueError("Deterministic cross-validation could not form valid folds")
            tp = sum(score >= 0.5 and label == 1 for score, label in predictions)
            tn = sum(score < 0.5 and label == 0 for score, label in predictions)
            fp = sum(score >= 0.5 and label == 0 for score, label in predictions)
            fn = sum(score < 0.5 and label == 1 for score, label in predictions)
            sensitivity = tp / (tp + fn) if tp + fn else None
            specificity = tn / (tn + fp) if tn + fp else None
            metrics = {
                "evaluation": "deterministic_5_fold_cross_validation",
                "evaluated": len(predictions),
                "balanced_accuracy": (sensitivity + specificity) / 2 if sensitivity is not None and specificity is not None else None,
                "precision": tp / (tp + fp) if tp + fp else None,
                "recall": sensitivity,
                "brier_score": statistics.fmean((score - label) ** 2 for score, label in predictions),
                "automatic_merge": False,
            }
            weights = _fit_logistic(items)
            uid = _uid("model")
            source_snapshot = _snapshot((row["match_uid"], row["review_status"], row["score"], row["feature_json"]) for row in rows)
            self.repository.consume_usage_in_transaction(con, project_uid, "calculation_runs", 1, actor, "matching_model_training", uid, {"training_count": len(rows)})
            con.execute(
                "INSERT INTO matching_models(matching_model_uid,project_uid,feature_schema_json,coefficients_json,metrics_json,training_snapshot_hash,training_count,accepted_count,rejected_count,trained_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uid, project_uid, _json(["intercept", "sequence", "token_jaccard", "exact", "heuristic_score"]), _json(weights), _json(metrics), source_snapshot, len(rows), accepted, rejected, actor),
            )
            self.repository._audit(con, "train", "matching_model", uid, actor, detail={"metrics": metrics, "review_status": "candidate", "automatic_merge": False})
            con.commit()
            return dict(con.execute("SELECT * FROM matching_models WHERE matching_model_uid=?", (uid,)).fetchone())

    def apply(self, project_uid: str, model_uid: str, actor: str) -> dict[str, Any]:
        with self.repository.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self.repository._membership(con, project_uid, actor, {"owner", "admin", "analyst"})
            model = con.execute("SELECT * FROM matching_models WHERE matching_model_uid=? AND project_uid=? AND review_status='approved'", (model_uid, project_uid)).fetchone()
            if model is None:
                raise PermissionError("Only a specialist-approved matching model can prioritize candidates")
            weights = json.loads(model["coefficients_json"])
            rows = list(con.execute("SELECT * FROM food_match_candidates WHERE project_uid=? AND review_status='candidate'", (project_uid,)))
            if not rows:
                raise ValueError("No candidate matches are available to prioritize")
            self.repository.consume_usage_in_transaction(con, project_uid, "calculation_runs", 1, actor, "matching_model_inference", model_uid, {"candidate_count": len(rows)})
            for row in rows:
                con.execute("UPDATE food_match_candidates SET triage_model_uid=?,triage_score=? WHERE match_uid=?", (model_uid, _predict(weights, _features(row)), row["match_uid"]))
            self.repository._audit(con, "prioritize", "food_match_candidates", model_uid, actor, detail={"candidate_count": len(rows), "automatic_merge": False})
            con.commit()
            return {"matching_model_uid": model_uid, "prioritized": len(rows), "automatic_merge": False}

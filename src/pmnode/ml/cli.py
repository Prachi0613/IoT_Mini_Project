"""Command-line workflow for Task 02 DSP, models, scoring, and adaptation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pmnode.storage import Storage

from .adaptation import AdaptationPolicy, ControlledAdapter
from .evaluation import evaluate_results
from .features import FeatureConfig
from .health import HealthState
from .model import load_model
from .pipeline import extract_sessions, score_sessions, train_from_storage
from .registry import MLRegistry


def parse_session(value: str) -> tuple[str, str]:
    try:
        device, boot = value.split(":", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("session must be DEVICE:BOOT") from exc
    if not device or not boot:
        raise argparse.ArgumentTypeError("session must be DEVICE:BOOT")
    return device, boot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    def common(name):
        item = sub.add_parser(name)
        item.add_argument("--database", required=True, type=Path)
        return item
    extract = common("extract"); extract.add_argument("--session", action="append", required=True, type=parse_session)
    extract.add_argument("--window-seconds", type=float, default=1.0); extract.add_argument("--recompute", action="store_true")
    train = common("train"); train.add_argument("--train", action="append", required=True, type=parse_session)
    train.add_argument("--calibration", action="append", required=True, type=parse_session)
    train.add_argument("--artifact", required=True, type=Path); train.add_argument("--model-version", default="model-1")
    train.add_argument("--seed", type=int, default=42); train.add_argument("--quantile", type=float, default=0.99)
    score = common("score"); score.add_argument("--session", action="append", required=True, type=parse_session)
    score.add_argument("--model", required=True, type=Path)
    evaluate = common("evaluate"); evaluate.add_argument("--session", action="append", required=True, type=parse_session)
    evaluate.add_argument("--model", required=True, type=Path); evaluate.add_argument("--output-dir", required=True, type=Path)
    evaluate.add_argument("--anomaly-onset-us", type=int)
    adapt = common("adapt"); adapt.add_argument("--session", action="append", required=True, type=parse_session)
    adapt.add_argument("--model", required=True, type=Path); adapt.add_argument("--enable", action="store_true")
    adapt.add_argument("--approve", action="store_true"); adapt.add_argument("--minimum-candidates", type=int, default=30)
    rollback = common("rollback"); rollback.add_argument("--model-version", required=True)
    rollback.add_argument("--reference-version", required=True, type=int)
    status = common("baseline-status"); status.add_argument("--model-version", required=True)
    status.add_argument("--events", action="store_true")
    common("model-list")
    activate_model = common("model-activate"); activate_model.add_argument("--model-version", required=True)
    import_model = common("model-import")
    import_model.add_argument("--artifact", required=True, type=Path)
    import_model.add_argument("--activate", action="store_true")
    import_model.add_argument("--requeue-unscored", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    storage = Storage(args.database); registry = MLRegistry(storage)
    try:
        if args.command == "extract":
            windows = extract_sessions(storage, args.session,
                                       FeatureConfig(window_duration_s=args.window_seconds),
                                       args.recompute)
            print(json.dumps({"windows": len(windows),
                              "valid": sum(w.window_quality.value == "VALID" for w in windows)}))
        elif args.command == "train":
            bundle = train_from_storage(storage, args.train, args.calibration,
                                        args.artifact, model_version=args.model_version,
                                        random_state=args.seed, threshold_quantile=args.quantile)
            print(json.dumps({"model_version": bundle.model_version,
                              "isolation_threshold": bundle.isolation_threshold,
                              "reference_threshold": bundle.reference.threshold,
                              "declaration": bundle.metadata["declaration"]}))
        elif args.command == "score":
            results = score_sessions(storage, args.session, args.model)
            print(json.dumps({"scored_windows": len(results),
                              "alerts": sum(r["isolation_score"] is not None and
                                            r["isolation_score"] > load_model(args.model).isolation_threshold
                                            for r in results),
                              "sensor_errors": sum(r["health_state"] == "SENSOR_ERROR"
                                                   for r in results)}))
        elif args.command == "evaluate":
            bundle = load_model(args.model); results = score_sessions(storage, args.session, args.model)
            labels = None
            if args.anomaly_onset_us is not None:
                labels = [r["window"].window_end_us > args.anomaly_onset_us for r in results]
            report = evaluate_results(bundle, results, args.output_dir, labels,
                                      args.anomaly_onset_us)
            print(json.dumps(report, sort_keys=True))
        elif args.command == "adapt":
            bundle = load_model(args.model)
            adapter = ControlledAdapter(bundle, registry,
                AdaptationPolicy(enabled=args.enable, minimum_candidates=args.minimum_candidates))
            results = score_sessions(storage, args.session, args.model, persist=False)
            decisions = []
            for index, result in enumerate(results):
                decisions.append(adapter.consider(
                    result["window"], result["isolation_score"], result["reference_score"],
                    HealthState(result["health_state"]),
                    approve=args.approve and index == len(results) - 1,
                ).__dict__)
            print(json.dumps({"decisions": decisions, "active_version": adapter.version}))
        elif args.command == "rollback":
            restored = registry.activate_reference(args.model_version, args.reference_version)
            registry.log_adaptation(args.model_version, args.reference_version, "ROLLBACK",
                                    f"restored reference {args.reference_version}", 0)
            print(json.dumps({"active_reference": restored["reference_version"]}))
        elif args.command == "baseline-status":
            payload = {"active": registry.active_reference(args.model_version),
                       "versions": registry.list_references(args.model_version)}
            if payload["active"]:
                payload["active"]["center"] = payload["active"]["center"].tolist()
                payload["active"]["spread"] = payload["active"]["spread"].tolist()
            if args.events:
                payload["events"] = registry.recent_adaptation_events(args.model_version)
            print(json.dumps(payload, sort_keys=True))
        elif args.command == "model-list":
            print(json.dumps({"models": registry.list_models()}, sort_keys=True))
        elif args.command == "model-activate":
            print(json.dumps({"model_version": args.model_version,
                              "status": registry.activate_model(args.model_version)}))
        elif args.command == "model-import":
            bundle = load_model(args.artifact)
            registry.register_model(bundle, args.artifact)
            ControlledAdapter(bundle, registry)  # Ensure immutable reference v1 exists.
            status = registry.activate_model(bundle.model_version) if args.activate else "CANDIDATE"
            requeued = 0
            if args.requeue_unscored:
                if not args.activate:
                    raise ValueError("--requeue-unscored requires --activate")
                with storage.connect() as connection:
                    rows = connection.execute(
                        "SELECT p.device_id,p.boot_id FROM processing_checkpoints p "
                        "JOIN device_sessions s ON s.device_id=p.device_id AND s.boot_id=p.boot_id "
                        "WHERE s.source_type=? AND p.processing_status IN "
                        "('MODEL_UNAVAILABLE','MODEL_INCOMPATIBLE') AND NOT EXISTS ("
                        "SELECT 1 FROM model_predictions x WHERE x.device_id=p.device_id "
                        "AND x.boot_id=p.boot_id)",
                        (bundle.source_type,),
                    ).fetchall()
                    connection.executemany(
                        "DELETE FROM processing_checkpoints WHERE device_id=? AND boot_id=?",
                        [(row["device_id"], row["boot_id"]) for row in rows],
                    )
                    requeued = len(rows)
            print(json.dumps({
                "model_version": bundle.model_version,
                "source_type": bundle.source_type,
                "feature_schema_version": bundle.feature_schema_version,
                "reference_version": registry.active_reference(bundle.model_version)["reference_version"],
                "status": status, "requeued_unscored_sessions": requeued,
            }, sort_keys=True))
    except (ValueError, FileNotFoundError) as exc:
        print(f"Task 02 command failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

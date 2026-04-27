#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pickle
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_squared_error,
    precision_score,
    recall_score,
    r2_score,
)
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_DB = ROOT / "sniper_brain.db"
DEFAULT_MODELS_DIR = ROOT / "models"


@dataclass
class DatasetBundle:
    x_ghost: pd.DataFrame
    x_consensus: np.ndarray
    y_class: np.ndarray
    y_reg: np.ndarray
    rows: int
    filtered_noise_rows: int
    valid_ghost: int
    valid_consensus: int


def _load_runtime_classes():
    from core.strategy.consensus_nn import AgentConsensusNN
    from ultimate_ml import UltimateMLSystem

    return AgentConsensusNN, UltimateMLSystem


def load_trade_rows(db_path: Path, min_abs_pnl: float) -> tuple[list[sqlite3.Row], int]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    filtered_noise = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE market_snapshot IS NOT NULL
          AND market_snapshot != ''
          AND pnl_percent IS NOT NULL
          AND pnl_percent != -99.0
          AND ABS(pnl_percent) < ?
        """,
        (min_abs_pnl,),
    ).fetchone()[0]
    rows = conn.execute(
        """
        SELECT id, symbol, pnl_percent, market_snapshot
        FROM trades
        WHERE market_snapshot IS NOT NULL
          AND market_snapshot != ''
          AND pnl_percent IS NOT NULL
          AND pnl_percent != -99.0
          AND ABS(pnl_percent) >= ?
        ORDER BY id ASC
        """,
        (min_abs_pnl,),
    ).fetchall()
    conn.close()
    return rows, int(filtered_noise)


def build_dataset(rows: list[sqlite3.Row], filtered_noise_rows: int) -> DatasetBundle:
    AgentConsensusNN, UltimateMLSystem = _load_runtime_classes()
    extractor = UltimateMLSystem()
    consensus = AgentConsensusNN(model_path=str(DEFAULT_MODELS_DIR / "_probe.pkl"))

    ghost_features = []
    consensus_features = []
    y_class = []
    y_reg = []
    valid_consensus = 0

    for row in rows:
        try:
            snap = json.loads(row["market_snapshot"] or "{}")
            if not isinstance(snap, dict):
                continue
            pnl = float(row["pnl_percent"])
            features = extractor.extract_features(snap)
            ghost_features.append(features)

            votes = snap.get("votos") or {}
            if isinstance(votes, dict) and votes:
                valid_consensus += 1
            consensus_features.append(
                [float(votes.get(agent, 50.0)) for agent in consensus.AGENT_NAMES]
            )
            y_class.append(1 if pnl > 0 else 0)
            y_reg.append(pnl)
        except Exception:
            continue

    return DatasetBundle(
        x_ghost=pd.DataFrame(ghost_features),
        x_consensus=np.array(consensus_features, dtype=float),
        y_class=np.array(y_class, dtype=int),
        y_reg=np.array(y_reg, dtype=float),
        rows=len(rows),
        filtered_noise_rows=filtered_noise_rows,
        valid_ghost=len(ghost_features),
        valid_consensus=valid_consensus,
    )


def validate_dataset(bundle: DatasetBundle, min_samples: int) -> None:
    if bundle.valid_ghost < min_samples:
        raise SystemExit(
            f"Dataset insuficiente: {bundle.valid_ghost} muestras válidas, mínimo {min_samples}."
        )
    class_counts = np.bincount(bundle.y_class, minlength=2)
    if int(class_counts.min()) < 5:
        raise SystemExit(f"Clases insuficientes para CV: counts={class_counts.tolist()}.")
    if bundle.x_ghost.empty or bundle.x_ghost.isnull().any().any():
        raise SystemExit("Features Ghost inválidas: NaN o dataset vacío.")
    if not np.isfinite(bundle.x_consensus).all():
        raise SystemExit("Features Consensus inválidas: valores no finitos.")


def train_ghost(bundle: DatasetBundle, output_path: Path, positive_class_weight: float) -> dict:
    _, UltimateMLSystem = _load_runtime_classes()
    trainer = UltimateMLSystem(model_path=str(output_path))
    trainer.train(
        bundle.x_ghost,
        bundle.y_class,
        bundle.y_reg,
        positive_class_weight=positive_class_weight,
    )

    model_data = pickle.loads(output_path.read_bytes())
    clf = model_data.get("clf", {}).get("rf")
    reg = model_data.get("reg", {}).get("rf")
    metrics = {"samples": int(len(bundle.x_ghost))}
    if clf is not None and hasattr(clf, "predict"):
        pred = clf.predict(bundle.x_ghost)
        metrics["rf_train_accuracy"] = float(accuracy_score(bundle.y_class, pred))
        metrics["rf_train_f1"] = float(f1_score(bundle.y_class, pred, zero_division=0))
    if reg is not None and hasattr(reg, "predict"):
        pred_reg = reg.predict(bundle.x_ghost)
        metrics["rf_train_r2"] = float(r2_score(bundle.y_reg, pred_reg))
        metrics["rf_train_rmse"] = float(np.sqrt(mean_squared_error(bundle.y_reg, pred_reg)))
    return metrics


def _oversample_positive_class(
    X: np.ndarray, y: np.ndarray, positive_class_weight: float, seed: int = 42
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    pos_idx = np.flatnonzero(y == 1)
    neg_idx = np.flatnonzero(y == 0)
    if len(pos_idx) == 0 or len(neg_idx) == 0:
        return X, y

    target_pos = int(np.ceil(len(neg_idx) * max(1.0, positive_class_weight)))
    extra = max(0, target_pos - len(pos_idx))
    sampled_extra = rng.choice(pos_idx, size=extra, replace=True) if extra else np.array([], dtype=int)
    final_idx = np.concatenate([np.arange(len(y)), sampled_extra])
    rng.shuffle(final_idx)
    return X[final_idx], y[final_idx]


def train_consensus(
    bundle: DatasetBundle,
    output_path: Path,
    positive_class_weight: float,
    min_val_f1: float,
    min_recall: float,
) -> dict:
    AgentConsensusNN, _ = _load_runtime_classes()
    X_train, X_val, y_train, y_val = train_test_split(
        bundle.x_consensus,
        bundle.y_class,
        test_size=0.2,
        random_state=42,
        stratify=bundle.y_class,
    )

    X_train_balanced, y_train_balanced = _oversample_positive_class(
        X_train,
        y_train,
        positive_class_weight=positive_class_weight,
    )

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train_balanced)
    X_val_s = scaler.transform(X_val)

    clf = MLPClassifier(
        hidden_layer_sizes=(24, 12),
        activation="tanh",
        solver="adam",
        alpha=0.005,
        learning_rate="adaptive",
        max_iter=900,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=40,
        random_state=42,
        verbose=False,
    )
    clf.fit(X_train_s, y_train_balanced)

    proba = clf.predict_proba(X_val_s)[:, 1]
    actual_wins = int(y_val.sum())
    max_predicted_wins = min(
        len(y_val) - 1,
        max(actual_wins + 3, int(np.ceil(actual_wins * 1.5))),
    )
    threshold_rows = []
    best = None
    for threshold in np.round(np.arange(0.50, 0.901, 0.01), 2):
        preds_at_threshold = (proba >= threshold).astype(int)
        predicted_wins = int(preds_at_threshold.sum())
        precision = float(precision_score(y_val, preds_at_threshold, zero_division=0))
        recall = float(recall_score(y_val, preds_at_threshold, zero_division=0))
        f1 = float(f1_score(y_val, preds_at_threshold, zero_division=0))
        row = {
            "threshold": float(threshold),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "predicted_wins": predicted_wins,
        }
        threshold_rows.append(row)
        if recall < min_recall:
            continue
        if predicted_wins <= 0 or predicted_wins > max_predicted_wins:
            continue
        if best is None or (f1, precision, -predicted_wins) > (
            best["f1"],
            best["precision"],
            -best["predicted_wins"],
        ):
            best = row

    if best is None:
        fallback = max(threshold_rows, key=lambda item: (item["f1"], item["precision"]))
        raise SystemExit(
            "Consensus no discrimina bajo restricciones: "
            f"best_any_threshold={fallback}, actual_wins={actual_wins}, "
            f"max_predicted_wins={max_predicted_wins}. No se publica artefacto."
        )

    preds = (proba >= best["threshold"]).astype(int)
    val_f1 = float(f1_score(y_val, preds, zero_division=0))
    if val_f1 < min_val_f1:
        raise SystemExit(
            f"Consensus val_f1={val_f1:.4f} < mínimo requerido {min_val_f1:.4f}; no se publica artefacto."
        )

    output_path.write_bytes(
        pickle.dumps(
            {
                "model": clf,
                "scaler": scaler,
                "n_samples": int(len(X_train_balanced)),
                "source_train_samples": int(len(X_train)),
                "positive_class_weight": float(positive_class_weight),
                "probability_threshold": float(best["threshold"]),
                "threshold_policy": {
                    "range": "0.50..0.90 step 0.01",
                    "min_recall": float(min_recall),
                    "max_predicted_wins": int(max_predicted_wins),
                },
                "agent_names": AgentConsensusNN.AGENT_NAMES,
            }
        )
    )
    return {
        "samples": int(len(bundle.x_consensus)),
        "train_samples": int(len(X_train)),
        "balanced_train_samples": int(len(X_train_balanced)),
        "val_samples": int(len(X_val)),
        "valid_vote_rows": int(bundle.valid_consensus),
        "positive_class_weight": float(positive_class_weight),
        "probability_threshold": float(best["threshold"]),
        "threshold_precision": float(best["precision"]),
        "threshold_recall": float(best["recall"]),
        "max_allowed_predicted_wins": int(max_predicted_wins),
        "val_accuracy": float(accuracy_score(y_val, preds)),
        "val_f1": val_f1,
        "val_predicted_wins": int(preds.sum()),
        "val_actual_wins": int(y_val.sum()),
    }


def publish_legacy(models_dir: Path, root_dir: Path) -> None:
    copies = {
        models_dir / "agent_models.pkl": root_dir / "agent_models.pkl",
        models_dir / "v118_1H_consensus.pkl": root_dir / "v118_1H_consensus.pkl",
    }
    for src, dst in copies.items():
        if src.exists():
            shutil.copy2(src, dst)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline reproducible de modelos Sniper AI")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument("--min-samples", type=int, default=50)
    parser.add_argument("--min-abs-pnl", type=float, default=0.10)
    parser.add_argument("--positive-class-weight", type=float, default=3.0)
    parser.add_argument("--min-consensus-f1", type=float, default=0.35)
    parser.add_argument("--min-consensus-recall", type=float, default=0.30)
    parser.add_argument("--no-legacy-copy", action="store_true")
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"DB no encontrada: {args.db}")
    args.models_dir.mkdir(parents=True, exist_ok=True)

    rows, filtered_noise_rows = load_trade_rows(args.db, args.min_abs_pnl)
    bundle = build_dataset(rows, filtered_noise_rows)
    validate_dataset(bundle, args.min_samples)

    ghost_path = args.models_dir / "agent_models.pkl"
    consensus_path = args.models_dir / "v118_1H_consensus.pkl"
    ghost_metrics = train_ghost(bundle, ghost_path, args.positive_class_weight)
    if consensus_path.exists():
        consensus_path.unlink()
    legacy_consensus_path = ROOT / "v118_1H_consensus.pkl"
    if legacy_consensus_path.exists():
        legacy_consensus_path.unlink()
    consensus_metrics = train_consensus(
        bundle,
        consensus_path,
        positive_class_weight=args.positive_class_weight,
        min_val_f1=args.min_consensus_f1,
        min_recall=args.min_consensus_recall,
    )

    manifest = {
        "db": str(args.db),
        "rows_loaded": bundle.rows,
        "filtered_noise_rows": bundle.filtered_noise_rows,
        "min_abs_pnl": float(args.min_abs_pnl),
        "valid_ghost_rows": bundle.valid_ghost,
        "valid_consensus_vote_rows": bundle.valid_consensus,
        "class_balance": {
            "losses": int((bundle.y_class == 0).sum()),
            "wins": int((bundle.y_class == 1).sum()),
        },
        "artifacts": {
            "ghost": str(ghost_path),
            "consensus": str(consensus_path),
        },
        "metrics": {
            "ghost": ghost_metrics,
            "consensus": consensus_metrics,
        },
    }
    (args.models_dir / "training_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )

    if not args.no_legacy_copy:
        publish_legacy(args.models_dir, ROOT)

    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

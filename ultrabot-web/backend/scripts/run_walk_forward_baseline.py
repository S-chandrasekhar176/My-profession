"""Offline Walk-Forward Validation & Baseline Training Runner for UltraBot M3a.

Loads empirical point-in-time shadow outcomes from the database, executes strict
chronological cross-validation to eliminate lookahead bias, computes calibration
scorecards, and trains the baseline M3a model.
"""
import json
import logging
import sqlite3
import sys
from pathlib import Path

# Add backend directory to sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ml.dataset import MLDatasetBuilder
from ml.models import CalibratedLinearModel
from ml.walk_forward import WalkForwardValidator
from ml.inference import get_inference_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("walk_forward_runner")


def find_database_path() -> Path:
    candidates = [
        BACKEND_DIR / "data" / "ultrabot.db",
        BACKEND_DIR.parent.parent / "persist" / "ultrabot.db",
        BACKEND_DIR.parent / "persist" / "ultrabot.db",
        BACKEND_DIR / "persist" / "ultrabot.db",
        BACKEND_DIR / "ultrabot.db",
    ]
    for p in candidates:
        if p.exists() and p.stat().st_size > 0:
            return p
    raise FileNotFoundError("Could not locate active ultrabot.db SQLite database")


def run():
    db_path = find_database_path()
    logger.info("Connecting to database at %s", db_path)
    
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Query resolved shadow outcomes
    query = """
        SELECT * FROM shadow_outcomes
        WHERE outcome IN ('SHADOW_TARGET', 'SHADOW_SL', 'SHADOW_TIME_STOP')
        ORDER BY created_at ASC
    """
    rows = c.execute(query).fetchall()
    total_found = len(rows)
    logger.info("Found %d resolved shadow outcomes in database", total_found)
    
    if total_found < 20:
        logger.warning("Fewer than 20 resolved outcomes found (%d); aborting walk-forward run", total_found)
        return

    outcomes = [dict(r) for r in rows]
    
    # Build dataset
    engine = get_inference_engine()
    X, y = engine.builder.build_dataset(outcomes, fit_scaler=True)
    logger.info("Extracted feature matrix X: %s, target vector y: %s", X.shape, y.shape)
    
    wins = int(y.sum())
    losses = len(y) - wins
    baseline_win_rate = (wins / len(y)) * 100.0
    logger.info("Class distribution: Wins = %d (%.2f%%), Losses = %d (%.2f%%)", wins, baseline_win_rate, losses, 100.0 - baseline_win_rate)
    
    # Run training and walk-forward validation
    report = engine.train(outcomes, save_to_disk=True)
    
    print("\n" + "=" * 60)
    print("      ULTRABOT M3a OFFLINE WALK-FORWARD BASELINE REPORT      ")
    print("=" * 60)
    print(f"Total Point-in-Time Samples : {len(outcomes):,}")
    print(f"Features Evaluated          : {len(engine.builder.feature_names)}")
    print(f"Baseline Win Rate           : {report['baseline_win_rate_pct']:.2f}%")
    print(f"Model Win Rate (Conf >= 55%): {report['model_win_rate_pct']:.2f}%")
    print(f"Overall Edge Uplift         : {report['overall_uplift_pct']:+.2f}%")
    print(f"Overall Brier Score         : {report['overall_brier_score']:.4f}  (closer to 0.0 is better)")
    print(f"Overall ROC-AUC             : {report.get('overall_roc_auc', 0.5):.3f}  (>0.50 indicates edge)")
    print(f"Folds Evaluated             : {len(report.get('folds', []))}")
    print("-" * 60)
    print("TEMPORAL FOLD BREAKDOWN (Strict Chronological Forward Splits):")
    for f in report.get("folds", []):
        print(f"  Fold {f['fold']}: Train N={f.get('train_size', 0)}, Test N={f.get('test_size', 0)} | "
              f"Base WR: {f.get('baseline_win_rate', 0.0):.1f}% -> Model WR: {f.get('model_win_rate', 0.0):.1f}% "
              f"(Uplift {f.get('win_rate_uplift', 0.0):+.1f}%, Brier {f.get('brier_score', 0.0):.4f}, Veto Rate: {f.get('veto_rate', 0.0):.1f}%)")
    print("-" * 60)
    print(f"Model saved to: {engine.model_path}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    run()

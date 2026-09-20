"""Offline Walk-Forward Validation & Baseline Training Runner for UltraBot M3a.

Loads empirical point-in-time shadow outcomes from the database, executes strict
chronological cross-validation to eliminate lookahead bias, computes calibration
scorecards, and trains the baseline M3a model.
"""
import asyncio
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any, List

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


async def load_outcomes_from_repo() -> List[Any]:
    """Load all resolved shadow outcomes via Repository (NF11-A)."""
    from db.database import async_session_factory
    from db.repository import Repository
    async with async_session_factory() as session:
        repo = Repository(session)
        return await repo.get_shadow_outcomes_history(limit=None, only_resolved=True)


def run():
    logger.info("Loading resolved shadow outcomes from repository (NF11-A single source of truth)...")
    try:
        outcomes = asyncio.run(load_outcomes_from_repo())
        logger.info("Loaded %d resolved shadow outcomes from Repository", len(outcomes))
    except Exception as exc:
        logger.warning("Repository load failed (%s); falling back to direct SQLite", exc)
        db_path = find_database_path()
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        query = """
            SELECT * FROM shadow_outcomes
            WHERE outcome IN ('SHADOW_TARGET', 'SHADOW_SL', 'SHADOW_TIME_STOP', 'SHADOW_EXPIRED')
            ORDER BY created_at ASC
        """
        rows = c.execute(query).fetchall()
        outcomes = [dict(r) for r in rows]
        logger.info("Loaded %d resolved shadow outcomes via SQLite fallback", len(outcomes))

    total_found = len(outcomes)
    if total_found < 20:
        logger.warning("Fewer than 20 resolved outcomes found (%d); aborting walk-forward run", total_found)
        return
    
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

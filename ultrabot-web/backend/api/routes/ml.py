"""FastAPI router for UltraBot M3a Machine Learning Pipeline."""
import asyncio
import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_current_user, get_repository
from db.repository import Repository
from ml.inference import get_inference_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ml", tags=["ml"])


class ScoreSignalRequest(BaseModel):
    symbol: str = Field(..., description="NSE Trading symbol")
    direction: str = Field("BUY", description="BUY or SELL")
    strategy: str = Field("ORB", description="Strategy identifier")
    vix: float = Field(15.0, description="India VIX at signal time")
    regime: str = Field("sideways", description="Market regime")
    pcr: float = Field(1.0, description="Put-Call Ratio")
    iv_rank: float = Field(50.0, description="Implied Volatility Rank (0-100)")


class TrainModelRequest(BaseModel):
    use_synthetic_bootstrap_if_sparse: bool = Field(True, description="Augment with statistical bootstrap if live samples < 50")
    min_samples_threshold: int = Field(50, ge=10, le=1000)


@router.get("/status")
async def get_ml_status(
    _user: Any = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return runtime diagnostic telemetry for the M3a ML model."""
    engine = get_inference_engine()
    return engine.get_status()


@router.get("/metrics")
async def get_ml_metrics(
    _user: Any = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return institutional scorecard metrics, drift analysis, and calibration curve."""
    engine = get_inference_engine()
    return engine.get_scorecard_metrics()


@router.get("/evaluations")
async def get_ml_evaluations(
    limit: int = 50,
    action: Optional[str] = None,
    _user: Any = Depends(get_current_user),
) -> Dict[str, Any]:
    """Retrieve recent signal evaluations with feature contributions and pipeline steps."""
    engine = get_inference_engine()
    items = engine.get_recent_evaluations(limit=limit, action=action)
    return {
        "count": len(items),
        "evaluations": items,
    }


@router.post("/train")
async def trigger_training(
    req: TrainModelRequest = TrainModelRequest(),
    repo: Repository = Depends(get_repository),
    _user: Any = Depends(get_current_user),
) -> Dict[str, Any]:
    """Train M3a model using accumulated shadow_outcomes with walk-forward validation."""
    engine = get_inference_engine()

    # Query all historical shadow outcomes from DB
    outcomes = []
    try:
        if hasattr(repo, "get_shadow_outcomes_history"):
            outcomes = await repo.get_shadow_outcomes_history(limit=500)
        elif hasattr(repo, "get_shadow_outcomes_today"):
            outcomes = await repo.get_shadow_outcomes_today()
    except Exception as e:
        logger.warning("Could not load shadow_outcomes from DB: %s", e)

    # If database samples are fewer than threshold, augment or bootstrap
    if len(outcomes) < req.min_samples_threshold and req.use_synthetic_bootstrap_if_sparse:
        logger.info(
            "Live shadow outcomes (%d) < threshold (%d); augmenting with synthetic bootstrap",
            len(outcomes),
            req.min_samples_threshold,
        )
        bootstrap = engine.builder.generate_synthetic_bootstrap(n_samples=req.min_samples_threshold)
        training_data = list(outcomes) + bootstrap
    else:
        training_data = list(outcomes)

    if not training_data:
        raise HTTPException(status_code=400, detail="Insufficient training data available")

    # Run training and walk-forward validation
    try:
        report = engine.train(training_data, save_to_disk=True)
        return {
            "status": "success",
            "message": f"M3a model trained successfully on {len(training_data)} samples",
            "samples_count": len(training_data),
            "report": report,
        }
    except Exception as exc:
        logger.error("M3a training failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Training failed: {str(exc)}")


@router.post("/score")
async def score_signal_endpoint(
    req: ScoreSignalRequest,
    _user: Any = Depends(get_current_user),
) -> Dict[str, Any]:
    """Score a signal with the M3a model to get win probability and G21 advisory rating."""
    engine = get_inference_engine()
    signal_dict = {
        "symbol": req.symbol,
        "direction": req.direction,
        "strategy": req.strategy,
    }
    return engine.score_signal(
        signal=signal_dict,
        candles_df=None,
        vix=req.vix,
        regime=req.regime,
        pcr=req.pcr,
        iv_rank=req.iv_rank,
    )

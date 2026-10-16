import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from threading import Lock
from datetime import timedelta

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_recruiter
from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.database.session import Base, SessionLocal, engine, get_db
from app.models.entities import AnalysisJob, ApiUsage, AuditEvent, AudioAsset, Candidate, Interview, Recruiter, Review, now_utc
from app.schemas.api import AnalyzeRequest, CandidateCreate, InterviewCreate, LoginRequest, RecruiterCreate, ReviewCreate
from app.services.audio_service import delete_storage_file, save_upload
from app.services.detectif_service import DetectifError, DetectifService


Base.metadata.create_all(bind=engine)
@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not settings.demo_mode:
        if not settings.detectif_api_key:
            raise RuntimeError("DETECTIF_API_KEY must be configured when DEMO_MODE=false.")
        if len(settings.jwt_secret) < 32 or settings.jwt_secret == "change-this-development-secret-before-deployment" or "replace-with" in settings.jwt_secret.lower():
            raise RuntimeError("Set JWT_SECRET to a unique random value of at least 32 characters when DEMO_MODE=false.")
    apply_audio_retention()
    yield


app = FastAPI(title="HireProof AI", version="1.0.0", description="Recruiter-led interview audio authenticity review. AI output is a signal for human review.", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True, allow_methods=["GET", "POST", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])
if os.getenv("VERCEL"):
    app.frontend("/", directory="frontend/dist")

_rate_events: dict[int, list[float]] = {}
_credit_lock = Lock()


def apply_audio_retention():
    if settings.audio_retention_days <= 0:
        return
    cutoff = (now_utc() - timedelta(days=settings.audio_retention_days)).replace(tzinfo=None)
    with SessionLocal() as db:
        expired = db.scalars(select(AudioAsset).where(AudioAsset.created_at < cutoff)).all()
        for asset in expired:
            delete_storage_file(asset.storage_path)
            db.query(AnalysisJob).filter(AnalysisJob.interview_id == asset.interview_id, AnalysisJob.audio_sha256 == asset.sha256).delete(synchronize_session=False)
            db.delete(asset)
        if expired:
            db.commit()


def _audit(db: Session, recruiter: Recruiter, action: str, resource_type: str, resource_id: object):
    db.add(AuditEvent(recruiter_id=recruiter.id, action=action, resource_type=resource_type, resource_id=str(resource_id)))


def _own_interview(db: Session, interview_id: int, recruiter: Recruiter) -> Interview:
    row = db.scalar(select(Interview).where(Interview.id == interview_id, Interview.created_by == recruiter.id))
    if not row:
        raise HTTPException(404, "Interview not found.")
    return row


def _rate_limit(recruiter_id: int):
    now = time.monotonic()
    recent = [stamp for stamp in _rate_events.get(recruiter_id, []) if now - stamp < 60]
    if len(recent) >= settings.max_requests_per_minute:
        raise HTTPException(429, "Too many requests. Please try again shortly.")
    recent.append(now)
    _rate_events[recruiter_id] = recent


def _reserve_budget(db: Session, recruiter: Recruiter, service: DetectifService, idempotency_key: str, operation: str, interview_id: int, checks: int = 1) -> tuple[int, ApiUsage]:
    # Serialize local budget checks and persist a reservation before starting a paid operation.
    with _credit_lock:
        existing = db.scalar(select(ApiUsage).where(ApiUsage.recruiter_id == recruiter.id, ApiUsage.idempotency_key == idempotency_key))
        if existing and existing.status in {"reserved", "unknown"}:
            if existing.created_at.replace(tzinfo=None) < (now_utc() - timedelta(hours=24)).replace(tzinfo=None):
                raise DetectifError(409, "idempotency_window_expired", "The operation outcome is unresolved and its provider idempotency key is older than 24 hours. Reconcile it in the DetectifAI dashboard before retrying.")
            return int((existing.credits_reserved or 0) / checks) if checks else 0, existing
        credits_body, _ = service.get_credits()
        available = credits_body.get("available")
        price = credits_body.get("price_per_operation")
        unlimited = credits_body.get("unlimited") is True
        spent = db.scalar(select(func.coalesce(func.sum(ApiUsage.credits_charged), 0)).where(ApiUsage.recruiter_id == recruiter.id)) or 0
        reserved = db.scalar(select(func.coalesce(func.sum(ApiUsage.credits_reserved), 0)).where(ApiUsage.recruiter_id == recruiter.id)) or 0
        if price is None or (not unlimited and available is None):
            raise DetectifError(503, "credit_quote_unavailable", "Could not confirm the documented provider operation price and balance.")
        estimated = int(price) * checks
        if not unlimited and available < estimated:
            raise DetectifError(402, "insufficient_credits", "Provider balance is below the documented operation price.")
        if spent + reserved + estimated > settings.max_api_credit_budget:
            raise DetectifError(402, "local_credit_budget_exceeded", "Configured local API credit budget reached. An authorized recruiter must raise the limit to continue.")
        if existing:
            existing.status = "reserved"
            existing.credits_reserved = estimated
            existing.error_code = None
            existing.latency_ms = None
            reservation = existing
        else:
            reservation = ApiUsage(recruiter_id=recruiter.id, interview_id=interview_id, operation=operation, status="reserved", credits_reserved=estimated, idempotency_key=idempotency_key)
            db.add(reservation)
        db.commit()
        db.refresh(reservation)
        return int(price), reservation


def _settle_usage(db: Session, usage: ApiUsage, status: str, price: int, *, body: dict | None = None, latency: float | None = None, error: DetectifError | None = None):
    payload = error.payload if error else (body or {})
    charged = payload.get("credits", {}).get("charged")
    if charged is None and payload.get("partial"):
        charged = payload.get("partial", {}).get("credits", {}).get("charged")
    usage.status = "unknown" if error and error.code == "provider_timeout" and charged is None else status
    usage.credits_reserved = price if usage.status == "unknown" else 0
    usage.credits_charged = charged
    if status == "completed" and usage.credits_charged is None:
        usage.credits_charged = price
    usage.latency_ms = latency
    usage.provider_operation_id = (body or {}).get("operation_id")
    usage.result_json = json.dumps(payload) if payload else None
    usage.error_code = error.code if error else None
    db.commit()


@app.get("/api/health")
def health():
    return {"status": "ok", "demo_mode": settings.demo_mode}


@app.post("/api/auth/register", status_code=201)
def register(body: RecruiterCreate, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(Recruiter).where(Recruiter.email == email)):
        raise HTTPException(409, "An account with this email already exists.")
    recruiter = Recruiter(name=body.name.strip(), email=email, password_hash=hash_password(body.password))
    db.add(recruiter)
    db.commit()
    db.refresh(recruiter)
    return {"access_token": create_access_token(str(recruiter.id)), "token_type": "bearer", "recruiter": {"id": recruiter.id, "name": recruiter.name, "email": recruiter.email}}


@app.post("/api/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    recruiter = db.scalar(select(Recruiter).where(Recruiter.email == body.email.lower()))
    if not recruiter or not verify_password(body.password, recruiter.password_hash):
        raise HTTPException(401, "Email or password is incorrect.")
    return {"access_token": create_access_token(str(recruiter.id)), "token_type": "bearer", "recruiter": {"id": recruiter.id, "name": recruiter.name, "email": recruiter.email}}


@app.get("/api/auth/me")
def me(recruiter: Recruiter = Depends(current_recruiter)):
    return {"id": recruiter.id, "name": recruiter.name, "email": recruiter.email}


@app.post("/api/candidates", status_code=201)
def create_candidate(body: CandidateCreate, db: Session = Depends(get_db), recruiter: Recruiter = Depends(current_recruiter)):
    if not body.consent_audio:
        raise HTTPException(400, "Candidate audio-processing consent is required before registration.")
    row = Candidate(name=body.name.strip(), email=body.email.lower(), job_role=body.job_role.strip(), consent_audio=True, created_by=recruiter.id)
    db.add(row)
    db.flush()
    _audit(db, recruiter, "candidate.created", "candidate", row.id)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "name": row.name, "email": row.email, "job_role": row.job_role, "consent_audio": row.consent_audio, "created_at": row.created_at}


@app.get("/api/candidates")
def list_candidates(q: str = "", db: Session = Depends(get_db), recruiter: Recruiter = Depends(current_recruiter)):
    stmt = select(Candidate).where(Candidate.created_by == recruiter.id)
    if q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where((Candidate.name.ilike(term)) | (Candidate.email.ilike(term)) | (Candidate.job_role.ilike(term)))
    rows = db.scalars(stmt.order_by(Candidate.created_at.desc())).all()
    return [{"id": c.id, "name": c.name, "email": c.email, "job_role": c.job_role, "consent_audio": c.consent_audio, "created_at": c.created_at} for c in rows]


@app.post("/api/interviews", status_code=201)
def create_interview(body: InterviewCreate, db: Session = Depends(get_db), recruiter: Recruiter = Depends(current_recruiter)):
    candidate = db.scalar(select(Candidate).where(Candidate.id == body.candidate_id, Candidate.created_by == recruiter.id))
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    if not candidate.consent_audio:
        raise HTTPException(400, "Candidate has not consented to audio processing.")
    row = Interview(interview_code=f"HP-{uuid.uuid4().hex[:8].upper()}", candidate_id=candidate.id, created_by=recruiter.id, scheduled_at=body.scheduled_at)
    db.add(row)
    db.flush()
    _audit(db, recruiter, "interview.created", "interview", row.id)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "interview_code": row.interview_code, "candidate_id": row.candidate_id, "status": row.status, "scheduled_at": row.scheduled_at, "created_at": row.created_at}


@app.get("/api/interviews")
def list_interviews(q: str = "", result: str = "", db: Session = Depends(get_db), recruiter: Recruiter = Depends(current_recruiter)):
    stmt = select(Interview).join(Candidate).where(Interview.created_by == recruiter.id)
    if q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where((Candidate.name.ilike(term)) | (Candidate.email.ilike(term)) | (Interview.interview_code.ilike(term)))
    rows = db.scalars(stmt.order_by(Interview.created_at.desc())).all()
    output = []
    for interview in rows:
        latest = db.scalar(select(AnalysisJob).where(AnalysisJob.interview_id == interview.id).order_by(AnalysisJob.created_at.desc()))
        if result and (not latest or latest.status != result):
            continue
        review = db.scalar(select(Review).where(Review.interview_id == interview.id).order_by(Review.created_at.desc()))
        output.append({"id": interview.id, "interview_code": interview.interview_code, "candidate_id": interview.candidate_id, "candidate_name": interview.candidate.name, "candidate_email": interview.candidate.email, "job_role": interview.candidate.job_role, "status": interview.status, "analysis_status": latest.status if latest else "pending", "analysis_result": json.loads(latest.result_json) if latest and latest.result_json else None, "review_status": review.decision if review else "pending", "created_at": interview.created_at})
    return output


@app.post("/api/interviews/{interview_id}/audio", status_code=201)
async def upload_audio(interview_id: int, consent: bool = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db), recruiter: Recruiter = Depends(current_recruiter)):
    _rate_limit(recruiter.id)
    interview = _own_interview(db, interview_id, recruiter)
    if not consent or not interview.candidate.consent_audio:
        raise HTTPException(400, "Explicit audio-processing consent is required before upload.")
    saved = await save_upload(file)
    previous = db.scalars(select(AudioAsset).where(AudioAsset.interview_id == interview.id)).all()
    for asset in previous:
        delete_storage_file(asset.storage_path)
        db.delete(asset)
    asset = AudioAsset(interview_id=interview.id, sha256=saved["sha256"], storage_path=saved["path"], media_type=saved["mime"], original_name=saved["name"], size_bytes=saved["size"], duration_seconds=saved["duration"])
    db.add(asset)
    interview.status = "audio_uploaded"
    _audit(db, recruiter, "audio.uploaded", "interview", interview.id)
    db.commit()
    db.refresh(asset)
    return {"id": asset.id, "sha256": asset.sha256, "size_bytes": asset.size_bytes, "media_type": asset.media_type, "duration_seconds": asset.duration_seconds, "status": "ready_for_analysis"}


@app.post("/api/interviews/{interview_id}/analyze")
def analyze(interview_id: int, body: AnalyzeRequest, db: Session = Depends(get_db), recruiter: Recruiter = Depends(current_recruiter)):
    _rate_limit(recruiter.id)
    if not body.confirm:
        raise HTTPException(400, "Confirm paid audio analysis before submitting.")
    interview = _own_interview(db, interview_id, recruiter)
    asset = db.scalar(select(AudioAsset).where(AudioAsset.interview_id == interview.id).order_by(AudioAsset.created_at.desc()))
    if not asset:
        raise HTTPException(404, "Upload consented audio before analysis.")
    if asset.duration_seconds is not None and not 4 < asset.duration_seconds <= 120:
        raise HTTPException(422, "Deepfake analysis requires audio longer than 4 seconds and no longer than 120 seconds.")
    same_analysis = (AnalysisJob.created_by == recruiter.id, AnalysisJob.interview_id == interview.id, AnalysisJob.audio_sha256 == asset.sha256, AnalysisJob.operation == "deepfake.detect", AnalysisJob.demo == settings.demo_mode)
    recent_cutoff = (now_utc() - timedelta(hours=24)).replace(tzinfo=None)
    cache_query = select(AnalysisJob).where(*same_analysis, AnalysisJob.status == "completed")
    if not settings.demo_mode:
        cache_query = cache_query.where(AnalysisJob.completed_at >= recent_cutoff)
    prior = db.scalar(cache_query.order_by(AnalysisJob.completed_at.desc()))
    if prior:
        return {"id": prior.id, "status": prior.status, "result": json.loads(prior.result_json or "{}"), "cached": True, "demo": prior.demo}
    existing = db.scalar(select(AnalysisJob).where(*same_analysis, AnalysisJob.status == "processing").order_by(AnalysisJob.created_at.desc()))
    if existing:
        if existing.created_at.replace(tzinfo=None) >= (now_utc() - timedelta(seconds=180)).replace(tzinfo=None):
            raise HTTPException(409, "This audio is already being analyzed.")
        existing.status = "failed"
        existing.error_code = "provider_timeout"
        existing.completed_at = now_utc()
        db.commit()
    job = db.scalar(select(AnalysisJob).where(*same_analysis, AnalysisJob.status == "failed").order_by(AnalysisJob.created_at.desc()))
    if job and job.error_code == "provider_timeout" and job.created_at.replace(tzinfo=None) < recent_cutoff:
        unresolved = db.scalar(select(ApiUsage).where(ApiUsage.recruiter_id == recruiter.id, ApiUsage.idempotency_key == job.provider_idempotency_key, ApiUsage.status.in_(["reserved", "unknown"])))
        if unresolved:
            raise HTTPException(409, "The provider outcome is unknown and the idempotency key is older than 24 hours. Reconcile the operation in the DetectifAI dashboard before submitting again.")
        job = None
    if job and job.created_at.replace(tzinfo=None) < recent_cutoff:
        job = None
    if job:
        job.status = "processing"
        job.error_code = None
        job.provider_operation_id = None
        job.result_json = None
        job.interview_id = interview.id
        job.demo = settings.demo_mode
        job.provider_idempotency_key = job.provider_idempotency_key or str(uuid.uuid4())
    else:
        job = AnalysisJob(created_by=recruiter.id, interview_id=interview.id, audio_sha256=asset.sha256, operation="deepfake.detect", config_version=f"detectif-default:{uuid.uuid4().hex}", status="processing", demo=settings.demo_mode, provider_idempotency_key=str(uuid.uuid4()))
        db.add(job)
    db.commit()
    db.refresh(job)
    if settings.demo_mode:
        # Demo output is illustrative and intentionally inconclusive, never represented as provider analysis.
        result = {"verdict": "not_sure", "score": None, "threshold": None, "score_semantics": "No model ran; demo mode is illustrative only.", "decided_by": "demo", "duration_seconds": asset.duration_seconds, "demo_notice": "Simulated demo output — not a DetectifAI result."}
        status, credits, latency, operation_id = "completed", None, None, None
    else:
        service = DetectifService()
        reservation = None
        price = 0
        try:
            provider_key = job.provider_idempotency_key or str(uuid.uuid4())
            job.provider_idempotency_key = provider_key
            db.commit()
            price, reservation = _reserve_budget(db, recruiter, service, provider_key, "deepfake.detect", interview.id)
            provider, latency = service.detect(asset.storage_path, provider_key)
            _settle_usage(db, reservation, "completed", price, body=provider, latency=latency)
            result = provider
            status = "completed"
            operation_id = provider.get("operation_id")
            credits = provider.get("credits", {}).get("charged")
            if credits is None:
                credits = price
        except DetectifError as exc:
            if reservation:
                _settle_usage(db, reservation, "failed", price, error=exc)
            else:
                db.add(ApiUsage(recruiter_id=recruiter.id, interview_id=interview.id, operation="deepfake.detect", status="failed", error_code=exc.code))
            status, operation_id, latency, credits = "failed", None, None, None
            result = {"error": {"code": exc.code, "message": exc.message, "retry_after": exc.retry_after}}
            job.error_code = exc.code
    job.status = status
    job.provider_operation_id = operation_id
    job.result_json = json.dumps(result)
    job.completed_at = now_utc()
    interview.status = "analysis_complete" if status == "completed" else "analysis_failed"
    _audit(db, recruiter, "analysis.submitted", "interview", interview.id)
    db.commit()
    return {"id": job.id, "status": job.status, "result": result, "cached": False, "demo": job.demo}


@app.get("/api/interviews/{interview_id}")
def interview_detail(interview_id: int, db: Session = Depends(get_db), recruiter: Recruiter = Depends(current_recruiter)):
    interview = _own_interview(db, interview_id, recruiter)
    asset = db.scalar(select(AudioAsset).where(AudioAsset.interview_id == interview.id).order_by(AudioAsset.created_at.desc()))
    analyses = db.scalars(select(AnalysisJob).where(AnalysisJob.interview_id == interview.id).order_by(AnalysisJob.created_at.desc())).all()
    reviews = db.scalars(select(Review).where(Review.interview_id == interview.id).order_by(Review.created_at.desc())).all()
    speaker_checks = db.scalars(select(ApiUsage).where(ApiUsage.interview_id == interview.id, ApiUsage.operation.in_(["speaker.enroll", "speaker.verify"])).order_by(ApiUsage.created_at.desc())).all()
    return {"id": interview.id, "interview_code": interview.interview_code, "candidate": {"id": interview.candidate.id, "name": interview.candidate.name, "email": interview.candidate.email, "job_role": interview.candidate.job_role}, "status": interview.status, "audio": {"sha256": asset.sha256, "size_bytes": asset.size_bytes, "duration_seconds": asset.duration_seconds, "consented_at": asset.consented_at} if asset else None, "analyses": [{"id": a.id, "operation": a.operation, "status": a.status, "provider_operation_id": a.provider_operation_id, "result": json.loads(a.result_json) if a.result_json else None, "error_code": a.error_code, "demo": a.demo, "created_at": a.created_at} for a in analyses], "speaker_checks": [{"id": s.id, "operation": s.operation, "status": s.status, "provider_operation_id": s.provider_operation_id, "result": json.loads(s.result_json) if s.result_json else None, "error_code": s.error_code, "created_at": s.created_at} for s in speaker_checks], "reviews": [{"id": r.id, "decision": r.decision, "note": r.note, "recruiter": r.recruiter.name, "created_at": r.created_at} for r in reviews]}


@app.post("/api/interviews/{interview_id}/review", status_code=201)
def add_review(interview_id: int, body: ReviewCreate, db: Session = Depends(get_db), recruiter: Recruiter = Depends(current_recruiter)):
    interview = _own_interview(db, interview_id, recruiter)
    row = Review(interview_id=interview.id, recruiter_id=recruiter.id, decision=body.decision, note=body.note.strip())
    db.add(row)
    _audit(db, recruiter, "review.added", "interview", interview.id)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "decision": row.decision, "note": row.note, "created_at": row.created_at}


@app.delete("/api/interviews/{interview_id}/audio")
def delete_audio(interview_id: int, db: Session = Depends(get_db), recruiter: Recruiter = Depends(current_recruiter)):
    interview = _own_interview(db, interview_id, recruiter)
    assets = db.scalars(select(AudioAsset).where(AudioAsset.interview_id == interview.id)).all()
    for asset in assets:
        delete_storage_file(asset.storage_path)
        db.delete(asset)
    db.query(AnalysisJob).filter(AnalysisJob.interview_id == interview.id).delete(synchronize_session=False)
    db.query(ApiUsage).filter(ApiUsage.interview_id == interview.id, ApiUsage.operation == "deepfake.detect").update({ApiUsage.interview_id: None, ApiUsage.result_json: None, ApiUsage.provider_operation_id: None, ApiUsage.idempotency_key: None}, synchronize_session=False)
    interview.status = "created"
    _audit(db, recruiter, "audio.deleted", "interview", interview.id)
    db.commit()
    return {"deleted": True, "audio_assets_removed": len(assets), "analysis_records_removed": True}


@app.delete("/api/candidates/{candidate_id}")
def delete_candidate(candidate_id: int, db: Session = Depends(get_db), recruiter: Recruiter = Depends(current_recruiter)):
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id, Candidate.created_by == recruiter.id))
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    interviews = db.scalars(select(Interview).where(Interview.candidate_id == candidate.id, Interview.created_by == recruiter.id)).all()
    interview_ids = [row.id for row in interviews]
    speaker_cleanup = "not_configured"
    if not settings.demo_mode and settings.detectif_api_key:
        try:
            DetectifService().delete_speaker(f"candidate-{candidate.id}")
            speaker_cleanup = "deleted"
        except DetectifError as exc:
            if exc.status == 404:
                speaker_cleanup = "not_found"
            else:
                raise HTTPException(502, {"code": "provider_speaker_cleanup_failed", "message": "Could not delete the candidate's provider speaker profile; local data was retained."})
    for interview in interviews:
        assets = db.scalars(select(AudioAsset).where(AudioAsset.interview_id == interview.id)).all()
        for asset in assets:
            delete_storage_file(asset.storage_path)
        db.query(AudioAsset).filter(AudioAsset.interview_id == interview.id).delete(synchronize_session=False)
        db.query(AnalysisJob).filter(AnalysisJob.interview_id == interview.id).delete(synchronize_session=False)
        db.query(Review).filter(Review.interview_id == interview.id).delete(synchronize_session=False)
        db.delete(interview)
    if interview_ids:
        db.query(ApiUsage).filter(ApiUsage.interview_id.in_(interview_ids)).update({ApiUsage.interview_id: None, ApiUsage.result_json: None, ApiUsage.provider_operation_id: None, ApiUsage.idempotency_key: None}, synchronize_session=False)
    _audit(db, recruiter, "candidate.deleted", "candidate", candidate.id)
    db.delete(candidate)
    db.commit()
    return {"deleted": True, "interviews_removed": len(interviews), "provider_speaker_cleanup": speaker_cleanup}


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), recruiter: Recruiter = Depends(current_recruiter)):
    interview_ids = select(Interview.id).where(Interview.created_by == recruiter.id)
    analyses = db.scalars(select(AnalysisJob).where(AnalysisJob.interview_id.in_(interview_ids))).all()
    interviews_count = db.scalar(select(func.count()).select_from(Interview).where(Interview.created_by == recruiter.id)) or 0
    candidates_count = db.scalar(select(func.count()).select_from(Candidate).where(Candidate.created_by == recruiter.id)) or 0
    latest_jobs = {}
    for job in sorted(analyses, key=lambda a: a.created_at):
        latest_jobs[job.interview_id] = job
    suspicious = sum(1 for a in latest_jobs.values() if a.status == "completed" and _verdict(a) == "fake")
    inconclusive = sum(1 for a in latest_jobs.values() if a.status == "completed" and _verdict(a) == "not_sure")
    verified = sum(1 for a in latest_jobs.values() if a.status == "completed" and _verdict(a) == "real")
    reviewed = {r.interview_id for r in db.scalars(select(Review).where(Review.interview_id.in_(interview_ids))).all()}
    return {"total_interviews": interviews_count, "total_candidates": candidates_count, "verified_analyses": verified, "suspicious_results": suspicious, "inconclusive_results": inconclusive, "pending_reviews": sum(1 for key, a in latest_jobs.items() if key not in reviewed and a.status == "completed" and _verdict(a) in {"fake", "not_sure"}), "demo_mode": settings.demo_mode}


def _verdict(job: AnalysisJob):
    try:
        return json.loads(job.result_json or "{}").get("result", {}).get("verdict") or json.loads(job.result_json or "{}").get("verdict")
    except (ValueError, TypeError):
        return None


@app.get("/api/usage")
def usage(db: Session = Depends(get_db), recruiter: Recruiter = Depends(current_recruiter)):
    records = db.scalars(select(ApiUsage).where(ApiUsage.recruiter_id == recruiter.id).order_by(ApiUsage.created_at.desc()).limit(100)).all()
    spent = db.scalar(select(func.coalesce(func.sum(ApiUsage.credits_charged), 0)).where(ApiUsage.recruiter_id == recruiter.id)) or 0
    reserved = db.scalar(select(func.coalesce(func.sum(ApiUsage.credits_reserved), 0)).where(ApiUsage.recruiter_id == recruiter.id)) or 0
    return {"tracked_credits_spent": spent, "reserved_credits": reserved, "local_budget_limit": settings.max_api_credit_budget, "provider_balance": None, "provider_balance_status": "not refreshed", "warning_threshold": settings.low_credit_warning_threshold, "tracked_requests": len(records), "records": [{"operation": r.operation, "status": r.status, "credits_charged": r.credits_charged, "credits_reserved": r.credits_reserved, "latency_ms": r.latency_ms, "error_code": r.error_code, "created_at": r.created_at} for r in records]}


@app.post("/api/usage/refresh-balance")
def refresh_provider_balance(recruiter: Recruiter = Depends(current_recruiter)):
    if settings.demo_mode or not settings.detectif_api_key:
        return {"provider_balance": None, "provider_balance_status": "unknown/not configured"}
    try:
        body, _ = DetectifService().get_credits()
        return {"provider_balance": body, "provider_balance_status": "live"}
    except DetectifError as exc:
        return {"provider_balance": None, "provider_balance_status": f"unavailable: {exc.code}"}


@app.post("/api/interviews/{interview_id}/speaker-enrollment", status_code=201)
async def speaker_enrollment(interview_id: int, confirm: bool = Form(...), candidate_consent: bool = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db), recruiter: Recruiter = Depends(current_recruiter)):
    _rate_limit(recruiter.id)
    if settings.demo_mode:
        raise HTTPException(409, "Speaker enrollment is disabled in demo mode; no real identity check has run.")
    if not confirm:
        raise HTTPException(400, "Confirm voice enrollment and associated provider credit use.")
    if not candidate_consent:
        raise HTTPException(400, "Separate informed consent is required before voice enrollment.")
    interview = _own_interview(db, interview_id, recruiter)
    saved = await save_upload(file)
    if saved["duration"] is not None and not 15 <= saved["duration"] <= 60:
        delete_storage_file(saved["path"])
        raise HTTPException(422, "Voice enrollment requires 15 to 60 seconds of audio.")
    idem = str(uuid.uuid5(uuid.NAMESPACE_URL, f"hireproof:{recruiter.id}:{interview.candidate_id}:speaker.enroll:{saved['sha256']}"))
    previous = db.scalar(select(ApiUsage).where(ApiUsage.recruiter_id == recruiter.id, ApiUsage.idempotency_key == idem))
    if previous and previous.status == "completed" and previous.result_json:
        delete_storage_file(saved["path"])
        return {"status": "completed", "result": json.loads(previous.result_json), "cached": True, "meaning": "Speaker enrollment is separate from AI-generated speech detection."}
    reservation = None
    price = 0
    try:
        service = DetectifService()
        price, reservation = _reserve_budget(db, recruiter, service, idem, "speaker.enroll", interview.id)
        body, latency = service.enroll(saved["path"], f"candidate-{interview.candidate_id}", f"Candidate {interview.candidate_id}", idem)
        _settle_usage(db, reservation, "completed", price, body=body, latency=latency)
    except DetectifError as exc:
        if reservation:
            _settle_usage(db, reservation, "failed", price, error=exc)
        else:
            db.add(ApiUsage(recruiter_id=recruiter.id, interview_id=interview.id, operation="speaker.enroll", status="failed", error_code=exc.code))
            db.commit()
        delete_storage_file(saved["path"])
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message})
    delete_storage_file(saved["path"])
    _audit(db, recruiter, "speaker.enrolled", "interview", interview.id)
    db.commit()
    return {"status": "completed", "result": body, "meaning": "Speaker enrollment is separate from AI-generated speech detection."}


@app.post("/api/interviews/{interview_id}/speaker-verification")
async def speaker_verification(interview_id: int, confirm: bool = Form(...), candidate_consent: bool = Form(...), detect_deepfake: bool = Form(False), file: UploadFile = File(...), db: Session = Depends(get_db), recruiter: Recruiter = Depends(current_recruiter)):
    _rate_limit(recruiter.id)
    if settings.demo_mode:
        raise HTTPException(409, "Speaker verification is disabled in demo mode; no real identity check has run.")
    if not confirm:
        raise HTTPException(400, "Confirm speaker verification and associated provider credit use.")
    if not candidate_consent:
        raise HTTPException(400, "Separate informed consent is required before speaker verification.")
    interview = _own_interview(db, interview_id, recruiter)
    saved = await save_upload(file)
    if saved["duration"] is not None and not 3 <= saved["duration"] <= 60:
        delete_storage_file(saved["path"])
        raise HTTPException(422, "Speaker verification requires 3 to 60 seconds of audio.")
    idem = str(uuid.uuid5(uuid.NAMESPACE_URL, f"hireproof:{recruiter.id}:{interview.candidate_id}:speaker.verify:{saved['sha256']}:{detect_deepfake}"))
    previous = db.scalar(select(ApiUsage).where(ApiUsage.recruiter_id == recruiter.id, ApiUsage.idempotency_key == idem))
    if previous and previous.status == "completed" and previous.result_json:
        delete_storage_file(saved["path"])
        return {"status": "completed", "result": json.loads(previous.result_json), "cached": True, "meaning": "Similarity is not a percentage and is not conclusive proof of identity."}
    reservation = None
    price = 0
    try:
        service = DetectifService()
        price, reservation = _reserve_budget(db, recruiter, service, idem, "speaker.verify", interview.id, 2 if detect_deepfake else 1)
        body, latency = service.verify(saved["path"], f"candidate-{interview.candidate_id}", idem, detect_deepfake)
        _settle_usage(db, reservation, "completed", price * (2 if detect_deepfake else 1), body=body, latency=latency)
    except DetectifError as exc:
        if reservation:
            _settle_usage(db, reservation, "failed", price * (2 if detect_deepfake else 1), error=exc)
        else:
            db.add(ApiUsage(recruiter_id=recruiter.id, interview_id=interview.id, operation="speaker.verify", status="failed", error_code=exc.code))
            db.commit()
        delete_storage_file(saved["path"])
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message})
    delete_storage_file(saved["path"])
    _audit(db, recruiter, "speaker.verified", "interview", interview.id)
    db.commit()
    return {"status": "completed", "result": body, "meaning": "Similarity is not a percentage and is not conclusive proof of identity."}

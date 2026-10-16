from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class RecruiterCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class CandidateCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    job_role: str = Field(min_length=2, max_length=160)
    consent_audio: bool


class InterviewCreate(BaseModel):
    candidate_id: int
    scheduled_at: datetime | None = None


class AnalyzeRequest(BaseModel):
    confirm: bool


class ReviewCreate(BaseModel):
    decision: str = Field(pattern="^(cleared|needs_further_verification|escalated)$")
    note: str = Field(default="", max_length=4000)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


import io
import sys
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database.session import Base, get_db
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    from app.core.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "max_requests_per_minute", 100)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def recruiter(client):
    response = client.post("/api/auth/register", json={"name": "Test Recruiter", "email": "recruiter@example.com", "password": "long-test-password"})
    assert response.status_code == 201
    client.headers.update({"Authorization": f"Bearer {response.json()['access_token']}"})
    return response.json()["recruiter"]


@pytest.fixture
def wav_bytes():
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * (16000 * 6))
    return out.getvalue()


@pytest.fixture
def interview(client, recruiter):
    candidate = client.post("/api/candidates", json={"name": "Jordan Lee", "email": "jordan@example.com", "job_role": "Engineer", "consent_audio": True})
    assert candidate.status_code == 201
    created = client.post("/api/interviews", json={"candidate_id": candidate.json()["id"]})
    assert created.status_code == 201
    return created.json()

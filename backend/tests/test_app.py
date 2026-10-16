from app.core.config import settings
from app.main import DetectifError, DetectifService


def auth(client, email="recruiter@example.com"):
    response = client.post("/api/auth/register", json={"name": "Test Recruiter", "email": email, "password": "long-test-password"})
    client.headers.update({"Authorization": f"Bearer {response.json()['access_token']}"})
    return response.json()


def upload(client, interview_id, content, name="sample.wav", consent="true"):
    return client.post(f"/api/interviews/{interview_id}/audio", data={"consent": consent}, files={"file": (name, content, "audio/wav")})


def test_registration_requires_strong_password(client):
    response = client.post("/api/auth/register", json={"name": "Recruiter", "email": "r@example.com", "password": "short"})
    assert response.status_code == 422


def test_candidate_registration_and_consent(client, recruiter):
    no_consent = client.post("/api/candidates", json={"name": "Jordan Lee", "email": "jordan@example.com", "job_role": "Engineer", "consent_audio": False})
    assert no_consent.status_code == 400
    accepted = client.post("/api/candidates", json={"name": "Jordan Lee", "email": "jordan@example.com", "job_role": "Engineer", "consent_audio": True})
    assert accepted.status_code == 201
    assert accepted.json()["consent_audio"] is True


def test_authentication_and_recruiter_workspace_isolation(client, recruiter):
    client.headers.pop("Authorization")
    assert client.get("/api/candidates").status_code == 401
    first = auth(client, "second@example.com")
    own = client.post("/api/candidates", json={"name": "Own Candidate", "email": "own@example.com", "job_role": "Analyst", "consent_audio": True}).json()
    client.headers.pop("Authorization")
    auth(client, "other@example.com")
    assert client.get("/api/candidates").json() == []
    assert client.post("/api/interviews", json={"candidate_id": own["id"]}).status_code == 404


def test_audio_upload_requires_consent_and_valid_audio(client, interview, wav_bytes):
    denied = upload(client, interview["id"], wav_bytes, consent="false")
    assert denied.status_code == 400
    invalid = upload(client, interview["id"], b"not an audio file" * 10, name="bad.wav")
    assert invalid.status_code == 415
    good = upload(client, interview["id"], wav_bytes)
    assert good.status_code == 201
    assert len(good.json()["sha256"]) == 64
    assert good.json()["duration_seconds"] == 6


def test_unsupported_extension_and_upload_limit(client, interview, wav_bytes, monkeypatch):
    assert upload(client, interview["id"], wav_bytes, name="sample.txt").status_code == 415
    monkeypatch.setattr(settings, "max_audio_upload_mb", 1)
    large = b"RIFF" + b"0" * (1024 * 1024 + 50)
    assert upload(client, interview["id"], large).status_code == 413


def test_demo_analysis_is_labeled_and_duplicate_is_cached(client, interview, wav_bytes):
    assert upload(client, interview["id"], wav_bytes).status_code == 201
    assert upload(client, interview["id"], wav_bytes).json()["sha256"] == __import__("hashlib").sha256(wav_bytes).hexdigest()
    first = client.post(f"/api/interviews/{interview['id']}/analyze", json={"confirm": True})
    second = client.post(f"/api/interviews/{interview['id']}/analyze", json={"confirm": True})
    assert first.status_code == second.status_code == 200
    assert first.json()["demo"] is True
    assert first.json()["result"]["verdict"] == "not_sure"
    assert "Simulated" in first.json()["result"]["demo_notice"]
    assert second.json()["cached"] is True
    assert client.get("/api/usage").json()["tracked_requests"] == 0


def test_real_analysis_success_uses_documented_fields(client, interview, wav_bytes, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)

    class FakeProvider:
        def get_credits(self):
            return ({"available": 1000, "price_per_operation": 25, "unlimited": False}, 2)
        def detect(self, file_path, idempotency_key):
            return ({"operation_id": "op_test", "credits": {"charged": 25}, "model": {"name": "test-model", "version": "1"}, "result": {"verdict": "real", "score": 0.02, "threshold": 0.62, "score_semantics": "Higher means more likely AI-generated."}}, 10)

    monkeypatch.setattr("app.main.DetectifService", FakeProvider)
    upload(client, interview["id"], wav_bytes)
    result = client.post(f"/api/interviews/{interview['id']}/analyze", json={"confirm": True})
    assert result.status_code == 200
    assert result.json()["demo"] is False
    assert result.json()["result"]["result"]["verdict"] == "real"
    usage = client.get("/api/usage").json()
    assert usage["tracked_credits_spent"] == 25
    assert usage["records"][0]["operation"] == "deepfake.detect"


def test_provider_auth_failure_is_recorded_without_exposing_key(client, interview, wav_bytes, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "detectif_api_key", "SECRET-TEST-KEY")

    class FakeProvider:
        def get_credits(self):
            raise DetectifError(401, "invalid_api_key", "Invalid key")

    monkeypatch.setattr("app.main.DetectifService", FakeProvider)
    upload(client, interview["id"], wav_bytes)
    response = client.post(f"/api/interviews/{interview['id']}/analyze", json={"confirm": True})
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "SECRET-TEST-KEY" not in response.text
    assert client.get("/api/usage").json()["records"][0]["error_code"] == "invalid_api_key"


def test_provider_key_is_not_exposed_in_frontend_or_usage(client, recruiter, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(settings, "detectif_api_key", "SECRET-TEST-KEY")
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "src"
    source = "\n".join(path.read_text(encoding="utf-8") for path in frontend.rglob("*.*") if path.is_file())
    response = client.get("/api/usage")
    assert "DETECTIF_API_KEY" not in source
    assert "SECRET-TEST-KEY" not in source
    assert "SECRET-TEST-KEY" not in response.text


def test_usage_dashboard_does_not_call_provider_automatically(client, recruiter, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)

    class NoAutomaticProviderCall:
        def __init__(self):
            raise AssertionError("Usage page should not make provider calls automatically")

    monkeypatch.setattr("app.main.DetectifService", NoAutomaticProviderCall)
    assert client.get("/api/usage").status_code == 200


def test_budget_protection_blocks_provider_call(client, interview, wav_bytes, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "max_api_credit_budget", 20)
    called = {"detect": False}

    class FakeProvider:
        def get_credits(self):
            return ({"available": 1000, "price_per_operation": 25, "unlimited": False}, 1)
        def detect(self, *args):
            called["detect"] = True
            raise AssertionError("paid operation must not run")

    monkeypatch.setattr("app.main.DetectifService", FakeProvider)
    upload(client, interview["id"], wav_bytes)
    response = client.post(f"/api/interviews/{interview['id']}/analyze", json={"confirm": True})
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["result"]["error"]["code"] == "local_credit_budget_exceeded"
    assert called["detect"] is False


def test_inconclusive_provider_result_is_preserved_as_inconclusive(client, interview, wav_bytes, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)

    class FakeProvider:
        def get_credits(self):
            return ({"available": 100, "price_per_operation": 1, "unlimited": False}, 1)
        def detect(self, *args):
            return ({"operation_id": "op_unsure", "credits": {"charged": 1}, "result": {"verdict": "not_sure", "score": 0.49, "threshold": 0.62}}, 8)

    monkeypatch.setattr("app.main.DetectifService", FakeProvider)
    upload(client, interview["id"], wav_bytes)
    response = client.post(f"/api/interviews/{interview['id']}/analyze", json={"confirm": True})
    assert response.json()["result"]["result"]["verdict"] == "not_sure"


def test_explicit_retry_reuses_provider_idempotency_key_after_timeout(client, interview, wav_bytes, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    seen_keys = []

    class FakeProvider:
        calls = 0
        def get_credits(self):
            return ({"available": 100, "price_per_operation": 1, "unlimited": False}, 1)
        def detect(self, _path, key):
            seen_keys.append(key)
            self.calls += 1
            if self.calls == 1:
                raise DetectifError(504, "provider_timeout", "Timed out")
            return ({"operation_id": "op_retry", "credits": {"charged": 1}, "result": {"verdict": "real", "score": 0.01, "threshold": 0.62}}, 9)

    provider = FakeProvider()
    monkeypatch.setattr("app.main.DetectifService", lambda: provider)
    upload(client, interview["id"], wav_bytes)
    first = client.post(f"/api/interviews/{interview['id']}/analyze", json={"confirm": True})
    assert first.json()["status"] == "failed"
    assert len(seen_keys) == 1
    second = client.post(f"/api/interviews/{interview['id']}/analyze", json={"confirm": True})
    assert second.json()["status"] == "completed"
    assert len(seen_keys) == 2 and seen_keys[0] == seen_keys[1]


def test_local_expensive_operation_rate_limit(client, interview, wav_bytes, monkeypatch):
    monkeypatch.setattr(settings, "max_requests_per_minute", 0)
    assert upload(client, interview["id"], wav_bytes).status_code == 429


def test_manual_review_is_a_separate_auditable_action(client, interview):
    response = client.post(f"/api/interviews/{interview['id']}/review", json={"decision": "needs_further_verification", "note": "Please schedule an alternate check."})
    assert response.status_code == 201
    detail = client.get(f"/api/interviews/{interview['id']}").json()
    assert detail["reviews"][0]["decision"] == "needs_further_verification"
    assert detail["reviews"][0]["note"].startswith("Please schedule")


def test_audio_deletion_removes_file_and_associated_analysis(client, interview, wav_bytes):
    uploaded = upload(client, interview["id"], wav_bytes)
    assert uploaded.status_code == 201
    client.post(f"/api/interviews/{interview['id']}/analyze", json={"confirm": True})
    response = client.delete(f"/api/interviews/{interview['id']}/audio")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    detail = client.get(f"/api/interviews/{interview['id']}").json()
    assert detail["audio"] is None and detail["analyses"] == []


def test_candidate_deletion_removes_candidate_interviews_and_audio(client, interview, wav_bytes):
    candidate_id = interview["candidate_id"]
    upload(client, interview["id"], wav_bytes)
    response = client.delete(f"/api/candidates/{candidate_id}")
    assert response.status_code == 200
    assert response.json()["interviews_removed"] == 1
    assert client.get("/api/candidates").json() == []
    assert client.get(f"/api/interviews/{interview['id']}").status_code == 404


def test_real_api_integration_requires_separate_explicit_opt_in(tmp_path, monkeypatch):
    import os
    import uuid
    import wave
    import pytest
    if not (os.getenv("RUN_REAL_DETECTIF_TESTS") == "1" and os.getenv("REAL_DETECTIF_CONFIRM") == "YES_SPEND_ONE_CREDIT" and os.getenv("DETECTIF_API_KEY")):
        pytest.skip("Set RUN_REAL_DETECTIF_TESTS=1, REAL_DETECTIF_CONFIRM=YES_SPEND_ONE_CREDIT, and DETECTIF_API_KEY to explicitly authorize a real check.")
    sample_path = os.getenv("DETECTIF_TEST_AUDIO_PATH")
    if not sample_path or not __import__("pathlib").Path(sample_path).is_file():
        pytest.fail("Set DETECTIF_TEST_AUDIO_PATH to a consented 5–120 second audio file. This test may use one provider credit.")
    monkeypatch.setattr(settings, "detectif_api_key", os.environ["DETECTIF_API_KEY"])
    monkeypatch.setattr(settings, "demo_mode", False)
    service = DetectifService()
    balance, _ = service.get_credits()
    if not balance.get("unlimited") and (balance.get("available") is None or balance["available"] < balance.get("price_per_operation", 1)):
        pytest.fail("Provider balance is below one documented operation price.")
    result, _latency = service.detect(sample_path, str(uuid.uuid4()))
    assert result.get("operation_id") and result.get("result", {}).get("verdict") in {"real", "fake", "not_sure"}

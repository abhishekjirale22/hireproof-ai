import time
import uuid
from pathlib import Path

import httpx

from app.core.config import settings


class DetectifError(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.retry_after = status, code, message, retry_after
        self.payload = payload or {}


class DetectifService:
    def __init__(self):
        # The official OpenAPI document publishes production as its only server; do not forward keys to arbitrary hosts.
        self.base_url = "https://platform.detectif.ai"
        self.api_key = settings.detectif_api_key

    def _request(self, method: str, path: str, *, file_path: str | None = None, data: dict | None = None, idempotency_key: str | None = None):
        if not self.api_key:
            raise DetectifError(503, "provider_not_configured", "DetectifAI API key is not configured.")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=httpx.Timeout(150.0, connect=10.0)) as client:
                if file_path:
                    path_obj = Path(file_path)
                    with path_obj.open("rb") as audio:
                        response = client.request(method, f"{self.base_url}{path}", headers=headers, data=data, files={"file": (path_obj.name, audio, "application/octet-stream")})
                else:
                    response = client.request(method, f"{self.base_url}{path}", headers=headers, params=data)
        except httpx.TimeoutException as exc:
            raise DetectifError(504, "provider_timeout", "DetectifAI timed out. Check usage before retrying.") from exc
        except httpx.HTTPError as exc:
            raise DetectifError(502, "provider_unavailable", "Could not reach DetectifAI.") from exc
        latency = round((time.perf_counter() - started) * 1000, 1)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not response.is_success:
            error = body.get("error", {}) if isinstance(body, dict) else {}
            retry_after = response.headers.get("Retry-After")
            raise DetectifError(response.status_code, error.get("code", "provider_error"), error.get("message", "DetectifAI request failed."), int(retry_after) if retry_after and retry_after.isdigit() else None, body if isinstance(body, dict) else None)
        return body, latency

    def detect(self, file_path: str, idempotency_key: str):
        return self._request("POST", "/v1/deepfake/detect", file_path=file_path, idempotency_key=idempotency_key)

    def enroll(self, file_path: str, speaker_id: str, display_name: str, idempotency_key: str):
        return self._request("POST", "/v1/speakers/enroll", file_path=file_path, data={"speaker_id": speaker_id, "display_name": display_name}, idempotency_key=idempotency_key)

    def verify(self, file_path: str, speaker_id: str, idempotency_key: str, detect_deepfake: bool = False):
        return self._request("POST", "/v1/speakers/verify", file_path=file_path, data={"speaker_id": speaker_id, "detect_deepfake": str(detect_deepfake).lower()}, idempotency_key=idempotency_key)

    def get_credits(self):
        return self._request("GET", "/v1/credits")

    def get_usage(self):
        return self._request("GET", "/v1/usage")

    def delete_speaker(self, speaker_id: str):
        return self._request("DELETE", f"/v1/speakers/{speaker_id}")

"""
AWS Lambda entry point (Function URL, payload v2) for the StreamPulse assistant.

    POST /ask     {"question": "...", "history": [{"role","content"}, ...]}
    GET  /health  data freshness

Cold start: pull the chat Parquet tables from S3 into /tmp and read the LLM keys from SSM.
Warm: re-checks S3 for a newer data version every 15 minutes.
Abuse/cost control: DynamoDB atomic counters (per-IP per hour, global per day).
CORS is handled by the Function URL config, not here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger("chatbot")
log.setLevel(logging.INFO)

LAKE = os.environ["LAKE_BUCKET"]
PREFIX = os.environ.get("CHAT_PREFIX", "chat/")
SSM_PREFIX = os.environ.get("SSM_PREFIX", "/streampulse")
TABLE = os.environ.get("LIMITS_TABLE", "")
IP_PER_HOUR = int(os.environ.get("IP_PER_HOUR", "30"))
GLOBAL_PER_DAY = int(os.environ.get("GLOBAL_PER_DAY", "1500"))
DATA_DIR = Path("/tmp/chat_data")
RECHECK_S = 900

s3 = boto3.client("s3")
ssm = boto3.client("ssm")
ddb = boto3.client("dynamodb") if TABLE else None

_state = {"bot": None, "etag": None, "checked": 0.0}


def _load_keys():
    for env, name in (("GROQ_API_KEY", "groq_api_key"), ("GEMINI_API_KEY", "gemini_api_key")):
        if os.environ.get(env):
            continue
        try:
            os.environ[env] = ssm.get_parameter(Name=f"{SSM_PREFIX}/{name}", WithDecryption=True)["Parameter"]["Value"]
        except ClientError as e:
            log.warning("ssm %s unavailable: %s", name, e.response["Error"]["Code"])


def _sync_data() -> bool:
    """Download chat tables if the published version changed. Returns True if data was (re)loaded."""
    try:
        head = s3.head_object(Bucket=LAKE, Key=PREFIX + "_version.json")
    except ClientError:
        if _state["bot"]:
            return False  # keep serving the data we have
        raise
    if head["ETag"] == _state["etag"]:
        return False
    DATA_DIR.mkdir(exist_ok=True)
    for t in ("daily_country", "track_all", "track_month", "track_country", "track_artist",
              "artist_all", "track_day", "track_country_day", "rank_country_day"):
        s3.download_file(LAKE, f"{PREFIX}{t}.parquet", str(DATA_DIR / f"{t}.parquet"))
    _state["etag"] = head["ETag"]
    return True


def _bot():
    now = time.time()
    if _state["bot"] is None or now - _state["checked"] > RECHECK_S:
        _state["checked"] = now
        reloaded = _sync_data()
        if _state["bot"] is None or reloaded:
            _load_keys()
            from agent import Assistant
            from tools import Facts
            _state["bot"] = Assistant(Facts(DATA_DIR))
            log.info(json.dumps({"event": "loaded", "as_of": str(_state["bot"].facts.as_of)}))
    return _state["bot"]


def _allow(ip: str) -> tuple[bool, str]:
    if not ddb:
        return True, ""
    now = int(time.time())
    who = hashlib.sha256(ip.encode()).hexdigest()[:16]  # never store raw IPs
    checks = ((f"ip#{who}#{time.strftime('%Y%m%d%H', time.gmtime(now))}", IP_PER_HOUR, 7200, "Too many questions from your connection this hour. Please try again later."),
              (f"day#{time.strftime('%Y%m%d', time.gmtime(now))}", GLOBAL_PER_DAY, 172800, "The assistant has reached its daily limit. Please try again tomorrow."))
    for key, cap, ttl, msg in checks:
        try:
            ddb.update_item(TableName=TABLE, Key={"pk": {"S": key}},
                            UpdateExpression="ADD n :one SET expires = :t",
                            ConditionExpression="attribute_not_exists(n) OR n < :cap",
                            ExpressionAttributeValues={":one": {"N": "1"}, ":cap": {"N": str(cap)}, ":t": {"N": str(now + ttl)}})
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False, msg
            log.warning("limiter error (failing open): %s", e.response["Error"]["Code"])
    return True, ""


def _resp(code: int, body: dict):
    return {"statusCode": code, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(body, default=str)}


def handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    method, path = http.get("method", ""), http.get("path", "")
    try:
        if method == "GET" and path.endswith("/health"):
            return _resp(200, {"ok": True, "as_of": str(_bot().facts.as_of)})
        if method != "POST" or not path.endswith("/ask"):
            return _resp(404, {"error": "not found"})

        try:
            body = json.loads(event.get("body") or "{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return _resp(400, {"error": "invalid JSON body"})
        question = str(body.get("question", ""))[:600]
        history = [h for h in body.get("history", []) if isinstance(h, dict)][-6:]

        ok, msg = _allow(http.get("sourceIp", "unknown"))
        if not ok:
            return _resp(429, {"answer": msg, "sources": [], "limited": True})

        r = _bot().ask(question, history)
        log.info(json.dumps({"event": "ask", "q": question[:200], "tools": [t["name"] for t in r["tools"]],
                             "ok": [t["ok"] for t in r["tools"]], "verified": r["verified"], "blocked": r["blocked"],
                             "model": r["model"], "ms": r["ms"], "tok_in": r["tokens"]["in"], "tok_out": r["tokens"]["out"]}))
        return _resp(200, {"answer": r["answer"], "sources": r["sources"], "verified": r["verified"],
                           "model": r["model"], "ms": r["ms"], "as_of": r["as_of"]})
    except Exception:
        log.exception("unhandled error")
        return _resp(500, {"error": "internal"})

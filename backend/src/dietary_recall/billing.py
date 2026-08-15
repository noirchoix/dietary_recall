"""Provider-neutral subscription entitlements with Stripe and Paystack adapters."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .research_core import _json, _uid
from .validated_research import ADMIN_ROLES, PlatformRepository


class BillingError(ValueError):
    pass


def verify_stripe_signature(raw_body: bytes, signature_header: str, secret: str, *, now: int | None = None, tolerance: int = 300) -> None:
    fields: dict[str, list[str]] = {}
    for item in signature_header.split(","):
        if "=" not in item:
            continue
        key, value = item.strip().split("=", 1)
        fields.setdefault(key, []).append(value)
    try:
        timestamp = int(fields["t"][0])
    except (KeyError, ValueError, IndexError) as exc:
        raise BillingError("Invalid Stripe-Signature header") from exc
    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > max(1, tolerance):
        raise BillingError("Stripe webhook timestamp is outside the replay tolerance")
    signed = str(timestamp).encode("ascii") + b"." + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in fields.get("v1", [])):
        raise BillingError("Stripe webhook signature is invalid")


def verify_paystack_signature(raw_body: bytes, signature_header: str, secret: str) -> None:
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    if not signature_header or not hmac.compare_digest(expected, signature_header.strip()):
        raise BillingError("Paystack webhook signature is invalid")


def _validate_redirect_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme == "https" and parsed.netloc:
        return value
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}:
        return value
    raise BillingError("Checkout redirect URLs must use HTTPS, except localhost development URLs")


def _default_http_post(url: str, headers: Mapping[str, str], body: bytes) -> dict[str, Any]:
    request = urllib.request.Request(url, body, dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise BillingError(f"Billing provider returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise BillingError(f"Billing provider request failed: {exc.reason}") from exc
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise BillingError("Billing provider returned an invalid response")
    return value


class BillingService:
    def __init__(self, repository: PlatformRepository, http_post: Callable[[str, Mapping[str, str], bytes], dict[str, Any]] | None = None):
        self.repository = repository
        self.http_post = http_post or _default_http_post

    def list_prices(self, actor: str, project_uid: str) -> list[dict[str, Any]]:
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor)
            return [dict(row) for row in con.execute("SELECT * FROM billing_price_mappings WHERE active=1 ORDER BY provider,amount_minor")]

    def register_price(self, project_uid: str, data: Mapping[str, Any], actor: str) -> dict[str, Any]:
        provider = str(data.get("provider") or "")
        external = str(data.get("external_price_id") or "").strip()
        name = str(data.get("display_name") or "").strip()
        imports = int(data.get("import_rows_limit") or 0)
        calculations = int(data.get("calculation_runs_limit") or 0)
        if provider not in {"stripe", "paystack", "manual"} or not external or not name:
            raise BillingError("provider, external_price_id and display_name are required")
        if imports <= 100 or calculations <= 100:
            raise BillingError("Paid price entitlements must both be greater than 100")
        currency = str(data.get("currency") or "").upper() or None
        if currency and (len(currency) != 3 or not currency.isalpha()):
            raise BillingError("currency must be a three-letter code")
        amount_minor = data.get("amount_minor")
        if provider == "paystack" and (amount_minor in (None, "") or int(amount_minor) <= 0 or not currency):
            raise BillingError("Paystack mappings require a positive amount_minor and currency")
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor, ADMIN_ROLES)
            uid = _uid("price")
            con.execute(
                "INSERT INTO billing_price_mappings(price_mapping_uid,provider,external_price_id,display_name,plan_code,import_rows_limit,calculation_runs_limit,amount_minor,currency,created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uid, provider, external, name, "paid", imports, calculations, int(amount_minor) if amount_minor not in (None, "") else None, currency, actor),
            )
            result = dict(con.execute("SELECT * FROM billing_price_mappings WHERE price_mapping_uid=?", (uid,)).fetchone())
            self.repository._audit(con, "create", "billing_price", uid, actor, after=result)
            return result

    def billing_summary(self, project_uid: str, actor: str) -> dict[str, Any]:
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor)
            subscription = con.execute(
                "SELECT b.*,p.display_name price_name,p.import_rows_limit,p.calculation_runs_limit FROM billing_subscriptions b JOIN billing_price_mappings p USING(price_mapping_uid) WHERE b.project_uid=? ORDER BY b.updated_at DESC LIMIT 1",
                (project_uid,),
            ).fetchone()
            checkout = con.execute("SELECT * FROM billing_checkout_sessions WHERE project_uid=? ORDER BY created_at DESC LIMIT 1", (project_uid,)).fetchone()
            return {
                "usage": self.repository._usage_summary(con, project_uid),
                "subscription": dict(subscription) if subscription else None,
                "latest_checkout": dict(checkout) if checkout else None,
                "providers": [row[0] for row in con.execute("SELECT DISTINCT provider FROM billing_price_mappings WHERE active=1 ORDER BY provider")],
            }

    def create_checkout(
        self,
        project_uid: str,
        data: Mapping[str, Any],
        actor: str,
        *,
        stripe_api_key: str | None = None,
        paystack_secret_key: str | None = None,
    ) -> dict[str, Any]:
        mapping_uid = str(data.get("price_mapping_uid") or "")
        email = str(data.get("email") or actor).strip().casefold()
        success_url = _validate_redirect_url(str(data.get("success_url") or ""))
        cancel_url = _validate_redirect_url(str(data.get("cancel_url") or ""))
        if "@" not in email:
            raise BillingError("A purchaser email is required")
        with self.repository.connect() as con:
            self.repository._membership(con, project_uid, actor, ADMIN_ROLES)
            mapping = con.execute("SELECT * FROM billing_price_mappings WHERE price_mapping_uid=? AND active=1", (mapping_uid,)).fetchone()
            if mapping is None or mapping["provider"] == "manual":
                raise BillingError("An active Stripe or Paystack price mapping is required")
            mapping = dict(mapping)
        checkout_uid = _uid("checkout")
        if mapping["provider"] == "stripe":
            if not stripe_api_key:
                raise BillingError("Stripe API key is not configured")
            params = {
                "mode": "subscription",
                "success_url": success_url,
                "cancel_url": cancel_url,
                "client_reference_id": project_uid,
                "customer_email": email,
                "line_items[0][price]": mapping["external_price_id"],
                "line_items[0][quantity]": "1",
                "metadata[project_uid]": project_uid,
                "metadata[checkout_uid]": checkout_uid,
                "subscription_data[metadata][project_uid]": project_uid,
                "subscription_data[metadata][checkout_uid]": checkout_uid,
            }
            response = self.http_post(
                "https://api.stripe.com/v1/checkout/sessions",
                {"Authorization": f"Bearer {stripe_api_key}", "Content-Type": "application/x-www-form-urlencoded"},
                urllib.parse.urlencode(params).encode("ascii"),
            )
            external_id = response.get("id")
            checkout_url = response.get("url")
            customer_id = response.get("customer")
        else:
            if not paystack_secret_key:
                raise BillingError("Paystack secret key is not configured")
            request_body = {
                "email": email,
                "amount": int(mapping["amount_minor"]),
                "plan": mapping["external_price_id"],
                "callback_url": success_url,
                "metadata": {"project_uid": project_uid, "checkout_uid": checkout_uid, "cancel_url": cancel_url},
            }
            response = self.http_post(
                "https://api.paystack.co/transaction/initialize",
                {"Authorization": f"Bearer {paystack_secret_key}", "Content-Type": "application/json"},
                json.dumps(request_body, separators=(",", ":")).encode("utf-8"),
            )
            provider_data = response.get("data") or {}
            external_id = provider_data.get("reference") or provider_data.get("access_code")
            checkout_url = provider_data.get("authorization_url")
            customer_id = None
        if not external_id or not checkout_url:
            raise BillingError("Billing provider did not return a checkout identifier and URL")
        with self.repository.connect() as con:
            con.execute(
                "INSERT INTO billing_checkout_sessions(checkout_uid,project_uid,price_mapping_uid,provider,purchaser_email,external_checkout_id,external_customer_id,status,created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                (checkout_uid, project_uid, mapping_uid, mapping["provider"], email, external_id, customer_id, "redirected", actor),
            )
            self.repository._audit(con, "create", "billing_checkout", checkout_uid, actor, detail={"provider": mapping["provider"], "price_mapping_uid": mapping_uid})
        return {"checkout_uid": checkout_uid, "provider": mapping["provider"], "checkout_url": checkout_url, "status": "redirected"}

    def grant_manual(
        self,
        project_uid: str,
        price_mapping_uid: str,
        external_reference: str,
        actor: str,
        *,
        status: str = "active",
        period_end: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"trialing", "active", "past_due", "paused", "canceled", "expired"}:
            raise BillingError("Invalid subscription status")
        payload = {"manual_reference": external_reference, "project_uid": project_uid, "status": status, "period_end": period_end}
        with self.repository.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self.repository._membership(con, project_uid, actor, ADMIN_ROLES)
            mapping = con.execute("SELECT * FROM billing_price_mappings WHERE price_mapping_uid=? AND provider='manual' AND active=1", (price_mapping_uid,)).fetchone()
            if mapping is None:
                raise BillingError("Active manual price mapping not found")
            result = self._upsert_subscription(con, project_uid, dict(mapping), "manual", external_reference, None, status, period_end, False, payload)
            self.repository._audit(con, "grant", "billing_subscription", result["billing_subscription_uid"], actor, after=result)
            con.commit()
            return result

    @staticmethod
    def _metadata(data: Mapping[str, Any]) -> dict[str, Any]:
        value = data.get("metadata") or {}
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = {}
        return dict(value) if isinstance(value, Mapping) else {}

    def process_webhook(self, provider: str, raw_body: bytes, signature: str, secret: str, *, now: int | None = None) -> dict[str, Any]:
        if provider == "stripe":
            verify_stripe_signature(raw_body, signature, secret, now=now)
        elif provider == "paystack":
            verify_paystack_signature(raw_body, signature, secret)
        else:
            raise BillingError("Unsupported billing provider")
        event = json.loads(raw_body.decode("utf-8"))
        if not isinstance(event, dict):
            raise BillingError("Webhook payload must be a JSON object")
        payload_hash = hashlib.sha256(raw_body).hexdigest()
        event_type = str(event.get("type") if provider == "stripe" else event.get("event") or "")
        data = event.get("data") or {}
        obj = data.get("object") if provider == "stripe" and isinstance(data, Mapping) else data
        obj = obj if isinstance(obj, Mapping) else {}
        external_event_id = str(event.get("id") or f"{event_type}:{obj.get('id') or obj.get('subscription_code') or payload_hash}")
        with self.repository.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("SELECT * FROM billing_webhook_events WHERE provider=? AND external_event_id=?", (provider, external_event_id)).fetchone()
            if existing:
                con.rollback()
                return {"provider": provider, "external_event_id": external_event_id, "status": "duplicate", "idempotent": True}
            handled = False
            detail: dict[str, Any] = {}
            if provider == "stripe" and event_type.startswith("customer.subscription."):
                handled, detail = self._process_stripe_subscription(con, event_type, obj, payload_hash)
            elif provider == "paystack" and event_type in {"subscription.create", "subscription.disable", "subscription.not_renew"}:
                handled, detail = self._process_paystack_subscription(con, event_type, obj, payload_hash)
            elif provider == "paystack" and event_type == "charge.success":
                metadata = self._metadata(obj)
                checkout_uid = metadata.get("checkout_uid")
                if checkout_uid:
                    con.execute("UPDATE billing_checkout_sessions SET status='completed',external_customer_id=?,updated_at=CURRENT_TIMESTAMP WHERE checkout_uid=?", ((obj.get("customer") or {}).get("customer_code") if isinstance(obj.get("customer"), Mapping) else None, checkout_uid))
                    handled = True
                    detail = {"checkout_uid": checkout_uid, "entitlement_changed": False}
            con.execute(
                "INSERT INTO billing_webhook_events(billing_event_uid,provider,external_event_id,event_type,payload_sha256,signature_verified,processing_status,detail_json) VALUES (?,?,?,?,?,1,?,?)",
                (_uid("bevt"), provider, external_event_id, event_type, payload_hash, "processed" if handled else "ignored", _json(detail)),
            )
            con.commit()
            return {"provider": provider, "external_event_id": external_event_id, "event_type": event_type, "status": "processed" if handled else "ignored", **detail}

    def _process_stripe_subscription(self, con: sqlite3.Connection, event_type: str, obj: Mapping[str, Any], payload_hash: str) -> tuple[bool, dict[str, Any]]:
        subscription_id = str(obj.get("id") or "")
        metadata = self._metadata(obj)
        project_uid = str(metadata.get("project_uid") or "")
        items = ((obj.get("items") or {}).get("data") or []) if isinstance(obj.get("items"), Mapping) else []
        price_id = ""
        if items and isinstance(items[0], Mapping):
            price = items[0].get("price") or {}
            price_id = str(price.get("id") if isinstance(price, Mapping) else "")
        existing = con.execute("SELECT * FROM billing_subscriptions WHERE provider='stripe' AND external_subscription_id=?", (subscription_id,)).fetchone()
        if not project_uid and existing:
            project_uid = existing["project_uid"]
        mapping = con.execute("SELECT * FROM billing_price_mappings WHERE provider='stripe' AND external_price_id=? AND active=1", (price_id,)).fetchone()
        if mapping is None or not project_uid or not con.execute("SELECT 1 FROM research_projects WHERE project_uid=?", (project_uid,)).fetchone():
            return False, {"reason": "subscription could not be mapped to a project and active price"}
        status = "canceled" if event_type.endswith("deleted") else str(obj.get("status") or "past_due")
        if status not in {"trialing", "active", "past_due", "paused", "canceled", "expired"}:
            status = "past_due" if status in {"unpaid", "incomplete"} else "expired"
        period_end = obj.get("current_period_end")
        if period_end:
            period_end = datetime.fromtimestamp(int(period_end), tz=timezone.utc).isoformat()
        result = self._upsert_subscription(con, project_uid, dict(mapping), "stripe", subscription_id, obj.get("customer"), status, period_end, bool(obj.get("cancel_at_period_end")), obj, payload_hash)
        return True, {"project_uid": project_uid, "billing_subscription_uid": result["billing_subscription_uid"], "subscription_status": status}

    def _process_paystack_subscription(self, con: sqlite3.Connection, event_type: str, obj: Mapping[str, Any], payload_hash: str) -> tuple[bool, dict[str, Any]]:
        subscription_id = str(obj.get("subscription_code") or obj.get("id") or "")
        plan = obj.get("plan") or {}
        price_id = str(plan.get("plan_code") if isinstance(plan, Mapping) else plan or "")
        customer = obj.get("customer") or {}
        customer_id = str(customer.get("customer_code") if isinstance(customer, Mapping) else "") or None
        email = str(customer.get("email") if isinstance(customer, Mapping) else "").casefold()
        metadata = self._metadata(obj)
        project_uid = str(metadata.get("project_uid") or "")
        existing = con.execute("SELECT * FROM billing_subscriptions WHERE provider='paystack' AND external_subscription_id=?", (subscription_id,)).fetchone()
        if not project_uid and existing:
            project_uid = existing["project_uid"]
        mapping = con.execute("SELECT * FROM billing_price_mappings WHERE provider='paystack' AND external_price_id=? AND active=1", (price_id,)).fetchone()
        if not project_uid and mapping is not None and email:
            checkout = con.execute("SELECT project_uid FROM billing_checkout_sessions WHERE provider='paystack' AND price_mapping_uid=? AND purchaser_email=? ORDER BY created_at DESC LIMIT 1", (mapping["price_mapping_uid"], email)).fetchone()
            project_uid = checkout[0] if checkout else ""
        if mapping is None or not project_uid:
            return False, {"reason": "subscription could not be mapped to a project and active plan"}
        status = "active" if event_type in {"subscription.create", "subscription.not_renew"} else "canceled"
        cancel_at_period_end = event_type == "subscription.not_renew"
        period_end = obj.get("next_payment_date")
        result = self._upsert_subscription(con, project_uid, dict(mapping), "paystack", subscription_id, customer_id, status, period_end, cancel_at_period_end, obj, payload_hash)
        return True, {"project_uid": project_uid, "billing_subscription_uid": result["billing_subscription_uid"], "subscription_status": status}

    @staticmethod
    def _upsert_subscription(
        con: sqlite3.Connection,
        project_uid: str,
        mapping: Mapping[str, Any],
        provider: str,
        external_subscription_id: str,
        external_customer_id: str | None,
        status: str,
        period_end: str | None,
        cancel_at_period_end: bool,
        payload: Mapping[str, Any],
        payload_hash: str | None = None,
    ) -> dict[str, Any]:
        payload_hash = payload_hash or hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
        uid = _uid("bsub")
        con.execute(
            "INSERT INTO billing_subscriptions(billing_subscription_uid,project_uid,price_mapping_uid,provider,external_subscription_id,external_customer_id,status,current_period_end,cancel_at_period_end,provider_payload_hash) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(provider,external_subscription_id) DO UPDATE SET project_uid=excluded.project_uid,price_mapping_uid=excluded.price_mapping_uid,external_customer_id=excluded.external_customer_id,status=excluded.status,current_period_end=excluded.current_period_end,cancel_at_period_end=excluded.cancel_at_period_end,provider_payload_hash=excluded.provider_payload_hash,updated_at=CURRENT_TIMESTAMP",
            (uid, project_uid, mapping["price_mapping_uid"], provider, external_subscription_id, external_customer_id, status, period_end, int(cancel_at_period_end), payload_hash),
        )
        active_entitlement = status in {"trialing", "active", "past_due"}
        if active_entitlement:
            con.execute(
                "UPDATE project_subscriptions SET fallback_plan_code=CASE WHEN billing_managed=0 AND plan_code!='paid' THEN plan_code ELSE COALESCE(fallback_plan_code,'independent') END,plan_code='paid',import_rows_override=?,calculation_runs_override=?,billing_managed=1,ends_at=? WHERE project_uid=?",
                (mapping["import_rows_limit"], mapping["calculation_runs_limit"], period_end, project_uid),
            )
        else:
            con.execute(
                "UPDATE project_subscriptions SET plan_code=COALESCE(fallback_plan_code,'independent'),import_rows_override=NULL,calculation_runs_override=NULL,billing_managed=0,ends_at=? WHERE project_uid=?",
                (period_end, project_uid),
            )
        return dict(con.execute("SELECT * FROM billing_subscriptions WHERE provider=? AND external_subscription_id=?", (provider, external_subscription_id)).fetchone())

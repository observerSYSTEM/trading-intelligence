# Stripe Test -> Live Switch Checklist

Use this before going live.

1. Environment mode
- Set `APP_ENV=production`.
- Confirm `APP_ENV` is not `development` on live servers.

2. Stripe keys and prices
- Set `STRIPE_SECRET_KEY` to a live key (`sk_live...`).
- Set `STRIPE_WEBHOOK_SECRET` to your live webhook signing secret.
- Set `STRIPE_PRICE_BASIC`, `STRIPE_PRICE_PRO`, `STRIPE_PRICE_ELITE` to live Price IDs.
- Run `GET /admin/ops/readiness` and verify:
  - `stripe_key_mode` = `live`
  - `stripe_price_ids_match_key_mode` = `OK` for all plans

3. Webhook endpoint
- Configure Stripe webhook endpoint to your production URL:
  - `/billing/webhook`
- Enable events:
  - `checkout.session.completed`
  - `customer.subscription.updated`
  - `invoice.payment_failed`
  - `invoice.payment_succeeded`
  - `customer.subscription.deleted`
- Send a Stripe test event from dashboard and confirm app returns `200`.

4. Idempotency and safety
- Confirm duplicate webhook events are ignored (response includes `duplicate=true`).
- Confirm table `stripe_webhook_idempotency` exists and records event IDs.

5. CORS and debug hardening
- Set `FRONTEND_URL` and `CORS_ALLOW_ORIGINS` to production origins only.
- Ensure no wildcard origin is used.
- Confirm docs are disabled in production (`/docs` not exposed).

6. Billing UX checks
- Create checkout session from UI and complete payment in live mode.
- Verify subscription status updates in DB.
- Verify Telegram billing notifications arrive for:
  - `invoice.payment_failed`
  - `invoice.payment_succeeded`
  - `customer.subscription.deleted` downgrade path

7. Final readiness gate
- Open admin dashboard readiness panel or call `GET /admin/ops/readiness`.
- Proceed only if overall status is `READY`.


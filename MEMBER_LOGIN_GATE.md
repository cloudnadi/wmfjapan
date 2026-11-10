# Member Login Gate

## Overview

Login to the registration flow is gated against a **members master list** imported
from the legacy site's CSV export. When someone enters their email on the sign-in
screen:

1. The email is looked up in the `members` DynamoDB table.
2. **If found** → a 6-digit OTP is emailed (existing passwordless flow).
3. **If not found** → no OTP is sent. The UI shows a "Register as a member" call to
   action linking to <https://www.wmfjapan.com/en/member/register>.

This ensures only known members can reach the registration form; everyone else is
directed to register first.

---

## Members Master Table

- **Table name**: `{project-name}-members-{suffix}` (see
  `terraform output members_table_name`)
- **Hash key**: `email` (lowercased, trimmed)
- **Billing**: on-demand (`PAY_PER_REQUEST`), PITR + SSE enabled

Only a lean subset of the CSV's 32 columns is stored — enough to gate login,
personalize the OTP email, and reflect NPO status:

| Attribute             | Source CSV column       | Type    |
|-----------------------|-------------------------|---------|
| `email` (key)         | `email`                 | String  |
| `memberid`            | `memberid`              | String  |
| `fullname`            | `fullname`              | String  |
| `phone`               | `phone`                 | String  |
| `mobile`              | `mobile`                | String  |
| `statusid`            | `statusid`              | String  |
| `npoMembershipActive` | `npo_membship_active`   | Bool    |
| `npoMembInterested`   | `npo_memb_interested`   | Bool    |

Empty string fields are omitted from the item; the two NPO flags are always
written as booleans.

---

## Login Gate Behavior (auth_otp Lambda)

`POST /auth/request-otp` now checks the members table **before** issuing a code:

- **Known member** → `200` and an OTP email (unchanged).
- **Unknown email** → `404` with:
  ```json
  {
    "error": "We couldn't find a membership for this email. Please register first.",
    "notRegistered": true,
    "registerUrl": "https://www.wmfjapan.com/en/member/register"
  }
  ```

**Fail-open safeguard**: if the `MEMBERS_TABLE` env var is unset (e.g. before the
table is provisioned) or DynamoDB errors, the gate is skipped and login proceeds,
so a misconfiguration can't lock everyone out. These cases are logged as warnings.

### Environment variables

| Variable              | Description                                              | Default |
|-----------------------|----------------------------------------------------------|---------|
| `MEMBERS_TABLE`       | Members master table name (wired by Terraform)           | —       |
| `MEMBER_REGISTER_URL` | Registration URL shown to non-members (`var.member_register_url`) | `https://www.wmfjapan.com/en/member/register` |

IAM: the shared Lambda role is granted `GetItem` / `BatchGetItem` / `Query` on the
members table.

---

## Frontend Behavior

- `requestLoginOtp()` surfaces `notRegistered` and `registerUrl` on the thrown
  error.
- `RegistrationLogin.jsx` shows a "Register as a member →" button (linking to the
  register URL) instead of a generic error when the email isn't a known member.
- **Mock mode** (`VITE_ONAM_MOCK` unset/`"true"`): set
  `localStorage["nk-mock-unknown"]` to an email to exercise the not-registered
  path locally without a backend.

---

## Importing the CSV

The members list is loaded with `scripts/import_members.py`. It reads the CSV,
keeps only the columns above, lowercases/trims emails as the key, skips
blank-email rows, dedups by email (last row wins), coerces the NPO flags to
booleans, and batch-writes (upsert by email — safe to re-run).

```bash
# Resolve the table name from Terraform, then import:
TABLE=$(terraform -chdir=backend output -raw members_table_name)
python3 scripts/import_members.py --csv members.csv --table "$TABLE" --region ap-northeast-1

# Preview without writing (works without boto3 installed):
python3 scripts/import_members.py --csv members.csv --table "$TABLE" --dry-run
```

**Options**

| Flag        | Description                              | Default          |
|-------------|------------------------------------------|------------------|
| `--csv`     | Path to the members CSV export (required)| —                |
| `--table`   | DynamoDB members table name (required)   | —                |
| `--region`  | AWS region                               | `ap-northeast-1` |
| `--dry-run` | Parse and report; write nothing          | off              |

The CSV must have an `email` column; extra columns are ignored.

---

---

## NPO Membership & Onam Pricing

Membership is **validated by date period**, not a year string. A member is
"valid now" when any of their transactions' `[membDateFrom, membDateTo]` range
covers today. Login email → `memberid` is resolved via the members master
table, then the `npo_membership` table is queried by `memberid`.

### NPO membership table

- **Table name**: `{project-name}-npo-membership-{suffix}` (see
  `terraform output npo_membership_table_name`)
- **Hash key**: `memberid` (String) · **Range key**: `transactionNo` (String)
- **Billing**: on-demand, PITR + SSE enabled

| Attribute              | Source CSV column         | Type   |
|------------------------|---------------------------|--------|
| `memberid` (key)       | `memberid`                | String |
| `transactionNo` (key)  | `transaction_no`          | String |
| `membershipAmountPaid` | `membership_amount_paid`  | Number |
| `paidDate`             | `paid_date`               | String |
| `membDateFrom`         | `memb_datefrom`           | String |
| `membDateTo`           | `memb_dateto`             | String |
| `paidVia`              | `paidvia`                 | String |

### Validity → discount rules

The membership Lambda (`GET /membership/status`) returns `isMember` (the
`npoMembershipActive` flag **and** a period covering today), `coversRenewal`
(a period already covers the renewal year), `canRenew`, and `canJoin`. The
registration then maps state to a **discount tier** (adults-only percentages —
kids always pay base price, under-6 free):

| Tier     | When                                        | Onam | Event | Combo |
|----------|---------------------------------------------|------|-------|-------|
| `none`   | not a member, not joining                   | 0%   | 0%    | 0%    |
| `member` | valid member, declines renewal              | 10%  | 0%    | 10%   |
| `full`   | joins / renews (or already covers renewal)  | 10%  | 10%   | 20%   |

A ¥1000 fee is added only when the user actively joins or renews. Pricing is
**recomputed server-side** in `onam_pricing.py` (duplicated across the register /
manage_registrations Lambdas); the client's amounts are never trusted.

On a successful join/renew the register Lambda appends a dated transaction
(`transactionNo` = max existing + 1, `membDateFrom`/`membDateTo` = the renewal
year span) to `npo_membership` and flips `npoMembershipActive` on the member.

### Importing the NPO membership CSV

`scripts/import_npo_membership.py` loads the transactions. Expected header:
`memberid, transaction_no, membership_amount_paid, paid_date, memb_datefrom,
memb_dateto, paidvia`. Rows missing `memberid`/`transaction_no` are skipped;
dedup is by `(memberid, transactionNo)` (upsert — safe to re-run).

```bash
TABLE=$(terraform -chdir=backend output -raw npo_membership_table_name)
python3 scripts/import_npo_membership.py --csv npo_membership.csv --table "$TABLE" --region ap-northeast-1

# Preview without writing (works without boto3 installed):
python3 scripts/import_npo_membership.py --csv npo_membership.csv --table "$TABLE" --dry-run
```

Date columns are stored verbatim; the Lambda parses `YYYY-MM-DD HH:MM:SS`, ISO,
or `YYYY-MM-DD`. Membership years run **July 1 → June 30** of the following year.

---

## Deployment

1. `cd backend && terraform apply` — creates the `members` and `npo_membership`
   tables, wires the env vars, and updates the Lambdas.
2. Run both import scripts (members, then NPO membership) to populate the tables.
3. Redeploy the frontend (`npm run build` + S3 sync + CloudFront invalidation).

# Admin Dashboard Setup Guide

## Overview
The admin dashboard now uses AWS Cognito SDK for secure authentication. Admins log in with email/password instead of pasting tokens.

## Setup Steps

### 1. Get Terraform Outputs
From your backend folder, run:
```bash
cd backend
terraform output
```

You'll see:
```
cognito_client_id = "xxxxxxxxxxxxxxxxxxxx"
cognito_region = "ap-northeast-1"
cognito_user_pool_id = "ap-northeast-1_xxxxxxxxxxxxx"
```

### 2. Configure Frontend Environment
Create `.env` in the `frontend` folder with these values:

```bash
# Copy from terraform output
VITE_COGNITO_USER_POOL_ID=ap-northeast-1_xxxxxxxxxxxxx
VITE_COGNITO_CLIENT_ID=xxxxxxxxxxxxxxxxxxxx
VITE_COGNITO_REGION=ap-northeast-1

# Optional: set custom API URL if different from default
VITE_API_BASE_URL=https://xxxxxxxxxxxxx.execute-api.ap-northeast-1.amazonaws.com/prod
```

Or use the `.env.example` template:
```bash
cp .env.example .env
# Then edit .env with your actual values
```

### 3. Create Admin User
After deploying Terraform, create your first admin user:

```bash
aws cognito-idp admin-create-user \
  --user-pool-id ap-northeast-1_xxxxxxxxxxxxx \
  --username admin@cloudnadi.com \
  --user-attributes Name=email,Value=admin@cloudnadi.com Name=email_verified,Value=true \
  --temporary-password "TempPass1234!" \
  --region ap-northeast-1

# Add user to Admins group
aws cognito-idp admin-add-user-to-group \
  --user-pool-id ap-northeast-1_xxxxxxxxxxxxx \
  --username admin@cloudnadi.com \
  --group-name Admins \
  --region ap-northeast-1
```

(Or copy the command from `terraform output create_admin_user_command`)

### 4. First Admin Login
1. Go to: `https://wmfjapan.cloudnadi.com/#admin`
2. Click "Admin" link in the event header
3. Log in with the email/temporary password
4. You'll be prompted to change your password (Cognito requirement)
5. Set a strong new password and log in again

### 5. Subsequent Admin Logins
Just log in with email and new password. Sessions last 8 hours.

## Features

The dashboard is organized into four tabs:

### Registrations tab

- **View Registrations**: Sortable table with all event registrations
- **Search**: Filter by name, email, or WhatsApp number
- **Filter by event & date**: Narrow the table to a single event or date
- **Add / Edit / Delete**: Manage individual registrations via a modal. Onam
  registrations recompute pricing **server-side** — the admin sets the option,
  attendee counts, and NPO membership action; the backend computes the totals
  (NPO discount, ¥1000 fee) using the same authoritative rules as the public flow.
- **Export CSV**: Download all registrations with timestamps
- **Statistics**: Total attendees, food preferences breakdown

### Events tab

- **Manage events**: Add, edit, view, and delete events (name, date, status,
  venue, description, capacity, registration path). Backed by the
  `/admin/events` routes and the `manage_events` Lambda.

### Users tab

- **Registrants + NPO status**: Every distinct registrant (deduped by email),
  joined against the NPO membership table to show member/non-member status,
  active year, and organization. Includes search and summary stats.

### Partners tab

- **Manage partners**: Add, edit, and delete event partner profiles.

Logout clears the session and returns to the registration form.

### Authentication Flow
1. **Email/Password Login** → Cognito validates credentials
2. **Token Management** → ID token stored in localStorage (8hr validity)
3. **Auto-Refresh** → Token automatically refreshed before API calls
4. **Logout** → Tokens cleared, session ended

## Environment Variables Reference

| Variable | Description | Where to Find |
|----------|------------------|---|
| `VITE_COGNITO_USER_POOL_ID` | Cognito User Pool ID | `terraform output cognito_user_pool_id` |
| `VITE_COGNITO_CLIENT_ID` | Cognito App Client ID | `terraform output cognito_client_id` |
| `VITE_COGNITO_REGION` | AWS Region | Usually `ap-northeast-1` |
| `VITE_API_BASE_URL` | API Gateway base URL | `terraform output api_base_url` |

## Troubleshooting

### "Configuration incomplete" warning
- Make sure `.env` file exists with Cognito variables
- Restart `npm run dev` after creating `.env`
- Check browser console for actual error messages

### Login fails with "Invalid client"
- Verify `VITE_COGNITO_CLIENT_ID` matches exactly (copy-paste from terraform output)
- Check that Cognito user pool exists in correct region

### "New password required" error
- User hasn't changed temporary password yet
- Have user log in, Cognito will prompt for new password
- Or set permanent password via AWS CLI:
  ```bash
  aws cognito-idp admin-set-user-password \
    --user-pool-id ap-northeast-1_xxxxxxxxxxxxx \
    --username admin@cloudnadi.com \
    --password "NewPassword123!" \
    --permanent \
    --region ap-northeast-1
  ```

### Token refresh fails silently
- Check browser localStorage for `adminIdToken` key
- If missing, user needs to log in again
- Check browser console for detailed error

## Security Notes

- **Passwords**: Enforce strong passwords (12+ chars, uppercase, lowercase, numbers, symbols)
- **Token Storage**: ID tokens stored in localStorage (accessible via browser DevTools)
- **Token Expiry**: 8-hour validity; auto-refresh before expiry
- **HTTPS Only**: Always use HTTPS in production (CloudFront + ACM certificate)
- **Rate Limiting**: Cognito enforces account lockout after failed login attempts

## Adding More Admin Users

After first user is created, you can add more admins:

```bash
aws cognito-idp admin-create-user \
  --user-pool-id ap-northeast-1_xxxxxxxxxxxxx \
  --username anotherAdmin@cloudnadi.com \
  --user-attributes Name=email,Value=anotherAdmin@cloudnadi.com Name=email_verified,Value=true \
  --temporary-password "TempPass1234!" \
  --region ap-northeast-1

aws cognito-idp admin-add-user-to-group \
  --user-pool-id ap-northeast-1_xxxxxxxxxxxxx \
  --username anotherAdmin@cloudnadi.com \
  --group-name Admins \
  --region ap-northeast-1
```

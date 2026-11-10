# CloudFront + S3 Frontend Deployment Guide

## Overview

This setup creates:
- **S3 Bucket**: Stores your built React SPA (dist files)
- **CloudFront Distribution**: CDN to serve your frontend with caching
- **CloudFront Logs**: Bucket for access logs
- **Origin Access Identity (OAI)**: Secure S3 access from CloudFront (no public access)

## Quick Start

### 1. Deploy Infrastructure

```bash
cd wmfjapan/backend

# Initialize Terraform (if first time)
cp backend.production.hcl.example backend.production.hcl
terraform init -reconfigure -backend-config=backend.production.hcl

# Review changes
terraform plan

# Apply infrastructure
terraform apply
```

This creates:
- S3 bucket for frontend
- CloudFront distribution
- All required IAM policies

### 2. Get Your CloudFront URL

After deployment, check the outputs:

```bash
terraform output cloudfront_domain_name
```

Example output: `d123abc.cloudfront.net`

This is your temporary frontend URL. **Save it** — you'll need it for the API endpoint configuration.

### 3. Build and Deploy Frontend

From your frontend directory:

```bash
cd wmfjapan/frontend

# Build the React app
npm run build

# Deploy to S3
aws s3 sync dist/ s3://wmfjapan-prod-spa-<suffix> --delete --region ap-northeast-1

# Invalidate CloudFront cache (force refresh)
aws cloudfront create-invalidation \
  --distribution-id <your-distribution-id> \
  --paths "/*" \
  --region ap-northeast-1
```

**Note**: Replace `<suffix>` and `<distribution-id>` with actual values from `terraform output`.

### 4. Update Frontend API Endpoint

In `wmfjapan/frontend/src/api.js`, update the API base URL:

```javascript
// Change from:
const API_BASE_URL = "https://<api-gateway-id>.execute-api.ap-northeast-1.amazonaws.com/prod";

// To use your CloudFront URL if frontend is hosted there:
// (but API calls still go to API Gateway, not CloudFront)
```

**Important**: The frontend is served by CloudFront, but API calls should still go to your API Gateway endpoint. Update your CORS settings in `terraform.tfvars`:

```hcl
# Allow requests from your CloudFront domain
allowed_origins = "https://d123abc.cloudfront.net"
```

Then re-apply Terraform: `terraform apply`

## Custom Domain Setup (Optional)

### Prerequisites:
1. **AWS Certificate Manager (ACM) Certificate** in `us-east-1` region (required for CloudFront HTTPS)
2. **Route53 Hosted Zone** for your domain (or manual DNS records)

### Steps:

1. **Create ACM Certificate** (must be in `us-east-1`):
   ```bash
   aws acm request-certificate \
     --domain-name example.com \
     --subject-alternative-names "*.example.com" \
     --region us-east-1
   ```

2. **Update terraform.tfvars**:
   ```hcl
   frontend_domain     = "example.com"
   acm_certificate_arn = "arn:aws:acm:us-east-1:123456789:certificate/12345"
   ```

3. **Apply Terraform**:
   ```bash
   terraform apply
   ```

4. **Create DNS Records** (choose one):

   **Option A: Manual DNS (GoDaddy, Namecheap, etc.)**
   - Create `A` record: `example.com` → CloudFront domain (from `terraform output`)
   - Optional: Create `CNAME` for `www`: `www.example.com` → CloudFront domain

   **Option B: Route53 Automation** (uncomment in `frontendcdn.tf`):
   - Add `route53_zone_id` variable to `variables.tf`
   - Uncomment Route53 resources in `frontendcdn.tf`
   - Run `terraform apply`

## File Structure

```
backend/
├── frontendcdn.tf          # S3 + CloudFront configuration
├── variables.tf            # New variables: frontend_domain, acm_certificate_arn
├── outputs.tf              # New outputs: S3 bucket, CloudFront URL
├── terraform.tfvars        # Frontend configuration
└── [other files]
```

## Outputs Reference

```bash
# Get all frontend-related outputs:
terraform output s3_frontend_bucket_name
terraform output cloudfront_distribution_id
terraform output cloudfront_domain_name
terraform output frontend_url
terraform output deploy_frontend_command
```

## Caching Strategy

- **HTML files** (`/index.html`): Cached for 1 hour (TTL: 3600s)
- **Static assets** (`/assets/*`): Cached for 1 year (TTL: 31536000s)
- **Assets versioned by Vite**: Names include hash (e.g., `index-BHBJ8z1N.css`), so no cache invalidation needed for updates

## SPA Routing

CloudFront is configured to:
- Serve `index.html` for all 404/403 errors (root path `/`)
- This enables client-side routing in React (React Router handles the URL parsing)

## Common Commands

### Deploy preview environment

```bash
cd wmfjapan/backend
terraform init -reconfigure -backend-config=backend.preview.hcl
terraform plan -var-file=terraform.preview.tfvars
terraform apply -var-file=terraform.preview.tfvars

cd ../frontend
npm run build:preview
aws s3 sync dist/ s3://<preview-frontend-bucket> --delete
aws cloudfront create-invalidation --distribution-id <preview-distribution-id> --paths "/*"
```

Use preview stack outputs to populate `.env.preview` (`VITE_API_BASE_URL`, Cognito IDs).

The backend uses S3 native lockfile mode (`use_lockfile = true`).

### Deploy latest frontend:
```bash
cd wmfjapan/frontend
npm run build
aws s3 sync dist/ s3://<prod-frontend-bucket> --delete
aws cloudfront create-invalidation --distribution-id $(terraform output -raw cloudfront_distribution_id) --paths "/*"
```

### View CloudFront logs:
```bash
aws s3 ls s3://wmfjapan-prod-cf-logs-<suffix>/cloudfront-logs/ --recursive
```

### Delete everything (cleanup):
```bash
cd wmfjapan/backend
terraform destroy
```

## Troubleshooting

### **403 Access Denied when viewing CloudFront URL**
- Ensure S3 bucket policy is applied (should be automatic)
- Check CloudFront distribution status (check "Disabled" status)

### **Stale content after deployment**
- Invalidate CloudFront cache: `aws cloudfront create-invalidation --distribution-id <id> --paths "/*"`
- Clear browser cache (hard refresh: Cmd+Shift+Ctrl+R)

### **Custom domain shows Certificate Mismatch**
- Verify ACM certificate ARN is correct and in `us-east-1`
- Ensure certificate includes the domain you're using

### **Page refresh returns 404**
- CloudFront SPA routing should handle this (configured with 404 → index.html redirect)
- If not working, verify `custom_error_response` blocks in `frontendcdn.tf`

## Security Notes

✓ S3 bucket is **private** — only CloudFront can access it via OAI  
✓ CloudFront uses **HTTPS** (CloudFront default domain has auto-HTTPS)  
✓ Custom domain uses **ACM certificates** (no self-signed certs)  
✓ CloudFront access logs are stored securely in a separate S3 bucket  

## Next Steps

1. ✅ Deploy infrastructure: `terraform apply`
2. ✅ Build frontend: `npm run build`
3. ✅ Deploy to S3: `aws s3 sync ...`
4. ✅ Visit `https://<cloudfront-domain>`
5. (Optional) Set up custom domain with ACM certificate
6. (Optional) Update CORS in API Gateway

---

**For more info**, see AWS documentation:
- [CloudFront SPA Routing](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/custom-error-pages.html)
- [S3 + CloudFront](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/getting-started-cloudfront-distribution-cdn.html)

# Frontend App (React + Vite)

This is the frontend for the Aura event registration system.

## Tech Stack

- React 18
- Vite 5

## Prerequisites

- Node.js 18+ (recommended)
- npm

## Install Dependencies

```bash
npm install
```

## Run Locally

```bash
npm run dev
```

Vite will start a local dev server and print the local URL in terminal.

## Environment Variables

Create a `.env` file in this folder if you want to override the API endpoint:

```bash
VITE_API_BASE_URL=https://<your-api-id>.execute-api.<region>.amazonaws.com/prod
```

If this variable is not set, the app uses the fallback URL defined in [src/api.js](src/api.js).

Registration route:

- `/onam/register`

## Build for Production

```bash
cp env.production.example .env.production
npm run build
```

Production files are generated in the `dist/` folder.

This uses `.env.production` values (for example, API stage `/prod`) and outputs the build to `dist/`.

## Build for Preview Environment

Use a dedicated preview env file and Vite mode:

```bash
cp env.preview.example .env.preview
npm run build:preview
```

This uses `.env.preview` values (for example, API stage `/preview`) and outputs the build to `dist/`.

## Preview Production Build Locally

```bash
npm run preview
```

## Deploy to Amazon S3 (Static Hosting)

1. Build the app:

```bash
npm run build
```

1. Upload the contents of `dist/` to your S3 bucket.

Example using AWS CLI:

```bash
aws s3 sync dist/ s3://<your-bucket-name> --delete
```

1. Ensure bucket static website hosting is enabled and correct bucket policy is applied.

1. Optional: Put CloudFront in front of S3 for HTTPS and better caching.

## Scripts

- `npm run dev` - Start development server
- `npm run build` - Create production build
- `npm run build:preview` - Create a build using `.env.preview`
- `npm run preview` - Preview production build locally

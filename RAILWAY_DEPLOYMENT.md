# Railway Deployment Guide for Sitespace FastAPI

This guide walks you through deploying your Sitespace FastAPI application to Railway.

## Prerequisites

1. [Railway account](https://railway.app) (free tier available)
2. [Railway CLI](https://docs.railway.app/develop/cli) (optional but recommended)
3. Git repository with your code

## Deployment Steps

### 1. Prepare Your Repository

Ensure your repository has the following files (already created):
- `Dockerfile` ✅
- `railway.toml` ✅
- `requirements.txt` ✅
- `railway.env.example` ✅

### 2. Create a New Railway Project

#### Option A: Using Railway Web Interface
1. Go to [Railway.app](https://railway.app)
2. Click "New Project"
3. Select "Deploy from GitHub repo"
4. Connect your GitHub account and select this repository
5. Railway will automatically detect the Dockerfile and begin building

#### Option B: Using Railway CLI
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login to Railway
railway login

# Initialize project in your repository
railway init

# Deploy
railway up
```

### 3. Add PostgreSQL Database

1. In your Railway project dashboard, click "New Service"
2. Select "Database" → "PostgreSQL"
3. Railway will automatically provision a PostgreSQL database
4. The `DATABASE_URL` environment variable will be automatically set

### 4. Configure Environment Variables

In your Railway project dashboard, go to the "Variables" tab and add these environment variables:

**Required Variables:**
```
JWT_SECRET=your_secure_jwt_secret_here_change_this
SECRET_KEY=your_secure_secret_key_here_change_this
```

**Optional Variables (will use defaults if not set):**
```
JWT_ALGORITHM=HS512
JWT_EXPIRATION_MS=86400000
EXPORT_FILES_ABSOLUTE_PATH=/app/uploads/
EXPORT_FILES_SERVER_PATH=getFile
HOST=0.0.0.0
DEBUG=False
```

**⚠️ Important Security Notes:**
- Replace `JWT_SECRET` with a strong, randomly generated secret (at least 32 characters)
- Replace `SECRET_KEY` with a strong, randomly generated key (at least 32 characters)
- Keep `DEBUG=False` for production

### 5. Configure Custom Domain (Optional)

1. In your Railway project, go to "Settings" → "Domains"
2. Click "Generate Domain" for a Railway subdomain
3. Or add your custom domain if you have one

### 6. Monitor Deployment

1. Check the "Deployments" tab to monitor build progress
2. View logs in the "Logs" tab if there are any issues
3. Once deployed, test your API at the provided Railway URL

## API Endpoints

Your deployed API will be available at your Railway domain with these endpoints:

- **Root:** `GET /` - API information
- **Health Check:** `GET /health` - Health status
- **API Documentation:** `GET /docs` - Swagger UI
- **Authentication:** `POST /api/auth/login`
- **Assets:** `/api/assets/*`
- **File Upload:** `/api/file-upload/*`
- **Site Projects:** `/api/site-projects/*`
- **Slot Booking:** `/api/slot-booking/*`
- **Subcontractors:** `/api/subcontractors/*`

## Database Migrations

Since your app uses SQLAlchemy with `create_all()`, tables will be automatically created on startup. For production, consider using Alembic migrations:

```bash
# Generate migration
alembic revision --autogenerate -m "Initial migration"

# Apply migration
alembic upgrade head
```

## File Uploads

File uploads will be stored in `/app/uploads/` within the container. For persistent storage, consider:

1. Railway Volumes (when available)
2. External storage service (AWS S3, Google Cloud Storage)
3. Database storage for smaller files

## Troubleshooting

### Common Issues:

1. **Build Fails:**
   - Check Dockerfile syntax
   - Ensure all dependencies are in requirements.txt
   - Check build logs in Railway dashboard

2. **Database Connection Issues:**
   - Verify PostgreSQL service is running
   - Check DATABASE_URL is automatically set
   - Review connection logs

3. **Environment Variables:**
   - Ensure all required variables are set
   - Check variable names match exactly
   - Restart deployment after adding variables

4. **Port Issues:**
   - Railway automatically sets PORT environment variable
   - Your app listens on HOST=0.0.0.0 and PORT from environment

### Useful Commands:

```bash
# View logs
railway logs

# Connect to database
railway connect postgres

# Open project in browser
railway open

# Redeploy
railway up --detach
```

## Production Checklist

- [ ] Set strong JWT_SECRET and SECRET_KEY
- [ ] Set DEBUG=False
- [ ] Configure proper CORS origins (replace "*" with your domains)
- [ ] Set up database backups
- [ ] Configure monitoring and alerting
- [ ] Set up custom domain with SSL
- [ ] Implement proper logging strategy
- [ ] Configure file upload limits and validation

## Support

- [Railway Documentation](https://docs.railway.app)
- [Railway Discord](https://discord.gg/railway)
- [FastAPI Documentation](https://fastapi.tiangolo.com)

Your Sitespace FastAPI application is now ready for Railway deployment! 🚀

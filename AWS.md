# Embedding service (catalyst-services) — AWS notes
#
# Image: ECR catalyst-embed (built from this repo's Dockerfile + main.py)
# Consumed by: Catalyst-main infra/aws/ec2/docker-compose.yml
#
# Auth on AWS (same host as API):
#   EMBED_SERVICE_SECRET=<shared> on both containers
#   API sets EMBED_AUTH_MODE=shared_secret
#
# Auth on current GCP Cloud Run:
#   Leave EMBED_SERVICE_SECRET unset on Cloud Run
#   API uses EMBED_AUTH_MODE=google_id_token (IAM)
#
# Local push example:
#   aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_REGISTRY
#   docker build -t $ECR_REGISTRY/catalyst-embed:latest .
#   docker push $ECR_REGISTRY/catalyst-embed:latest
#
# Or: GitHub Actions → Deploy embed to AWS ECR (workflow_dispatch)

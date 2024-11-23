#!/bin/bash

# Exit on any error
set -e

# Configuration (Modify these variables as needed)
REGISTRY_NAME="gnosis-content-processor-registry"
IMAGE_NAME="gnosis/content-processor"
INSTANCE_ID="i-07711d4c0535af444"
INSTANCE_PUBLIC_IP="34.207.87.176"
AWS_REGION="us-east-1"
KEY_PATH="/Users/chim/Working/cloud/Gnosis/gnosis.pem"
EC2_USER="ec2-user"
CONTAINER_PORT="5000"
HOST_PORT="80"

# Create a new ECR registry
echo "🚀 Creating a new ECR registry named $REGISTRY_NAME..."
aws ecr create-repository --repository-name $REGISTRY_NAME --region $AWS_REGION || echo "Repository may already exist. Skipping creation."

# Get the ECR registry URI
ECR_REGISTRY_URI=$(aws ecr describe-repositories --repository-names $REGISTRY_NAME --region $AWS_REGION --query "repositories[0].repositoryUri" --output text)

# Authenticate with AWS ECR
echo "🔑 Authenticating with AWS ECR..."
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_REGISTRY_URI

# Build the Docker image
echo "🏗️ Building Docker image..."
docker build --platform linux/amd64 -t $IMAGE_NAME:latest .

# Tag the image for ECR
echo "🏷️ Tagging image for ECR..."
docker tag $IMAGE_NAME:latest $ECR_REGISTRY_URI:latest

# Push to ECR
echo "⬆️ Pushing image to ECR..."
docker push $ECR_REGISTRY_URI:latest

echo "✨ Build and push complete!"

# SSH into the EC2 instance and execute commands
echo "🚀 Starting deployment process on EC2 instance..."
ssh -i "$KEY_PATH" "$EC2_USER@$INSTANCE_PUBLIC_IP" << EOF
    # Get the current container ID if it exists
    CONTAINER_ID=\$(docker ps -q --filter ancestor=$ECR_REGISTRY_URI:latest)

    # Login to ECR
    echo "🔑 Logging into ECR..."
    aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_REGISTRY_URI

    # Pull the latest image
    echo "⬇️ Pulling latest image..."
    docker pull $ECR_REGISTRY_URI:latest

    # Stop and remove the old container if it exists
    if [ ! -z "\$CONTAINER_ID" ]; then
        echo "🛑 Stopping old container..."
        docker stop \$CONTAINER_ID
        docker rm \$CONTAINER_ID
    fi

    # Start the new container
    echo "▶️ Starting new container..."
    docker run -d \
        --restart unless-stopped \
        -p $HOST_PORT:$CONTAINER_PORT \
        $ECR_REGISTRY_URI:latest

    # Verify the new container is running
    echo "✅ Verifying deployment..."
    docker ps
EOF

echo "✨ Deployment complete!"

#!/bin/bash

# Initialize Terraform (only needed first time or when adding new providers)
echo "Initializing Terraform..."
terraform init

# Format the Terraform files (optional but recommended)
echo "Formatting Terraform files..."
terraform fmt

# Validate the Terraform files
echo "Validating Terraform configuration..."
terraform validate

# Show the planned changes
echo "Planning Terraform changes..."
terraform plan

# Apply the changes automatically without requiring approval
echo "Applying Terraform changes..."
terraform apply -auto-approve

# Show the outputs
echo "Deployment complete! Here are your outputs:"
terraform output
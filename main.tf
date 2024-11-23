# Fill in these values
locals {
  service_name      = "gnosis-content-processor" # Name of your service
  aws_region        = "us-east-1"                # AWS region
  instance_type     = "t2.micro"                 # EC2 instance size
  key_name          = "gnosis"                   # Your SSH key name
  subnet_id         = "subnet-06ddd48eda2bc839c" # Your subnet ID
  instance_profile  = "ecsTaskExecutionRole"     # Your IAM role name
  security_group_id = "sg-098fb3e06778858fc"
}

# Provider configuration
provider "aws" {
  region = local.aws_region
}

# EC2 Instance
resource "aws_instance" "service_instance" {
  ami           = "ami-0ebfd941bbafe70c6" # Amazon Linux 2 AMI - you can change this if needed
  instance_type = local.instance_type
  key_name      = local.key_name

  iam_instance_profile   = local.instance_profile
  subnet_id              = local.subnet_id
  vpc_security_group_ids = [local.security_group_id]

  user_data = <<-EOF
    #!/bin/bash
    yum update -y
    yum install -y docker
    service docker start
    systemctl enable docker
    usermod -a -G docker ec2-user
  EOF

  tags = {
    Name = local.service_name
  }
}

# Outputs
output "instance_public_ip" {
  value = aws_instance.service_instance.public_ip
}

output "instance_id" {
  value = aws_instance.service_instance.id
}
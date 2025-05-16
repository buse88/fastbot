FROM python:3.12-slim

WORKDIR /app

# Install git and curl
RUN apt-get update && apt-get install -y git curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Configure pip to use Chinese mirrors globally
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn \
    && pip install --upgrade pip

# Download requirements.txt from GitHub
RUN curl -L https://raw.githubusercontent.com/buse88/fastbot/refs/heads/main/fastbot-2025.1.28/requirements.txt -o requirements.txt

# Install dependencies using Chinese mirrors
RUN pip install --no-cache-dir -r requirements.txt

# Create directories for mounted volumes
RUN mkdir -p /app/config

# Create entrypoint script to check configuration
RUN echo '#!/bin/sh\n\
python -c "\n\
import sys\n\
import os\n\
sys.path.append(os.path.dirname(os.path.realpath(__file__)))\n\
try:\n\
    from config.config import WATCHED_GROUP_IDS\n\
    if WATCHED_GROUP_IDS == \"000,111\":\n\
        print(\"请根据提示修改config.py中的WATCHED_GROUP_IDS参数\")\n\
        sys.exit(1)\n\
except (ImportError, AttributeError):\n\
    pass\n\
"\n\
# If script execution reaches here, config is valid, so start the main application\n\
exec python main.py' > /app/entrypoint.sh && \
    chmod +x /app/entrypoint.sh

# Expose port 5670
EXPOSE 5670

# Set the entrypoint script as the container's entry point
ENTRYPOINT ["/app/entrypoint.sh"]

# This CMD will be overridden by the entrypoint if config validation fails
CMD ["python", "main.py"] 

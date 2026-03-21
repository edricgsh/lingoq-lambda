FROM public.ecr.aws/lambda/python:3.12

# Install ffmpeg for yt-dlp
RUN dnf install -y ffmpeg || (yum install -y epel-release && yum install -y ffmpeg)

# Copy requirements and install Python dependencies
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy function code
COPY src/handler.py ${LAMBDA_TASK_ROOT}/

# Set the CMD to your handler
CMD [ "handler.handler" ]

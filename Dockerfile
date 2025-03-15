FROM python:3.11.8-alpine

# Install system dependencies including FFmpeg
RUN apk add --no-cache \
    ffmpeg \
    gcc \
    musl-dev \
    python3-dev \
    libffi-dev

WORKDIR /code

COPY pyproject.toml requirements.txt ./

RUN pip install -r requirements.txt

COPY app app

CMD [ "python","-u", "app/main.py" ]
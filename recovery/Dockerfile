FROM python:3.12-slim-bookworm
WORKDIR /controller
COPY controller.py ./
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 65534:65534
CMD ["python", "controller.py"]

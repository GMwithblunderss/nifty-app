FROM python:3.11-slim
# git is needed so persist.py can save your data to the private GitHub data repo; tzdata for Indian time
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 TZ=Asia/Kolkata
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY nify_1.py persist.py autolab.py chainrec.py mobile.py swinglab.py swinglab_ui.py ./
CMD ["python", "nify_1.py"]

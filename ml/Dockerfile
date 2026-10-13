# Образ для обучения на реальных метках: сервер (wsn.*, numpy) + scikit-learn/scipy.
# docker build -t wsn-ml -f ml/Dockerfile ml/
FROM wsn-server:latest
USER root
RUN pip install --no-cache-dir scikit-learn scipy

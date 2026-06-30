FROM python:3.11-slim

# Analysis libs baked in — runtime has NO network, so nothing installs at run time.
RUN pip install --no-cache-dir \
    pandas==2.2.3 \
    matplotlib==3.9.2 \
    openpyxl==3.1.5

# Non-root, no home, no shell.
RUN useradd --uid 5000 --no-create-home --shell /usr/sbin/nologin sandbox

# Agg = headless backend; MPLCONFIGDIR points at the writable tmpfs (/tmp) at run time.
ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mpl \
    PYTHONDONTWRITEBYTECODE=1

COPY bootstrap.py /opt/bootstrap.py

USER 5000
WORKDIR /tmp
ENTRYPOINT ["python", "/opt/bootstrap.py"]

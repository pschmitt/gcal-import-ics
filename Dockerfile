FROM python:3-alpine

ADD ./requirements.txt /app/requirements.txt

RUN pip3 install -r /app/requirements.txt && \
  adduser -D -u 1099 gcal

ENV TZ=UTC
VOLUME ["/config"]
WORKDIR /app

ADD ./gcal_import.py /app/gcal_import.py
ADD ./entrypoint.sh /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["--help"]

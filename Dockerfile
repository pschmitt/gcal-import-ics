FROM python:3-alpine

ADD ./requirements.txt /app/requirements.txt

RUN pip3 install -r /app/requirements.txt && \
  apk add --no-cache bash sudo && \
  adduser -D -u 1099 gcal

ENV TZ=UTC INTERVAL= DEBUG= CALENDAR= ICS_URL= DELETE= CLEAR= PROXY=
VOLUME ["/config"]
WORKDIR /app

ADD ./gcal_import.py /app/gcal_import.py
ADD ./entrypoint.sh /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

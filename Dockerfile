FROM alpine:3.18

# Need it to have a correct time zone
RUN apk add --no-cache tzdata

# Install requirements for add-on
RUN apk add --no-cache python3 py3-requests py3-pip
COPY requirements.txt /
RUN pip3 install -r requirements.txt

# Copy data for add-on
COPY run.sh /
COPY envoy_to_mqtt_json.py /
COPY password_calc.py /
RUN mkdir data
COPY data/options.json /data/options.json

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
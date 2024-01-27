#!/usr/bin/python3
# This version reads json from Envoy then publishes the json to mqtt broker
#
# Version 1.0 1st September 2021 - Initial release
# Version 1.1 7th November 2021 - Include date/time to output for checking
# Version 1.2 6th April 2022 - tidy up some comments
# Version 1.3 7th April 2022 - converted to work as a Home Assistant Addon
# Version 1.4 17th July 2023 - converted to work with V7 firmware by https://github.com/helderfmf
#
# Ian Mills
# vk2him@gmail.com
#

import logging
import json
import sys
import urllib3
import requests
from requests.auth import HTTPDigestAuth
import threading
import time
import paho.mqtt.client as mqtt
import xml.etree.ElementTree as ElementTree
import os
from password_calc import get_mobile_passwd


with open("data/options.json", "r") as f:
    option_dict = json.load(f)

#
# Settings Start here
#
MQTT_HOST = option_dict["MQTT_HOST"]  # Note - if issues connecting, use FQDN for broker IP instead of hassio.local
MQTT_PORT = option_dict["MQTT_PORT"]
MQTT_TOPIC_PRODUCTION_POWER = option_dict["MQTT_TOPIC_PRODUCTION_POWER"]
MQTT_TOPIC_CONSUMPTION_POWER = option_dict["MQTT_TOPIC_CONSUMPTION_POWER"]
MQTT_TOPIC_GRID_POWER = option_dict["MQTT_TOPIC_GRID_POWER"]
MQTT_CLIENT_ID = option_dict["MQTT_CLIENT_ID"]
MQTT_USER = option_dict["MQTT_USER"]
MQTT_PASSWORD = option_dict["MQTT_PASSWORD"]
ENVOY_HOST = option_dict["ENVOY_HOST"]  # ** Enter envoy-s IP. Note - use FQDN and not envoy.local if issues connecting
ENVOY_USER = option_dict["ENVOY_USER"]
ENVOY_PASSWORD = option_dict["ENVOY_PASSWORD"]
SLEEP_TIME = option_dict["SLEEP_TIME"]
DEBUG = option_dict["DEBUG"]
#  End Settings - no changes after this line

# Token generator
LOGIN_URL = 'https://enlighten.enphaseenergy.com/login/login.json'
USERNAME = b'installer'
TOKEN_FILE = 'data/token.txt'
TOKEN_URL = 'https://entrez.enphaseenergy.com/tokens'

REQUEST_TIMEOUT = 30

logger = logging.getLogger()
logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

urllib3.disable_warnings()  # disable warnings of self-signed certificate https


# json validator
def is_json_valid(json_data):
    try:
        json.loads(json_data)
    except ValueError:
        return False
    return True


# Get info
url_info = 'https://%s/info' % ENVOY_HOST
response_info = requests.get(url_info, verify=False)
if response_info.status_code != 200:
    logger.error('Failed connect to Envoy to get info got: %s. Verify URL: %s', response_info, url_info)
    exit(1)

root = ElementTree.fromstring(response_info.content)
serial_number = [child.text for child in root.iter('sn')]
version = [child.text for child in root.iter('software')]

if len(serial_number) != 0:
    serial_number = serial_number[0]
    logger.info('Serial number: %s', serial_number)
else:
    logger.info('Cannot decode serial number did not got valid XML for <sn> from %s', url_info)
    logger.info('Response content: %s', response_info.content)

if len(version) != 0:
    if version[0].count('D7.') == 1:
        logger.info('Detected FW version 7')
        envoy_version = 7
    elif version[0].count('D8.') == 1:
        logger.info('Detected Firmware version D8')
        envoy_version = 7
    elif version[0].count('R5.') == 1:
        logger.info('Detected Firmware version R5')
        envoy_version = 5
    elif version[0].count('D5.') == 1:
        logger.info('Detected Firmware version D5')
        envoy_version = 5
    else:
        logger.error('Cannot match firmware version, got: %s', version)
        exit(1)
else:
    logger.error('Cannot decode firmware version, did not got valid XML for <software> from %s', url_info)
    logger.info('Response content: %s', response_info.content)
    exit(1)


# Token generator
def token_gen(token):
    if not token:
        logger.info('Generating new token')
        data = {'user[email]': ENVOY_USER, 'user[password]': ENVOY_PASSWORD}
        logger.debug('Token data: %s', data)
        response = requests.post(LOGIN_URL, data=data)
        if response.status_code != 200:
            logger.error('Failed connect to %s to generate token part 1 got: %s using this info: %s',
                         LOGIN_URL, response, data)
        else:
            logger.debug('Token response: %s', response.text)
            response_data = json.loads(response.text)
            data = {'session_id': response_data['session_id'], 'serial_num': serial_number, 'username': ENVOY_USER}
            response = requests.post(TOKEN_URL, json=data)
            if response.status_code != 200:
                logger.error('Failed connect to %s to generate token part 2 got %s using this info %s',
                             TOKEN_URL, response, data)
            else:
                logger.info('Token generated')
                logger.debug(response.text)
                with open(TOKEN_FILE, 'w') as file:
                    file.write(response.text)
                return response.text
    else:
        return token


# cache token
if envoy_version != 5:
    if not os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'w') as f:
            f.write('')

    with open(TOKEN_FILE, 'r') as f:
        try:
            envoy_token = f.read()
            if envoy_token:
                logger.debug('Read token from file %s: %s', TOKEN_FILE, envoy_token)
                pass
            else:
                logger.info('No token in file: %s', TOKEN_FILE)
                envoy_token = token_gen(None)
                pass
        except Exception as e:
            logger.error(e)

# The callback for when the client receives a CONNACK response from the server.
    # Subscribing after on_connect() means that if the connection is lost
    # the subscription will be renewed when reconnecting.
    # The parameter rc is an integer giving the return code:
    # 0: Success
    # 1: Refused – unacceptable protocol version
    # 2: Refused – identifier rejected
    # 3: Refused – server unavailable
    # 4: Refused – bad user name or password (MQTT v3.1 broker only)
    # 5: Refused – not authorised (MQTT v3.1 broker only


def on_connect(cli, userdata, flags, rc):
    """
    Handle connections (or failures) to the broker.
    This is called after the client has received a CONNACK message
    from the broker in response to calling connect().
    The parameter rc is an integer giving the return code:
    0: Success
    1: Refused . unacceptable protocol version
    2: Refused . identifier rejected
    3: Refused . server unavailable
    4: Refused . bad username or password (MQTT v3.1 broker only)
    5: Refused . not authorised (MQTT v3.1 broker only)
    """
    if rc == 0:
        logger.info("Connected to %s:%s", MQTT_HOST, MQTT_PORT)
    elif rc == 1:
        logger.info("Connection refused - unacceptable protocol version")
    elif rc == 2:
        logger.info("Connection refused - identifier rejected")
    elif rc == 3:
        logger.info("Connection refused - server unavailable")
    elif rc == 4:
        logger.info("Connection refused - bad user name or password")
    elif rc == 5:
        logger.info("Connection refused - not authorised")
    else:
        logger.info("Connection failed - result code %d", rc)


def on_publish(cli, userdata, mid):
    logger.debug("mid: {0}".format(str(mid)))


def on_disconnect(cli, userdata, rc):
    logger.debug("Disconnect returned:")
    logger.debug("client: {0}".format(str(cli)))
    logger.debug("userdata: {0}".format(str(userdata)))
    logger.debug("result: {0}".format(str(rc)))


client = mqtt.Client(MQTT_CLIENT_ID)
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
logger.debug('Will wait for mqtt connect')
wait: client.connect(MQTT_HOST, int(MQTT_PORT), 30)
logger.debug('Finished waiting for mqtt connect')
wait: client.loop_start()


def scrape_stream_production():
    global envoy_token
    envoy_token = token_gen(envoy_token)
    url = 'https://%s/production.json' % ENVOY_HOST
    while True:
        try:
            headers = {"Authorization": "Bearer " + envoy_token}
            stream = requests.get(url, timeout=REQUEST_TIMEOUT, verify=False, headers=headers)
            if stream.status_code == 401:
                logger.error('Failed to authenticate %s generating new token', stream)
                envoy_token = token_gen(None)
            elif stream.status_code != 200:
                logger.error('Failed connect to Envoy got: %s', stream)
            else:
                if is_json_valid(stream.content):
                    data = stream.json()
                    production_power = round(data['production'][0]['wNow'])
                    consumption_power = round(data['consumption'][0]['wNow'])
                    grid_power = round(data['production'][0]['wNow'] - data['consumption'][0]['wNow'])
                    client.publish(topic=MQTT_TOPIC_PRODUCTION_POWER, payload=production_power, qos=0)
                    client.publish(topic=MQTT_TOPIC_CONSUMPTION_POWER, payload=consumption_power, qos=0)
                    client.publish(topic=MQTT_TOPIC_GRID_POWER, payload=grid_power, qos=0)
                    time.sleep(SLEEP_TIME)
                else:
                    logger.error('Invalid Json Response: %s', stream.content)
        except requests.exceptions.RequestException as ex:
            logger.info('Exception fetching stream data: %s', ex)


def scrape_stream_livedata():
    global envoy_token
    envoy_token = token_gen(envoy_token)
    activate_json = {"enable": 1}
    url = 'https://%s/ivp/livedata/status' % ENVOY_HOST
    while True:
        try:
            headers = {"Authorization": "Bearer " + envoy_token}
            stream = requests.get(url, timeout=REQUEST_TIMEOUT, verify=False, headers=headers)
            if stream.status_code == 401:
                logger.error('Failed to authenticate %s generating new token', stream)
                envoy_token = token_gen(None)
            elif stream.status_code != 200:
                logger.error('Failed connect to Envoy got %s', stream)
            elif is_json_valid(stream.content):
                if stream.json()['connection']['sc_stream'] == 'disabled':
                    url_activate = 'https://%s/ivp/livedata/stream' % ENVOY_HOST
                    logger.info('Stream is not active, trying to enable')
                    response_activate = requests.post(url_activate, verify=False, headers=headers, json=activate_json)
                    if is_json_valid(response_activate.content):
                        if response_activate.json()['sc_stream'] == 'enabled':
                            requests.get(url, stream=True, timeout=REQUEST_TIMEOUT, verify=False, headers=headers)
                            logger.info('Success, stream is active now')
                        else:
                            logger.error('Failed to activate stream %s', response_activate.content)
                    else:
                        logger.info('Invalid Json Response: %s', response_activate.content)
                else:
                    consumption_power = json.dumps(round(stream.json()["meters"]["grid"]["agg_p_mw"]*0.001))
                    client.publish(topic=MQTT_TOPIC_CONSUMPTION_POWER, payload=consumption_power, qos=0)
                    time.sleep(SLEEP_TIME)
            elif not is_json_valid(stream.content):
                logger.info('Invalid Json Response: %s', stream.content)

        except requests.exceptions.RequestException as ex:
            logger.info('Exception fetching stream data: %s', ex)


def scrape_stream_meters():
    global envoy_token
    envoy_token = token_gen(envoy_token)
    url = 'https://%s/ivp/meters/readings' % ENVOY_HOST
    logger.debug('Url: %s', url)
    while True:
        try:
            headers = {"Authorization": "Bearer " + envoy_token}
            logger.debug('headers: %s', headers)
            stream = requests.get(url, timeout=REQUEST_TIMEOUT, verify=False, headers=headers)
            logger.debug('stream: %s', stream.content)
            if stream.status_code == 401:
                logger.error('Failed to authenticate %s generating new token', stream)
                envoy_token = token_gen(None)
                headers = {"Authorization": "Bearer " + envoy_token}
                logger.debug('headers after 401: %s', headers)
                stream = requests.get(url, timeout=REQUEST_TIMEOUT, verify=False, headers=headers)
                logger.debug('stream after 401: %s', stream.content)
            elif stream.status_code != 200:
                logger.error('Failed connect to Envoy got %s', stream)
                logger.debug('stream after != 200: %s', stream.content)
            else:
                if is_json_valid(stream.content):
                    data = stream.json()
                    logger.debug('Response: %s', data)
                    production_power = round(data[0]["activePower"])
                    consumption_power = round(data[1]["activePower"])
                    grid_power = round(data[0]["activePower"] - data[1]["activePower"])
                    logger.debug('production power: %d', production_power)
                    logger.debug('consumption power: %d', consumption_power)
                    logger.debug('grid power: %d', grid_power)
                    client.publish(topic=MQTT_TOPIC_PRODUCTION_POWER, payload=production_power, qos=0)
                    client.publish(topic=MQTT_TOPIC_CONSUMPTION_POWER, payload=consumption_power, qos=0)
                    client.publish(topic=MQTT_TOPIC_GRID_POWER, payload=grid_power, qos=0)
                    time.sleep(SLEEP_TIME)
                else:
                    logger.error('Invalid Json Response: %s', stream.content)
        except requests.exceptions.RequestException as ex:
            logger.error('Exception fetching stream data: %s', ex)


def scrape_stream():
    serial = serial_number.encode("utf-8")
    envoy_password = get_mobile_passwd(serial, USERNAME)
    logger.info('Envoy password is %s', envoy_password)
    logger.debug('username: %s', USERNAME.decode())
    auth = HTTPDigestAuth(USERNAME.decode(), envoy_password)
    logger.debug('auth: %s', auth)
    marker = b'data: '
    url = 'https://%s/stream/meter' % ENVOY_HOST
    logger.debug('Url: %s', url)
    while True:
        try:
            stream = requests.get(url, verify=False, auth=auth, stream=True, timeout=REQUEST_TIMEOUT)
            for line in stream.iter_lines():
                if line.startswith(marker):
                    logger.debug('Line marker: %s', line)
                    # data = json.loads(line.replace(marker, b''))
                    # json_string = json.dumps(data)
                    # client.publish(topic=MQTT_TOPIC, payload=json_string, qos=0)
        except requests.exceptions.RequestException as ex:
            logger.info('Exception fetching stream data: %s', ex)


def main():
    # Use url https://envoy.local/production.json
    # stream_thread = threading.Thread(target=scrape_stream_production)
    # Use url https://envoy.local/ivp/livedata/status
    # stream_thread = threading.Thread(target=scrape_stream_livedata)
    # Use url https://envoy.local/ivp/meters/reading
    # stream_thread = threading.Thread(target=scrape_stream_meters)

    if envoy_version == 7:
        stream_thread = threading.Thread(target=scrape_stream_meters)
        stream_thread.start()
    elif envoy_version == 5:
        stream_thread = threading.Thread(target=scrape_stream)
        stream_thread.start()
    else:
        logger.info("Don't know what version to use, will not start")


if __name__ == '__main__':
    main()

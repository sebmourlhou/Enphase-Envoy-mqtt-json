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


import json
import urllib3
import requests
from requests.auth import HTTPDigestAuth
import threading
import pprint
from datetime import datetime
import time
import paho.mqtt.client as mqtt
import xml.etree.ElementTree as ElementTree
import os
from password_calc import get_mobile_passwd

urllib3.disable_warnings()  # disable warnings of self-signed certificate https
client = mqtt.Client()
pp = pprint.PrettyPrinter()

with open("data/options.json", "r") as f:
    option_dict = json.load(f)

now = datetime.now()
dt_string = now.strftime("%d/%m/%Y %H:%M:%S")

#
# Settings Start here
#
MQTT_HOST = option_dict["MQTT_HOST"]  # Note - if issues connecting, use FQDN for broker IP instead of hassio.local
MQTT_PORT = option_dict["MQTT_PORT"]
MQTT_TOPIC = option_dict["MQTT_TOPIC"]  # Note - if you change this topic, you'll need to also change config.yaml
MQTT_USER = option_dict["MQTT_USER"]
MQTT_PASSWORD = option_dict["MQTT_PASSWORD"]
ENVOY_HOST = option_dict["ENVOY_HOST"]  # ** Enter envoy-s IP. Note - use FQDN and not envoy.local if issues connecting
ENVOY_USER = option_dict["ENVOY_USER"]
ENVOY_USER_PASS = option_dict["ENVOY_USER_PASS"]
USE_FREEDS = option_dict["USE_FREEDS"]
DEBUG = option_dict["DEBUG"]
MQTT_TOPIC_FREEDS = "Inverter/GridWatts"
#  End Settings - no changes after this line

# Token generator
LOGIN_URL = 'https://enlighten.enphaseenergy.com/login/login.json'
USERNAME = b'installer'
TOKEN_FILE = 'data/token.txt'
TOKEN_URL = 'https://entrez.enphaseenergy.com/tokens'


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
    print(dt_string, 'Failed connect to Envoy to get info got ', response_info, 'Verify URL', url_info)
    exit(1)

root = ElementTree.fromstring(response_info.content)
serial_number = [child.text for child in root.iter('sn')]
version = [child.text for child in root.iter('software')]

if len(serial_number) != 0:
    serial_number = serial_number[0]
    print(dt_string, 'Serial number:', serial_number)
else:
    print(dt_string, 'Cannot decode serial number did not got valid XML for <sn> from ', url_info)
    print(dt_string, 'Response content:', response_info.content)

if len(version) != 0:
    if version[0].count('D7.') == 1:
        print(dt_string, 'Detected FW version 7')
        envoy_version = 7
    elif version[0].count('D8.') == 1:
        print(dt_string, 'Detected Firmware version D8')
        envoy_version = 7
    elif version[0].count('R5.') == 1:
        print(dt_string, 'Detected Firmware version R5')
        envoy_version = 5
    elif version[0].count('D5.') == 1:
        print(dt_string, 'Detected Firmware version D5')
        envoy_version = 5
    else:
        print(dt_string, 'Cannot match firmware version, got ', version)
        exit(1)
else:
    print(dt_string, 'Cannot decode firmware version, did not got valid XML for <software> from ', url_info)
    print(dt_string, 'Response content:', response_info.content)
    exit(1)

if USE_FREEDS:
    print(dt_string, 'FREEDS is active, using topic:', MQTT_TOPIC_FREEDS)
else:
    print(dt_string, 'FREEDS is inactive')


# Token generator
def token_gen(token):
    if not token:
        print(dt_string, 'Generating new token')
        data = {'user[email]': ENVOY_USER, 'user[password]': ENVOY_USER_PASS}
        if DEBUG:
            print(dt_string, 'Token data:', data)
        response = requests.post(LOGIN_URL, data=data)
        if response.status_code != 200:
            print(dt_string, 'Failed connect to %s to generate token part 1 got' % LOGIN_URL,
                  response, 'using this info', data)
        else:
            if DEBUG:
                print(dt_string, 'Token response', response.text)
            response_data = json.loads(response.text)
            data = {'session_id': response_data['session_id'], 'serial_num': serial_number, 'username': ENVOY_USER}
            response = requests.post(TOKEN_URL, json=data)
            if response.status_code != 200:
                print(dt_string, 'Failed connect to %s to generate token part 2 got' % TOKEN_URL,
                      response, 'using this info', data)
            else:
                print(dt_string, 'Token generated', response.text)
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
                print(dt_string, 'Read token from file', TOKEN_FILE, ': ', envoy_token)
                pass
            else:
                print(dt_string, 'No token in file:', TOKEN_FILE)
                envoy_token = token_gen(None)
                pass
        except Exception as e:
            print(e)

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
    4: Refused . bad user name or password (MQTT v3.1 broker only)
    5: Refused . not authorised (MQTT v3.1 broker only)
    """
    if rc == 0:
        print(dt_string, "Connected to %s:%s" % (MQTT_HOST, MQTT_PORT))
        # Subscribe to our incoming topic
        cli.subscribe(MQTT_TOPIC)
        print(dt_string, 'Subscribed to MQTT_TOPIC:', "{0}".format(MQTT_TOPIC))
    elif rc == 1:
        print(dt_string, " Connection refused - unacceptable protocol version")
    elif rc == 2:
        print(dt_string, " Connection refused - identifier rejected")
    elif rc == 3:
        print(dt_string, " Connection refused - server unavailable")
    elif rc == 4:
        print(dt_string, " Connection refused - bad user name or password")
    elif rc == 5:
        print(dt_string, " Connection refused - not authorised")
    else:
        print(dt_string, " Connection failed - result code %d" % rc)


def on_publish(cli, userdata, mid):
    print("mid: {0}".format(str(mid)))


def on_disconnect(cli, userdata, rc):
    print("Disconnect returned:")
    print("client: {0}".format(str(cli)))
    print("userdata: {0}".format(str(userdata)))
    print("result: {0}".format(str(rc)))


client = mqtt.Client()
client.on_connect = on_connect
client.on_disconnect = on_disconnect
# Uncomment to enable debug messages
# client.on_log       = on_log
client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
if DEBUG:
    print(dt_string, 'Will wait for mqtt connect')
wait: client.connect(MQTT_HOST, int(MQTT_PORT), 30)
if DEBUG:
    print(dt_string, 'Finished waiting for mqtt connect')
wait: client.loop_start()


def scrape_stream_production():
    global envoy_token
    envoy_token = token_gen(envoy_token)
    url = 'https://%s/production.json' % ENVOY_HOST
    while True:
        try:
            headers = {"Authorization": "Bearer " + envoy_token}
            stream = requests.get(url, timeout=5, verify=False, headers=headers)
            if stream.status_code == 401:
                print(dt_string, 'Failed to autenticate', stream, 'generating new token')
                envoy_token = token_gen(None)
            elif stream.status_code != 200:
                print(dt_string, 'Failed connect to Envoy got ', stream)
            else:
                if is_json_valid(stream.content):
                    json_string = json.dumps(stream.json())
                    client.publish(topic=MQTT_TOPIC, payload=json_string, qos=0)
                    if USE_FREEDS:
                        json_string_freeds = json.dumps(round(stream.json()['consumption'][0]['wNow']))
                        client.publish(topic=MQTT_TOPIC_FREEDS, payload=json_string_freeds, qos=0)
                    time.sleep(1)
                else:
                    print(dt_string, 'Invalid Json Response:', stream.content)
        except requests.exceptions.RequestException as ex:
            print(dt_string, 'Exception fetching stream data: %s' % ex)


def scrape_stream_livedata():
    global envoy_token
    envoy_token = token_gen(envoy_token)
    activate_json = {"enable": 1}
    url = 'https://%s/ivp/livedata/status' % ENVOY_HOST
    while True:
        try:
            headers = {"Authorization": "Bearer " + envoy_token}
            stream = requests.get(url, timeout=5, verify=False, headers=headers)
            if stream.status_code == 401:
                print(dt_string, 'Failed to autenticate', stream, 'generating new token')
                envoy_token = token_gen(None)
            elif stream.status_code != 200:
                print(dt_string, 'Failed connect to Envoy got ', stream)
            elif is_json_valid(stream.content):
                if stream.json()['connection']['sc_stream'] == 'disabled':
                    url_activate = 'https://%s/ivp/livedata/stream' % ENVOY_HOST
                    print(dt_string, 'Stream is not active, trying to enable')
                    response_activate = requests.post(url_activate, verify=False, headers=headers, json=activate_json)
                    if is_json_valid(response_activate.content):
                        if response_activate.json()['sc_stream'] == 'enabled':
                            stream = requests.get(url, stream=True, timeout=5, verify=False, headers=headers)
                            print(dt_string, 'Success, stream is active now')
                        else:
                            print(dt_string, 'Failed to activate stream ', response_activate.content)
                    else:
                        print(dt_string, 'Invalid Json Response:', response_activate.content)
                else:
                    json_string = json.dumps(stream.json())
                    client.publish(topic=MQTT_TOPIC, payload=json_string, qos=0)
                    if USE_FREEDS:
                        json_string_freeds = json.dumps(round(stream.json()["meters"]["grid"]["agg_p_mw"]*0.001))
                        client.publish(topic=MQTT_TOPIC_FREEDS, payload=json_string_freeds, qos=0)
                    time.sleep(0.6)
            elif not is_json_valid(stream.content):
                print(dt_string, 'Invalid Json Response:', stream.content)

        except requests.exceptions.RequestException as ex:
            print(dt_string, 'Exception fetching stream data: %s' % ex)


def scrape_stream_meters():
    global envoy_token
    envoy_token = token_gen(envoy_token)
    url = 'https://%s/ivp/meters/readings' % ENVOY_HOST
    if DEBUG:
        print(dt_string, 'Url:', url)
    while True:
        try:
            headers = {"Authorization": "Bearer " + envoy_token}
            if DEBUG:
                print(dt_string, 'headers:', headers)
            stream = requests.get(url, timeout=5, verify=False, headers=headers)
            if DEBUG:
                print(dt_string, 'stream:', stream.content)
            if stream.status_code == 401:
                print(dt_string, 'Failed to autenticate', stream, 'generating new token')
                envoy_token = token_gen(None)
                headers = {"Authorization": "Bearer " + envoy_token}
                if DEBUG:
                    print(dt_string, 'headers after 401:', headers)
                stream = requests.get(url, timeout=5, verify=False, headers=headers)
                if DEBUG:
                    print(dt_string, 'stream after 401:', stream.content)
            elif stream.status_code != 200:
                print(dt_string, 'Failed connect to Envoy got ', stream)
                if DEBUG:
                    print(dt_string, 'stream after != 200:', stream.content)
            else:
                if is_json_valid(stream.content):
                    if DEBUG:
                        print(dt_string, 'Json Response:', stream.json())
                    json_string = json.dumps(stream.json())
                    client.publish(topic=MQTT_TOPIC, payload=json_string, qos=0)
                    if USE_FREEDS:
                        json_string_freeds = json.dumps(round(stream.json()[1]["activePower"]))
                        if DEBUG:
                            print(dt_string, 'Json freeds:', stream.json()[1]["activePower"])
                        client.publish(topic=MQTT_TOPIC_FREEDS, payload=json_string_freeds, qos=0)
                    time.sleep(0.6)
                else:
                    print(dt_string, 'Invalid Json Response:', stream.content)
        except requests.exceptions.RequestException as ex:
            print(dt_string, 'Exception fetching stream data: %s' % ex)


def scrape_stream():
    serial = serial_number.encode("utf-8")
    envoy_password = get_mobile_passwd(serial, USERNAME)
    print(dt_string, 'Envoy password is', envoy_password)
    if DEBUG:
        print(dt_string, 'username:', USERNAME.decode())
    auth = HTTPDigestAuth(USERNAME.decode(), envoy_password)
    if DEBUG:
        print(dt_string, 'auth:', auth)
    marker = b'data: '
    url = 'https://%s/stream/meter' % ENVOY_HOST
    if DEBUG:
        print(dt_string, 'Url:', url)
    while True:
        try:
            stream = requests.get(url, verify=False, auth=auth, stream=True, timeout=5)
            for line in stream.iter_lines():
                if line.startswith(marker):
                    if DEBUG:
                        print(dt_string, 'Line marker:', line)
                    data = json.loads(line.replace(marker, b''))
                    json_string = json.dumps(data)
                    client.publish(topic=MQTT_TOPIC, payload=json_string, qos=0)
        except requests.exceptions.RequestException as ex:
            print(dt_string, 'Exception fetching stream data: %s' % ex)


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
        print(dt_string, "Don't know what version to use, will not start")


if __name__ == '__main__':
    main()

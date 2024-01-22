#!/usr/bin/python3

import hashlib
import time

serial_number = b'122112123456'
user_name = b'installer'

DEFAULT_REALM = b'enphaseenergy.com'
g_serial_number = None


# Generation of Envoy password based on serial number, copy from https://github.com/sarnau/EnphaseEnergy/passwordCalc.py
# Credits to Markus Fritze https://github.com/sarnau/EnphaseEnergy
def get_passwd_for_sn(serial_num, username, realm):
    if not serial_num or not username:
        return None
    if not realm:
        realm = DEFAULT_REALM
    return hashlib.md5(b'[e]' + username + b'@' + realm + b'#' + serial_num + b' EnPhAsE eNeRgY ').hexdigest()


def get_passwd(username, realm):
    global g_serial_number
    if g_serial_number:
        return get_passwd_for_sn(g_serial_number, username, realm)
    return None


def get_public_passwd(serial_num, username, realm, expiry_timestamp=0):
    if expiry_timestamp == 0:
        expiry_timestamp = int(time.time())
    return hashlib.md5(username + b'@' + realm + b'#' + serial_num + b'%d' % expiry_timestamp).hexdigest()


def get_mobile_passwd(serial_num, username, realm=None):
    global g_serial_number
    g_serial_number = serial_num
    digest = get_passwd_for_sn(serial_num, username, realm)
    count_zero = digest.count('0')
    count_one = digest.count('1')
    password = ''
    for cc in digest[::-1][:8]:
        if count_zero == 3 or count_zero == 6 or count_zero == 9:
            count_zero = count_zero - 1
        if count_zero > 20:
            count_zero = 20
        if count_zero < 0:
            count_zero = 0
        if count_one == 9 or count_one == 15:
            count_one = count_one - 1
        if count_one > 26:
            count_one = 26
        if count_one < 0:
            count_one = 0
        if cc == '0':
            password += chr(ord('f') + count_zero)
            count_zero = count_zero - 1
        elif cc == '1':
            password += chr(ord('@') + count_one)
            count_one = count_one - 1
        else:
            password += cc
    return password


print(get_mobile_passwd(serial_number, user_name))

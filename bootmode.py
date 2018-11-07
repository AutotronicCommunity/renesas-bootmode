import sys
import time
import struct
import serial
from hexdump import hexdump

SERIALPORT = '/dev/ttyUSB0'
BAUDRATE = 9600
DEBUG = True

def handshake(ser):
    print('[HANDSHAKE]')

    ser.reset_input_buffer()
    ser.reset_output_buffer()

    ser.write('\x00' * 30)
    get_response(ser, '\x00', no_data=True)
    send_request(ser, '\x55')
    get_response(ser, '\xE6', no_data=True)

def get_checksum(req):
    chksum = -sum(map(ord, req))
    return chr(chksum & 0xFF)

def send_request(ser, id, data=None):
    if DEBUG: print('TX --->')
    req = id
    if data and len(data):
        req += chr(len(data)) + data
        req += get_checksum(req)
    if DEBUG: hexdump(req)
    ser.write(req)

def get_response(ser, id, no_data=False, no_checksum=False, size_len=1):
    res = ser.read()
    assert len(res) == 1, 'TIMEOUT!'
    if res != id:
        if DEBUG: print('RX <---')
        if DEBUG: hexdump(res + ser.read())
        raise Exception('ERROR RESPONSE!')

    try:
        if no_data:
            return None

        size = ser.read(size_len)
        res += size
        assert len(size) == size_len, 'TIMEOUT!'

        byte_cnt = ord(size)
        data = ser.read(byte_cnt)
        res += data
        assert len(data) == byte_cnt, 'TIMEOUT!'

        if no_checksum:
            return data

        expect_checksum = get_checksum(res)
        actual_checksum = ser.read()
        res += actual_checksum
        assert len(actual_checksum) == 1, 'TIMEOUT!'
        assert expect_checksum == actual_checksum, 'INVALID CHECKSUM!'

        return data
    finally:
        if DEBUG: print('RX <---')
        if DEBUG: hexdump(res)

def device_inquiry(ser):
    print('[DEVICE INQUIRY]')
    send_request(ser, '\x20')
    data = get_response(ser, '\x30')

    devices = list()
    count = ord(data[0])
    idx = 1
    for i in range(count):
        char_count = ord(data[idx])
        devices.append(data[idx+1:idx+5]) # device code is 4 bytes
        idx += char_count # skip product code

    return devices

def device_select(ser, device):
    print('[DEVICE SELECT] device={}'.format(device))
    send_request(ser, '\x10', device)
    get_response(ser, '\x06', no_data=True)

def clock_inquiry(ser):
    print('[CLOCK INQUIRY]')
    send_request(ser, '\x21')
    data = get_response(ser, '\x31')

    clocks = list()
    for i in range(len(data)):
        clocks.append(ord(data[i]))

    return clocks

def clock_select(ser, clock):
    print('[CLOCK SELECT] clock={}'.format(clock))
    send_request(ser, '\x11', chr(clock))
    get_response(ser, '\x06', no_data=True)

def user_boot_mat_inquiry(ser):
    print('[USER BOOT MEMORY ADDR INQUIRY]')
    send_request(ser, '\x24')
    data = get_response(ser, '\x34')

    mat_count = ord(data[0])
    mat_ranges = list()
    for i in range(1, len(data), 8):
        mat_ranges.append({
            'start_addr': struct.unpack('!I', data[i:i+4])[0],
            'end_addr': struct.unpack('!I', data[i+4:i+8])[0],
        })

    return mat_ranges

def user_mat_inquiry(ser):
    print('[USER MEMORY ADDR INQUIRY]')
    send_request(ser, '\x25')
    data = get_response(ser, '\x35')

    mat_count = ord(data[0])
    mat_ranges = list()
    for i in range(1, len(data), 8):
        mat_ranges.append({
            'start_addr': struct.unpack('!I', data[i:i+4])[0],
            'end_addr': struct.unpack('!I', data[i+4:i+8])[0],
        })

    return mat_ranges

def multiplication_ratio_inquiry(ser):
    print('[MULTIPLICATION RATIO INQUIRY]')
    send_request(ser, '\x22')
    data = get_response(ser, '\x32')

    clock_type_count = ord(data[0])
    clock_multi_ratios = list()
    idx = 1
    for i in range(clock_type_count):
        ratio_count = ord(data[idx])
        idx += 1
        ratios = map(ord, data[idx:idx+ratio_count])
        clock_multi_ratios.append(ratios)
        idx += ratio_count

    return clock_multi_ratios

def operating_freq_inquiry(ser):
    print('[OPERATING FREQUENCY INQUIRY]')
    send_request(ser, '\x23')
    data = get_response(ser, '\x33')

    clock_type_count = ord(data[0])
    clock_freq_ranges = list()
    for i in range(1, 1+clock_type_count*4, 4):
        clock_freq_ranges.append({
            'min_mhz': struct.unpack('!H', data[i:i+2])[0] / 100,
            'max_mhz': struct.unpack('!H', data[i+2:i+4])[0] / 100,
        })

    return clock_freq_ranges

def bitrate_select(ser, baud_rate, input_freq_mhz, clock_count, ratio1, ratio2):
    print('[BITRATE SELECT] baud_rate={} input_freq_mhz={} clock_count={} ratio1={} ratio2={}'.format(baud_rate, input_freq_mhz, clock_count, ratio1, ratio2))
    send_request(ser, '\x3F', struct.pack('!H', int(baud_rate/100)) + struct.pack('!H', int(input_freq_mhz*100)) + chr(clock_count) + chr(ratio1) + chr(ratio2))
    get_response(ser, '\x06', no_data=True)

    # wait 1 bit time step before changing
    time.sleep(1/ser.baudrate)
    ser.baudrate = baud_rate

    # confirmation    
    send_request(ser, '\x06')
    get_response(ser, '\x06', no_data=True)

def keycode_check(ser, key_code):
    print('[KEYCODE CHECK]')
    # transition to key-code determination state
    send_request(ser, '\x40')
    get_response(ser, '\x16', no_data=True)
    # perform key-code check
    send_request(ser, '\x60', key_code)
    get_response(ser, '\x26', no_data=True)

def status_inquiry(ser):
    print('[STATUS INQUIRY]')
    send_request(ser, '\x4F')
    data = get_response(ser, '\x5F', no_checksum=True)
    return {
        "status": data[0],
        "error": data[1],
    }

def read_memory(ser, mem_area, start, end, block_size):
    print('[READ MEMORY] area={} start={} end={} block_size={}'.format(mem_area, start, end, block_size))
    data = ''
    for i in range(start, end, block_size):
        send_request(ser, '\x52', chr(mem_area) + struct.pack('!I', i) + struct.pack('!I', block_size))
        data += get_response(ser, '\x52', size_len=4)
    return data

if __name__ == "__main__":
    with serial.Serial(SERIALPORT, BAUDRATE, timeout=0.2) as ser:
        handshake(ser)
        # status = status_inquiry(ser)
        # print status

        devices = device_inquiry(ser)
        # print devices
        device_select(ser, devices[0])

        clocks = clock_inquiry(ser)
        # print clocks
        clock_select(ser, clocks[0])

        multi_ratios = multiplication_ratio_inquiry(ser)
        # print multi_ratios
        operating_freqs = operating_freq_inquiry(ser)
        # print operating_freqs
        ratio1 = multi_ratios[0][0]
        ratio2 = multi_ratios[1][0]
        base1 = operating_freqs[0]['max_mhz'] / ratio1
        base2 = operating_freqs[1]['max_mhz'] / ratio2
        assert base1 == base2, "failed to find base clock for both multipliers"
        bitrate_select(ser, BAUDRATE, base1, 2, ratio1, ratio2)

        user_boot_mat = user_boot_mat_inquiry(ser)
        # print user_boot_mat
        user_mat = user_mat_inquiry(ser)
        # print user_mat

        keycode_check(ser, '\x00' * 16)

        mem_area = 0 # user boot memory area
        start_addr = user_boot_mat[0]['start_addr']
        end_addr = user_boot_mat[0]['end_addr']
        with open('user_boot.bin', 'w+') as f:
            data = read_memory(ser, mem_area, start_addr, end_addr+1, 0x40)
            f.write(data)

        mem_area = 1 # user memory area
        start_addr = user_mat[0]['start_addr']
        end_addr = user_mat[0]['end_addr']
        with open('user.bin', 'w+') as f:
            data = read_memory(ser, mem_area, start_addr, end_addr+1, 0x40)
            f.write(data)

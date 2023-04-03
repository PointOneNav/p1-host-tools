import sys

from p1_runner.nmea_framer import NMEAFramer

import logging
logging.getLogger('point_one').setLevel(logging.TRACE)


def test_single_message():
    message = [
        "$GPGGA,000000.000,3746.37327400,N,12224.26599800,W,2,13,2.1,3.260,M,34.210,M,11.1,0234*5B\r\n"
    ]

    # Test message framing.
    input = message[0]
    framer = NMEAFramer()
    count = [0,]
    def _callback(data):
        assert data == message[count[0]]
        count[0] += 1

    framer.set_callback(_callback)
    framer.on_data(input)
    assert count[0] == 1

    # Now test framing with extra leading bytes before the message.
    input = "abcd" + message[0]
    framer.reset()
    count[0] = 0
    results = framer.on_data(input)
    assert len(results) == 1
    assert count[0] == 1


def test_partial_message():
    message = [
        "$GPGGA,000000.000,3746.37327400,N,12224.26599800,W,2,13,2.1,3.260,M,34.210,M,11.1,0234*5B\r\n",
        "$GPGGA,180532.000,3745.90318740,N,12226.18945360,W,2,26,0.5,83.332,M,-25.332,M,3.0,0131*7A\r\n",
    ]

    # Test message framing.
    count = [0,]
    def _callback(data):
        assert data == message[count[0]]
        count[0] += 1

    framer = NMEAFramer()
    framer.set_callback(_callback)

    # Send the first full message and part of the next one.
    input = message[0] + message[1][:20]
    results = framer.on_data(input)
    assert len(results) == 1
    assert count[0] == 1

    # Now send the rest of the next message.
    input = message[1][20:]
    results = framer.on_data(input)
    assert len(results) == 1
    assert count[0] == 2


def test_multi_message():
    message = [
        "$GPGGA,000000.000,3746.37327400,N,12224.26599800,W,2,13,2.1,3.260,M,34.210,M,11.1,0234*5B\r\n",
        "$GPGGA,180532.000,3745.90318740,N,12226.18945360,W,2,26,0.5,83.332,M,-25.332,M,3.0,0131*7A\r\n",
    ]

    # Test message framing.
    input = message[0] + message[1]
    framer = NMEAFramer()
    count = [0,]
    def _callback(data):
        assert data == message[count[0]]
        count[0] += 1

    framer.set_callback(_callback)
    results = framer.on_data(input)
    assert len(results) == 2
    assert count[0] == 2

    # Now test framing with extra leading bytes before the message.
    input = "abcd" + message[0] + message[1]
    framer.reset()
    count[0] = 0
    results = framer.on_data(input)
    assert len(results) == 2
    assert count[0] == 2


def test_bytes():
    message = [
        "$GPGGA,000000.000,3746.37327400,N,12224.26599800,W,2,13,2.1,3.260,M,34.210,M,11.1,0234*5B\r\n"
    ]

    # Test message framing.
    input = message[0].encode()
    framer = NMEAFramer()
    count = [0,]
    def _callback(data):
        assert not isinstance(data, bytes)
        assert data == message[count[0]]
        count[0] += 1

    framer.set_callback(_callback)
    framer.on_data(input)
    assert count[0] == 1


def test_misaligned():
    message = [
        "$GPGGA,000000.000,3746.37327400,N,12224.26599800,W,2,13,2.1,3.260,M,34.210,M,11.1,0234*5B\r\n",
        "$GPGGA,180532.000,3745.90318740,N,12226.18945360,W,2,26,0.5,83.332,M,-25.332,M,3.0,0131*7A\r\n",
    ]

    # Extra junk in the input --v-- data
    input = message[0] + "abcd\r\n" + message[1]
    framer = NMEAFramer()
    count = [0,]
    def _callback(data):
        assert data == message[count[0]]
        count[0] += 1

    framer.set_callback(_callback)
    framer.on_data(input)
    assert count[0] == 2


def test_missing_star():
    message = [
        # v-- Missing *
        "$GPGGA,000000.000,3746.37327400,N,12224.26599800,W,2,13,2.1,3.260,M,34.210,M,11.1,0234?5B\r\n",
        "$GPGGA,180532.000,3745.90318740,N,12226.18945360,W,2,26,0.5,83.332,M,-25.332,M,3.0,0131*7A\r\n",
    ]

    input = ''.join(message)
    framer = NMEAFramer()
    count = [0,]
    def _callback(data):
        assert count[0] < 1
        assert data == message[count[0] + 1]
        count[0] += 1

    framer.set_callback(_callback)
    framer.on_data(input)
    assert count[0] == 1


def test_extra_cr():
    message = [
        # v-- Too many \rs (will be ignored)
        "$GPGGA,000000.000,3746.37327400,N,12224.26599800,W,2,13,2.1,3.260,M,34.210,M,11.1,0234*5B\r\r\r\n",
        "$GPGGA,180532.000,3745.90318740,N,12226.18945360,W,2,26,0.5,83.332,M,-25.332,M,3.0,0131*7A\r\n",
    ]

    input = ''.join(message)
    framer = NMEAFramer()
    count = [0,]
    def _callback(data):
        assert data == message[count[0]]
        count[0] += 1

    framer.set_callback(_callback)
    framer.on_data(input)
    assert count[0] == 2


def test_lf():
    message = [
        # v-- Missing \n
        "$GPGGA,000000.000,3746.37327400,N,12224.26599800,W,2,13,2.1,3.260,M,34.210,M,11.1,0234*5B\r%",
        "$GPGGA,180532.000,3745.90318740,N,12226.18945360,W,2,26,0.5,83.332,M,-25.332,M,3.0,0131*7A\r\n",
    ]

    input = ''.join(message)
    framer = NMEAFramer()
    count = [0,]
    def _callback(data):
        assert count[0] < 1
        assert data == message[count[0] + 1]
        count[0] += 1

    framer.set_callback(_callback)
    framer.on_data(input)
    assert count[0] == 1


def test_checksum_failure():
    message = [
        "$GPGGA,000000.000,3746.37327400,N,12224.26599800,W,2,13,2.1,3.260,M,34.210,M,11.1,0234*5B\r\n",
        "$GPGGA,180532.000,3745.90318740,N,12226.18945360,W,2,26,0.5,83.332,M,-25.332,M,3.0,0131*7A\r\n",
        "$GPGSV,3,1,09,02,80,082,50,05,18,150,45,06,43,047,48,12,70,332,48*AB\r\n", # <-- Incorrect checksum
        "$GPGSV,3,2,09,17,05,072,44,19,21,064,45,24,35,216,45,25,38,314,45*70\r\n",
    ]

    input = ''.join(message)
    framer = NMEAFramer()
    count = [0,]
    def _callback(data):
        index = count[0] if count[0] < 2 else count[0] + 1
        assert data == message[index]
        count[0] += 1

    framer.set_callback(_callback)
    framer.on_data(input)
    assert count[0] == 3


def test_resynchronization():
    message = [
        "$GPGGA,000000.000,3746.37327400,N,12224.26599800,W,2,13,2.1,3.260,M,34.210,M,11.1,0234*5B\r\n",
        "$GPGGA,180532.000,3745.90318740,N,12226.18945360,W,2,26,0.5,83.332,M,-25.332,M,3.0,0131*7A\r\n",
    ]

    # Test resync with a single leading bogus message.
    input = "$bogus" + ''.join(message)
    framer = NMEAFramer()
    count = [0,]
    def _callback(data):
        assert data == message[count[0]]
        count[0] += 1

    framer.set_callback(_callback)
    framer.on_data(input)
    assert count[0] == 2

    # Now test with two bogus messages. The resync should find "$bogusB", but then keep going.
    input = "$bogusA$bogusB" + ''.join(message)
    framer.reset()
    count[0] = 0
    framer.on_data(input)
    assert count[0] == 2

    # Now test with 3 bogus messages, the 2 from before and an incomplete message with a \r. All 3 bad messages should
    # stop at the \r with a "no LF" error after hitting the $ from message[0]. That means the resync will process
    # everything including the $ from message[0], but not any of the rest of message[0]. Then OnData() should continue
    # after that.
    input = "$bogusA$bogusB$GPGGC\r" + ''.join(message)
    framer.reset()
    count[0] = 0
    framer.on_data(input)
    assert count[0] == 2


def test_bogus_characters():
    message = [
        "$GPGGA,000000.000,3746.37327400,N,12224.26599800,W,2,13,2.1,3.260,M,34.210,M,11.1,0234*5B\r\n",
    ]

    # The following sequence starts with a $ so looks like the start of a NMEA message and the following characters +
    # the message above (including its $) end up at the same checksum (5B). The 0x01 byte is clearly not valid though,
    # so the message will be rejected.
    input = "$\001\040" + ''.join(message)
    framer = NMEAFramer()
    count = [0,]
    def _callback(data):
        assert data == message[count[0]]
        count[0] += 1

    framer.set_callback(_callback)
    framer.on_data(input)
    assert count[0] == 1

    # The same is true for this input, except it doesn't have any _obviously_ bogus characters (non-displayable). It
    # will be caught when the framer reaches the second $. The characters "41cB" have values: 0x34, 0x31, 0x63, 0x42.
    # They end up at 5B, but are all legit symbols to find in an otherwise valid NMEA message.
    input = "$41cB" + ''.join(message)
    framer.reset()
    count[0] = 0
    framer.on_data(input)
    assert count[0] == 1


def test_displayable_chars():
    message = [
        "$PQTMVERNO,LG69TAMNR01A01_RTK_PO,2022/01/04,20:31:05*25\r\n",
        "$TEST,!@#%^&()abcdefg[]{}<>,.;':\"/\\*33\r\n",
    ]

    # Test message framing.
    input = message[0] + message[1]
    framer = NMEAFramer()
    count = [0,]
    def _callback(data):
        assert data == message[count[0]]
        count[0] += 1

    framer.set_callback(_callback)
    results = framer.on_data(input)
    assert len(results) == 2
    assert count[0] == 2

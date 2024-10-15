import logging

from p1_runner.rtcm_framer import *

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger('point_one').setLevel(logging.TRACE)

# 4050 control reset message: nav + ephemeris.
P1_RESET_MESSAGE = b'\xD3\x00\x08\xFD\x20\x02\x02\x00\x00\x00\x03\x1B\x27\x7D'

# 4050 control response message: ERROR.
P1_RESPONSE_MESSAGE = b'\xD3\x00\x05\xFD\x20\x02\x01\x02\x80\xED\xA8'

# ST 999 message.
ST999_MESSAGE = b'\xD3\x00\x0E\x3E\x74\x01\xE9\xDD\xD5\xEF\x3F\xF9\x1F\xE5\x54\x23\x20\x83\x19\x7B'


def test_crc():
    crc = RTCMFramer.calculate_crc24q(P1_RESET_MESSAGE[:-3])
    assert crc == 0x1B277D


def test_parse_4050_reset():
    result = rtcm3_frame.parse(P1_RESET_MESSAGE)
    assert result.header.message_id == 4050
    assert result.payload.sub_type == PO4050SubType.CONTROL.value
    assert result.payload.sub.control_type == PO4050ControlType.RESET.value
    assert result.payload.sub.contents.mask == 0x3


def test_parse_unrecognized():
    result = rtcm3_frame.parse(ST999_MESSAGE)
    assert result.header.message_id == 999
    assert result.payload == ST999_MESSAGE[3:-3]


def test_build_4050_reset():
    # Build a message by hand.
    contents = {
        'header': {
            'info': {'payload_length': 8},
            'message_id': 4050
        },
        'payload': {
            'sub_type': PO4050SubType.CONTROL.value,
            'sub': {
                'control_type': PO4050ControlType.RESET.value,
                'contents': {
                    'mask': 0x3
                }
            }
        },
        'crc': 0x1B277D
    }

    result = rtcm3_frame.build(contents)
    assert result == P1_RESET_MESSAGE

    # Now use the encoding helper function, specifying the message payload.
    result = build_rtcm_message(4050, contents['payload'])
    assert result == P1_RESET_MESSAGE


def test_frame_single_message():
    # Test message framing.
    input = P1_RESET_MESSAGE
    framer = RTCMFramer()
    count = [0,]

    def _callback(result):
        assert result.header.message_id == 4050
        count[0] += 1

    framer.set_callback(_callback)
    results = framer.on_data(input)
    assert len(results) == 1
    assert results[0].header.message_id == 4050
    assert count[0] == 1

    # Now test framing with extra leading bytes before the message.
    input = b'\xDE\xAD\xBE\xEF' + P1_RESET_MESSAGE
    framer.reset()
    count[0] = 0
    results = framer.on_data(input)
    assert len(results) == 1
    assert results[0].header.message_id == 4050
    assert count[0] == 1


def test_frame_multi_message():
    # Test message framing.
    input = P1_RESET_MESSAGE + P1_RESPONSE_MESSAGE
    framer = RTCMFramer()
    results = framer.on_data(input)
    assert len(results) == 2
    assert results[0].header.message_id == 4050
    assert results[0].payload.sub.control_type == PO4050ControlType.RESET.value
    assert results[1].payload.sub.control_type == PO4050ControlType.RESPONSE.value

    # Now test framing with extra between the messages.
    input = P1_RESET_MESSAGE + b'\xDE\xAD\xBE\xEF' + P1_RESPONSE_MESSAGE
    framer.reset()
    results = framer.on_data(input)
    assert len(results) == 2
    assert results[0].header.message_id == 4050
    assert results[0].payload.sub.control_type == PO4050ControlType.RESET.value
    assert results[1].payload.sub.control_type == PO4050ControlType.RESPONSE.value


def test_frame_return_size():
    # Test message framing.
    input = P1_RESET_MESSAGE + P1_RESPONSE_MESSAGE
    framer = RTCMFramer()
    results = framer.on_data(input, return_size=True)
    assert len(results) == 2
    assert results[0]['message'].header.message_id == 4050
    assert results[0]['size'] == len(P1_RESET_MESSAGE)
    assert results[1]['message'].header.message_id == 4050
    assert results[1]['size'] == len(P1_RESPONSE_MESSAGE)


def test_frame_checksum_failure():
    # Test message framing.
    input = bytearray(P1_RESET_MESSAGE + P1_RESPONSE_MESSAGE)
    input[-2] = 0x00
    framer = RTCMFramer()
    results = framer.on_data(input)
    assert len(results) == 1
    assert results[0].header.message_id == 4050
    assert results[0].payload.sub.control_type == PO4050ControlType.RESET.value


def test_lossless_resync():
    # Test decoding messages with sync bytes in between.
    input = P1_RESET_MESSAGE + b'\xD3' + P1_RESET_MESSAGE + \
        b'\xD3\xD3' + P1_RESET_MESSAGE + bytes(1024)
    framer = RTCMFramer()
    results = framer.on_data(input)
    assert len(results) == 3

    # Test decoding messages with checksum error in between.
    input = bytearray(P1_RESET_MESSAGE + P1_RESET_MESSAGE)
    input[-2] = 0x00
    input += P1_RESET_MESSAGE + bytes(1024)
    framer = RTCMFramer()
    results = framer.on_data(input)
    assert len(results) == 2

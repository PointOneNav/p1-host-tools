from enum import IntEnum

from construct import *

from . import trace as logging

################################################################################
# Point One Proprietary 4050 Messages
################################################################################


class PO4050SubType(IntEnum):
    UNDEFINED = 0
    DIAG = 1
    CONTROL = 2


########################################
# Point One 4050 control messages
########################################


class PO4050ControlType(IntEnum):
    UNDEFINED = 0
    RESPONSE = 1
    RESET = 2


class PO4050ResponseType(IntEnum):
    OK = 0
    ERROR = 1


class PO4050ResetMasks(IntEnum):
    NAVIGATION = 0x00000001
    EPHEMERIS = 0x00000002
    CORRECTIONS = 0x00000004
    SOFTWARE = 0x00FFFFFF


po4050_control_reset = Struct(
    "mask" / BitsInteger(32),
)

po4050_control_response = Struct(
    "response" / BitsInteger(8),
)

po4050_control = Struct(
    "control_type" / BitsInteger(8),
    "contents" / Switch(this.control_type, {
        PO4050ControlType.RESPONSE.value: po4050_control_response,
        PO4050ControlType.RESET.value: po4050_control_reset,
    }),
)

########################################
# Point One 4050 diagnostic messages
########################################


class PO4050DiagType(IntEnum):
    UNDEFINED = 0
    VER = 1
    STATUS = 2
    ARM_H7_CRASH = 3


po4050_diag = Struct(
    "diag_type" / BitsInteger(8),
    "contents" / Switch(this.diag_type, {}),
)

########################################
# Point One 4050 top-level message
########################################

po4050 = Struct(
    "message_id" / Const(4050, BitsInteger(12)),
    Padding(4),
    "sub_type" / BitsInteger(8),
    "sub" / Switch(this.sub_type, {
        PO4050SubType.CONTROL.value: po4050_control,
        PO4050SubType.DIAG.value: po4050_diag,
    }),
)

################################################################################
# RTCM Generic Definitions
################################################################################

RTCM3_PREAMBLE = 0xD3
RTCM3_HEADER_LENGTH = 3
RTCM3_CRC_LENGTH = 3
RTCM3_MAX_LENGTH = RTCM3_HEADER_LENGTH + (2**10 - 1) + RTCM3_CRC_LENGTH

rtcm3_header = Struct(
    "preamble" / Const(RTCM3_PREAMBLE, Int8ul),
    "info" / BitStruct(
        Padding(6),
        "payload_length" / BitsInteger(10),
    ),
    'message_id' / Peek(Bitwise(Aligned(8, BitsInteger(12))))
)

rtcm3_crc = BytesInteger(3)

rtcm3_payloads = {
    4050: po4050
}

rtcm3_frame = Struct(
    "header" / rtcm3_header,
    "message_id" / Computed(this.header.message_id),
    "payload_length" / Computed(this.header.info.payload_length),
    "payload" / Padded(this.payload_length,
                       Switch(this.message_id, {k: Bitwise(Aligned(8, v)) for k, v in rtcm3_payloads.items()},
                              default=Bytes(this.payload_length))),
    "crc" / rtcm3_crc,
)


def build_rtcm_message(message_id, payload):
    if isinstance(payload, (bytes, bytearray)):
        payload_bytes = payload
    else:
        if message_id in rtcm3_payloads:
            payload_bytes = Bitwise(rtcm3_payloads[message_id]).build(payload)
        else:
            raise ValueError('Unsupported message ID.')

    contents = {
        'header': {
            'info': {'payload_length': len(payload_bytes)},
            'message_id': message_id
        },
        'payload': payload_bytes,
        'crc': 0
    }

    header_bytes = rtcm3_header.build({'info': {'payload_length': len(payload_bytes)}, 'message_id': message_id})
    contents = header_bytes + payload_bytes

    crc = RTCMFramer.calculate_crc24q(contents)
    crc_bytes = bytearray(3)
    crc_bytes[2] = crc & 0xFF
    crc >>= 8
    crc_bytes[1] = crc & 0xFF
    crc >>= 8
    crc_bytes[0] = crc & 0xFF

    message_bytes = contents + crc_bytes
    return message_bytes

################################################################################
# RTCM Framer
################################################################################


class RTCMFramer(object):
    logger = logging.getLogger('point_one.rtcm_framer')

    CRC24Q_TABLE = [
        0x000000, 0x864CFB, 0x8AD50D, 0x0C99F6, 0x93E6E1, 0x15AA1A, 0x1933EC,
        0x9F7F17, 0xA18139, 0x27CDC2, 0x2B5434, 0xAD18CF, 0x3267D8, 0xB42B23,
        0xB8B2D5, 0x3EFE2E, 0xC54E89, 0x430272, 0x4F9B84, 0xC9D77F, 0x56A868,
        0xD0E493, 0xDC7D65, 0x5A319E, 0x64CFB0, 0xE2834B, 0xEE1ABD, 0x685646,
        0xF72951, 0x7165AA, 0x7DFC5C, 0xFBB0A7, 0x0CD1E9, 0x8A9D12, 0x8604E4,
        0x00481F, 0x9F3708, 0x197BF3, 0x15E205, 0x93AEFE, 0xAD50D0, 0x2B1C2B,
        0x2785DD, 0xA1C926, 0x3EB631, 0xB8FACA, 0xB4633C, 0x322FC7, 0xC99F60,
        0x4FD39B, 0x434A6D, 0xC50696, 0x5A7981, 0xDC357A, 0xD0AC8C, 0x56E077,
        0x681E59, 0xEE52A2, 0xE2CB54, 0x6487AF, 0xFBF8B8, 0x7DB443, 0x712DB5,
        0xF7614E, 0x19A3D2, 0x9FEF29, 0x9376DF, 0x153A24, 0x8A4533, 0x0C09C8,
        0x00903E, 0x86DCC5, 0xB822EB, 0x3E6E10, 0x32F7E6, 0xB4BB1D, 0x2BC40A,
        0xAD88F1, 0xA11107, 0x275DFC, 0xDCED5B, 0x5AA1A0, 0x563856, 0xD074AD,
        0x4F0BBA, 0xC94741, 0xC5DEB7, 0x43924C, 0x7D6C62, 0xFB2099, 0xF7B96F,
        0x71F594, 0xEE8A83, 0x68C678, 0x645F8E, 0xE21375, 0x15723B, 0x933EC0,
        0x9FA736, 0x19EBCD, 0x8694DA, 0x00D821, 0x0C41D7, 0x8A0D2C, 0xB4F302,
        0x32BFF9, 0x3E260F, 0xB86AF4, 0x2715E3, 0xA15918, 0xADC0EE, 0x2B8C15,
        0xD03CB2, 0x567049, 0x5AE9BF, 0xDCA544, 0x43DA53, 0xC596A8, 0xC90F5E,
        0x4F43A5, 0x71BD8B, 0xF7F170, 0xFB6886, 0x7D247D, 0xE25B6A, 0x641791,
        0x688E67, 0xEEC29C, 0x3347A4, 0xB50B5F, 0xB992A9, 0x3FDE52, 0xA0A145,
        0x26EDBE, 0x2A7448, 0xAC38B3, 0x92C69D, 0x148A66, 0x181390, 0x9E5F6B,
        0x01207C, 0x876C87, 0x8BF571, 0x0DB98A, 0xF6092D, 0x7045D6, 0x7CDC20,
        0xFA90DB, 0x65EFCC, 0xE3A337, 0xEF3AC1, 0x69763A, 0x578814, 0xD1C4EF,
        0xDD5D19, 0x5B11E2, 0xC46EF5, 0x42220E, 0x4EBBF8, 0xC8F703, 0x3F964D,
        0xB9DAB6, 0xB54340, 0x330FBB, 0xAC70AC, 0x2A3C57, 0x26A5A1, 0xA0E95A,
        0x9E1774, 0x185B8F, 0x14C279, 0x928E82, 0x0DF195, 0x8BBD6E, 0x872498,
        0x016863, 0xFAD8C4, 0x7C943F, 0x700DC9, 0xF64132, 0x693E25, 0xEF72DE,
        0xE3EB28, 0x65A7D3, 0x5B59FD, 0xDD1506, 0xD18CF0, 0x57C00B, 0xC8BF1C,
        0x4EF3E7, 0x426A11, 0xC426EA, 0x2AE476, 0xACA88D, 0xA0317B, 0x267D80,
        0xB90297, 0x3F4E6C, 0x33D79A, 0xB59B61, 0x8B654F, 0x0D29B4, 0x01B042,
        0x87FCB9, 0x1883AE, 0x9ECF55, 0x9256A3, 0x141A58, 0xEFAAFF, 0x69E604,
        0x657FF2, 0xE33309, 0x7C4C1E, 0xFA00E5, 0xF69913, 0x70D5E8, 0x4E2BC6,
        0xC8673D, 0xC4FECB, 0x42B230, 0xDDCD27, 0x5B81DC, 0x57182A, 0xD154D1,
        0x26359F, 0xA07964, 0xACE092, 0x2AAC69, 0xB5D37E, 0x339F85, 0x3F0673,
        0xB94A88, 0x87B4A6, 0x01F85D, 0x0D61AB, 0x8B2D50, 0x145247, 0x921EBC,
        0x9E874A, 0x18CBB1, 0xE37B16, 0x6537ED, 0x69AE1B, 0xEFE2E0, 0x709DF7,
        0xF6D10C, 0xFA48FA, 0x7C0401, 0x42FA2F, 0xC4B6D4, 0xC82F22, 0x4E63D9,
        0xD11CCE, 0x575035, 0x5BC9C3, 0xDD8538
    ]

    def __init__(self):
        self.buffer = bytes()
        self.header = None
        self.message_length = None
        self.callback = None
        self.total_data_offset = 0
        self.preamble_found = False

    def set_callback(self, callback):
        self.callback = callback

    def reset(self):
        self.buffer = bytes()
        self.header = None
        self.message_length = None
        self.preamble_found = False

    def on_data(self, data, return_size=False, return_bytes=False, return_offset=False):
        messages = []
        if isinstance(data, str):
            data = data.encode()
        self.buffer += data

        while True:
            # Search for the RTCM preamble.
            if not self.preamble_found:
                try:
                    idx = self.buffer.index(RTCM3_PREAMBLE)
                except ValueError:
                    self.logger.trace('Skipping %d bytes searching for preamble.' % len(self.buffer))
                    self.total_data_offset += len(self.buffer)
                    self.buffer = bytes()
                    break

                self.total_data_offset += idx
                self.buffer = self.buffer[idx:]

                self.logger.trace('Found preamble.')
                self.preamble_found = True

            if len(self.buffer) < RTCM3_HEADER_LENGTH + 2:
                break
            elif self.message_length is None:
                self.header = rtcm3_header.parse(self.buffer)
                self.logger.debug('Received RTCM %d message header. Waiting for payload. [payload_size=%d B]' %
                                (self.header.message_id, self.header.info.payload_length))
                self.message_length = RTCM3_HEADER_LENGTH + self.header.info.payload_length + RTCM3_CRC_LENGTH

            # Collect the payload and CRC.
            if len(self.buffer) >= self.message_length:
                self.logger.debug('Message complete. Validating CRC.')
                content_len = self.message_length - RTCM3_CRC_LENGTH
                expected_crc = self.calculate_crc24q(self.buffer[:content_len])
                received_crc = rtcm3_crc.parse(self.buffer[content_len:])
                if expected_crc == received_crc:
                    self.logger.debug(
                        'CRC passed. Dispatching message. [message=%d, size=%d B, checksum=0x%06X]' %
                        (self.header.message_id, self.message_length, received_crc))
                    self.logger.trace(''.join(['\\x%02X' % b for b in self.buffer[:self.message_length]]))
                    message = rtcm3_frame.parse(self.buffer[:self.message_length])
                    if return_size or return_bytes or return_offset:
                        ret = {'message': message}
                        if return_size:
                            ret['size'] = self.message_length
                        if return_bytes:
                            ret['bytes'] = self.buffer[:self.message_length]
                        if return_offset:
                            ret['offset'] = self.total_data_offset
                        messages.append(ret)
                    else:
                        messages.append(message)
                    if self.callback is not None:
                        self.callback(message)
                    # Message complete. Reset and search for the next preamble.
                    self.buffer = self.buffer[self.message_length:]
                    self.total_data_offset += self.message_length
                else:
                    self.logger.debug(
                        'CRC check failed, resyncing. [message=%d, size=%d B, crc=0x%62X, expected=0x%06X]' %
                        (self.header.message_id, self.message_length, received_crc, expected_crc))
                    # Add all but first byte of failed message back onto data buffer to retry parsing.
                    self.buffer = self.buffer[1:]
                    self.total_data_offset += 1

                self.header = None
                self.message_length = None
                self.preamble_found = False
            else:
                break

        return messages

    @classmethod
    def calculate_crc24q(cls, data):
        crc = 0
        for b in data:
            crc = ((crc << 8) & 0xFFFFFF) ^ cls.CRC24Q_TABLE[b ^ (crc >> 16)]
        return crc

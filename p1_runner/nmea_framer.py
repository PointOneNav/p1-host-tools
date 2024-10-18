import re
from enum import IntEnum

from . import trace as logging


class NMEAFramer(object):
    # A NMEA payload may contain any displayable ASCII character except $ and *, which are used to denote the start
    # and end of a message, respectively. This corresponds with all ASCII characters from 0x20-0x7E, excluding 0x24
    # and 0x2A.
    VALID_NMEA_CONTENTS = re.compile(r'^[\x20-\x23\x25-\x29\x2B-\x7E]+$')

    logger = logging.getLogger('point_one.nmea_framer')

    def __init__(self, return_offset=False):
        self.buffer = ''
        self.callback = None
        self.return_offset = return_offset
        self.next_msg_start_offset = 0

    def set_callback(self, callback):
        self.callback = callback

    def reset(self):
        self.buffer = ''

    def on_data(self, data):
        if isinstance(data, bytes):
            data = data.decode('latin-1')

        self.logger.trace('Received %d bytes. [%s]' % (len(data), str(data.encode('latin-1'))))

        buffer = self.buffer + data
        candidates = buffer.split('\n')
        self.buffer = candidates[-1]
        candidates = candidates[:-1]

        if len(candidates) > 0:
            self.logger.debug('Processing %d candidate messages.' % len(candidates))

        messages = []
        for i, candidate in enumerate(candidates):
            # Search for the start of a NMEA string, ignoring any content before it. Since we know each candidate string
            # begin after a \n, any characters before the $ can't possibly be a valid NMEA string:
            #   $bogus$GPGGA...*XX\r\n
            #         ^-- Try to find this
            start_idx = candidate.rfind('$')
            msg_start_offset = self.next_msg_start_offset + start_idx
            self.next_msg_start_offset += len(candidate) + 1
            if start_idx < 0:
                self.logger.debug('Sync byte not found. Discarding candidate %d. [size=%d B]' % (i, len(candidate)))
                self.logger.trace(candidate.encode('latin-1'))
                continue

            nmea_string = candidate[start_idx:] + '\n'
            candidate = candidate[start_idx + 1:]

            self.logger.trace('Testing candidate %d: %s' % (i, nmea_string.encode('latin-1')))

            # Strip off any trailing \r characters. Normally, a NMEA string should end in \r\n (\n already removed by
            # split() above), but we have seen some cases (RTKLIB) where there are multiple consecutive \r characters so
            # we ignore them all.
            candidate = candidate.rstrip('\r')

            # The string must contain a talker ID + message ID (typically 5+ chars, but we'll allow as small as 1 char),
            # plus a checksum (3 chars).
            if len(candidate) < (1 + 3):
                self.logger.debug('Candidate string too short. Discarding candidate %d. [size=%d B]' %
                                  (i, len(nmea_string)))
                continue

            # Now that we've stripped off \r\n, the last 3 characters should be a checksum (*XX).
            if candidate[-3] != '*':
                self.logger.debug('Checksum not found. Discarding candidate %d. [size=%d B]' % (i, len(nmea_string)))
                continue

            # Pull out the NMEA message ID for the prints below.
            id_end_idx = candidate.find(',')
            if id_end_idx < 0:
                id_end_idx = len(candidate) - 3
            message_id = candidate[:id_end_idx]

            # Extract the checksum and convert to an integer.
            try:
                expected_checksum = int(candidate[-2:], 16)
            except:
                self.logger.debug('Checksum bytes not valid. Discarding candidate %d. [message=%s, size=%d B]' %
                                  (i, message_id, len(nmea_string)))
                continue

            candidate = candidate[:-3]

            # Next, if there are any non-ASCII characters in the string, it can't be a NMEA string.
            if not re.match(self.VALID_NMEA_CONTENTS, candidate):
                self.logger.debug('Found non-ASCII contents. Discarding candidate %d. [message=%s, size=%d B]' %
                                  (i, message_id, len(nmea_string)))
                continue

            # Finally, validate the checksum.
            calculated_checksum = self._calculate_checksum(candidate, is_stripped=True)

            if expected_checksum == calculated_checksum:
                self.logger.debug(
                    'Checksum passed. Dispatching message %d. [message=%s, size=%d B, checksum=0x%02X]' %
                    (i, message_id, len(nmea_string), calculated_checksum))

                if self.return_offset:
                    nmea_msg = (nmea_string, msg_start_offset)
                else:
                    nmea_msg = nmea_string
                messages.append(nmea_msg)
                if self.callback is not None:
                    self.callback(nmea_msg)
            else:
                self.logger.debug('Checksum mismatch. Discarding candidate %d. [message=%s, size=%d B, '
                                  'checksum=0x%02X, expected_checksum=0x%02X]' %
                                  (i, message_id, len(nmea_string), calculated_checksum, expected_checksum))

        self.logger.debug('%d bytes remaining in the buffer.' % len(self.buffer))

        return messages

    @classmethod
    def _calculate_checksum(cls, data, is_stripped=False):
        if not is_stripped:
            if data[0] == '$':
                data = data[1:]

            checksum_idx = data.rfind('*')
            if checksum_idx >= 0:
                data = data[:checksum_idx]

        checksum = 0
        for c in data:
            checksum ^= ord(c)
        return checksum

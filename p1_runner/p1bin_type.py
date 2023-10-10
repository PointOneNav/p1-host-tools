import re
from typing import NamedTuple, List, Set, Union

from fusion_engine_client.utils.enum_utils import IntEnum

from p1_runner import trace as logging

_logger = logging.getLogger('point_one.p1bin_type')


class P1BinType(IntEnum):
    INVALID = 0xFFFF
    DEBUG = 0x01,
    RTCM3_POLARIS = 0x21
    EXTERNAL_UNFRAMED_GNSS = 0x42


class P1BinRecord(NamedTuple):
    unix_serialization_time: float
    message_type: P1BinType
    contents: bytes


def find_matching_p1bin_types(pattern: Union[str, List[str]]) -> Set[P1BinType]:
    """!
    @brief Find one or more @ref P1BinType%s that match the specified pattern(s).

    Examples:
    ```py
    find_matching_message_types('unframed_gnss')  # {P1BinType.EXTERNAL_UNFRAMED_GNSS}
    find_matching_message_types('external_unframed_gnss')  # {P1BinType.EXTERNAL_UNFRAMED_GNSS}
    find_matching_message_types('EXTERNAL_UNFRAMED_GNSS')  # {P1BinType.EXTERNAL_UNFRAMED_GNSS}
    find_matching_message_types('e')  # ValueError - multiple possible matches
    find_matching_message_types('e*')  # {P1BinType.DEBUG, P1BinType.EXTERNAL_UNFRAMED_GNSS}
    find_matching_message_types('DEBUG,UNFRAMED_GNSS')  # {P1BinType.DEBUG, P1BinType.UNFRAMED_GNSS}
    find_matching_message_types(['DEBUG', 'UNFRAMED_GNSS'])  # {P1BinType.DEBUG, P1BinType.UNFRAMED_GNSS}
    ```

    @param pattern A `list` or a comma-separated string containing one or more search patterns. Patterns may match
            part or all of a class name. Patterns may include wildcards (`*`) to match multiple classes. If no
            wildcards are specified and multiple classes match, a single result will be returned if there is an exact
            match. All matches are case-insensitive.

    @return A set containing the matching @ref P1BinType.
    """
    # Generate a list of requested types.
    requested_types = []

    if isinstance(pattern, str):
        patterns = [pattern]
    else:
        patterns = pattern

    # Split and flatten comma-separated lists of names/patterns:
    #   ['VersionInfoMessage', 'PoseMessage,GNSS*'] ->
    #   ['VersionInfoMessage', 'PoseMessage', 'GNSS*']
    requested_types = [p.strip()
                       for entry in patterns for p in entry.split(',')]

    # Now find matches to each pattern.
    result = set()
    for pattern in requested_types:
        try:
            int_val = int(pattern)
            result.add(P1BinType(int_val))
        except:
            allow_multiple = '*' in pattern
            re_pattern = pattern.replace('*', '.*')
            # if pattern[0] != '^':
            #     re_pattern = r'.*' + re_pattern
            # if pattern[-1] != '$':
            #     re_pattern += '.*'

            # Check for matches.
            matched_types = [v for v in P1BinType
                             if re.match(re_pattern, v.name, flags=re.IGNORECASE)]

            # Fall back to partial match.
            if len(matched_types) == 0:
                matched_types = [v for v in P1BinType
                                 if re.search(re_pattern, v.name, flags=re.IGNORECASE)]

            if len(matched_types) == 0:
                _logger.warning(
                    "No message types matching pattern '%s'." % pattern)
                continue

            # If there are still too many matches, fail.
            if len(matched_types) > 1 and not allow_multiple:
                raise ValueError("Pattern '%s' matches multiple message types:%s\n\nAdd a wildcard (%s*) to display "
                                 "all matching types." %
                                 (pattern, ''.join(['\n  %s' % c for c in matched_types]), pattern))
            # Otherwise, update the set of message types.
            else:
                result.update(matched_types)

    return result

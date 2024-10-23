import logging
from pathlib import Path
from typing import List, NamedTuple, Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger('point_one.hitl.slack')

BOT_ICON_URL = 'https://ca.slack-edge.com/T07VC1TD4-U07T0UTJKC2-2eded3019586-512'
BOT_NAME = 'HITL'


class FileEntry(NamedTuple):
    file_path: Path
    comment: str


def send_slack_message(channel: str, token: str, message: str, files: Optional[List[FileEntry]] = None) -> bool:
    client = WebClient(token=token)
    try:
        response = client.chat_postMessage(
            channel=channel,
            text=message,
            type='mrkdwn',
            # While the name and Icon can be customized for postMessage, file uploads have to use defaults.
            username=BOT_NAME,
            icon_url=BOT_ICON_URL)
        if files is not None:
            message_ts = response['ts']
            for file in files:
                client.files_upload_v2(
                    channel=channel,
                    file=str(file.file_path),
                    initial_comment=file.comment,
                    thread_ts=message_ts
                )
        return True
    except SlackApiError as e:
        # str like 'invalid_auth', 'channel_not_found'
        logger.warning(f"Error posting to slack: {e.response['error']}")
        return False


def _test_main():
    import os
    channel = os.environ['SLACK_CHANNEL']
    token = os.environ['SLACK_BOT_TOKEN']
    msg = f'''\
*HITL {"CONFIGURATION"} Build Failed*
See [Jenkins Build](https://build.pointonenav.com/view/HITL/job/hitl-atlas/lastBuild/) for info on PR and full job log.
---
HITL runner timed out, killing process.
See attached for full report.
'''
    send_slack_message(channel, token, msg)


if __name__ == '__main__':
    _test_main()

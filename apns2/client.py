import collections
import json
import logging
import time
import typing
import weakref
from enum import Enum
from threading import Thread
from typing import Dict, Iterable, Optional, Tuple, Union
import httpx

from .credentials import CertificateCredentials, Credentials
from .errors import ConnectionFailed, exception_class_for_reason
# We don't generally need to know about the Credentials subclasses except to
# keep the old API, where APNsClient took a cert_file
from .payload import Payload


class NotificationPriority(Enum):
    Immediate = '10'
    Delayed = '5'


class NotificationType(Enum):
    Alert = 'alert'
    Background = 'background'
    VoIP = 'voip'
    Complication = 'complication'
    FileProvider = 'fileprovider'
    MDM = 'mdm'


RequestStream = collections.namedtuple('RequestStream', ['response', 'token'])
Notification = collections.namedtuple('Notification', ['token', 'payload'])

DEFAULT_APNS_PRIORITY = NotificationPriority.Immediate
CONCURRENT_STREAMS_SAFETY_MAXIMUM = 1000
MAX_CONNECTION_RETRIES = 3

logger = logging.getLogger(__name__)


class APNsClient(object):
    SANDBOX_SERVER = 'api.development.push.apple.com'
    LIVE_SERVER = 'api.push.apple.com'

    DEFAULT_PORT = 443
    ALTERNATIVE_PORT = 2197

    def __init__(self,
                 credentials: Union[Credentials, str],
                 use_sandbox: bool = False, use_alternative_port: bool = False, proto: Optional[str] = None,
                 json_encoder: Optional[type] = None, password: Optional[str] = None,
                 proxy_host: Optional[str] = None, proxy_port: Optional[int] = None,
                 heartbeat_period: Optional[float] = None) -> None:
        if isinstance(credentials, str):
            self.__credentials = CertificateCredentials(credentials, password)  # type: Credentials
        else:
            self.__credentials = credentials
        self._init_connection(use_sandbox, use_alternative_port, proto, proxy_host, proxy_port)

        if heartbeat_period:
            self._start_heartbeat(heartbeat_period)

        self.__json_encoder = json_encoder
        self.__max_concurrent_streams = 0
        self.__previous_server_max_concurrent_streams = None

    def _init_connection(self, use_sandbox: bool, use_alternative_port: bool, proto: Optional[str],
                         proxy_host: Optional[str], proxy_port: Optional[int]) -> None:
        self._server = self.SANDBOX_SERVER if use_sandbox else self.LIVE_SERVER
        self._port = self.ALTERNATIVE_PORT if use_alternative_port else self.DEFAULT_PORT
        self._connection = self.__credentials.create_connection(self._server, self._port, proto, proxy_host, proxy_port)

    def _start_heartbeat(self, heartbeat_period: float) -> None:
        # httpx doesn't support ping, so this is a no-op
        pass

    def send_notification(self, token_hex: str, notification: Payload, topic: Optional[str] = None,
                          priority: NotificationPriority = NotificationPriority.Immediate,
                          expiration: Optional[int] = None, collapse_id: Optional[str] = None,
                          *, push_type: Optional[NotificationType] = None) -> None:
        response = self.send_notification_async(
            token_hex, notification, topic, priority, expiration, collapse_id, push_type
        )
        result = self.get_notification_result(response)
        if result != 'Success':
            if isinstance(result, tuple):
                reason, info = result
                raise exception_class_for_reason(reason)(info)
            else:
                raise exception_class_for_reason(result)

    def send_voip_notification(self, token_hex: str, notification: Payload, topic: str,
                               collapse_id: Optional[str] = None) -> None:
        voip_topic = topic if topic.endswith('.voip') else f'{topic}.voip'
        self.send_notification(
            token_hex,
            notification,
            topic=voip_topic,
            priority=NotificationPriority.Immediate,
            expiration=0,
            collapse_id=collapse_id,
            push_type=NotificationType.VoIP,
        )

    def send_notification_async(self, token_hex: str, notification: Payload, topic: Optional[str] = None,
                                priority: NotificationPriority = NotificationPriority.Immediate,
                                expiration: Optional[int] = None, collapse_id: Optional[str] = None,
                                push_type: Optional[NotificationType] = None) -> httpx.Response:
        json_str = json.dumps(notification.dict(), cls=self.__json_encoder, ensure_ascii=False, separators=(',', ':'))
        json_payload = json_str.encode('utf-8')

        headers = {}

        inferred_push_type = None  # type: Optional[str]
        if topic is not None:
            headers['apns-topic'] = topic
            if topic.endswith('.voip'):
                inferred_push_type = NotificationType.VoIP.value
            elif topic.endswith('.complication'):
                inferred_push_type = NotificationType.Complication.value
            elif topic.endswith('.pushkit.fileprovider'):
                inferred_push_type = NotificationType.FileProvider.value
            elif any([
                notification.alert is not None,
                notification.badge is not None,
                notification.sound is not None,
            ]):
                inferred_push_type = NotificationType.Alert.value
            else:
                inferred_push_type = NotificationType.Background.value

        if push_type:
            inferred_push_type = push_type.value

        if inferred_push_type:
            headers['apns-push-type'] = inferred_push_type

        if priority != DEFAULT_APNS_PRIORITY or inferred_push_type == NotificationType.VoIP.value:
            headers['apns-priority'] = priority.value

        if expiration is not None:
            headers['apns-expiration'] = '%d' % expiration

        auth_header = self.__credentials.get_authorization_header(topic)
        if auth_header is not None:
            headers['authorization'] = auth_header

        if collapse_id is not None:
            headers['apns-collapse-id'] = collapse_id

        url = f'https://{self._server}:{self._port}/3/device/{token_hex}'
        response = self._connection.post(url, content=json_payload, headers=headers)
        return response

    def get_notification_result(self, response: httpx.Response) -> Union[str, Tuple[str, str]]:
        """
        Get result for a given response
        The function returns: 'Success' or 'failure reason' or ('Unregistered', timestamp)
        """
        if response.status_code == 200:
            return 'Success'
        else:
            raw_data = response.read().decode('utf-8')
            data = json.loads(raw_data)  # type: Dict[str, str]
            if response.status_code == 410:
                return data['reason'], data['timestamp']
            else:
                return data['reason']

    def send_notification_batch(self, notifications: Iterable[Notification], topic: Optional[str] = None,
                                priority: NotificationPriority = NotificationPriority.Immediate,
                                expiration: Optional[int] = None, collapse_id: Optional[str] = None,
                                push_type: Optional[NotificationType] = None) -> Dict[str, Union[str, Tuple[str, str]]]:
        """
        Send a notification to a list of tokens in batch. Instead of sending a synchronous request
        for each token, send multiple requests concurrently. This is done on the same connection,
        using HTTP/2 streams (one request per stream).

        APNs allows many streams simultaneously, but the number of streams can vary depending on
        server load. This method reads the SETTINGS frame sent by the server to figure out the
        maximum number of concurrent streams. Typically, APNs reports a maximum of 500.

        The function returns a dictionary mapping each token to its result. The result is "Success"
        if the token was sent successfully, or the string returned by APNs in the 'reason' field of
        the response, if the token generated an error.
        """
        notification_iterator = iter(notifications)
        next_notification = next(notification_iterator, None)
        # Make sure we're connected to APNs, so that we receive and process the server's SETTINGS
        # frame before starting to send notifications.
        self.connect()

        results = {}
        open_streams = collections.deque()  # type: typing.Deque[RequestStream]
        # Loop on the tokens, sending as many requests as possible concurrently to APNs.
        # When reaching the maximum concurrent streams limit, wait for a response before sending
        # another request.
        while len(open_streams) > 0 or next_notification is not None:
            # Update the max_concurrent_streams on every iteration since a SETTINGS frame can be
            # sent by the server at any time.
            self.update_max_concurrent_streams()
            if next_notification is not None and len(open_streams) < self.__max_concurrent_streams:
                logger.info('Sending to token %s', next_notification.token)
                response = self.send_notification_async(next_notification.token, next_notification.payload, topic,
                                                         priority, expiration, collapse_id, push_type)
                open_streams.append(RequestStream(response, next_notification.token))

                next_notification = next(notification_iterator, None)
                if next_notification is None:
                    # No tokens remaining. Proceed to get results for pending requests.
                    logger.info('Finished sending all tokens, waiting for pending requests.')
            else:
                # We have at least one request waiting for response (otherwise we would have either
                # sent new requests or exited the while loop.) Wait for the first outstanding stream
                # to return a response.
                pending_stream = open_streams.popleft()
                result = self.get_notification_result(pending_stream.response)
                logger.info('Got response for %s: %s', pending_stream.token, result)
                results[pending_stream.token] = result

        return results

    def update_max_concurrent_streams(self) -> None:
        # httpx does not expose http2 settings, so we'll use a safe default
        max_concurrent_streams = CONCURRENT_STREAMS_SAFETY_MAXIMUM

        if max_concurrent_streams == self.__previous_server_max_concurrent_streams:
            # The server hasn't issued an updated SETTINGS frame.
            return

        self.__previous_server_max_concurrent_streams = max_concurrent_streams
        # Handle and log unexpected values sent by APNs, just in case.
        if max_concurrent_streams > CONCURRENT_STREAMS_SAFETY_MAXIMUM:
            logger.warning('APNs max_concurrent_streams too high (%s), resorting to default maximum (%s)',
                           max_concurrent_streams, CONCURRENT_STREAMS_SAFETY_MAXIMUM)
            self.__max_concurrent_streams = CONCURRENT_STREAMS_SAFETY_MAXIMUM
        elif max_concurrent_streams < 1:
            logger.warning('APNs reported max_concurrent_streams less than 1 (%s), using value of 1',
                           max_concurrent_streams)
            self.__max_concurrent_streams = 1
        else:
            logger.info('APNs set max_concurrent_streams to %s', max_concurrent_streams)
            self.__max_concurrent_streams = max_concurrent_streams

    def connect(self) -> None:
        """
        Establish a connection to APNs. If already connected, the function does nothing. If the
        connection fails, the function retries up to MAX_CONNECTION_RETRIES times.
        With httpx, connections are managed automatically, so this is a no-op.
        """
        pass

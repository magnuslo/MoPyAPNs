import json
from unittest.mock import patch, MagicMock

import pytest
from httpx import Response

from apns2.client import APNsClient, Notification, NotificationPriority, NotificationType
from apns2.credentials import Credentials
from apns2.errors import ConnectionFailed, APNsException
from apns2.payload import Payload

TOPIC = 'com.example.myapp'


@pytest.fixture
def credentials():
    return Credentials()


@pytest.fixture
def client(credentials):
    return APNsClient(credentials)


@pytest.fixture
def payload():
    return Payload(alert='message', badge=1, sound='chime')


@pytest.fixture
def tokens():
    return [
        '0000000000000000000000000000000000000000000000000000000000000000',
        '0000000000000000000000000000000000000000000000000000000000000001',
        '0000000000000000000000000000000000000000000000000000000000000002',
        '0000000000000000000000000000000000000000000000000000000000000003',
        '0000000000000000000000000000000000000000000000000000000000000004',
        '0000000000000000000000000000000000000000000000000000000000000005',
    ]


@pytest.fixture
def notifications(tokens, payload):
    return [Notification(token, payload) for token in tokens]


def test_send_notification(client, payload, httpx_mock, tokens):
    httpx_mock.add_response(status_code=200)
    client.send_notification(tokens[0], payload, topic=TOPIC)


def test_send_notification_accepts_explicit_push_type(client, payload, httpx_mock, tokens):
    # Given
    httpx_mock.add_response(status_code=200)

    # When
    client.send_notification(
        tokens[0],
        payload,
        topic=TOPIC,
        push_type=NotificationType.Background,
    )

    # Then
    request = httpx_mock.get_request()
    assert request.headers['apns-push-type'] == NotificationType.Background.value


def test_send_voip_notification_sets_pushkit_headers(client, payload, httpx_mock, tokens):
    # Given
    httpx_mock.add_response(status_code=200)

    # When
    client.send_voip_notification(tokens[0], payload, topic=TOPIC)

    # Then
    request = httpx_mock.get_request()
    assert request.headers['apns-push-type'] == NotificationType.VoIP.value
    assert request.headers['apns-topic'] == f'{TOPIC}.voip'
    assert request.headers['apns-priority'] == NotificationPriority.Immediate.value
    assert request.headers['apns-expiration'] == '0'


def test_send_notification_keeps_normal_alert_defaults(client, payload, httpx_mock, tokens):
    # Given
    httpx_mock.add_response(status_code=200)

    # When
    client.send_notification(tokens[0], payload, topic=TOPIC)

    # Then
    request = httpx_mock.get_request()
    assert request.headers['apns-push-type'] == NotificationType.Alert.value
    assert 'apns-priority' not in request.headers
    assert 'apns-expiration' not in request.headers


def test_send_notification_raises_apns_exception(client, payload, httpx_mock, tokens):
    response_payload = {'reason': 'BadDeviceToken'}
    httpx_mock.add_response(status_code=400, json=response_payload)
    with pytest.raises(APNsException):
        client.send_notification(tokens[0], payload, topic=TOPIC)


def test_send_notification_batch_returns_results_in_order(client, notifications, httpx_mock):
    for _ in notifications:
        httpx_mock.add_response(status_code=200)
    results = client.send_notification_batch(notifications, TOPIC)
    assert len(results) == len(notifications)


def test_send_notification_batch_reports_different_results(client, notifications, httpx_mock):
    statuses = {
        'Success': {'status_code': 200},
        'BadDeviceToken': {'status_code': 400, 'json': {'reason': 'BadDeviceToken'}},
        'DeviceTokenNotForTopic': {'status_code': 400, 'json': {'reason': 'DeviceTokenNotForTopic'}},
        'PayloadTooLarge': {'status_code': 413, 'json': {'reason': 'PayloadTooLarge'}},
    }
    for status in statuses.keys():
        httpx_mock.add_response(**statuses[status])

    # Construct a list of notifications that will yield one of each status
    test_notifications = [
        notifications[0], # Success
        notifications[1], # BadDeviceToken
        notifications[2], # DeviceTokenNotForTopic
        notifications[3], # PayloadTooLarge
    ]

    results = client.send_notification_batch(test_notifications, TOPIC)
    assert results == {
        notifications[0].token: 'Success',
        notifications[1].token: 'BadDeviceToken',
        notifications[2].token: 'DeviceTokenNotForTopic',
        notifications[3].token: 'PayloadTooLarge',
    }

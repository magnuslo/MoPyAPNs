# MoPyAPNs

[![PyPI version](https://img.shields.io/pypi/v/pyapns.svg)](https://pypi.org/project/pyapns/)
[![PyPI version](https://img.shields.io/pypi/pyversions/pyapns.svg)](https://pypi.org/project/pyapns/)
[![Build Status](https://drone.pr0ger.dev/api/badges/Pr0Ger/PyAPNs2/status.svg)](https://drone.pr0ger.dev/Pr0Ger/PyAPNs2)

Python library for interacting with the Apple Push Notification service (APNs) via HTTP/2 protocol using httpx

## Installation

Either download the source from GitHub or use pip:

    pip install mopyapns

## Sample usage

```python
import collections
from apns2.client import APNsClient
from apns2.payload import Payload

token_hex = 'b5bb9d8014a0f9b1d61e21e796d78dccdf1352f23cd32812f4850b87'
payload = Payload(alert="Hello World!", sound="default", badge=1)
topic = 'com.example.App'
client = APNsClient('key.pem', use_sandbox=False, use_alternative_port=False)
client.send_notification(token_hex, payload, topic)

# To send a notification with a specific interruption level (iOS 15+)
payload = Payload(alert="Hello World!", sound="default", interruption_level='time-sensitive')
client.send_notification(token_hex, payload, topic)

# To send multiple notifications in a batch
Notification = collections.namedtuple('Notification', ['token', 'payload'])
notifications = [Notification(payload=payload, token=token_hex)]
client.send_notification_batch(notifications=notifications, topic=topic)

# To use token based authentication
from apns2.credentials import TokenCredentials

auth_key_path = 'path/to/auth_key'
auth_key_id = 'app_auth_key_id'
team_id = 'app_team_id'
token_credentials = TokenCredentials(auth_key_path=auth_key_path, auth_key_id=auth_key_id, team_id=team_id)
client = APNsClient(credentials=token_credentials, use_sandbox=False)
client.send_notification_batch(notifications=notifications, topic=topic)
```

## Requirements

- Python 3.10 or later
- httpx 0.28.1 or later (with http2 support)
- cryptography 45.0.4 or later
- PyJWT 2.10.1 or later

## Further Info

[iOS Reference Library: Local and Push Notification Programming Guide][a1]

## Contributing

To develop mopyapns, check out the code and install dependencies using PDM:

```shell
# Clone the source code.
git clone https://github.com/magnuslo/mopyapns.git
cd mopyapns
# Create a virtualenv and install dependencies.
pdm install
```

To run the tests:

```shell
pdm run pytest
```

To run the linter:

```shell
pdm run pylint --reports=n apns2 test
```

## License

mopyapns is distributed under the terms of the MIT license.

See [LICENSE](LICENSE) file for the complete license details.

[a1]:https://developer.apple.com/documentation/usernotifications?language=objc
